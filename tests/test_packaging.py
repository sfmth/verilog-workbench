"""Verify bundling boundaries and extraction safety without network access."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("native_bundler", ROOT / "scripts" / "build_native_bundle.py")
BUNDLE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUNDLE)


class NativePackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def tar(self, members):
        archive = self.root / "runtime.tar.gz"
        with tarfile.open(archive, "w:gz") as target:
            for name, kind, value in members:
                member = tarfile.TarInfo(name)
                if kind == "file":
                    member.size = len(value)
                    target.addfile(member, io.BytesIO(value))
                else:
                    member.type = {"symlink": tarfile.SYMTYPE, "hardlink": tarfile.LNKTYPE,
                                   "device": tarfile.CHRTYPE}[kind]
                    member.linkname = value
                    target.addfile(member)
        return archive

    def runtime(self, windows=False):
        runtime = self.root / "runtime with spaces"
        executable = runtime / ("python.exe" if windows else "bin/python3")
        executable.parent.mkdir(parents=True)
        executable.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        executable.chmod(0o755)
        (runtime / "LICENSE.txt").write_text("Test runtime; not redistributable production CPython")
        return runtime

    def require_symlinks(self):
        target, link = self.root / "symlink-target", self.root / "symlink-probe"
        target.write_text("")
        try:
            link.symlink_to(target)
        except OSError:
            if os.name == "nt":
                self.skipTest("Windows runner does not grant symlink creation privilege")
            raise
        link.unlink()
        target.unlink()

    def test_six_targets_have_exact_pins_including_windows_arm(self):
        manifest = BUNDLE.load_manifest()
        self.assertEqual(set(manifest["targets"]), {"linux-x86_64", "linux-arm64", "windows-x86_64",
            "windows-arm64", "macos-x86_64", "macos-arm64"})
        self.assertEqual(len({r["sha256"] for r in manifest["targets"].values()}), 6)
        self.assertEqual(manifest["targets"]["windows-arm64"]["triple"], "aarch64-pc-windows-msvc")

    def test_archive_digest_must_match_even_when_size_matches(self):
        path = self.root / "bad.tar.gz"
        path.write_bytes(b"bad")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            BUNDLE.verify_archive(path, {"size": 3, "sha256": "0" * 64})

    def test_corrupt_cached_download_is_not_used(self):
        runtime = {"url": "https://github.com/owner/repo/asset.tar.gz", "size": 3, "sha256": "0" * 64}
        (self.root / "asset.tar.gz").write_bytes(b"bad")
        with patch.object(BUNDLE.urllib.request, "urlopen") as network:
            with self.assertRaises(ValueError):
                BUNDLE.download_runtime(runtime, self.root)
            network.assert_not_called()

    def test_extract_rejects_traversal_and_absolute_paths(self):
        for index, name in enumerate(("python/../../outside", "/python/absolute", "other/file")):
            archive = self.tar([(name, "file", b"bad")])
            with self.assertRaises(ValueError):
                BUNDLE.extract_runtime(archive, self.root / f"extract{index}")
        self.assertFalse((self.root.parent / "outside").exists())

    def test_extract_rejects_escaping_links_and_devices(self):
        for index, member in enumerate((("python/bin/python3", "symlink", "../../../outside"),
            ("python/bin/python3", "hardlink", "../../outside"), ("python/device", "device", ""))):
            archive = self.tar([member])
            with self.assertRaises((ValueError, tarfile.FilterError)):
                BUNDLE.extract_runtime(archive, self.root / f"extract{index}")

    def test_extract_preserves_internal_runtime_symlinks(self):
        self.require_symlinks()
        archive = self.tar([("python/bin/python3.12", "file", b"runtime"),
                            ("python/bin/python3", "symlink", "python3.12")])
        runtime = BUNDLE.extract_runtime(archive, self.root / "extract")
        self.assertTrue((runtime / "bin/python3").is_symlink())
        self.assertEqual((runtime / "bin/python3").read_bytes(), b"runtime")

    def test_extract_limits_expanded_bytes(self):
        archive = self.tar([("python/test", "file", b"data")])
        with patch.object(BUNDLE, "MAX_RUNTIME_BYTES", 3):
            with self.assertRaisesRegex(ValueError, "extraction limits"):
                BUNDLE.extract_runtime(archive, self.root / "extract")

    def test_custom_runtime_rejects_external_symlink(self):
        self.require_symlinks()
        runtime = self.runtime()
        (runtime / "external").symlink_to(self.root / "outside")
        with self.assertRaisesRegex(ValueError, "symlink leaves"):
            BUNDLE.validate_runtime(runtime, "bin/python3")

    def test_bundle_copies_only_application_and_provenance(self):
        bundle, archive = BUNDLE.build_bundle("linux-x86_64", self.root / "output", self.runtime(), archive=True)
        self.assertTrue(archive.is_file())
        self.assertTrue((bundle / "app/vwb.py").is_file())
        self.assertTrue((bundle / "app/companion/__main__.py").is_file())
        self.assertFalse((bundle / "app/tests").exists())
        self.assertFalse((bundle / "app/.git").exists())
        self.assertFalse(any(bundle.rglob("*.pyc")))
        metadata = json.loads((bundle / "bundle.json").read_text())
        self.assertFalse(metadata["runtime"]["archive_verified"])
        self.assertFalse(metadata["toolchains_included"])
        with tarfile.open(archive) as tar:
            self.assertTrue(all(n.startswith(bundle.name + "/") or n == bundle.name for n in tar.getnames()))

    @unittest.skipIf(os.name == "nt", "POSIX launcher behavior")
    def test_posix_launcher_handles_spaces_and_preserves_arguments(self):
        bundle, _ = BUNDLE.build_bundle("linux-x86_64", self.root / "output with spaces", self.runtime())
        result = subprocess.run([str(bundle / "vwb-companion"), "--project", "project with spaces"],
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.splitlines(), ["-I", str(bundle / "app/_launch.py"),
            "companion", "--project", "project with spaces"])

    def test_windows_launcher_uses_bundled_python(self):
        bundle, archive = BUNDLE.build_bundle("windows-arm64", self.root / "output", self.runtime(True), archive=True)
        self.assertEqual(archive.suffix, ".zip")
        self.assertIn(b'"%~dp0runtime\\python.exe" -I', (bundle / "vwb-companion.cmd").read_bytes())
        self.assertFalse((bundle / "vwb-companion").exists())

    def test_build_refuses_to_overwrite_existing_bundle(self):
        runtime = self.runtime()
        BUNDLE.build_bundle("linux-x86_64", self.root / "output", runtime)
        with self.assertRaisesRegex(ValueError, "already exists"):
            BUNDLE.build_bundle("linux-x86_64", self.root / "output", runtime)

    def test_application_symlinks_cannot_include_external_files(self):
        self.require_symlinks()
        repo = self.root / "repo"
        (repo / "companion").mkdir(parents=True)
        (repo / "companion/__main__.py").write_text("")
        (repo / "vwb.py").symlink_to(ROOT / "vwb.py")
        with self.assertRaisesRegex(ValueError, "symlink"):
            BUNDLE.copy_application(repo, self.root / "app")

    def test_application_does_not_sweep_nested_tests_or_hidden_files(self):
        repo = self.root / "repo"
        (repo / "companion/tests").mkdir(parents=True)
        (repo / "packaging").mkdir()
        (repo / "vwb.py").write_text("")
        (repo / "companion/__main__.py").write_text("")
        (repo / "companion/tests/local_credentials.py").write_text("fixture = 'private'")
        (repo / "companion/.private.py").write_text("secret = 'private'")
        (repo / "packaging/launch.py").write_text("")
        destination = self.root / "app"
        BUNDLE.copy_application(repo, destination)
        self.assertEqual({p.relative_to(destination).as_posix() for p in destination.rglob("*.py")},
                         {"vwb.py", "companion/__main__.py", "_launch.py"})


if __name__ == "__main__":
    unittest.main()
