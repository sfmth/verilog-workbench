import importlib.util
import contextlib
import io
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest import mock

from companion.toolchains import binary_architecture, capability_report, compatibility_route, normalize_arch, wrapper_payload

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("probe_toolchain", REPO / "scripts" / "probe_toolchain.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class ToolchainTests(unittest.TestCase):
    def test_missing_engines_are_blocked_and_never_spawned(self):
        with mock.patch("companion.toolchains.shutil.which", return_value=None), mock.patch("companion.toolchains.run_process") as run:
            report = capability_report(env={"PATH": ""})
        run.assert_not_called()
        self.assertTrue(all(value["state"] == "blocked" for value in report["workflows"].values()))
        self.assertEqual(report["mode"], "discovery")
        json.dumps(report)

    def test_available_tools_do_not_claim_a_workflow_pass(self):
        with mock.patch("companion.toolchains.shutil.which", return_value="/a/tool"):
            report = capability_report(probe_versions=False)
        self.assertTrue(all(value["state"] == "not_run" for value in report["workflows"].values()))
        self.assertTrue(all(value["version"] is None for value in report["tools"].values()))

    def test_broken_executable_and_version_errors_are_explicit(self):
        failed = subprocess.CompletedProcess(["/a/tool"], 127, "", "missing shared library")
        with mock.patch("companion.toolchains.shutil.which", return_value="/a/tool"), mock.patch("companion.toolchains.run_process", return_value=failed):
            report = capability_report()
        self.assertEqual(report["tools"]["yosys"]["version_state"], "unavailable")
        self.assertEqual(report["tools"]["yosys"]["version_returncode"], 127)
        self.assertIn("missing shared library", report["tools"]["yosys"]["version"])
        self.assertEqual(report["workflows"]["synthesis"]["state"], "not_run")

    def test_binary_architecture_inspects_elf_pe_and_scripts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            elf = bytearray(64)
            elf[:6] = b"\x7fELF\x02\x01"
            struct.pack_into("<H", elf, 18, 183)
            path = root / "elf"
            path.write_bytes(elf)
            self.assertEqual(binary_architecture(path)["architectures"], ["arm64"])
            pe = bytearray(128)
            pe[:2] = b"MZ"
            struct.pack_into("<I", pe, 60, 80)
            pe[80:84] = b"PE\0\0"
            struct.pack_into("<H", pe, 84, 0x8664)
            path.write_bytes(pe)
            self.assertEqual(binary_architecture(path)["architectures"], ["x64"])
            path.write_text("#!/usr/bin/env python3\n")
            architecture = binary_architecture(path)
            self.assertEqual(architecture["format"], "script")
            self.assertEqual(architecture["architectures"], [])

    def test_windows_arm_translation_never_claims_native_or_verified(self):
        binary = {"architectures": ["x64"]}
        windows = compatibility_route(binary, {"architecture": "arm64", "system": "windows"})
        self.assertEqual(windows["mechanism"], "windows-11-x64-emulation")
        self.assertFalse(windows["verified"])
        linux = compatibility_route(binary, {"architecture": "arm64", "system": "linux"})
        self.assertEqual(linux["mechanism"], "unsupported-architecture")
        self.assertEqual(normalize_arch("AMD64"), "x64")

    def test_suite_launcher_reports_inspected_payload_without_executing_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bin").mkdir()
            (root / "libexec").mkdir()
            launcher = root / "bin" / "yosys"
            launcher.write_text('#!/bin/sh\nexec "$release_topdir_abs"/libexec/yosys "$@"\n')
            data = bytearray(64)
            data[:6] = b"\x7fELF\x02\x01"
            struct.pack_into("<H", data, 18, 62)
            (root / "libexec" / "yosys").write_bytes(data)
            payload = wrapper_payload(launcher)
            self.assertEqual(payload["binary"]["architectures"], ["x64"])
            launcher.write_text("#!/bin/sh\necho pretend\n")
            self.assertIsNone(wrapper_payload(launcher))

    def test_all_commands_are_accepted_by_real_cli_and_never_flash(self):
        import vwb
        for workflow in probe.SMOKE_WORKFLOWS:
            with self.subTest(workflow=workflow):
                arguments = probe.command_for(workflow)
                vwb.make_parser().parse_args(arguments)
                self.assertNotIn("flash", arguments)

    def test_verilator_native_engine_fallback_is_used_for_lint(self):
        import vwb
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir(); (root / "test").mkdir()
            (root / "src/gate.v").write_text("module gate(input a, output y); assign y=a; endmodule\n")
            workbench = vwb.Workbench(root, root / "src", root / "test", root / "build")
            executable = str(root / "verilator_bin.exe")
            with mock.patch.object(workbench, "require_tool", return_value=executable), mock.patch.object(workbench, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
                self.assertTrue(workbench.lint_with_tool("gate", "verilator", (), ()))
            command = run.call_args.args[0]
            self.assertEqual(command[0], executable)
            self.assertIn("--lint-only", command)

    def test_missing_workflows_do_not_execute_or_pass(self):
        with mock.patch("companion.toolchains.shutil.which", return_value=None):
            report = capability_report(probe_versions=False)
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(probe, "run_process") as run:
            result = probe.execute_probe(report, Path(directory))
        run.assert_not_called()
        self.assertFalse(result["smoke_passed"])
        self.assertEqual(result["mode"], "execution")

    def test_formal_crash_is_not_a_counterexample_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertFalse(probe.inspect_result("formal_counterexample", root, 2, "FAIL")[0])
            build = root / "build" / "formal"
            build.mkdir(parents=True)
            (build / "status").write_text("ERROR 16 0\n")
            (build / "trace.vcd").write_text("$var wire 1 a a $end\n#0\n1a\n")
            self.assertFalse(probe.inspect_result("formal_counterexample", root, 2, "")[0])
            (build / "status").write_text("FAIL 2 0\n")
            self.assertTrue(probe.inspect_result("formal_counterexample", root, 2, "")[0])
            (build / "status").write_text("PASS 0 0\n")
            self.assertTrue(probe.inspect_result("formal_proof", root, 0, "")[0])

    def test_cocotb_requires_executed_assertions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / "build"
            build.mkdir()
            result = build / "results.xml"
            result.write_text('<testsuite tests="0"/>')
            self.assertFalse(probe.inspect_result("cocotb_verilog", root, 0, "")[0])
            result.write_text('<testsuite><testcase name="xor"><failure/></testcase></testsuite>')
            self.assertFalse(probe.inspect_result("cocotb_verilog", root, 0, "")[0])
            result.write_text('<testsuite><testcase name="xor"/></testsuite>')
            self.assertTrue(probe.inspect_result("cocotb_verilog", root, 0, "")[0])

    def test_release_lock_has_six_targets_and_exact_integrity(self):
        lock = json.loads((REPO / "toolchains" / "native-lock.json").read_text())
        self.assertEqual(set(lock["targets"]), {"linux-x64", "linux-arm64", "windows-x64", "windows-arm64", "darwin-x64", "darwin-arm64"})
        for asset in lock["suite"]["assets"].values():
            self.assertRegex(asset["sha256"], r"^[a-f0-9]{64}$")
            self.assertGreater(asset["size_bytes"], 0)
            self.assertNotIn("latest", asset["url"])
        arm = lock["targets"]["windows-arm64"]
        self.assertEqual(arm["binary_architecture"], "x64")
        self.assertEqual(arm["status"], "blocked")

    def test_blocked_workflow_summary_survives_json_file_output(self):
        with mock.patch("companion.toolchains.shutil.which", return_value=None):
            report = capability_report(probe_versions=False)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output, stderr, stdout = root / "probe.json", io.StringIO(), io.StringIO()
            arguments = ["probe_toolchain.py", "--execute", "--only", "vhdl_simulation", "--work-dir", str(root / "logs"), "--output", str(output)]
            with mock.patch("sys.argv", arguments), mock.patch.object(probe, "capability_report", return_value=report), contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
                result = probe.main()
            self.assertEqual(result, 1)
            self.assertEqual(json.loads(output.read_text())["workflows"]["vhdl_simulation"]["state"], "blocked")
            self.assertIn("vhdl_simulation: blocked; missing tools: ghdl", stderr.getvalue())
            self.assertEqual(stdout.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
