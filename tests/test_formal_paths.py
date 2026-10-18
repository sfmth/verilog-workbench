"""Real SBY regressions; missing native engines are explicitly skipped."""
import contextlib
import io
from pathlib import Path
import shutil
import tempfile
import unittest

import vwb


@unittest.skipUnless(all(shutil.which(tool) for tool in ("sby", "yosys", "z3")), "native SBY/Yosys/Z3 required")
class FormalPathIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="vwb-formal-path-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "project paths µ"
        (self.root / "src").mkdir(parents=True)
        (self.root / "test").mkdir()
        self.config_dir = self.root / "checks with spaces µ"
        self.config_dir.mkdir()
        fixture = Path(__file__).resolve().parents[1] / "toolchains" / "fixtures" / "formal"
        for source in fixture.iterdir():
            shutil.copy2(source, self.config_dir / source.name)
        self.workbench = vwb.Workbench(self.root, self.root / "src", self.root / "test", self.root / "build", color="never")

    def test_proof_resolves_relative_sources_from_actual_config_directory(self):
        output = self.workbench.run_formal(str(self.config_dir / "good.sby"), False)
        self.assertEqual((output / "status").read_text().split()[0], "PASS")
        self.assertEqual((output / "src" / "good.sv").read_bytes(), (self.config_dir / "good.sv").read_bytes())
        self.assertEqual(output, self.root / "build" / "formal" / "good")

    def test_false_assertion_is_real_fail_with_counterexample_from_spaced_project(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(vwb.VWBError):
                self.workbench.run_formal(str(self.config_dir / "bad.sby"), False)
        output = self.root / "build" / "formal" / "bad"
        self.assertEqual((output / "status").read_text().split()[0], "FAIL")
        self.assertTrue(any(path.stat().st_size > 0 for path in output.rglob("*.vcd")))

    def test_custom_spaced_build_preserves_proof_and_counterexample_artifacts(self):
        for build_name in ("build artifacts µ", "build(artifacts) µ", "build(artifacts)"):
            build = self.root / build_name
            workbench = vwb.Workbench(self.root, self.root / "src", self.root / "test", build, color="never")
            for name, expected in (("good", "PASS"), ("bad", "FAIL")):
                with self.subTest(build=build_name, result=expected):
                    config = self.config_dir / f"{name} proof µ.sby"
                    shutil.copy2(self.config_dir / f"{name}.sby", config)
                    output = build / "formal" / vwb.artifact_component(config.stem)
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        if expected == "PASS":
                            self.assertEqual(workbench.run_formal(str(config), False), output)
                        else:
                            with self.assertRaises(vwb.VWBError):
                                workbench.run_formal(str(config), False)
                    self.assertEqual((output / "status").read_text().split()[0], expected)
                    self.assertEqual((output / "src" / f"{name}.sv").read_bytes(), (self.config_dir / f"{name}.sv").read_bytes())
                    self.assertTrue((output / "vwb-formal-run.json").is_file())
                    self.assertFalse(list(self.config_dir.glob(".vwb-sby-*")))
                    self.assertFalse(list((build / "formal").glob(".vwb-formal-*")))
                    if expected == "FAIL":
                        self.assertTrue(any(path.stat().st_size > 0 for path in output.rglob("*.vcd")))


if __name__ == "__main__":
    unittest.main()
