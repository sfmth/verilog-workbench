"""Pinned verification dependencies stay isolated, verified, and reproducible."""
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import acquire_upstream_test_tools as acquire


class VerificationToolAcquisitionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="vwb dependency checks ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache = self.root / "cache"
        self.cache.mkdir()
        self.content = b"pinned package bytes"
        self.record = {
            "filename": "package-1.0-py3-none-any.whl",
            "url": "https://files.pythonhosted.org/packages/pinned/package.whl",
            "size": len(self.content),
            "sha256": hashlib.sha256(self.content).hexdigest(),
        }
        self.archive = self.cache / self.record["filename"]
        self.definition = {"packages": ["runtime"], "build_packages": ["builder"]}
        self.packages = {
            "runtime": {"version": "1.0", "artifact": self.record},
            "builder": {"version": "2.0", "artifact": self.record},
        }
        self.environment = {"PATH": os.environ.get("PATH", os.defpath)}
        self.lock_hash = "a" * 64

    def install(self, definition=None):
        return acquire.install_profile("bench", definition or self.definition, self.packages,
            self.root, self.cache, sys.executable, self.environment, self.lock_hash, True)

    def fake_pip(self, argv, **kwargs):
        site = Path(argv[argv.index("--target") + 1])
        site.mkdir(exist_ok=True)
        (site / "runtime.py").write_text("VERSION = 'verified'\n")
        (site / "bin").mkdir(exist_ok=True)
        (site / "bin/config").write_text("selected profile console script")

    def test_offline_cache_requires_exact_size_and_hash(self):
        self.archive.write_bytes(self.content)
        with patch.object(acquire.urllib.request, "urlopen") as network:
            self.assertEqual(acquire.download(self.record, self.cache, True), self.archive)
            network.assert_not_called()
            self.archive.write_bytes(b"x" * len(self.content))
            with self.assertRaisesRegex(ValueError, "integrity"):
                acquire.download(self.record, self.cache, True)
            self.archive.unlink()
            with self.assertRaisesRegex(ValueError, "not cached"):
                acquire.download(self.record, self.cache, True)

    def test_online_download_checks_content_before_publishing_cache_entry(self):
        with patch.object(acquire.urllib.request, "urlopen", return_value=io.BytesIO(self.content)):
            self.assertEqual(acquire.download(self.record, self.cache), self.archive)
        self.assertEqual(self.archive.read_bytes(), self.content)
        self.archive.unlink()
        for body in (self.content + b"too long", b"x" * len(self.content), b"short"):
            with self.subTest(body=body), patch.object(acquire.urllib.request, "urlopen", return_value=io.BytesIO(body)):
                with self.assertRaisesRegex(ValueError, "integrity|exceeds"):
                    acquire.download(self.record, self.cache)
                self.assertEqual(list(self.cache.iterdir()), [])

    def test_untrusted_unbounded_or_traversing_records_are_rejected_before_network(self):
        changes = [
            {"url": "http://files.pythonhosted.org/package"},
            {"url": "https://example.com/package"},
            {"size": True},
            {"size": 64 * 1024 * 1024 + 1},
            {"sha256": "not-a-digest"},
            {"filename": "../outside.whl"},
            {"filename": "folder\\outside.whl"},
        ]
        for change in changes:
            with self.subTest(change=change), patch.object(acquire.urllib.request, "urlopen") as network:
                with self.assertRaises(ValueError):
                    acquire.download({**self.record, **change}, self.cache)
                network.assert_not_called()

    def test_install_uses_only_verified_local_artifacts_without_dependency_resolution(self):
        with patch.object(acquire, "download", return_value=self.archive), patch.object(acquire.subprocess, "run", side_effect=self.fake_pip) as run:
            receipt = self.install()
        self.assertEqual(run.call_count, 2)
        for call in run.call_args_list:
            argv = call.args[0]
            for flag in ("--no-index", "--no-deps", "--no-build-isolation", "--upgrade"):
                self.assertIn(flag, argv)
            self.assertEqual(argv[:4], [sys.executable, "-m", "pip", "install"])
            self.assertEqual(call.kwargs["env"]["PYTHON_BIN"], sys.executable)
            self.assertEqual(call.kwargs["env"]["PYGPI_PYTHON_BIN"], sys.executable)
        self.assertEqual(receipt["packages"], {"builder": "2.0", "runtime": "1.0"})
        self.assertEqual(receipt["environment"]["PYTHONPATH"], str(Path(receipt["root"]) / "site"))

    def test_reuse_reverifies_profile_and_ignores_only_generated_python_bytecode(self):
        with patch.object(acquire, "download", return_value=self.archive), patch.object(acquire.subprocess, "run", side_effect=self.fake_pip) as run:
            receipt = self.install()
            site = Path(receipt["root"]) / "site"
            bytecode = site / "__pycache__"
            bytecode.mkdir()
            (bytecode / "runtime.cpython-311.pyc").write_bytes(b"generated bytecode")
            self.assertEqual(self.install(), receipt)
            self.assertEqual(run.call_count, 2)
            (site / "runtime.py").write_text("changed source")
            with self.assertRaisesRegex(ValueError, "profile changed"):
                self.install()

    def test_failed_package_install_removes_incomplete_profile(self):
        with patch.object(acquire, "download", return_value=self.archive), patch.object(acquire.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "pip")):
            with self.assertRaises(subprocess.CalledProcessError): self.install()
        self.assertFalse((self.root / "profiles/bench-aaaaaaaaaaaaaaaa").exists())

    def test_native_plugin_must_be_built_and_stay_inside_profile(self):
        definition = deepcopy(self.definition)
        definition["native_builds"] = [{"cwd": "site", "argv": ["make"], "tools": [], "outputs": ["missing.vpi"]}]
        def fake(argv, **kwargs):
            if "--target" in argv: self.fake_pip(argv, **kwargs)
        with patch.object(acquire, "download", return_value=self.archive), patch.object(acquire.subprocess, "run", side_effect=fake):
            with self.assertRaisesRegex(ValueError, "plugin was not built"):
                self.install(definition)
        self.assertFalse((self.root / "profiles/bench-aaaaaaaaaaaaaaaa").exists())

    def test_profile_names_cannot_escape_install_directory(self):
        with self.assertRaisesRegex(ValueError, "profile name"):
            acquire.install_profile("../outside", self.definition, self.packages, self.root,
                self.cache, sys.executable, self.environment, self.lock_hash, True)

    def test_python_aliases_use_selected_interpreter_with_spaces_in_its_path(self):
        if os.name == "nt": self.skipTest("The Linux-only profile installer writes POSIX launcher scripts")
        selected = self.root / "selected Python"
        selected.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n')
        selected.chmod(0o755)
        directory = acquire.python_shims(self.root / "shim bin", str(selected))
        for alias in ("python", "python3"):
            output = subprocess.check_output([str(directory / alias), "-c", "import sys;print(sys.version_info[:2])"], text=True)
            self.assertEqual(output.strip(), str(sys.version_info[:2]))
        self.assertEqual(acquire.python_shims(directory, str(selected)), directory)
        (directory / "python").write_text("changed launcher")
        with self.assertRaisesRegex(ValueError, "shim changed"):
            acquire.python_shims(directory, str(selected))

    def test_profile_reuse_rejects_unrecorded_symbolic_links(self):
        with patch.object(acquire, "download", return_value=self.archive), patch.object(acquire.subprocess, "run", side_effect=self.fake_pip):
            receipt = self.install()
            link = Path(receipt["root"]) / "unexpected-link"
            try: link.symlink_to(self.root)
            except OSError as error: self.skipTest("Symlink creation unavailable: " + str(error))
            with self.assertRaisesRegex(ValueError, "symbolic links"):
                self.install()

    def test_main_reports_shared_python_aliases_for_non_python_profiles(self):
        acquisition = self.root / "toolchain.json"
        acquisition.write_text(json.dumps({"environment": self.environment, "probe": {"python": sys.executable}}))
        output = self.root / "report.json"
        directory = self.root / "tools"
        receipt = {"files": {}, "root": "test profile", "environment": {}}
        argv = ["acquire_upstream_test_tools.py", "--directory", str(directory), "--toolchain-acquisition", str(acquisition), "--profile", "fusesoc", "--output", str(output)]
        with patch.object(sys, "argv", argv), patch.object(acquire.platform, "system", return_value="Linux"), patch.object(acquire.platform, "machine", return_value="x86_64"), patch.object(acquire.subprocess, "check_output", return_value="3.11\n"), patch.object(acquire, "install_profile", return_value=receipt) as install, patch.object(acquire, "install_perl", return_value={"PERL5LIB": "/pinned/perl"}):
            acquire.main()
        environment = json.loads(output.read_text())["native_environment"]
        self.assertEqual(environment["PATH"].split(os.pathsep)[0], str(directory / "python-bin"))
        self.assertEqual(environment["PYTHON_BIN"], sys.executable)
        self.assertEqual(environment["PERL5LIB"], "/pinned/perl")
        self.assertEqual(install.call_args.args[6]["PATH"], environment["PATH"])


if __name__ == "__main__":
    unittest.main()
