import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SETUP = ROOT / "setup.sh"
DOCKERFILE = ROOT / "Dockerfile"
DOCKER_RUNNER = ROOT / "run-docker.sh"
README = ROOT / "README.md"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


class SetupScriptTests(unittest.TestCase):
    def make_mock_docker(self, root: Path) -> Path:
        executable = root / "bin" / "docker"
        executable.parent.mkdir(parents=True)
        executable.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' \"$*\" >> \"${DOCKER_LOG}\"\n"
            "if [[ \"$1 $2\" == \"container inspect\" ]]; then\n"
            "  exit \"${MOCK_CONTAINER_EXISTS:-1}\"\n"
            "fi\n"
            "if [[ \"$1 $2\" == \"image inspect\" ]]; then\n"
            "  printf '%s\\n' \"${MOCK_IMAGE_ID:-sha256:new}\"\n"
            "  exit 0\n"
            "fi\n"
            "if [[ \"$1\" == \"inspect\" && \"$2\" == \"-f\" ]]; then\n"
            "  case \"$3\" in\n"
            "    *'.Image'*) printf '%s\\n' \"${MOCK_CONTAINER_IMAGE:-sha256:new}\" ;;\n"
            "    *'/home/docker/verilog-workbench'*) printf '%s\\n' \"${MOCK_WORKDIR:-}\" ;;\n"
            "    *'.State.Running'*) printf 'false\\n' ;;\n"
            "  esac\n"
            "fi\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)
        for command, status in (("git", 99), ("hostname", 98)):
            blocked = executable.parent / command
            blocked.write_text(
                "#!/usr/bin/env bash\n"
                f"echo 'run-docker.sh must not require {command}' >&2\n"
                f"exit {status}\n",
                encoding="utf-8",
            )
            blocked.chmod(0o755)
        return executable.parent

    def run_mocked_docker(
        self,
        checkout: Path,
        fake_bin: Path,
        log: Path,
        **settings: str,
    ) -> subprocess.CompletedProcess[str]:
        checkout.mkdir(parents=True)
        runner = checkout / "run-docker.sh"
        runner.write_text(DOCKER_RUNNER.read_text(encoding="utf-8"), encoding="utf-8")
        runner.chmod(0o755)
        environment = os.environ.copy()
        environment.pop("DISPLAY", None)
        environment.update(
            {
                "PATH": f"{fake_bin}:{environment['PATH']}",
                "DOCKER_LOG": str(log),
                **settings,
            }
        )
        return subprocess.run(
            [str(runner)],
            cwd=checkout,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

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

    def test_docker_runner_forwards_linux_usb_programmers(self):
        source = DOCKER_RUNNER.read_text(encoding="utf-8")
        self.assertIn('USB_BUS="/dev/bus/usb"', source)
        self.assertIn('USB_CGROUP_RULE="c 189:* rwm"', source)
        self.assertIn('--device-cgroup-rule "${USB_CGROUP_RULE}"', source)
        self.assertIn('DOCKER_ARGS+=(--group-add "${GROUP_ID}")', source)
        self.assertIn("Existing container is missing the current USB", source)
        self.assertNotIn("git hash-object", source)
        self.assertIn("sha256sum", source)
        self.assertIn("shasum -a 256", source)
        self.assertIn("cksum", source)
        self.assertNotIn("$(hostname)", source)

    def test_docker_runner_uses_different_names_for_two_checkouts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = self.make_mock_docker(root)
            run_lines: list[str] = []
            for name in ("checkout-a", "checkout-b"):
                checkout = root / name
                log = root / f"{name}.log"
                result = self.run_mocked_docker(
                    checkout,
                    fake_bin,
                    log,
                    MOCK_CONTAINER_EXISTS="1",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                run_line = next(
                    line
                    for line in log.read_text(encoding="utf-8").splitlines()
                    if line.startswith("run ")
                )
                self.assertIn(
                    f"{checkout}:/home/docker/verilog-workbench", run_line
                )
                run_lines.append(run_line)

            first = run_lines[0].split()
            second = run_lines[1].split()
            first_name = first[first.index("--name") + 1]
            second_name = second[second.index("--name") + 1]
            self.assertNotEqual(first_name, second_name)
            self.assertNotEqual(first[-1], second[-1])

    def test_docker_runner_recreates_container_for_changed_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = self.make_mock_docker(root)
            checkout = root / "checkout"
            log = root / "docker.log"
            result = self.run_mocked_docker(
                checkout,
                fake_bin,
                log,
                MOCK_CONTAINER_EXISTS="0",
                MOCK_IMAGE_ID="sha256:new",
                MOCK_CONTAINER_IMAGE="sha256:old",
                MOCK_WORKDIR=str(checkout),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Docker image changed", result.stdout)
            commands = log.read_text(encoding="utf-8").splitlines()
            self.assertTrue(any(line.startswith("rm -f ") for line in commands))
            self.assertTrue(any(line.startswith("run ") for line in commands))

    def test_docker_runner_recreates_container_with_wrong_checkout_mount(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = self.make_mock_docker(root)
            checkout = root / "checkout"
            log = root / "docker.log"
            result = self.run_mocked_docker(
                checkout,
                fake_bin,
                log,
                MOCK_CONTAINER_EXISTS="0",
                MOCK_IMAGE_ID="sha256:same",
                MOCK_CONTAINER_IMAGE="sha256:same",
                MOCK_WORKDIR=str(root / "different-checkout"),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Container checkout changed", result.stdout)
            commands = log.read_text(encoding="utf-8").splitlines()
            self.assertTrue(any(line.startswith("rm -f ") for line in commands))
            self.assertTrue(any(line.startswith("run ") for line in commands))

    def test_readme_recommends_local_install_without_a_glossary(self):
        readme = README.read_text(encoding="utf-8")
        self.assertIn("### Local Linux (Recommended)", readme)
        self.assertNotIn("Docker (Recommended)", readme)
        self.assertNotIn("## Useful Words", readme)
        self.assertNotIn("## Tests And Results", readme)
        self.assertLess(readme.index("### Local Linux"), readme.index("### Docker"))
        self.assertIn("/dev/bus/usb", readme)

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
