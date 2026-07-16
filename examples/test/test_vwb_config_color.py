import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import vwb


class TTYStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


class CommandLineConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = vwb.make_parser()

    def test_array_dump_limit_defaults_to_32_words(self):
        self.assertEqual(vwb.DEFAULT_MAX_ARRAY_WORDS, 32)
        self.assertEqual(
            self.parser.parse_args(["test"]).max_array_words,
            vwb.DEFAULT_MAX_ARRAY_WORDS,
        )

    def test_color_mode_defaults_to_auto_and_rejects_unknown_values(self):
        self.assertEqual(self.parser.parse_args(["list"]).color, "auto")
        for mode in ("auto", "always", "never"):
            with self.subTest(mode=mode):
                self.assertEqual(
                    self.parser.parse_args(["--color", mode, "list"]).color,
                    mode,
                )
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.parser.parse_args(["--color", "sometimes", "list"])

    def test_test_language_replaces_kind_for_simulation_commands(self):
        for command in ("test", "sim", "wave", "gtkwave"):
            for language in ("auto", "cocotb", "verilog"):
                with self.subTest(command=command, language=language):
                    args = self.parser.parse_args(
                        [command, "--test-language", language]
                    )
                    self.assertEqual(args.test_language, language)

            for invalid_language in ("hdl", "invalid"):
                with self.subTest(command=command, language=invalid_language):
                    with contextlib.redirect_stderr(io.StringIO()):
                        with self.assertRaises(SystemExit):
                            self.parser.parse_args(
                                [command, "--test-language", invalid_language]
                            )

            with self.subTest(command=command, legacy_option="--kind"):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        self.parser.parse_args([command, "--kind", "verilog"])

    def test_init_persists_project_directories_and_cli_override_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            configured_src = root / "rtl"
            configured_test = root / "verification"
            alternate_src = root / "alternate-rtl"
            configured_src.mkdir()
            configured_test.mkdir()
            alternate_src.mkdir()
            (configured_src / "configured_dut.v").write_text(
                "module configured_dut; endmodule\n", encoding="utf-8"
            )
            (alternate_src / "alternate_dut.v").write_text(
                "module alternate_dut; endmodule\n", encoding="utf-8"
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                status = vwb.main(
                    [
                        "--root",
                        str(root),
                        "--src-dir",
                        "rtl",
                        "--test-dir",
                        "verification",
                        "--build-dir",
                        "output",
                        "init",
                    ]
                )

            self.assertEqual(status, 0)
            config_path = root / ".vwb.json"
            self.assertTrue(config_path.is_file())
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config["version"], vwb.CONFIG_VERSION)
            self.assertEqual(config["src_dir"], "rtl")
            self.assertEqual(config["test_dir"], "verification")
            self.assertEqual(config["build_dir"], "output")

            nested = root / "work" / "nested"
            nested.mkdir(parents=True)
            settings = vwb.resolve_project_settings(
                self.parser.parse_args(["list"]), cwd=nested
            )
            self.assertEqual(settings.root, root)
            self.assertEqual(settings.src_dir, "rtl")
            self.assertEqual(settings.test_dir, "verification")
            self.assertEqual(settings.build_dir, "output")

            overridden = vwb.resolve_project_settings(
                self.parser.parse_args(
                    ["--src-dir", "alternate-rtl", "list"]
                ),
                cwd=nested,
            )
            self.assertEqual(overridden.src_dir, "alternate-rtl")
            self.assertEqual(overridden.test_dir, "verification")
            self.assertEqual(overridden.build_dir, "output")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = vwb.main(
                    ["--root", str(root), "--color", "never", "list"]
                )
            self.assertEqual(status, 0)
            self.assertIn("configured_dut", output.getvalue())
            self.assertNotIn("alternate_dut", output.getvalue())

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = vwb.main(
                    [
                        "--root",
                        str(root),
                        "--src-dir",
                        "alternate-rtl",
                        "--color",
                        "never",
                        "list",
                    ]
                )
            self.assertEqual(status, 0)
            self.assertIn("alternate_dut", output.getvalue())
            self.assertNotIn("configured_dut", output.getvalue())

    def test_init_rejects_overlapping_project_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = [
                ("src", "src/tests", ".vwb"),
                ("test/sources", "test", ".vwb"),
                ("src", "test", "src/build"),
                ("output/src", "test", "output"),
                ("src", "test", "."),
                ("src", "test", ".."),
            ]

            for src_dir, test_dir, build_dir in cases:
                with self.subTest(
                    src_dir=src_dir, test_dir=test_dir, build_dir=build_dir
                ):
                    with self.assertRaises(vwb.VWBError):
                        vwb.write_project_config(
                            root,
                            src_dir,
                            test_dir,
                            build_dir,
                            force=False,
                            dry_run=True,
                        )

    def test_invalid_array_limits_return_argument_error_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "test").mkdir()
            for value in ("-1", str(1 << 128)):
                with self.subTest(value=value):
                    stderr = io.StringIO()
                    with contextlib.redirect_stderr(stderr):
                        status = vwb.main(
                            [
                                "--root",
                                str(root),
                                "test",
                                "--waves",
                                "--max-array-words",
                                value,
                            ]
                        )
                    self.assertEqual(status, 2)
                    self.assertIn("--max-array-words", stderr.getvalue())

    def test_cocotb_test_tree_may_be_outside_project_root(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "project"
            source = root / "src"
            tests = base / "external" / "verification"
            source.mkdir(parents=True)
            tests.mkdir(parents=True)
            (source / "dut.v").write_text("module dut; endmodule\n", encoding="utf-8")
            (tests / "__init__.py").write_text("", encoding="ascii")
            test_path = tests / "test_dut.py"
            test_path.write_text(
                "import cocotb\n@cocotb.test()\nasync def smoke(dut):\n    pass\n",
                encoding="utf-8",
            )
            workbench = vwb.Workbench(
                root=root,
                src_dir=source,
                test_dir=tests,
                build_dir=root / ".vwb",
            )
            spec = workbench.tests[0]
            captured: dict[str, str] = {}

            def fake_run(
                _command: object, *, env: dict[str, str] | None = None, **_kwargs: object
            ) -> object:
                assert env is not None
                captured.update(env)
                results_file = Path(env["COCOTB_RESULTS_FILE"])
                results_file.parent.mkdir(parents=True, exist_ok=True)
                results_file.write_text(
                    '<testsuite failures="0" errors="0"><testcase/></testsuite>\n',
                    encoding="utf-8",
                )
                return SimpleNamespace(returncode=0)

            args = vwb.make_parser().parse_args(
                ["test", "dut", "--test-language", "cocotb"]
            )
            with (
                mock.patch.object(
                    workbench,
                    "_compile_simulation",
                    return_value=(True, None, root / ".vwb" / "sim.vvp"),
                ),
                mock.patch.object(workbench, "require_tool", return_value="vvp"),
                mock.patch.object(
                    workbench, "cocotb_library", return_value=(Path("/lib"), "vpi")
                ),
                mock.patch.object(workbench, "run", side_effect=fake_run),
            ):
                passed, _wave = workbench.run_test_spec(spec, args)

            self.assertTrue(passed)
            self.assertEqual(captured["MODULE"], "verification.test_dut")
            self.assertEqual(
                captured["PYTHONPATH"].split(os.pathsep)[0], str(tests.parent)
            )

    def test_build_marker_is_portable_across_mount_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            first = base / "host" / "project"
            second = base / "container" / "project"
            for root in (first, second):
                (root / "src").mkdir(parents=True)
                (root / "test").mkdir()

            first_workbench = vwb.Workbench(
                root=first,
                src_dir=first / "src",
                test_dir=first / "test",
                build_dir=first / ".vwb",
            )
            first_workbench.prepare_build_dir()
            marker = first / ".vwb" / vwb.BUILD_MARKER
            data = json.loads(marker.read_text(encoding="utf-8"))
            self.assertEqual(data["schema"], vwb.BUILD_MARKER_SCHEMA)
            self.assertEqual(data["project_relative"], "..")
            self.assertNotIn(str(first), marker.read_text(encoding="utf-8"))

            (second / ".vwb").mkdir()
            second_marker = second / ".vwb" / vwb.BUILD_MARKER
            second_marker.write_text(marker.read_text(encoding="utf-8"), encoding="utf-8")
            second_workbench = vwb.Workbench(
                root=second,
                src_dir=second / "src",
                test_dir=second / "test",
                build_dir=second / ".vwb",
            )
            second_workbench.prepare_build_dir()


class ReportColorTests(unittest.TestCase):
    def run_report(
        self,
        root: Path,
        command: str,
        color: str,
        *,
        as_json: bool = False,
        tty: bool = False,
    ) -> tuple[int, str]:
        output = TTYStringIO() if tty else io.StringIO()
        argv = ["--root", str(root), "--color", color, command]
        if as_json:
            argv.append("--json")
        with mock.patch.dict(os.environ, {"TERM": "xterm"}):
            os.environ.pop("NO_COLOR", None)
            with contextlib.redirect_stdout(output):
                status = vwb.main(argv)
        return status, output.getvalue()

    def test_human_reports_honor_color_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "test").mkdir()
            (root / "src" / "dut.v").write_text(
                "module dut; endmodule\n", encoding="utf-8"
            )

            for command in ("list", "doctor"):
                with self.subTest(command=command, color="always"):
                    _, output = self.run_report(root, command, "always")
                    self.assertIn("\x1b[", output)

                with self.subTest(command=command, color="never"):
                    _, output = self.run_report(root, command, "never", tty=True)
                    self.assertNotIn("\x1b[", output)

                with self.subTest(command=command, color="auto", tty=True):
                    _, output = self.run_report(root, command, "auto", tty=True)
                    self.assertIn("\x1b[", output)

                with self.subTest(command=command, color="auto", tty=False):
                    _, output = self.run_report(root, command, "auto")
                    self.assertNotIn("\x1b[", output)

    def test_json_reports_remain_valid_and_uncolored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "test").mkdir()
            (root / "src" / "dut.v").write_text(
                "module dut; endmodule\n", encoding="utf-8"
            )

            for command in ("list", "doctor"):
                with self.subTest(command=command):
                    _, output = self.run_report(
                        root, command, "always", as_json=True, tty=True
                    )
                    self.assertNotIn("\x1b[", output)
                    self.assertIsInstance(json.loads(output), dict)


if __name__ == "__main__":
    unittest.main()
