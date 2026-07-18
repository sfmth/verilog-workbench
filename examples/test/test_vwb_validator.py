import importlib.util
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = ROOT / ".github" / "scripts" / "validate_vwb.py"
VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "_vwb_ci_validator", VALIDATOR_PATH
)
if VALIDATOR_SPEC is None or VALIDATOR_SPEC.loader is None:
    raise RuntimeError(f"cannot load CI validator: {VALIDATOR_PATH}")
validate_vwb = importlib.util.module_from_spec(VALIDATOR_SPEC)
sys.modules[VALIDATOR_SPEC.name] = validate_vwb
VALIDATOR_SPEC.loader.exec_module(validate_vwb)


class PythonTestDiscoveryAuditTests(unittest.TestCase):
    def test_compile_audit_covers_every_documented_python_test_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            test_dir = root / "test"
            test_dir.mkdir()
            paths = [
                test_dir / "test_dut.py",
                test_dir / "tb_dut.py",
                test_dir / "dut_test.py",
                test_dir / "dut_tb.py",
            ]
            source = (
                "import cocotb\n\n"
                "@cocotb.test()\n"
                "async def check_dut(dut):\n"
                "    pass\n"
            )
            tests: list[validate_vwb.TestCase] = []
            for path in paths:
                path.write_text(source, encoding="ascii")
                tests.append(
                    validate_vwb.TestCase(
                        module="dut",
                        design_language="verilog",
                        kind="cocotb",
                        language="cocotb",
                        path=str(path),
                        top=None,
                        dependency_count=0,
                    )
                )

            runner = validate_vwb.Runner(
                root=root,
                vwb=root / "vwb.py",
                src_dir="src",
                test_dir="test",
                build_dir=root / "build",
            )
            compiled = validate_vwb.compile_test_sources(runner, tests)

            self.assertEqual(set(compiled), {path.resolve() for path in paths})
            self.assertEqual(runner.failures, [])

    def test_compile_audit_reports_bad_alternate_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            test_dir = root / "test"
            test_dir.mkdir()
            broken = test_dir / "tb_dut.py"
            broken.write_text("async def broken(:\n", encoding="ascii")
            runner = validate_vwb.Runner(
                root=root,
                vwb=root / "vwb.py",
                src_dir="src",
                test_dir="test",
                build_dir=root / "build",
            )

            validate_vwb.compile_test_sources(runner, [])

            self.assertEqual(len(runner.failures), 1)
            self.assertIn("Python test does not compile", runner.failures[0])
            self.assertIn("tb_dut.py", runner.failures[0])


class GeneratedStarterWaveAuditTests(unittest.TestCase):
    @staticmethod
    def run_audit(
        *, all_formats: bool, waveform_content: bytes
    ) -> tuple[
        validate_vwb.CliMetadata,
        list[tuple[str, ...]],
        list[str],
        set[Path],
    ]:
        repository_root = Path(__file__).resolve().parents[2]
        metadata = validate_vwb.load_cli_metadata(repository_root / "vwb.py")
        calls: list[tuple[str, ...]] = []
        fixture_roots: set[Path] = set()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src"
            source.mkdir()
            (root / "test").mkdir()
            (source / "dut.v").write_text(
                "module dut(input wire value, output wire copy);\n"
                "  assign copy = value;\n"
                "endmodule\n",
                encoding="ascii",
            )
            runner = validate_vwb.Runner(
                root=root,
                vwb=repository_root / "vwb.py",
                src_dir="src",
                test_dir="test",
                build_dir=root / "build",
            )

            def fake_run_vwb(
                fixture: validate_vwb.Runner,
                arguments: list[str],
                **_kwargs: object,
            ) -> object:
                invocation = tuple(str(argument) for argument in arguments)
                calls.append(invocation)
                fixture_roots.add(fixture.root)
                starter = fixture.root / "test" / "test_dut_starter.py"
                generated = "--test" not in invocation
                if generated:
                    starter.write_text("# generated once\n", encoding="ascii")
                wave_format = invocation[invocation.index("--wave-format") + 1]
                waveform = (
                    fixture.build_dir
                    / "sim"
                    / "dut"
                    / "cocotb-test_dut_starter"
                    / f"dut.{wave_format}"
                )
                waveform.parent.mkdir(parents=True, exist_ok=True)
                waveform.write_bytes(waveform_content)
                stdout = "RTL PASS\n"
                if generated:
                    stdout += "generated starter test: test/test_dut_starter.py\n"
                    stdout += "GATE PASS\n"
                return validate_vwb.subprocess.CompletedProcess(
                    invocation, 0, stdout, ""
                )

            with mock.patch.object(
                validate_vwb.Runner,
                "run_vwb",
                autospec=True,
                side_effect=fake_run_vwb,
            ):
                validate_vwb.validate_discovered_starter_tests(
                    runner,
                    metadata,
                    ["dut"],
                    [],
                    seed=17,
                    all_formats=all_formats,
                )
            failures = list(runner.failures)

        return metadata, calls, failures, fixture_roots

    def test_default_format_runs_once_and_cleans_the_generated_project(self):
        metadata, calls, failures, fixture_roots = self.run_audit(
            all_formats=False,
            waveform_content=b"wave data",
        )

        self.assertEqual(failures, [])
        self.assertEqual(len(calls), 1)
        self.assertIn("--waves", calls[0])
        self.assertEqual(
            calls[0][calls[0].index("--wave-format") + 1],
            metadata.default_wave_format,
        )
        self.assertIn("--gate-level", calls[0])
        self.assertTrue(fixture_roots)
        self.assertTrue(all(not path.exists() for path in fixture_roots))

    def test_all_formats_reuse_one_starter_and_only_run_one_gate_check(self):
        metadata, calls, failures, _fixture_roots = self.run_audit(
            all_formats=True,
            waveform_content=b"wave data",
        )

        expected_formats = validate_vwb.validation_wave_formats(metadata, True)
        actual_formats = tuple(
            call[call.index("--wave-format") + 1] for call in calls
        )
        self.assertEqual(failures, [])
        self.assertEqual(actual_formats, expected_formats)
        self.assertNotIn("--test", calls[0])
        self.assertIn("--gate-level", calls[0])
        for call in calls[1:]:
            self.assertIn("--test", call)
            self.assertNotIn("--gate-level", call)

    def test_empty_generated_waveform_is_reported(self):
        _metadata, _calls, failures, _fixture_roots = self.run_audit(
            all_formats=False,
            waveform_content=b"",
        )

        self.assertEqual(len(failures), 1)
        self.assertIn("missing or empty generated-starter waveform", failures[0])


class RepresentativeModuleSelectionTests(unittest.TestCase):
    def test_profile_covers_languages_test_styles_and_complex_designs(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(prefix="vwb-profile-test-") as directory:
            runner = validate_vwb.Runner(
                root=root,
                vwb=root / "vwb.py",
                src_dir="examples/src",
                test_dir="examples/test",
                build_dir=Path(directory) / "build",
            )
            with mock.patch.object(validate_vwb.Runner, "_display"):
                inventory = validate_vwb.read_inventory(runner)

        tests = validate_vwb.flatten_tests(inventory, root)
        all_modules = validate_vwb.module_names(inventory)
        selected = validate_vwb.select_representative_modules(
            inventory, all_modules, tests
        )
        selected_tests = validate_vwb.select_tests(tests, [], selected)
        inventory_by_name = {
            module["name"]: module for module in inventory["modules"]
        }

        self.assertEqual(selected, list(validate_vwb.REPRESENTATIVE_MODULES))
        self.assertEqual(len(selected), 10)
        self.assertEqual(
            {inventory_by_name[name]["language"] for name in selected},
            {"verilog", "systemverilog", "vhdl"},
        )
        self.assertEqual(
            {test.kind for test in selected_tests},
            {test.kind for test in tests},
        )
        self.assertEqual(
            {name for name in selected if not inventory_by_name[name]["tests"]},
            {"processing_element"},
        )
        self.assertGreaterEqual(
            len(inventory_by_name["processor"]["dependencies"]), 6
        )
        self.assertGreater(
            (root / "examples/src/processing_array.v").stat().st_size,
            10_000,
        )
        self.assertEqual(
            len(inventory_by_name["vhdl_beginner_counter"]["files"]), 2
        )
        self.assertTrue(
            all(test.module in set(selected) for test in selected_tests)
        )

    def test_profile_fails_loudly_when_a_selected_module_disappears(self):
        modules = [
            {
                "name": name,
                "language": "verilog",
                "files": [f"{name}.v"],
                "dependencies": [],
                "tests": [],
            }
            for name in list(validate_vwb.REPRESENTATIVE_MODULES)[1:]
        ]

        with self.assertRaisesRegex(
            validate_vwb.HarnessError,
            "representative CI modules are missing",
        ):
            validate_vwb.select_representative_modules(
                {"modules": modules},
                [module["name"] for module in modules],
                [],
            )


class PortableToolValidationTests(unittest.TestCase):
    @staticmethod
    def runner() -> validate_vwb.Runner:
        root = Path(__file__).resolve().parents[2]
        return validate_vwb.Runner(
            root=root,
            vwb=root / "vwb.py",
            src_dir="examples/src",
            test_dir="examples/test",
            build_dir=root / ".vwb-portable-test",
        )

    def test_portable_doctor_allows_only_reported_optional_gaps(self):
        report = {
            "simulation": {"iverilog": "/usr/bin/iverilog"},
            "lint": {"verible-verilog-lint": None},
        }
        result = validate_vwb.subprocess.CompletedProcess(
            ["vwb", "doctor", "--json"], 0, validate_vwb.json.dumps(report), ""
        )
        portable = self.runner()
        strict = self.runner()

        with mock.patch.object(portable, "run_vwb", return_value=result):
            validate_vwb.validate_doctor(portable, portable_tools=True)
        with mock.patch.object(strict, "run_vwb", return_value=result):
            validate_vwb.validate_doctor(strict)

        self.assertEqual(portable.failures, [])
        self.assertEqual(len(strict.failures), 1)
        self.assertIn("verible-verilog-lint", strict.failures[0])

    def test_fpga_pack_accepts_supported_nextpnr_alternatives(self):
        runner = self.runner()
        available = {"nextpnr-himbaechel", "gowin_pack", "nextpnr-ice40"}

        def fake_which(command: str, *, path: str | None = None) -> str | None:
            del path
            return f"/usr/bin/{command}" if command in available else None

        with mock.patch.object(validate_vwb.shutil, "which", side_effect=fake_which):
            self.assertTrue(validate_vwb.fpga_pack_available(runner, "gowin"))
            self.assertFalse(validate_vwb.fpga_pack_available(runner, "ice40"))

    def test_portable_formal_skips_when_the_solver_is_unavailable(self):
        runner = self.runner()

        def fake_which(command: str, *, path: str | None = None) -> str | None:
            del path
            return f"/usr/bin/{command}" if command in {"sby", "yosys"} else None

        with (
            mock.patch.object(validate_vwb.shutil, "which", side_effect=fake_which),
            mock.patch.object(runner, "run_vwb") as run_vwb,
        ):
            validate_vwb.validate_formal(runner, portable_tools=True)

        run_vwb.assert_not_called()
        self.assertEqual(runner.failures, [])

    def test_portable_tool_work_skips_only_vhdl_without_ghdl(self):
        runner = self.runner()
        tests = [
            validate_vwb.TestCase(
                module=module,
                design_language=language,
                kind="cocotb",
                language="cocotb",
                path=f"examples/test/test_{module}.py",
                top=None,
                dependency_count=0,
            )
            for module, language in (
                ("verilog_dut", "verilog"),
                ("sv_dut", "systemverilog"),
                ("vhdl_dut", "vhdl"),
            )
        ]
        languages = {
            "verilog_dut": "verilog",
            "sv_dut": "systemverilog",
            "vhdl_dut": "vhdl",
        }

        with mock.patch.object(validate_vwb.shutil, "which", return_value=None):
            modules, selected_tests = validate_vwb.select_portable_tool_work(
                runner, list(languages), tests, languages
            )

        self.assertEqual(modules, ["verilog_dut", "sv_dut"])
        self.assertEqual(
            [test.module for test in selected_tests],
            ["verilog_dut", "sv_dut"],
        )

    def test_portable_tool_work_keeps_vhdl_when_ghdl_is_installed(self):
        runner = self.runner()
        test = validate_vwb.TestCase(
            module="vhdl_dut",
            design_language="vhdl",
            kind="cocotb",
            language="cocotb",
            path="examples/test/test_vhdl_dut.py",
            top=None,
            dependency_count=0,
        )

        with mock.patch.object(
            validate_vwb.shutil, "which", return_value="/usr/bin/ghdl"
        ):
            modules, selected_tests = validate_vwb.select_portable_tool_work(
                runner, ["vhdl_dut"], [test], {"vhdl_dut": "vhdl"}
            )

        self.assertEqual(modules, ["vhdl_dut"])
        self.assertEqual(selected_tests, [test])


class OptionSpellingAuditTests(unittest.TestCase):
    @staticmethod
    def metadata_and_runner() -> tuple[
        validate_vwb.CliMetadata, validate_vwb.Runner
    ]:
        root = Path(__file__).resolve().parents[2]
        metadata = validate_vwb.load_cli_metadata(root / "vwb.py")
        runner = validate_vwb.Runner(
            root=root,
            vwb=root / "vwb.py",
            src_dir="examples/src",
            test_dir="examples/test",
            build_dir=root / ".vwb-option-test",
        )
        return metadata, runner

    def test_new_alias_requires_an_explicit_spelling_classification(self):
        metadata, _runner = self.metadata_and_runner()
        actions = {
            scope: dict(destinations)
            for scope, destinations in metadata.option_actions.items()
        }
        actions["global"]["verbose"] = (
            *actions["global"]["verbose"],
            "--new-verbose-alias",
        )

        with self.assertRaisesRegex(
            validate_vwb.HarnessError,
            r"CLI option spellings for global\.verbose",
        ):
            validate_vwb.option_coverage(
                replace(metadata, option_actions=actions)
            )

    def test_observation_handles_equals_short_forms_and_command_aliases(self):
        metadata, runner = self.metadata_and_runner()
        runner.vwb_invocations = [
            (
                "--root",
                str(runner.root),
                "--src-dir=examples/src",
                "--test-dir",
                "test",
                "--build-dir=.vwb-audit",
                "--color=never",
                "-v",
                "sim",
                "-DDEBUG=1",
                "--define=TRACE=1",
                "-Iincludes",
                "--include=.",
                "--compile-arg=-Wall",
            ),
            ("gtkwave", "--tag=known-good"),
        ]

        observed = validate_vwb.invoked_option_spellings(runner, metadata)

        self.assertTrue(
            {"--root", "--src-dir", "--test-dir", "--build-dir", "--color", "-v"}
            <= observed["global"]
        )
        self.assertTrue(
            {"-D", "--define", "-I", "--include", "--compile-arg"}
            <= observed["test"]
        )
        self.assertIn("--tag", observed["wave"])

    def test_invocation_audit_reports_a_deliberately_missing_alias(self):
        metadata, runner = self.metadata_and_runner()
        runner.vwb_invocations = [("--verbose", "list")]

        validate_vwb.validate_option_spelling_invocations(runner, metadata)

        global_failure = next(
            failure
            for failure in runner.failures
            if "were not invoked for global" in failure
        )
        self.assertIn("-v", global_failure)

    def test_all_at_once_spelling_probes_are_parse_safe(self):
        metadata, runner = self.metadata_and_runner()

        validate_vwb.validate_option_spelling_probes(runner, metadata)

        self.assertEqual(runner.failures, [])


if __name__ == "__main__":
    unittest.main()
