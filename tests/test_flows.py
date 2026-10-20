"""Original project entrypoints require fresh, complete verification results."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from companion import flows


class ProjectFlowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="vwb upstream flow ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.environment = {
            "PATH": str(Path(sys.executable).parent) + os.pathsep + os.defpath,
            "PYTHONNOUSERSITE": "1",
        }
        if os.name == "nt":
            self.environment["SystemRoot"] = os.environ["SystemRoot"]
        self.python = "python" if shutil.which("python", path=self.environment["PATH"]) else "python3"
        self.flow = {
            "id": "original-bench",
            "driver": "python",
            "simulator": "icarus",
            "cwd": ".",
            "argv": [self.python, "bench.py"],
            "requirements": {"tools": [self.python]},
            "expected": {"kind": "junit", "paths": ["results.xml"], "minimum_tests": 1},
        }
        self.script("from pathlib import Path\nPath('results.xml').write_text('<testsuite><testcase name=\"upstream_assertions\"/></testsuite>')\n")

    def script(self, source):
        (self.root / "bench.py").write_text(source, encoding="utf-8")

    def manifest(self, flow=None):
        selected = self.flow if flow is None else flow
        (self.root / flows.MANIFEST).write_text(json.dumps({"schema_version": 1, "flows": [selected]}), encoding="utf-8")
        return flows.load_flows(self.root)["flows"][0]

    def run_bench(self, flow=None, **kwargs):
        return flows.run_flow(self.root, self.manifest(flow), environment=self.environment, timeout=5, **kwargs)

    def write_report(self, xml):
        self.script("from pathlib import Path\nPath('results.xml').write_text(" + repr(xml) + ")\n")

    def test_real_subprocess_requires_fresh_passing_junit_and_records_artifact(self):
        report = self.run_bench()
        self.assertEqual(report["state"], "passed", report)
        self.assertEqual(report["verdict"]["tests"], 1)
        self.assertEqual(report["verdict"]["failures"], 0)
        artifact = report["verdict"]["artifacts"][0]
        self.assertEqual(artifact["path"], "results.xml")
        self.assertEqual(artifact["bytes"], (self.root / "results.xml").stat().st_size)
        self.assertRegex(artifact["sha256"], r"^[a-f0-9]{64}$")

    def test_project_profile_resets_incompatible_launcher_environment(self):
        flow = deepcopy(self.flow)
        flow['unset_environment'] = ['PYTHONHOME', 'PYTHONPATH']
        flow['environment'] = {'PYTHONPATH': str(self.root)}
        self.script("import os\nfrom pathlib import Path\nassert 'PYTHONHOME' not in os.environ\nassert os.environ['PYTHONPATH'] == str(Path.cwd())\nPath('results.xml').write_text('<testsuite><testcase name=\"selected_profile\"/></testsuite>')\n")
        self.environment.update(PYTHONHOME='/incompatible/launcher/python', PYTHONPATH='/incompatible/launcher/site')
        self.assertEqual(self.run_bench(flow)['state'], 'passed')

    def test_malformed_manifest_shapes_are_rejected_before_execution(self):
        for value in ([], None, {'schema_version': 1, 'flows': [dict(self.flow, requirements=[])]},
                      {'schema_version': 1, 'flows': [dict(self.flow, expected=[])]},
                      {'schema_version': 1, 'flows': [dict(self.flow, simulators=[])]},
                      {'schema_version': 1, 'flows': [self.flow], 'source_files': '../escape'}):
            with self.subTest(value=value):
                (self.root / flows.MANIFEST).write_text(json.dumps(value), encoding='utf-8')
                with self.assertRaises(ValueError):
                    flows.load_flows(self.root)

    def test_zero_exit_does_not_hide_original_assertion_failure(self):
        self.write_report('<testsuite><testcase name="original"><failure message="data mismatch"/></testcase></testsuite>')
        report = self.run_bench()
        self.assertEqual(report["steps"][0]["returncode"], 0)
        self.assertEqual(report["state"], "failed")
        self.assertEqual(report["verdict"]["failures"], 1)

    def test_missing_or_unchanged_previous_report_cannot_pass(self):
        self.script("print('upstream process exited normally')\n")
        for stale in (False, True):
            with self.subTest(stale=stale):
                if stale:
                    (self.root / "results.xml").write_text('<testsuite><testcase name="old"/></testsuite>')
                report = self.run_bench()
                self.assertEqual(report["state"], "failed")
                self.assertIn("Missing or stale", report["verdict"]["error"])

    def test_empty_skipped_setup_errors_and_suite_counters_fail(self):
        reports = {
            "empty": "<testsuite tests='0'/>",
            "skip": "<testsuite><testcase><skipped/></testcase></testsuite>",
            "setup_error": "<testsuite><testcase><error/></testcase></testsuite>",
            "counter_error": "<testsuite errors='1'><testcase/></testsuite>",
            "counter_failure": "<testsuite failures='1'><testcase/></testsuite>",
            "counter_skip": "<testsuites skipped='1'><testsuite><testcase/></testsuite></testsuites>",
            "broken_xml": "<testsuite>",
        }
        for reason, xml in reports.items():
            with self.subTest(reason=reason):
                self.write_report(xml)
                self.assertEqual(self.run_bench()["state"], "failed")

    def test_partial_test_count_cannot_satisfy_full_bench_contract(self):
        flow = deepcopy(self.flow)
        flow["expected"]["minimum_tests"] = 2
        report = self.run_bench(flow)
        self.assertEqual(report["verdict"]["tests"], 1)
        self.assertEqual(report["state"], "failed")

    def test_duplicate_junit_paths_cannot_count_one_execution_twice(self):
        flow = deepcopy(self.flow)
        flow["expected"].update(paths=["results.xml", "results.xml"], minimum_tests=2)
        with self.assertRaisesRegex(ValueError, "unique"):
            self.manifest(flow)

    def test_distinct_hardlink_paths_cannot_count_one_report_twice(self):
        probe = self.root / "hardlink-probe"
        linked = self.root / "hardlink-probe-copy"
        probe.write_text("probe")
        try:
            os.link(probe, linked)
        except OSError as error:
            self.skipTest("Hardlink creation unavailable: " + str(error))
        finally:
            linked.unlink(missing_ok=True)
            probe.unlink(missing_ok=True)
        flow = deepcopy(self.flow)
        flow["expected"].update(paths=["results.xml", "same-report.xml"], minimum_tests=2)
        self.script("from pathlib import Path\nimport os\nPath('results.xml').write_text('<testsuite><testcase name=\"only_one\"/></testsuite>')\nos.link('results.xml', 'same-report.xml')\n")
        report = self.run_bench(flow)
        self.assertEqual(report["steps"][0]["returncode"], 0)
        self.assertEqual(report["state"], "failed")
        self.assertIn("same upstream result file", report["verdict"]["error"])

    def test_new_report_with_old_timestamp_is_rejected(self):
        self.script("from pathlib import Path\nimport os\np=Path('results.xml')\np.write_text('<testsuite><testcase/></testsuite>')\nos.utime(p, ns=(1, 1))\n")
        report = self.run_bench()
        self.assertEqual(report["state"], "failed")
        self.assertIn("stale", report["verdict"]["error"])

    def test_log_oracle_needs_all_declared_completion_assertions(self):
        flow = deepcopy(self.flow)
        flow["expected"] = {"kind": "log", "patterns": ["^UPSTREAM_PASS$", "signature=1234"]}
        self.script("print('UPSTREAM_PASS')\n")
        report = self.run_bench(flow)
        self.assertEqual(report["state"], "failed")
        self.assertEqual(report["verdict"]["missing_patterns"], ["signature=1234"])
        self.script("print('UPSTREAM_PASS')\nprint('signature=1234')\n")
        self.assertEqual(self.run_bench(flow)["state"], "passed")

    def test_file_log_oracle_requires_fresh_output_and_all_markers(self):
        flow = deepcopy(self.flow)
        flow["expected"] = {"kind": "log", "paths": ["uart.log"],
                            "patterns": ["^Hello original system$", "^Tick!$"]}
        self.script("print('simulator finished normally')\n")
        self.assertEqual(self.run_bench(flow)["state"], "failed")
        (self.root / "uart.log").write_text("Hello original system\nTick!\n")
        report = self.run_bench(flow)
        self.assertEqual(report["state"], "failed")
        self.assertIn("stale", report["verdict"]["error"])
        self.script("from pathlib import Path\nPath('uart.log').write_text('Hello original system\\nTick!\\n')\n")
        report = self.run_bench(flow)
        self.assertEqual(report["state"], "passed")
        self.assertEqual(report["verdict"]["artifacts"][0]["path"], "uart.log")
        self.script("from pathlib import Path\nPath('uart.log').write_text('Hello original system\\n')\n")
        report = self.run_bench(flow)
        self.assertEqual(report["state"], "failed")
        self.assertEqual(report["verdict"]["missing_patterns"], ["^Tick!$"])

    def test_nonzero_exit_never_passes_even_after_completion_marker(self):
        flow = deepcopy(self.flow)
        flow["expected"] = {"kind": "log", "patterns": ["UPSTREAM_PASS"]}
        self.script("print('UPSTREAM_PASS')\nraise SystemExit(7)\n")
        report = self.run_bench(flow)
        self.assertEqual(report["state"], "failed")
        self.assertEqual(report["steps"][0]["returncode"], 7)

    def test_log_artifact_must_be_fresh_even_when_stdout_has_pass_marker(self):
        flow = deepcopy(self.flow)
        flow["expected"] = {"kind": "log", "paths": ["completion.log"], "patterns": ["^CRC_MATCH$"]}
        self.script("print('CRC_MATCH')\n")
        for stale in (False, True):
            with self.subTest(stale=stale):
                if stale: (self.root / "completion.log").write_text("CRC_MATCH\n")
                report = self.run_bench(flow)
                self.assertEqual(report["state"], "failed")
                self.assertIn("Missing or stale", report["verdict"]["error"])
        self.script("from pathlib import Path\nPath('completion.log').write_text('CRC_MATCH\\n')\n")
        report = self.run_bench(flow)
        self.assertEqual(report["state"], "passed")
        self.assertEqual(report["verdict"]["artifacts"][0]["path"], "completion.log")

    def test_every_declared_upstream_trace_must_produce_fresh_output(self):
        flow = deepcopy(self.flow)
        flow["expected"] = {"kind": "log", "paths": ["first.log", "second.log"], "patterns": ["CRC_MATCH"]}
        self.script("from pathlib import Path\nPath('first.log').write_text('CRC_MATCH\\n')\n")
        report = self.run_bench(flow)
        self.assertEqual(report["state"], "failed")
        self.assertIn("second.log", report["verdict"]["error"])

    def test_oversized_new_report_fails_before_parsing(self):
        self.script("from pathlib import Path\nPath('results.xml').write_bytes(b'x' * (8 * 1024 * 1024 + 1))\n")
        report = self.run_bench()
        self.assertEqual(report["state"], "failed")
        self.assertIn("exceeds", report["error"])

    def test_real_timeout_does_not_accept_an_early_pass_marker(self):
        self.script("import time\nprint('UPSTREAM_PASS', flush=True)\ntime.sleep(30)\n")
        report = flows.run_flow(self.root, self.manifest(), environment=self.environment, timeout=0.1)
        self.assertEqual(report["state"], "timed_out")
        self.assertTrue(report["steps"][0]["timed_out"])

    @unittest.skipUnless(os.name == "posix", "POSIX nested process-group cancellation")
    def test_outer_companion_cancellation_reaps_stubborn_native_flow(self):
        # The companion owns the CLI's process group; each native flow step
        # owns another session. TERM must let the CLI finish the inner cleanup
        # before the companion's longer grace sends KILL to the CLI itself.
        self.script("import os,signal,time\nfrom pathlib import Path\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\nPath('native.pid').write_text(str(os.getpid()))\nprint('native bench started',flush=True)\nwhile True: time.sleep(1)\n")
        flow = deepcopy(self.flow)
        flow["expected"] = {"kind": "log", "patterns": ["^UPSTREAM_PASS$"]}
        self.manifest(flow)
        cancelled = threading.Event()
        pid_file = self.root / "native.pid"

        def cancel_after_native_start():
            deadline = time.monotonic() + 10
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            cancelled.set()

        watcher = threading.Thread(target=cancel_after_native_start, daemon=True)
        watcher.start()
        try:
            cli = Path(__file__).resolve().parents[1] / "vwb.py"
            report = flows.run_process([sys.executable, str(cli), "--root", str(self.root),
                                        "flow", flow["id"], "--timeout", "30"],
                                       env=self.environment, timeout=15,
                                       cancel_event=cancelled, termination_grace=0.6,
                                       max_output_bytes=8000)
            self.assertTrue(pid_file.exists(), report.stdout + report.stderr)
            self.assertTrue(report.cancelled)
            self.assertEqual(report.returncode, 130)
            pid = int(pid_file.read_text())
            with self.assertRaises(ProcessLookupError,
                                   msg="native simulator survived the cancelled CLI"):
                os.kill(pid, 0)
        finally:
            cancelled.set()
            watcher.join(timeout=11)
            if pid_file.exists():
                # If a regression leaves the owned session alive, terminate it
                # so the failing test itself never leaks a native simulator.
                try:
                    os.killpg(int(pid_file.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_missing_tool_or_python_dependency_blocks_execution(self):
        for kind, name in (("tools", "vwb_tool_that_is_not_installed"), ("python_modules", "vwb_module_that_does_not_exist")):
            with self.subTest(kind=kind):
                flow = deepcopy(self.flow)
                flow["requirements"][kind] = [name]
                runner = Mock()
                report = flows.run_flow(self.root, self.manifest(flow), environment=self.environment, runner=runner)
                self.assertEqual(report["state"], "blocked")
                runner.assert_not_called()

    def test_dependency_available_only_to_trusted_parent_does_not_satisfy_profile(self):
        with tempfile.TemporaryDirectory(prefix="vwb parent-only Python ") as folder:
            parent_module = Path(folder) / "vwb_parent_only_dependency.py"
            parent_module.write_text("AVAILABLE = True\n")
            profile = self.root / "empty-python-profile"
            profile.mkdir()
            self.environment["PYTHONPATH"] = str(profile)
            flow = deepcopy(self.flow)
            flow["requirements"]["python_modules"] = [parent_module.stem]
            runner = Mock()
            sys.path.insert(0, folder)
            try:
                # It is discoverable by the trusted process, but not by the
                # selected child interpreter's own profile environment.
                self.assertIsNotNone(importlib.util.find_spec(parent_module.stem))
                report = flows.run_flow(self.root, self.manifest(flow), environment=self.environment, runner=runner)
            finally:
                sys.path.remove(folder)
            self.assertEqual(report["state"], "blocked")
            self.assertEqual(report["dependencies"]["missing_python_modules"], [parent_module.stem])
            runner.assert_not_called()

    def test_dry_run_is_never_reported_as_verification_success(self):
        runner = Mock()
        report = self.run_bench(dry_run=True, runner=runner)
        self.assertEqual(report["state"], "dry_run")
        self.assertFalse((self.root / "results.xml").exists())
        runner.assert_not_called()

    def test_required_license_is_visible_and_not_verified_by_listing(self):
        flow = deepcopy(self.flow)
        flow["requirements"]["licenses"] = ["QUESTA_LICENSE"]
        listed = flows.dependencies(self.manifest(flow), self.environment)
        self.assertEqual(listed["licenses"], ["QUESTA_LICENSE"])
        self.assertEqual(listed["license_state"], "not_verified")
        self.assertEqual(self.run_bench(flow, dry_run=True)["state"], "dry_run")

    def test_failed_step_stops_before_later_steps_and_result_oracle(self):
        flow = deepcopy(self.flow)
        flow["steps"] = [
            {"argv": [self.python, "-c", "print('build complete')"]},
            {"argv": [self.python, "-c", "raise SystemExit(3)"]},
            {"argv": [self.python, "bench.py"]},
        ]
        report = self.run_bench(flow)
        self.assertEqual(report["state"], "failed")
        self.assertEqual(len(report["steps"]), 2)
        self.assertFalse((self.root / "results.xml").exists())

    def test_manifest_rejects_path_traversal_in_cwd_steps_and_results(self):
        for where in ("cwd", "step", "result"):
            for unsafe in ("../outside", "/tmp/outside", "folder\\outside", "a/../outside"):
                with self.subTest(where=where, unsafe=unsafe):
                    flow = deepcopy(self.flow)
                    if where == "cwd": flow["cwd"] = unsafe
                    elif where == "step": flow["steps"] = [{"argv": flow["argv"], "cwd": unsafe}]
                    else: flow["expected"]["paths"] = [unsafe]
                    with self.assertRaises((ValueError, FileNotFoundError)):
                        self.manifest(flow)

    def test_result_symlink_cannot_leave_project(self):
        outside = self.root.parent / (self.root.name + "-outside.xml")
        outside.write_text("<testsuite><testcase/></testsuite>")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        try:
            (self.root / "results.xml").symlink_to(outside)
        except OSError as error:
            self.skipTest("Symlink creation unavailable: " + str(error))
        with self.assertRaisesRegex(ValueError, "leaves the project|symbolic links"):
            self.manifest()

    def test_manifest_rejects_shell_driver_mismatched_driver_and_control_arguments(self):
        variants = [
            {"driver": "shell", "argv": ["sh", "bench.sh"]},
            {"driver": "make", "argv": [self.python, "bench.py"]},
            {"argv": [self.python, "bench.py\nsecond-command"]},
            {"simulator": "made-up-simulator"},
        ]
        for changes in variants:
            with self.subTest(changes=changes):
                flow = {**deepcopy(self.flow), **changes}
                with self.assertRaises(ValueError): self.manifest(flow)

    def test_simulator_override_requires_an_explicit_project_preset(self):
        for simulator in ("verilator", "made-up-simulator"):
            with self.subTest(simulator=simulator), self.assertRaisesRegex(ValueError, "does not declare"):
                self.run_bench(simulator=simulator)

    def test_declared_simulator_preset_replaces_default_simulator_argument(self):
        flow = deepcopy(self.flow)
        flow.update(driver="make", argv=["make", "SIM=icarus"])
        flow["simulators"] = {"verilator": {"arguments": ["SIM=verilator"], "tools": []}}
        flow["expected"] = {"kind": "log", "patterns": ["UPSTREAM_PASS"]}
        runner = Mock(return_value=SimpleNamespace(stdout="UPSTREAM_PASS", stderr="", returncode=0, timed_out=False))
        report = self.run_bench(flow, simulator="verilator", runner=runner)
        self.assertEqual(report["state"], "passed")
        self.assertEqual(runner.call_args.args[0], ["make", "SIM=verilator"])

    def test_manifest_rejects_duplicate_flow_ids_and_absent_oracles(self):
        (self.root / flows.MANIFEST).write_text(json.dumps({"schema_version": 1, "flows": [self.flow, self.flow]}))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            flows.load_flows(self.root)
        flow = deepcopy(self.flow)
        flow.pop("expected")
        with self.assertRaisesRegex(ValueError, "oracle"):
            self.manifest(flow)


if __name__ == "__main__":
    unittest.main()
