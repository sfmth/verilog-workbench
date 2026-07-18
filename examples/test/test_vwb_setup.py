import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SETUP = ROOT / "setup.sh"
DOCKERFILE = ROOT / "Dockerfile"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


class SetupScriptTests(unittest.TestCase):
    def run_dry(self, distro: str, *options: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "VWB_SETUP_DISTRO": distro,
                "VWB_SETUP_VERSION": "test-release",
            }
        )
        return subprocess.run(
            [str(SETUP), "--dry-run", "--no-aur", *options],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_core_install_uses_each_supported_system_package_manager(self):
        cases = {
            "ubuntu": "apt-get install",
            "debian": "apt-get install",
            "fedora": "dnf install",
            "arch": "pacman -Syu",
        }
        for distro, install_command in cases.items():
            with self.subTest(distro=distro):
                result = self.run_dry(distro)
                report = result.stdout + result.stderr
                self.assertEqual(result.returncode, 0, report)
                self.assertIn("core profile", report)
                self.assertIn(install_command, report)
                self.assertIn("iverilog", report)
                self.assertIn("cocotb", report)
                self.assertIn("Dry run complete", report)
                if distro == "arch":
                    self.assertIn("pacman -Sy --noconfirm", report)

    def test_full_install_requests_optional_tools(self):
        result = self.run_dry("fedora", "--full")
        report = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, report)
        self.assertIn("full profile", report)
        self.assertIn("ghdl", report)
        self.assertIn("yosys", report)
        self.assertIn("gtkwave", report)
        self.assertIn("npm install --global netlistsvg", report)
        self.assertIn("bitstring", report)
        self.assertIn("numpy", report)
        self.assertIn("pillow", report)

    def test_installer_has_no_pinned_download_fallbacks(self):
        source = SETUP.read_text(encoding="utf-8")
        self.assertNotIn("github.com/", source)
        self.assertNotIn("cocotb==", source)
        self.assertIn('"cocotb>=1.9,<3"', source)
        self.assertIn("paru", source)
        self.assertIn("yay", source)
        self.assertIn("COCOTB_IGNORE_PYTHON_REQUIRES=1", source)
        self.assertIn('select_package "C++ compiler for Cocotb"', source)
        self.assertIn(
            'select_package "static C++ runtime for Cocotb" required libstdc++-static',
            source,
        )

    def test_docker_uses_stable_python_base_and_development_library(self):
        source = DOCKERFILE.read_text(encoding="utf-8")
        self.assertIn("FROM ubuntu:24.04", source)
        self.assertIn("python3-dev", source)
        self.assertNotIn("libgvplugin-neato-layout8", source)

    def test_fedora_skips_broken_optional_packages(self):
        source = SETUP.read_text(encoding="utf-8")
        self.assertIn("--setopt=install_weak_deps=False --skip-broken", source)

    def test_ci_runs_supported_distros_in_parallel_and_focuses_on_png(self):
        ci = CI_WORKFLOW.read_text(encoding="utf-8")
        release = RELEASE_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("fail-fast: false", ci)
        for image in (
            "ubuntu:24.04",
            "debian:stable-slim",
            "fedora:latest",
            "archlinux:latest",
        ):
            self.assertIn(f"image: {image}", ci)
        self.assertIn("./setup.sh --full --no-aur", ci)
        self.assertIn("--representative-modules", ci)
        self.assertIn("--portable-tools", ci)
        self.assertIn("--phase doctor", ci)
        self.assertNotIn("test encoder --seed 1", ci)
        self.assertNotIn("--portable-tools", release)
        for workflow in (ci, release):
            self.assertIn("--synth-format png", workflow)
            self.assertNotIn("--all-wave-formats", workflow)
            self.assertNotIn("--synth-option-matrix", workflow)


if __name__ == "__main__":
    unittest.main()
