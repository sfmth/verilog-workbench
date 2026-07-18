import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SETUP = ROOT / "setup.sh"


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

    def test_full_install_requests_optional_tools(self):
        result = self.run_dry("fedora", "--full")
        report = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, report)
        self.assertIn("full profile", report)
        self.assertIn("ghdl", report)
        self.assertIn("yosys", report)
        self.assertIn("gtkwave", report)
        self.assertIn("npm install --global netlistsvg", report)

    def test_installer_has_no_pinned_download_fallbacks(self):
        source = SETUP.read_text(encoding="utf-8")
        self.assertNotIn("github.com/", source)
        self.assertNotIn("cocotb==", source)
        self.assertIn('"cocotb>=1.9,<3"', source)
        self.assertIn("paru", source)
        self.assertIn("yay", source)


if __name__ == "__main__":
    unittest.main()
