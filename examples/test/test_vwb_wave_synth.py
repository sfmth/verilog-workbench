import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import vwb


class ProjectMixin:
    def write(self, root: Path, relative: str, content: str) -> Path:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def make_project(self, root: Path, *, dry_run: bool = False) -> vwb.Workbench:
        self.write(root, "src/dut.v", "module dut; endmodule\n")
        self.write(
            root,
            "test/test_dut.v",
            "module test_dut; dut u_dut(); initial $finish; endmodule\n",
        )
        return vwb.Workbench(
            root=root,
            src_dir=root / "src",
            test_dir=root / "test",
            build_dir=root / ".vwb",
            dry_run=dry_run,
        )

    @staticmethod
    def simulation_args(**overrides: object) -> SimpleNamespace:
        values: dict[str, object] = {
            "waves": True,
            "wave_format": "vcd",
            "max_array_words": 32,
            "define": [],
            "include": [],
            "compile_arg": [],
            "sim_arg": [],
            "plusarg": [],
            "test_top": None,
            "testcase": None,
            "seed": None,
            "gate_level": False,
        }
        values.update(overrides)
        return SimpleNamespace(**values)


class WaveParserTests(unittest.TestCase):
    def test_simulation_option_names_and_array_default(self):
        parser = vwb.make_parser()

        args = parser.parse_args(["test"])

        self.assertEqual(args.test_language, "auto")
        self.assertEqual(args.max_array_words, vwb.DEFAULT_MAX_ARRAY_WORDS)
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["test", "--kind", "hdl"])

    def test_wave_saved_run_options_parse(self):
        parser = vwb.make_parser()

        tagged = parser.parse_args(["wave", "dut", "--tag", "known-good"])
        gated = parser.parse_args(["wave", "dut", "--gate-level"])
        loaded = parser.parse_args(["wave", "--load", "known-good"])
        listed = parser.parse_args(["wave", "--list-saved"])

        self.assertEqual(tagged.tag, "known-good")
        self.assertFalse(tagged.gate_level)
        self.assertTrue(gated.gate_level)
        self.assertEqual(loaded.load, "known-good")
        self.assertTrue(listed.list_saved)

    def test_wave_management_detects_explicit_default_valued_options(self):
        parser = vwb.make_parser()
        cases = [
            (["wave", "--load", "baseline", "--wave-format", "fst"], "--wave-format"),
            (
                [
                    "wave",
                    "--list-saved",
                    f"--max-array-words={vwb.DEFAULT_MAX_ARRAY_WORDS}",
                ],
                "--max-array-words",
            ),
            (["wave", "--list-saved", "--test-language", "auto"], "--test-language"),
            (["wave", "--list-saved", "-DDEFAULT=1"], "--define"),
            (["wave", "--list-saved", "--waves"], "--waves"),
            (["wave", "--load", "baseline", "--gate-level"], "--gate-level"),
        ]

        for argv, expected in cases:
            with self.subTest(argv=argv):
                args = parser.parse_args(argv)
                self.assertIn(expected, vwb.wave_management_overrides(args))

    def test_long_option_abbreviations_are_rejected(self):
        parser = vwb.make_parser()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["wave", "--load", "baseline", "--wave-f", "fst"])


class WaveLifecycleTests(ProjectMixin, unittest.TestCase):
    def test_simulation_cleanup_preserves_gtkw_but_removes_stale_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbench = self.make_project(root)
            workbench.prepare_build_dir()
            spec = workbench.tests[0]
            work_dir = root / ".vwb" / "sim" / "dut" / "verilog-test_dut"
            work_dir.mkdir(parents=True)
            save_file = self.write(work_dir, "dut.gtkw", "[*] preserved\n")
            stale = self.write(work_dir, "stale-output.txt", "old simulation\n")

            def compile_simulation(
                _spec: vwb.TestSpec, actual_work_dir: Path, **_kwargs: object
            ) -> tuple[bool, Path, Path]:
                self.assertEqual(actual_work_dir, work_dir)
                self.assertTrue(save_file.is_file())
                self.assertFalse(stale.exists())
                wave = self.write(actual_work_dir, "dut.vcd", "$enddefinitions $end\n")
                simulation = self.write(actual_work_dir, "sim.vvp", "compiled\n")
                return True, wave, simulation

            completed = subprocess.CompletedProcess(["vvp"], 0, "", "")
            with (
                mock.patch.object(
                    workbench,
                    "_compile_simulation",
                    side_effect=compile_simulation,
                ),
                mock.patch.object(workbench, "require_tool", return_value="vvp"),
                mock.patch.object(workbench, "run", return_value=completed),
            ):
                passed, wave = workbench.run_test_spec(
                    spec, self.simulation_args()
                )

            self.assertTrue(passed)
            self.assertEqual(wave, work_dir / "dut.vcd")
            self.assertEqual(save_file.read_text(encoding="utf-8"), "[*] preserved\n")

    def test_tagged_wave_is_self_contained_and_can_be_loaded_after_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbench = self.make_project(root)
            workbench.prepare_build_dir()
            work_dir = root / ".vwb" / "sim" / "dut" / "verilog-test_dut"
            wave = self.write(work_dir, "dut.vcd", "$enddefinitions $end\n")
            self.write(work_dir, "dut.gtkw", "[*] GTKWave Analyzer save file\n")
            parser = vwb.make_parser()
            tag_args = parser.parse_args(
                [
                    "wave",
                    "dut",
                    "--test-language",
                    "verilog",
                    "--wave-format",
                    "vcd",
                    "--tag",
                    "known-good",
                ]
            )
            completed = subprocess.CompletedProcess(["gtkwave"], 0, "", "")

            with (
                mock.patch.object(workbench, "run_tests", return_value=(True, [wave])),
                mock.patch.object(workbench, "require_tool", return_value="gtkwave"),
                mock.patch.object(workbench, "run", return_value=completed),
            ):
                self.assertEqual(vwb.command_wave(workbench, tag_args), 0)

            saved = root / ".vwb" / "saved-waves" / "known-good"
            self.assertTrue(saved.is_dir())
            self.assertEqual([path.name for path in saved.glob("*.vcd")], ["dut.vcd"])
            self.assertEqual([path.name for path in saved.glob("*.gtkw")], ["dut.gtkw"])
            metadata_files = list(saved.glob("*.json"))
            self.assertEqual(len(metadata_files), 1)
            metadata = json.loads(metadata_files[0].read_text(encoding="utf-8"))
            self.assertEqual(metadata["dut"], "dut")
            for key in ("waveform", "layout"):
                if metadata.get(key):
                    self.assertFalse(Path(metadata[key]).is_absolute())
                    self.assertTrue((saved / metadata[key]).is_file())

            # A saved run must not depend on files in the transient simulation tree.
            for path in work_dir.iterdir():
                path.unlink()
            work_dir.rmdir()
            calls: list[tuple[list[str], Path | None]] = []

            def record_run(
                command: list[str | Path],
                *,
                cwd: Path | None = None,
                **_kwargs: object,
            ) -> subprocess.CompletedProcess[str]:
                calls.append(([str(item) for item in command], cwd))
                return subprocess.CompletedProcess(command, 0, "", "")

            load_args = parser.parse_args(["wave", "--load", "known-good"])
            with (
                mock.patch.object(
                    workbench,
                    "run_tests",
                    side_effect=AssertionError("loading a tag must not rerun simulation"),
                ),
                mock.patch.object(workbench, "require_tool", return_value="gtkwave"),
                mock.patch.object(workbench, "run", side_effect=record_run),
            ):
                self.assertEqual(vwb.command_wave(workbench, load_args), 0)

            self.assertEqual(len(calls), 1)
            command, cwd = calls[0]
            self.assertEqual(command[0:2], ["gtkwave", "--autosavename"])
            self.assertEqual(cwd, saved)
            self.assertFalse(Path(command[2]).is_absolute())
            self.assertTrue((saved / command[2]).is_file())

    def test_saved_wave_listing_and_invalid_tags(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbench = self.make_project(root)
            workbench.prepare_build_dir()
            wave = self.write(
                root, "completed-run.vcd", "$enddefinitions $end\n"
            )
            workbench.archive_wave(
                "baseline",
                workbench.tests[0],
                wave,
                self.simulation_args(),
                None,
                replace=False,
            )
            parser = vwb.make_parser()
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                result = vwb.command_wave(
                    workbench, parser.parse_args(["wave", "--list-saved"])
                )

            self.assertEqual(result, 0)
            self.assertIn("baseline", output.getvalue())
            self.assertIn("dut", output.getvalue())

            for invalid in (
                "../escape",
                "nested/tag",
                ".",
                "",
                "_tag",
                "-tag",
                ".tag",
            ):
                with self.subTest(tag=invalid):
                    with self.assertRaises(vwb.VWBError):
                        workbench._validate_wave_tag(invalid)

    def test_saved_wave_replacement_is_explicit_and_atomic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbench = self.make_project(root)
            args = self.simulation_args()
            first = self.write(root, "first.vcd", "first waveform\n")
            second = self.write(root, "second.vcd", "second waveform\n")
            original = workbench.archive_wave(
                "baseline", workbench.tests[0], first, args, None, replace=False
            )

            with self.assertRaisesRegex(vwb.VWBError, "use --replace-tag"):
                workbench.archive_wave(
                    "baseline", workbench.tests[0], second, args, None, replace=False
                )
            self.assertEqual(original.waveform.read_text(encoding="utf-8"), "first waveform\n")

            real_replace = vwb.os.replace
            target = root / ".vwb" / "saved-waves" / "baseline"

            def fail_new_archive(source: str | Path, destination: str | Path) -> None:
                source_path = Path(source)
                destination_path = Path(destination)
                if source_path.name.endswith(".tmp") and destination_path == target:
                    raise OSError("injected commit failure")
                real_replace(source, destination)

            with (
                mock.patch.object(vwb.os, "replace", side_effect=fail_new_archive),
                self.assertRaisesRegex(vwb.VWBError, "could not replace"),
            ):
                workbench.archive_wave(
                    "baseline", workbench.tests[0], second, args, None, replace=True
                )

            restored = workbench._read_saved_wave("baseline")
            self.assertEqual(restored.waveform.read_text(encoding="utf-8"), "first waveform\n")
            self.assertEqual(list((root / ".vwb" / "saved-waves").glob(".*.backup")), [])

            replaced = workbench.archive_wave(
                "baseline", workbench.tests[0], second, args, None, replace=True
            )
            self.assertEqual(replaced.waveform.read_text(encoding="utf-8"), "second waveform\n")

    def test_gtkwave_uses_autosave_and_simulation_directory_as_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbench = self.make_project(root, dry_run=True)
            self.write(
                root,
                f".vwb/{vwb.BUILD_MARKER}",
                f"Verilog Work Bench {vwb.VERSION}\nproject={root.resolve()}\n",
            )
            work_dir = root / ".vwb" / "sim" / "dut" / "verilog-test_dut"
            self.write(work_dir, "dut.gtkw", "[*] existing view\n")
            args = vwb.make_parser().parse_args(
                [
                    "wave",
                    "dut",
                    "--test-language",
                    "verilog",
                    "--wave-format",
                    "vcd",
                ]
            )
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                result = vwb.command_wave(workbench, args)

            self.assertEqual(result, 0)
            gtkwave_line = next(
                line
                for line in output.getvalue().splitlines()
                if line.startswith("$ gtkwave")
            )
            self.assertIn("--autosavename", gtkwave_line)
            self.assertIn("dut.vcd", gtkwave_line)
            self.assertIn(f"cwd={work_dir}", gtkwave_line)

    def test_explicit_gtkwave_save_file_is_loaded_from_wave_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbench = self.make_project(root)
            wave = self.write(root, ".vwb/sim/dut/run/dut.vcd", "waveform\n")
            layout = self.write(root, "views/dut.gtkw", "[*] saved layout\n")
            completed = subprocess.CompletedProcess(["gtkwave"], 0, "", "")

            with (
                mock.patch.object(workbench, "require_tool", return_value="gtkwave"),
                mock.patch.object(workbench, "run", return_value=completed) as run,
            ):
                status, active_layout = workbench.open_waveform(
                    wave, explicit_save=str(layout)
                )

            self.assertEqual(status, 0)
            self.assertEqual(active_layout, layout)
            run.assert_called_once_with(
                ["gtkwave", f"--save={layout}", wave.name], cwd=wave.parent
            )

    def test_layout_next_to_configured_source_tree_is_loaded_automatically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "examples/src/dut.v", "module dut; endmodule\n")
            self.write(
                root,
                "examples/test/test_dut.v",
                "module test_dut; dut instance(); initial $finish; endmodule\n",
            )
            workbench = vwb.Workbench(
                root=root,
                src_dir=root / "examples" / "src",
                test_dir=root / "examples" / "test",
                build_dir=root / ".vwb",
            )
            legacy = self.write(
                root, "examples/dut.gtkw", "[*] bundled layout\n"
            )
            wave = self.write(root, ".vwb/sim/dut/run/dut.vcd", "waveform\n")
            completed = subprocess.CompletedProcess(["gtkwave"], 0, "", "")

            with (
                mock.patch.object(workbench, "require_tool", return_value="gtkwave"),
                mock.patch.object(workbench, "run", return_value=completed),
            ):
                status, active_layout = workbench.open_waveform(
                    wave, legacy_dut="dut"
                )

            self.assertEqual(status, 0)
            self.assertEqual(active_layout, wave.with_suffix(".gtkw"))
            self.assertEqual(
                active_layout.read_text(encoding="utf-8"),
                legacy.read_text(encoding="utf-8"),
            )

    def test_cleaning_symlinked_scope_does_not_follow_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbench = self.make_project(root)
            workbench.prepare_build_dir()
            outside = root / "outside"
            outside.mkdir()
            sentinel = self.write(outside, "keep.txt", "keep\n")
            sim_link = root / ".vwb" / "sim"
            sim_link.symlink_to(outside, target_is_directory=True)

            workbench.clean("sim")

            self.assertFalse(sim_link.exists())
            self.assertFalse(sim_link.is_symlink())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep\n")

    def test_default_clean_removes_temporary_outputs_but_preserves_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbench = self.make_project(root)
            workbench.prepare_build_dir()
            for scope in ("sim", "lint", "fpga", "formal", "synth", "saved-waves"):
                self.write(root, f".vwb/{scope}/result.txt", f"{scope}\n")
            layout = self.write(
                root, ".vwb/sim/dut/test-dut/dut.gtkw", "[*] saved layout\n"
            )

            args = vwb.make_parser().parse_args(["clean"])
            workbench.clean(args.scope)

            self.assertEqual(args.scope, "temp")
            self.assertFalse((root / ".vwb" / "lint").exists())
            self.assertFalse((root / ".vwb" / "sim" / "result.txt").exists())
            self.assertEqual(layout.read_text(encoding="utf-8"), "[*] saved layout\n")
            for scope in ("fpga", "formal", "synth", "saved-waves"):
                result = root / ".vwb" / scope / "result.txt"
                self.assertEqual(result.read_text(encoding="utf-8"), f"{scope}\n")

            workbench.clean("sim")
            self.assertFalse(layout.exists())
            for scope, directory_name in (
                ("fpga", "fpga"),
                ("formal", "formal"),
                ("synth", "synth"),
                ("waves", "saved-waves"),
            ):
                workbench.clean(scope)
                self.assertFalse((root / ".vwb" / directory_name).exists())

    def test_clean_works_after_source_and_test_directories_are_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbench = self.make_project(root)
            workbench.prepare_build_dir()
            temporary = self.write(root, ".vwb/lint/report.txt", "temporary\n")
            for tree in (root / "src", root / "test"):
                for path in tree.iterdir():
                    path.unlink()
                tree.rmdir()

            status = vwb.main(["--root", str(root), "clean"])

            self.assertEqual(status, 0)
            self.assertFalse(temporary.exists())

    def test_clean_dry_run_lists_only_artifacts_that_would_be_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbench = self.make_project(root)
            workbench.prepare_build_dir()
            stale = self.write(root, ".vwb/sim/dut/run/stale.vvp", "stale\n")
            layout = self.write(root, ".vwb/sim/dut/run/dut.gtkw", "layout\n")
            dry_run = vwb.Workbench(
                root=root,
                src_dir=root / "src",
                test_dir=root / "test",
                build_dir=root / ".vwb",
                dry_run=True,
            )
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                dry_run.clean("temp")
                dry_run.clean("formal")

            report = output.getvalue()
            lines = report.splitlines()
            self.assertIn(str(stale), report)
            self.assertNotIn(str(layout), report)
            self.assertNotIn(f"remove {root / '.vwb' / 'sim'}", lines)
            self.assertNotIn(str(root / ".vwb" / "formal"), report)
            self.assertTrue(stale.is_file())
            self.assertTrue(layout.is_file())

    def test_clean_rejects_legacy_marker_with_extra_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbench = self.make_project(root)
            marker = self.write(
                root,
                f".vwb/{vwb.BUILD_MARKER}",
                "not-owned Verilog Work Bench old\n"
                f"project={root.resolve()}\n",
            )
            self.write(root, ".vwb/lint/keep.txt", "keep\n")

            with self.assertRaisesRegex(vwb.VWBError, "another project"):
                workbench.clean("lint")

            self.assertTrue(marker.is_file())
            self.assertTrue((root / ".vwb" / "lint" / "keep.txt").is_file())

    def test_saved_wave_payload_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbench = self.make_project(root)
            wave = self.write(root, "run.vcd", "waveform\n")
            saved = workbench.archive_wave(
                "baseline",
                workbench.tests[0],
                wave,
                self.simulation_args(),
                None,
                replace=False,
            )
            outside_wave = self.write(root, "outside.vcd", "outside\n")
            saved.waveform.unlink()
            saved.waveform.symlink_to(outside_wave)

            with self.assertRaisesRegex(vwb.VWBError, "waveform file is missing"):
                workbench._read_saved_wave("baseline")

    def test_saved_wave_layout_sync_does_not_follow_destination_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbench = self.make_project(root)
            wave = self.write(root, "run.vcd", "waveform\n")
            saved = workbench.archive_wave(
                "baseline",
                workbench.tests[0],
                wave,
                self.simulation_args(),
                None,
                replace=False,
            )
            source_layout = self.write(root, "source.gtkw", "new layout\n")
            outside_layout = self.write(root, "outside.gtkw", "do not replace\n")
            archived_layout = saved.directory / "dut.gtkw"
            archived_layout.symlink_to(outside_layout)

            with self.assertRaisesRegex(vwb.VWBError, "symlinked GTKWave layout"):
                workbench.sync_saved_wave_layout("baseline", source_layout)
            self.assertEqual(
                outside_layout.read_text(encoding="utf-8"), "do not replace\n"
            )

    def test_saved_wave_root_symlink_is_rejected_before_archive_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbench = self.make_project(root)
            workbench.prepare_build_dir()
            outside = root / "outside-archives"
            outside.mkdir()
            workbench.saved_waves_dir.symlink_to(outside, target_is_directory=True)
            wave = self.write(root, "run.vcd", "waveform\n")

            with self.assertRaisesRegex(vwb.VWBError, "symlinked saved-wave directory"):
                workbench.archive_wave(
                    "baseline",
                    workbench.tests[0],
                    wave,
                    self.simulation_args(),
                    None,
                    replace=False,
                )
            with self.assertRaisesRegex(vwb.VWBError, "symlinked saved-wave directory"):
                workbench.saved_waves()
            self.assertEqual(list(outside.iterdir()), [])


class SynthesisTests(ProjectMixin, unittest.TestCase):
    def test_large_svg_requires_svg_instead_of_a_limited_png(self):
        with tempfile.TemporaryDirectory() as directory:
            svg = Path(directory) / "large.svg"
            svg.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" '
                'width="10000pt" height="10000pt"/>\n',
                encoding="utf-8",
            )

            self.assertTrue(vwb.Workbench._png_would_exceed_limit(svg))

    def test_synth_parser_defaults_and_canonical_schematic_options(self):
        parser = vwb.make_parser()

        defaults = parser.parse_args(["synth", "dut"])
        canonical = parser.parse_args(["synth", "dut", "--schematic"])
        disabled = parser.parse_args(["synth", "dut", "--no-schematic"])

        self.assertFalse(defaults.full)
        self.assertTrue(defaults.schematic)
        self.assertEqual(defaults.format, "png")
        self.assertEqual(defaults.view, "auto")
        self.assertTrue(canonical.schematic)
        self.assertFalse(disabled.schematic)
        for misspelling in ("--schemetic", "--no-schemetic"):
            with self.subTest(misspelling=misspelling):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        parser.parse_args(["synth", "dut", misspelling])

    def test_format_parses_with_every_full_and_schematic_combination(self):
        parser = vwb.make_parser()

        for full in (False, True):
            for schematic in (False, True):
                for output_format in ("json", "dot", "svg", "png"):
                    argv = ["synth", "dut", "--format", output_format, "--view", "none"]
                    if full:
                        argv.append("--full")
                    argv.append("--schematic" if schematic else "--no-schematic")
                    with self.subTest(
                        full=full, schematic=schematic, output_format=output_format
                    ):
                        args = parser.parse_args(argv)
                        self.assertEqual(args.format, output_format)
                        self.assertEqual(args.schematic, schematic)
                        self.assertEqual(args.full, full)

    def _synthesize_with_fake_tools(
        self,
        root: Path,
        argv: list[str],
        *,
        fail_netlistsvg: bool = False,
        invalid_netlistsvg: bool = False,
        fail_yosys_svg: bool = False,
        large_json: bool = False,
        large_svg: bool = False,
        stale_png: bool = False,
        fail_sfdp: bool = False,
        fail_dot: bool = False,
        missing_dot: bool = False,
        missing_sfdp: bool = False,
    ) -> tuple[Path, str, list[tuple[list[str], Path | None]]]:
        workbench = self.make_project(root)
        args = vwb.make_parser().parse_args(argv)
        calls: list[tuple[list[str], Path | None]] = []
        if stale_png:
            workbench.prepare_build_dir()
            stale = root / ".vwb" / "synth" / "dut" / "dut.png"
            stale.parent.mkdir(parents=True, exist_ok=True)
            stale.write_bytes(b"old PNG")

        def fake_run(
            command: list[str | Path],
            *,
            cwd: Path | None = None,
            **_kwargs: object,
        ) -> subprocess.CompletedProcess[str]:
            items = [str(item) for item in command]
            calls.append((items, cwd))
            output_dir = root / ".vwb" / "synth" / "dut"
            if items[0] == "yosys":
                script = ""
                if "-p" in items:
                    script = items[items.index("-p") + 1]
                elif "-s" in items:
                    script = Path(items[items.index("-s") + 1]).read_text(
                        encoding="utf-8"
                    )
                elif "-c" in items:
                    script = Path(items[items.index("-c") + 1]).read_text(
                        encoding="utf-8"
                    )
                for write_match in vwb.re.finditer(
                    r"write_json\s+(?:\"([^\"]+)\"|([^\s;]+))", script
                ):
                    written = Path(
                        write_match.group(1) or write_match.group(2)
                    )
                    if not written.is_absolute() and cwd is not None:
                        written = cwd / written
                    written.parent.mkdir(parents=True, exist_ok=True)
                    if written.name == "dut.json" and large_json:
                        size = vwb.SCALABLE_LAYOUT_JSON_LIMIT_BYTES + 1
                    else:
                        size = 0
                    payload = {
                        "modules": {
                            "dut": {
                                "ports": {
                                    "clk": {"direction": "input", "bits": [1]},
                                    "result": {
                                        "direction": "output",
                                        "bits": [2, 3],
                                    },
                                },
                                "cells": {
                                    "$not$1": {
                                        "type": "$not",
                                        "parameters": {},
                                        "attributes": {},
                                        "port_directions": {
                                            "A": "input",
                                            "Y": "output",
                                        },
                                        "connections": {"A": [1], "Y": [2]},
                                    }
                                },
                            }
                        }
                    }
                    if size:
                        payload["padding"] = "x" * size
                    written.write_text(
                        vwb.json.dumps(payload) + "\n", encoding="utf-8"
                    )
                prefix_match = vwb.re.search(r"-prefix\s+\"?([^\" ;]+)", script)
                format_match = vwb.re.search(r"-format\s+(dot|svg|png)", script)
                if prefix_match and format_match:
                    if fail_yosys_svg and format_match.group(1) == "svg":
                        return subprocess.CompletedProcess(
                            command, 124, "", "layout timed out"
                        )
                    prefix = Path(prefix_match.group(1))
                    if not prefix.is_absolute() and cwd is not None:
                        prefix = cwd / prefix
                    prefix.with_suffix("." + format_match.group(1)).write_text(
                        (
                            '<svg width="10000" height="10000"/>\n'
                            if large_svg and format_match.group(1) == "svg"
                            else "<svg/>\n"
                            if format_match.group(1) == "svg"
                            else "rendered\n"
                        ),
                        encoding="utf-8",
                    )
            elif items[0] == "sfdp":
                Path(items[items.index("-o") + 1]).write_text(
                    (
                        "partial output\n"
                        if fail_sfdp
                        else '<svg width="10000" height="10000"/>\n'
                        if large_svg
                        else "<svg/>\n"
                    ),
                    encoding="utf-8",
                )
                if fail_sfdp:
                    return subprocess.CompletedProcess(
                        command, 1, "", "layout failed"
                    )
            elif items[0] == "dot":
                Path(items[items.index("-o") + 1]).write_text(
                    "partial output\n" if fail_dot else "<svg/>\n",
                    encoding="utf-8",
                )
                if fail_dot:
                    return subprocess.CompletedProcess(
                        command, 1, "", "layout failed"
                    )
            elif items[0] == "netlistsvg":
                if fail_netlistsvg:
                    return subprocess.CompletedProcess(command, 1, "", "recursion failed")
                Path(items[items.index("-o") + 1]).write_text(
                    (
                        "not an svg\n"
                        if invalid_netlistsvg
                        else '<svg width="10000" height="10000"/>\n'
                        if large_svg
                        else "<svg/>\n"
                    ),
                    encoding="utf-8",
                )
            elif items[0] == "rsvg-convert":
                if "--output" in items:
                    output = items[items.index("--output") + 1]
                elif "-o" in items:
                    output = items[items.index("-o") + 1]
                else:
                    output = next(
                        item.split("=", 1)[1]
                        for item in items
                        if item.startswith("--output=")
                    )
                Path(output).write_bytes(b"PNG")
            return subprocess.CompletedProcess(command, 0, "", "")

        def require_tool(item: str) -> str:
            if missing_dot and item == "dot":
                raise vwb.VWBError("required command is not on PATH: dot")
            if missing_sfdp and item == "sfdp":
                raise vwb.VWBError("required command is not on PATH: sfdp")
            return item

        with (
            mock.patch.object(workbench, "require_tool", side_effect=require_tool),
            mock.patch.object(workbench, "run", side_effect=fake_run),
        ):
            artifact = workbench.synthesize("dut", args)

        script_path = root / ".vwb" / "synth" / "dut" / "synth.tcl"
        script = script_path.read_text(encoding="utf-8")
        return artifact, script, calls

    def test_default_synthesis_uses_makefile_style_flow_and_netlistsvg(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            artifact, script, calls = self._synthesize_with_fake_tools(
                root, ["synth", "dut"]
            )

            self.assertEqual(artifact, root / ".vwb" / "synth" / "dut" / "dut.png")
            self.assertIn("prep", script)
            self.assertNotIn("proc", script)
            self.assertNotIn("opt -full", script)
            self.assertNotIn("synth -top dut", script)
            commands = [command for command, _cwd in calls]
            self.assertTrue(any(command[0] == "netlistsvg" for command in commands))
            self.assertTrue(any(command[0] == "rsvg-convert" for command in commands))
            self.assertFalse(any(command[0] == "convert" for command in commands))
            self.assertTrue(any(command[0] == "geeqie" for command in commands))
            raster = next(command for command in commands if command[0] == "rsvg-convert")
            self.assertEqual(raster[raster.index("--zoom") + 1], "2")
            self.assertEqual(
                raster[raster.index("--background-color") + 1], "white"
            )
            self.assertIn("--unlimited", raster)
            self.assertTrue(raster[raster.index("--output") + 1].endswith(".png.tmp"))
            self.assertTrue(raster[-1].endswith("dut.svg"))

    def test_svg_uses_inkscape_as_its_default_viewer(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact, _script, calls = self._synthesize_with_fake_tools(
                Path(directory), ["synth", "dut", "--format", "svg"]
            )

        commands = [command for command, _cwd in calls]
        self.assertEqual(artifact.suffix, ".svg")
        self.assertTrue(any(command[0] == "inkscape" for command in commands))
        self.assertFalse(any(command[0] == "geeqie" for command in commands))

    def test_nonvisual_formats_do_not_open_a_default_viewer(self):
        for output_format in ("json", "dot"):
            with self.subTest(output_format=output_format):
                with tempfile.TemporaryDirectory() as directory:
                    _artifact, _script, calls = self._synthesize_with_fake_tools(
                        Path(directory), ["synth", "dut", "--format", output_format]
                    )

                commands = [command for command, _cwd in calls]
                self.assertFalse(
                    any(command[0] in {"geeqie", "inkscape"} for command in commands)
                )

    def test_netlistsvg_failure_falls_back_to_yosys_svg_rendering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            errors = io.StringIO()

            with contextlib.redirect_stderr(errors):
                artifact, _script, calls = self._synthesize_with_fake_tools(
                    root,
                    [
                        "synth",
                        "dut",
                        "--format",
                        "svg",
                        "--schematic",
                        "--view",
                        "none",
                    ],
                    fail_netlistsvg=True,
                )

            commands = [command for command, _cwd in calls]
            self.assertEqual(artifact, root / ".vwb" / "synth" / "dut" / "dut.svg")
            self.assertTrue(artifact.is_file())
            self.assertEqual(sum(command[0] == "netlistsvg" for command in commands), 1)
            self.assertEqual(sum(command[0] == "yosys" for command in commands), 2)
            self.assertIn("using the Yosys schematic instead", errors.getvalue())

    def test_invalid_netlistsvg_output_falls_back_to_yosys_svg_rendering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            artifact, _script, calls = self._synthesize_with_fake_tools(
                root,
                ["synth", "dut", "--format", "svg", "--view", "none"],
                invalid_netlistsvg=True,
            )

            commands = [command for command, _cwd in calls]
            self.assertTrue(vwb.Workbench._is_valid_svg(artifact))
            self.assertEqual(sum(command[0] == "netlistsvg" for command in commands), 1)
            self.assertEqual(sum(command[0] == "yosys" for command in commands), 2)

    def test_large_yosys_svg_uses_scalable_graphviz_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            errors = io.StringIO()

            with contextlib.redirect_stderr(errors):
                artifact, _script, calls = self._synthesize_with_fake_tools(
                    root,
                    [
                        "synth",
                        "dut",
                        "--format",
                        "svg",
                        "--no-schematic",
                        "--view",
                        "none",
                    ],
                    fail_yosys_svg=True,
                )

            commands = [command for command, _cwd in calls]
            self.assertTrue(vwb.Workbench._is_valid_svg(artifact))
            self.assertEqual(sum(command[0] == "yosys" for command in commands), 3)
            self.assertEqual(sum(command[0] == "sfdp" for command in commands), 1)
            self.assertIn("scalable sfdp layout", errors.getvalue())

    def test_large_json_uses_scalable_layout_without_waiting_for_dot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            errors = io.StringIO()

            with contextlib.redirect_stderr(errors):
                artifact, _script, calls = self._synthesize_with_fake_tools(
                    root,
                    [
                        "synth",
                        "dut",
                        "--format",
                        "svg",
                        "--no-schematic",
                        "--view",
                        "none",
                    ],
                    large_json=True,
                    missing_dot=True,
                )

            commands = [command for command, _cwd in calls]
            self.assertTrue(vwb.Workbench._is_valid_svg(artifact))
            self.assertEqual(sum(command[0] == "yosys" for command in commands), 2)
            self.assertEqual(sum(command[0] == "sfdp" for command in commands), 1)
            self.assertIn("graph is large", errors.getvalue())

    def test_large_json_uses_dot_when_sfdp_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            artifact, _script, calls = self._synthesize_with_fake_tools(
                root,
                [
                    "synth",
                    "dut",
                    "--format",
                    "svg",
                    "--no-schematic",
                    "--view",
                    "none",
                ],
                large_json=True,
                missing_sfdp=True,
            )

            commands = [command for command, _cwd in calls]
            self.assertTrue(vwb.Workbench._is_valid_svg(artifact))
            self.assertFalse(any(command[0] == "sfdp" for command in commands))
            self.assertEqual(sum(command[0] == "dot" for command in commands), 1)

    def test_large_json_still_uses_netlistsvg_when_it_succeeds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            errors = io.StringIO()

            with contextlib.redirect_stderr(errors):
                artifact, _script, calls = self._synthesize_with_fake_tools(
                    root,
                    [
                        "synth",
                        "dut",
                        "--format",
                        "svg",
                        "--schematic",
                        "--view",
                        "none",
                    ],
                    large_json=True,
                )

            commands = [command for command, _cwd in calls]
            self.assertTrue(vwb.Workbench._is_valid_svg(artifact))
            self.assertEqual(sum(command[0] == "netlistsvg" for command in commands), 1)
            self.assertFalse(any(command[0] == "sfdp" for command in commands))
            self.assertNotIn("Yosys schematic instead", errors.getvalue())

    def test_full_large_netlist_renders_full_yosys_schematic_and_keeps_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            errors = io.StringIO()

            with contextlib.redirect_stderr(errors):
                artifact, _script, calls = self._synthesize_with_fake_tools(
                    root,
                    [
                        "synth",
                        "dut",
                        "--full",
                        "--flatten",
                        "--format",
                        "svg",
                        "--schematic",
                        "--view",
                        "none",
                    ],
                    large_json=True,
                    fail_netlistsvg=True,
                )

            output_dir = root / ".vwb" / "synth" / "dut"
            full_json = output_dir / "dut.json"
            render_script = (output_dir / "render.ys").read_text(encoding="utf-8")
            commands = [command for command, _cwd in calls]

            self.assertTrue(vwb.Workbench._is_valid_svg(artifact))
            self.assertGreater(
                full_json.stat().st_size,
                vwb.SCALABLE_LAYOUT_JSON_LIMIT_BYTES,
            )
            netlist = json.loads(full_json.read_text(encoding="utf-8"))
            self.assertIn("$not$1", netlist["modules"]["dut"]["cells"])
            self.assertRegex(render_script, r'read_json\s+"?dut\.json"?')
            self.assertRegex(render_script, r'show\s+-format\s+dot\b')
            self.assertEqual(sum(command[0] == "yosys" for command in commands), 2)
            self.assertEqual(sum(command[0] == "sfdp" for command in commands), 1)
            self.assertEqual(sum(command[0] == "netlistsvg" for command in commands), 1)
            self.assertFalse((output_dir / "dut.visual.json").exists())
            self.assertFalse((output_dir / "dut.interface.dot").exists())
            self.assertIn("using the Yosys schematic instead", errors.getvalue())

    def test_full_large_png_returns_svg_and_opens_inkscape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            errors = io.StringIO()

            with contextlib.redirect_stderr(errors):
                artifact, _script, calls = self._synthesize_with_fake_tools(
                    root,
                    [
                        "synth",
                        "dut",
                        "--full",
                        "--format",
                        "png",
                        "--schematic",
                    ],
                    large_json=True,
                    large_svg=True,
                    fail_netlistsvg=True,
                    stale_png=True,
                )

            output_dir = root / ".vwb" / "synth" / "dut"
            commands = [command for command, _cwd in calls]
            self.assertEqual(artifact, output_dir / "dut.svg")
            self.assertTrue(vwb.Workbench._is_valid_svg(artifact))
            self.assertEqual(sum(command[0] == "netlistsvg" for command in commands), 1)
            self.assertEqual(sum(command[0] == "yosys" for command in commands), 2)
            self.assertEqual(sum(command[0] == "sfdp" for command in commands), 1)
            self.assertFalse(any(command[0] == "rsvg-convert" for command in commands))
            self.assertTrue(any(command[0] == "inkscape" for command in commands))
            self.assertFalse((output_dir / "dut.interface.dot").exists())
            self.assertFalse((output_dir / "dut.visual.json").exists())
            self.assertFalse((output_dir / "dut.png").exists())
            self.assertIn("keeping the SVG instead", errors.getvalue())

    def test_failed_scalable_layout_keeps_json_and_removes_partial_svg(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            with self.assertRaisesRegex(vwb.VWBError, "visual rendering failed"):
                self._synthesize_with_fake_tools(
                    root,
                    [
                        "synth",
                        "dut",
                        "--format",
                        "svg",
                        "--no-schematic",
                        "--view",
                        "none",
                    ],
                    large_json=True,
                    fail_sfdp=True,
                    fail_dot=True,
                )

            output_dir = root / ".vwb" / "synth" / "dut"
            self.assertTrue((output_dir / "dut.json").is_file())
            self.assertFalse((output_dir / "dut.svg").exists())

    def test_preparation_flow_matches_makefile_backend_matrix(self):
        cases = [
            (True, False, ["prep"], ["prep -flatten", "proc", "opt -full", "synth -top dut"]),
            (True, True, ["prep -flatten"], ["proc", "opt -full", "synth -top dut"]),
            (False, False, ["proc", "opt -full"], ["prep", "prep -flatten", "synth -top dut"]),
            (False, True, ["synth -top dut"], ["prep", "prep -flatten", "proc", "opt -full"]),
        ]

        for schematic, full, expected, absent in cases:
            with self.subTest(schematic=schematic, full=full):
                with tempfile.TemporaryDirectory() as directory:
                    argv = [
                        "synth",
                        "dut",
                        "--format",
                        "json",
                        "--view",
                        "none",
                        "--schematic" if schematic else "--no-schematic",
                    ]
                    if full:
                        argv.append("--full")
                    _artifact, script, _calls = self._synthesize_with_fake_tools(
                        Path(directory), argv
                    )
                    commands = [
                        item.removeprefix("yosys ")
                        for item in script.splitlines()
                        if item
                    ]
                    for command in expected:
                        self.assertIn(command, commands)
                    for command in absent:
                        self.assertNotIn(command, commands)

    def test_requested_format_is_produced_for_every_backend_combination(self):
        for full in (False, True):
            for schematic in (False, True):
                for output_format in ("json", "dot", "svg", "png"):
                    with self.subTest(
                        full=full, schematic=schematic, output_format=output_format
                    ):
                        with tempfile.TemporaryDirectory() as directory:
                            argv = [
                                "synth",
                                "dut",
                                "--format",
                                output_format,
                                "--view",
                                "none",
                                "--schematic" if schematic else "--no-schematic",
                            ]
                            if full:
                                argv.append("--full")

                            artifact, _script, _calls = (
                                self._synthesize_with_fake_tools(Path(directory), argv)
                            )

                            self.assertEqual(artifact.suffix, f".{output_format}")
                            self.assertTrue(artifact.is_file())

    def test_full_synthesis_and_yosys_rendering_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            artifact, script, calls = self._synthesize_with_fake_tools(
                root,
                [
                    "synth",
                    "dut",
                    "--full",
                    "--no-schematic",
                    "--format",
                    "dot",
                    "--view",
                    "none",
                ],
            )

            self.assertEqual(artifact, root / ".vwb" / "synth" / "dut" / "dut.dot")
            self.assertIn("synth -top dut", script)
            self.assertNotIn("opt -full", script)
            commands = [command for command, _cwd in calls]
            self.assertFalse(any(command[0] == "netlistsvg" for command in commands))
            self.assertFalse(
                any(
                    command[0] in {"geeqie", "inkscape", "xdg-open"}
                    for command in commands
                )
            )

    def test_yosys_read_script_separates_spaced_include_option_and_value(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project with spaces"
            include_dir = root / "include files"
            include_dir.mkdir(parents=True)

            _artifact, script, _calls = self._synthesize_with_fake_tools(
                root,
                [
                    "synth",
                    "dut",
                    "--format",
                    "json",
                    "--no-schematic",
                    "--view",
                    "none",
                    "-I",
                    str(include_dir),
                ],
            )

            self.assertIn(f'"-I{include_dir}"', script)
            self.assertIn(f'"{root / "src" / "dut.v"}"', script)

    def test_yosys_read_script_quotes_string_valued_definition(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            _artifact, script, _calls = self._synthesize_with_fake_tools(
                root,
                [
                    "synth",
                    "dut",
                    "--format",
                    "json",
                    "--no-schematic",
                    "--view",
                    "none",
                    "-D",
                    'LABEL="hello world"',
                ],
            )

            self.assertIn(r'"-DLABEL=\"hello world\""', script)

    def test_yosys_render_script_runs_from_spaced_output_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project with spaces"

            artifact, _script, calls = self._synthesize_with_fake_tools(
                root,
                [
                    "synth",
                    "dut",
                    "--format",
                    "dot",
                    "--no-schematic",
                    "--view",
                    "none",
                ],
            )

            output_dir = root / ".vwb" / "synth" / "dut"
            render_script = output_dir / "render.ys"
            render_text = render_script.read_text(encoding="utf-8")
            yosys_calls = [
                (command, cwd) for command, cwd in calls if command[0] == "yosys"
            ]

            self.assertEqual(artifact, output_dir / "dut.dot")
            self.assertEqual(len(yosys_calls), 2)
            self.assertEqual(yosys_calls[0][1], root)
            self.assertEqual(
                yosys_calls[1], (["yosys", "-s", str(render_script)], output_dir)
            )
            self.assertRegex(render_text, r'read_json\s+"?dut\.json"?')
            self.assertIn("-prefix dut", render_text)
            self.assertNotIn(str(output_dir), render_text)


if __name__ == "__main__":
    unittest.main()
