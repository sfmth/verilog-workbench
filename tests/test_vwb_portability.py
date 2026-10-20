"""Regression coverage for real local folders and backend-dependent outputs."""

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import vwb
from companion.process import run_process


REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "toolchains" / "fixtures"


class VwbPortabilityTests(unittest.TestCase):
    def copy_project(self, parent, name):
        root = Path(parent) / name
        shutil.copytree(FIXTURES, root)
        return root

    def cli(self, root, *arguments):
        result = run_process(
            [sys.executable, REPO / "vwb.py", "--root", root,
             "--src-dir", "src", "--test-dir", "test", "--build-dir", "build",
             "--verbose", "--color", "never", *arguments],
            cwd=root, timeout=60, max_output_bytes=2_000_000,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_uppercase_cocotb_and_formal_suffixes_keep_native_glob_case_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'src').mkdir()
            (root / 'test').mkdir()
            (root / 'src/top.sv').write_text('module top; endmodule\n', encoding='utf-8')
            python_test = root / 'test/test_top.PY'
            python_test.write_text('import cocotb\n@cocotb.test()\nasync def test_top(dut):\n    pass\n', encoding='utf-8')
            config = root / 'proof.SBY'
            config.write_text('[options]\nmode prove\n', encoding='utf-8')
            workbench = vwb.Workbench(root, root / 'src', root / 'test', root / 'build', dry_run=True)
            # Existing Path.rglob patterns use Windows' case-insensitive path
            # matching and POSIX's case-sensitive matching, independent of the
            # directory walk now used to prune private trash.
            if os.name == 'nt':
                self.assertEqual([(test.dut, test.kind, test.path) for test in workbench.tests],
                                 [('top', 'cocotb', python_test.resolve())])
                with (mock.patch.object(workbench, 'require_tool'),
                      mock.patch.object(workbench, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run):
                    self.assertEqual(workbench.run_formal(None, False), root / 'build/formal/proof')
                self.assertEqual(run.call_args.args[0][-1], 'proof.SBY')
            else:
                self.assertEqual(workbench.tests, [])
                with mock.patch.object(workbench, 'require_tool'):
                    with self.assertRaisesRegex(vwb.VWBError, 'none found'):
                        workbench.run_formal(None, False)

    def test_dump_module_uses_basename_in_real_simulation_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_project(directory, "project with spaces µ")
            workbench = vwb.Workbench(root, root / "src", root / "test", root / "build")
            simulation = root / "build" / "sim"
            simulation.mkdir(parents=True)
            dump_module = simulation / "vwb_dump.v"
            workbench._write_dump_module(dump_module, "tb_m0_gate", simulation / "m0_gate.vcd")
            source = dump_module.read_text(encoding="ascii")
            self.assertIn('$dumpfile("m0_gate.vcd")', source)
            self.assertNotIn(str(root), source)

    def test_ghdl_elaboration_and_run_share_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_project(directory, "project with spaces µ")
            workbench = vwb.Workbench(root, root / "src", root / "test", root / "build")
            spec = vwb.TestSpec("m0_vhdl", "vhdl", root / "test" / "test_m0_vhdl.vhd", "tb_m0_vhdl")
            args = vwb.make_parser().parse_args(["test", "m0_vhdl"])
            simulation = root / "build" / "simulation"
            with (
                mock.patch.object(workbench, "require_tool"),
                mock.patch.object(workbench, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run,
            ):
                self.assertTrue(workbench._run_vhdl_test(spec, args, simulation)[0])
            for call in run.call_args_list:
                command = [str(item) for item in call.args[0]]
                if command[1] in {"-m", "-r"}:
                    self.assertEqual(call.kwargs["cwd"], simulation)

    def test_darwin_compiled_ghdl_uses_direct_runtime_and_matching_python_libraries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_project(directory, "project with spaces µ")
            workbench = vwb.Workbench(root, root / "src", root / "test", root / "build")
            spec = vwb.TestSpec("m0_vhdl", "cocotb", root / "test" / "test_m0_cocotb.py")
            args = vwb.make_parser().parse_args(["test", "m0_vhdl"])
            simulation = root / "build" / "simulation"
            runtime = simulation / "m0_vhdl"
            libpython = root / "suite" / "lib" / "libpython3.11.dylib"
            for compiled in (True, False):
                with self.subTest(compiled=compiled):
                    if runtime.exists():
                        runtime.unlink()

                    def run(argv, **_kwargs):
                        if argv[1] == "-m" and compiled:
                            runtime.write_bytes(b"\xcf\xfa\xed\xfe" + b"test executable")
                        return subprocess.CompletedProcess(argv, 0, "", "")

                    environment = {"LIBPYTHON_LOC": str(libpython), "DYLD_LIBRARY_PATH": "/existing"}
                    with (
                        mock.patch.object(vwb.sys, "platform", "darwin"),
                        mock.patch.object(workbench, "require_tool"),
                        mock.patch.object(workbench, "ghdl_vpi_library", return_value="/suite/vpi.so"),
                        mock.patch.object(workbench, "_cocotb_environment", return_value=(environment, simulation / "results.xml")),
                        mock.patch.object(workbench, "_results_passed", return_value=True),
                        mock.patch.object(workbench, "run", side_effect=run) as execute,
                    ):
                        self.assertTrue(workbench._run_vhdl_test(spec, args, simulation)[0])
                    command = execute.call_args.args[0]
                    self.assertEqual(command[0], runtime if compiled else "ghdl")
                    self.assertIn("--vpi=/suite/vpi.so", command)
                    self.assertEqual(execute.call_args.kwargs["cwd"], simulation)
                    self.assertEqual(environment["DYLD_LIBRARY_PATH"],
                                     str(libpython.parent) + os.pathsep + "/existing" if compiled else "/existing")

    def test_interrupted_formal_job_publishes_partial_results_and_cleans_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_project(directory, "project with spaces µ")
            build = root / "build artifacts µ"
            workbench = vwb.Workbench(root, root / "src", root / "test", build)
            config = root / "formal" / "good.sby"

            def interrupted_run(argv, *, cwd):
                execution = cwd / str(argv[3])
                (execution / "status").write_text("INTERRUPTED\n")
                raise KeyboardInterrupt

            with mock.patch.object(workbench, "require_tool"), mock.patch.object(workbench, "run", side_effect=interrupted_run):
                with self.assertRaises(KeyboardInterrupt):
                    workbench.run_formal(str(config), False)
            output = build / "formal" / "good"
            self.assertEqual((output / "status").read_text().strip(), "INTERRUPTED")
            self.assertFalse(list(config.parent.glob(".vwb-sby-*")))

    def test_formal_publication_failure_restores_old_results_and_retains_new(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_project(directory, "project with spaces µ")
            workbench = vwb.Workbench(root, root / "src", root / "test", root / "build artifacts")
            output = root / "build artifacts" / "formal" / "good"
            output.mkdir(parents=True)
            (output / "status").write_text("OLD\n")
            execution = root / "formal" / ".vwb-sby-test"
            execution.mkdir()
            (execution / "status").write_text("PASS\n")
            original_replace = vwb.os.replace

            def fail_publish(source, target):
                if Path(source).name.startswith(".vwb-formal-result-") and Path(target) == output:
                    raise OSError("publication blocked")
                return original_replace(source, target)

            errors = io.StringIO()
            with mock.patch.object(vwb.os, "replace", side_effect=fail_publish), contextlib.redirect_stderr(errors):
                with self.assertRaisesRegex(vwb.VWBError, "results retained"):
                    workbench._publish_formal_workdir(execution, output, root / "formal" / "good.sby")
            self.assertEqual((output / "status").read_text().strip(), "OLD")
            recovered = list(output.parent.glob(".vwb-formal-result-*"))
            self.assertEqual(len(recovered), 1)
            self.assertEqual((recovered[0] / "status").read_text().strip(), "PASS")
            self.assertIn(str(recovered[0]), errors.getvalue())

    @unittest.skipUnless(shutil.which("iverilog") and shutil.which("vvp"), "needs native Icarus")
    def test_actual_verilog_waveform_with_unicode_project_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_project(directory, "project with spaces µ")
            result = self.cli(
                root, "test", "m0_gate", "--test", "test/test_m0_gate.v",
                "--test-language", "verilog", "--test-top", "tb_m0_gate",
                "--waves", "--wave-format", "vcd",
            )
            self.assertIn("M0_VERILOG_PASS", result.stdout)
            waves = list((root / "build").rglob("m0_gate.vcd"))
            self.assertEqual(len(waves), 1)
            self.assertIn("$var", waves[0].read_text())

    @unittest.skipUnless(shutil.which("ghdl"), "needs native GHDL")
    def test_actual_ghdl_in_ordinary_and_unicode_project_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ("ordinary", "project with spaces µ"):
                with self.subTest(project=name):
                    root = self.copy_project(directory, name)
                    result = self.cli(
                        root, "test", "m0_vhdl", "--test", "test/test_m0_vhdl.vhd",
                        "--test-language", "vhdl", "--test-top", "tb_m0_vhdl",
                        "--waves", "--wave-format", "vcd",
                    )
                    self.assertIn("M0_VHDL_PASS", result.stdout + result.stderr)
                    self.assertTrue(list((root / "build").rglob("m0_vhdl.vcd")))
                    self.assertFalse((root / "tb_m0_vhdl").exists(), "GHDL executable leaked into project root")
                    self.assertFalse((root / "tb_m0_vhdl.exe").exists())

    @unittest.skipUnless(shutil.which("yosys"), "needs native Yosys")
    def test_actual_yosys_preserves_quoted_include_and_define_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_project(directory, "project with spaces µ")
            includes = root / "include with spaces µ"
            includes.mkdir()
            (includes / "feature.vh").write_text("`define INCLUDED_FEATURE 1\n")
            (root / "src" / "m0_gate.v").write_text(
                '`include "feature.vh"\n'
                "module m0_gate(input a, b, output y);\n"
                '`ifndef INCLUDED_FEATURE\nmissing_include broken();\n`endif\n'
                '`ifndef SELECTED_FEATURE\nmissing_define broken();\n`endif\n'
                "assign y=a^b; endmodule\n", encoding="utf-8",
            )
            self.cli(root, "synth", "m0_gate", "--format", "json", "--no-view", "--no-schematic",
                     "-I", str(includes), "-D", "SELECTED_FEATURE=1")
            output = root / "build" / "synth" / "m0_gate"
            self.assertTrue((output / "m0_gate.json").is_file())
            script = (output / "synth.tcl").read_text(encoding="utf-8")
            self.assertIn(str(includes).replace("µ", r"\u00b5"), script)
            self.assertIn("yosys read_verilog", script)
            self.assertTrue(script.isascii())
            self.assertTrue(script.startswith("encoding system utf-8\ncd "))

    @unittest.skipUnless(shutil.which("yosys") and shutil.which("dot"), "needs native Yosys and Graphviz")
    def test_actual_schematic_uses_internal_design_with_unicode_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_project(directory, "project with spaces µ")
            self.cli(root, "synth", "m0_gate", "--format", "svg", "--no-view", "--no-schematic")
            output = root / "build" / "synth" / "m0_gate"
            self.assertTrue((output / "m0_gate.il").is_file())
            self.assertIn("<svg", (output / "m0_gate.svg").read_text())
            self.assertIn("read_rtlil m0_gate.il", (output / "render.ys").read_text())


if __name__ == "__main__":
    unittest.main()
