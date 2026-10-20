"""Private shutdown compatibility preserves source, scheduling and verdicts."""
import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from companion import cocotb_runtime as runtime
import vwb


class ShutdownCompatibilityTests(unittest.TestCase):
    def fake(self, version=runtime.AFFECTED_VERSION, simulator="Icarus Verilog"):
        cocotb = ModuleType("cocotb"); cocotb.__path__ = []
        cocotb.__version__ = version; cocotb.SIM_NAME = simulator
        handle = ModuleType("cocotb.handle")
        handle._write_calls = {"signal": ("write", "deposit", 1)}
        handle._apply_writes_cb = SimpleNamespace(cancel=Mock())
        init = ModuleType("cocotb._init"); callbacks = []
        init._register_shutdown_callback = callbacks.append
        cocotb.handle = handle; cocotb._init = init
        return {"cocotb": cocotb, "cocotb.handle": handle, "cocotb._init": init}, handle, callbacks

    def test_only_exact_version_uses_default_startup_and_explicit_config_always_wins(self):
        self.assertEqual(runtime.automatic_startup(runtime.AFFECTED_VERSION, {}), runtime.STARTUP_USERS)
        for version in ("2.1.0", "2.1.1", "2.0.1", "2.1.0.dev0+other", ""):
            self.assertIsNone(runtime.automatic_startup(version, {}))
        for name in runtime.STARTUP_ENVIRONMENT:
            for value in ("", "operator-configured"):
                self.assertIsNone(runtime.automatic_startup(runtime.AFFECTED_VERSION, {name: value}))

    def test_registration_never_mutates_live_writes_and_final_cleanup_cancels_once(self):
        modules, handle, callbacks = self.fake(); pending = handle._apply_writes_cb
        with patch.dict(sys.modules, modules):
            self.assertTrue(runtime.install([])); self.assertFalse(runtime.install([]))
        pending.cancel.assert_not_called(); self.assertEqual(len(handle._write_calls), 1)
        self.assertEqual(len(callbacks), 1)
        callbacks[0]()
        pending.cancel.assert_called_once_with(); self.assertIsNone(handle._apply_writes_cb)
        self.assertEqual(handle._write_calls, {})
        callbacks[0](); pending.cancel.assert_called_once_with()

    def test_other_builds_and_simulators_are_untouched(self):
        for version, simulator in (("2.1.0", "Icarus Verilog"), (runtime.AFFECTED_VERSION, "GHDL"), (runtime.AFFECTED_VERSION, "Verilator")):
            modules, handle, callbacks = self.fake(version, simulator); pending = handle._apply_writes_cb
            with patch.dict(sys.modules, modules): self.assertFalse(runtime.install([]))
            self.assertEqual(callbacks, []); self.assertIs(handle._apply_writes_cb, pending)
            pending.cancel.assert_not_called(); self.assertEqual(len(handle._write_calls), 1)

    def test_known_build_requires_expected_features_and_does_not_suppress_cancellation_errors(self):
        modules, handle, callbacks = self.fake(); del handle._write_calls
        with patch.dict(sys.modules, modules), self.assertRaisesRegex(RuntimeError, "unsupported shutdown layout"): runtime.install([])
        self.assertEqual(callbacks, [])
        modules, handle, callbacks = self.fake(); handle._apply_writes_cb.cancel.side_effect = RuntimeError("cannot cancel")
        with patch.dict(sys.modules, modules): runtime.install([])
        with self.assertRaisesRegex(RuntimeError, "cannot cancel"): callbacks[0]()
        self.assertEqual(len(handle._write_calls), 1)

    def test_trusted_staging_is_exact_idempotent_and_rejects_different_or_symlinked_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary); staged = runtime.stage_runtime(directory)
            self.assertEqual(staged.read_bytes(), Path(runtime.__file__).read_bytes())
            self.assertEqual(runtime.stage_runtime(directory), staged)
            staged.write_text("different")
            with self.assertRaisesRegex(ValueError, "different"): runtime.stage_runtime(directory)
            staged.unlink()
            try: staged.symlink_to(runtime.__file__)
            except OSError: return
            with self.assertRaisesRegex(ValueError, "different"): runtime.stage_runtime(directory)

    def test_cli_loads_known_build_hook_and_stages_only_private_module_before_project_imports(self):
        with tempfile.TemporaryDirectory(prefix="vwb runtime µ ") as temporary:
            root = Path(temporary); (root / "src").mkdir(); (root / "test").mkdir(); work = root / "build/sim/case"; work.mkdir(parents=True)
            wb = vwb.Workbench(root, root / "src", root / "test", root / "build")
            values = {"--python-bin": "/runtime/python", "--libpython": "/runtime/libpython.so", "--pygpi-entry-point": "/runtime/libcocotb.so,initialize", "--version": runtime.AFFECTED_VERSION}
            def run(command, **kwargs): return subprocess.CompletedProcess(command, 0, values[command[-1]], "")
            with patch.dict(os.environ, {}, clear=True), patch.object(wb, "require_tool", return_value="cocotb-config"), patch.object(wb, "run", side_effect=run):
                wb._load_cocotb_runtime()
                spec = vwb.TestSpec("dut", "cocotb", root / "test/check.py")
                environment, _ = wb._cocotb_environment(spec, work, SimpleNamespace(testcase=None, seed=None), "verilog")
            self.assertEqual(environment["PYGPI_USERS"], runtime.STARTUP_USERS)
            self.assertEqual(environment["PYTHONPATH"].split(os.pathsep)[0], str(work))
            self.assertEqual((work / (runtime.RUNTIME_MODULE + ".py")).read_bytes(), Path(runtime.__file__).read_bytes())


@unittest.skipUnless(os.environ.get("VWB_COCOTB_RUNTIME_TEST_ACQUISITION"), "Set VWB_COCOTB_RUNTIME_TEST_ACQUISITION to run the real managed runtime regressions")
class ManagedRuntimeTests(unittest.TestCase):
    def setUp(self):
        acquisition = json.loads(Path(os.environ["VWB_COCOTB_RUNTIME_TEST_ACQUISITION"]).read_text())
        self.suite = Path(acquisition["suitePath"])
        self.environment = os.environ.copy()
        for name in ("LD_LIBRARY_PATH", "PYTHONHOME", "PYTHONPATH", *runtime.STARTUP_ENVIRONMENT): self.environment.pop(name, None)
        self.environment.update(acquisition["environment"])
        self.python = acquisition.get("probe", {}).get("python") or str(self.suite / "py3bin/python3")
        version = subprocess.run([str(self.suite / "bin/cocotb-config"), "--version"], env=self.environment, capture_output=True, text=True, check=True).stdout.strip()
        if version != runtime.AFFECTED_VERSION: self.skipTest("The managed runtime is not the affected development build")
        self.temporary = tempfile.TemporaryDirectory(prefix="vwb pending callback µ "); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name); (self.root / "src").mkdir(); (self.root / "test").mkdir()
        (self.root / "test/__init__.py").touch()
        (self.root / "src/dut.v").write_text("module dut(input wire a,b,output wire y); assign y=a^b; endmodule\n")

    def run_fixture(self, *, fail=False, explicit_original_startup=False):
        body = 'import cocotb\nfrom cocotb.triggers import Timer\n@cocotb.test()\nasync def truth_table(dut):\n    for value in range(4):\n        dut.a.value=value>>1\n        dut.b.value=value&1\n        await Timer(1,unit="ns")\n        assert int(dut.y.value)==((value>>1)^(value&1))\n    dut.a.value=0\n'
        if fail: body += '    assert False, "VWB_PENDING_WRITE_NEGATIVE_CONTROL"\n'
        test = self.root / "test/check.py"; test.write_text(body)
        environment = self.environment.copy()
        if explicit_original_startup: environment["PYGPI_USERS"] = runtime.STARTUP_USERS.replace(runtime.RUNTIME_MODULE + ":install,", "")
        command = [self.python, str(Path(vwb.__file__)), "--root", str(self.root), "--src-dir", "src", "--test-dir", "test", "--build-dir", "build", "--verbose", "--color", "never", "test", "dut", "--test", "test/check.py", "--test-language", "cocotb", "--seed", "1"]
        result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=30)
        import xml.etree.ElementTree as ET
        xml = ET.parse(next((self.root / "build").rglob("results.xml")))
        return result, xml

    def test_real_final_queued_write_passes_with_unchanged_truth_table(self):
        result, xml = self.run_fixture()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(list(xml.iter("testcase"))), 1); self.assertEqual(list(xml.iter("failure")), [])
        self.assertEqual(list(xml.iter("skipped")), [])
        staged = list((self.root / "build").rglob(runtime.RUNTIME_MODULE + ".py"))
        self.assertEqual(len(staged), 1); self.assertEqual(staged[0].read_bytes(), Path(runtime.__file__).read_bytes())

    def test_real_assertion_failure_remains_failure_with_pending_write(self):
        result, xml = self.run_fixture(fail=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(list(xml.iter("failure"))), 1)
        self.assertIn("VWB_PENDING_WRITE_NEGATIVE_CONTROL", result.stdout + result.stderr)

    def test_explicit_original_startup_keeps_real_crash_a_failure(self):
        result, xml = self.run_fixture(explicit_original_startup=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(list(xml.iter("testcase"))), 1); self.assertEqual(list(xml.iter("failure")), [])
        self.assertEqual(list((self.root / "build").rglob(runtime.RUNTIME_MODULE + ".py")), [])


if __name__ == "__main__": unittest.main()
