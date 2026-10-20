import hashlib
import errno
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("acquire_toolchain", REPO / "scripts/acquire_toolchain.py")
acquire = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acquire)


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def require_symlink_permission(self):
        if os.name != "nt":
            return
        target, link = self.root / "symlink-probe-target", self.root / "symlink-probe-link"
        target.write_bytes(b"probe")
        try:
            link.symlink_to(target)
        except OSError as exc:
            if getattr(exc, "winerror", None) in (5, 1314) or exc.errno in (errno.EACCES, errno.EPERM):
                self.skipTest("Windows symlink-specific check requires Developer Mode or symlink privilege")
            raise
        finally:
            link.unlink(missing_ok=True)
            target.unlink(missing_ok=True)

    def archive(self, entries):
        path = self.root / "package.tgz"
        with tarfile.open(path, "w:gz") as output:
            for name, data, kind in entries:
                member = tarfile.TarInfo(name)
                member.mode = 0o4755
                if kind == "file":
                    member.size = len(data)
                    output.addfile(member, io.BytesIO(data))
                else:
                    member.type = {"symlink": tarfile.SYMTYPE, "hardlink": tarfile.LNKTYPE, "fifo": tarfile.FIFOTYPE}[kind]
                    member.linkname = data
                    output.addfile(member)
        return path

    def test_integrity_rejects_same_size_corrupt_bytes(self):
        archive = self.archive([("oss-cad-suite/bin/yosys", b"engine", "file")])
        asset = {"size_bytes": archive.stat().st_size, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
        acquire.verify_archive(archive, asset)
        data = bytearray(archive.read_bytes()); data[-1] ^= 1; archive.write_bytes(data)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            acquire.verify_archive(archive, asset)

    def test_internal_files_links_and_execute_permissions(self):
        archive = self.archive([("oss-cad-suite/bin/engine", b"abc", "file"), ("oss-cad-suite/bin/hard", "oss-cad-suite/bin/engine", "hardlink")])
        acquire.extract_archive(archive, self.root / "output")
        engine = self.root / "output/oss-cad-suite/bin/engine"
        self.assertEqual(engine.read_bytes(), b"abc")
        self.assertEqual((engine.parent / "hard").read_bytes(), b"abc")
        self.assertFalse(engine.stat().st_mode & 0o6000)

    def test_internal_symlink_preserves_internal_target(self):
        self.require_symlink_permission()
        archive = self.archive([("oss-cad-suite/bin/engine", b"abc", "file"), ("oss-cad-suite/bin/alias", "engine", "symlink")])
        acquire.extract_archive(archive, self.root / "output")
        alias = self.root / "output/oss-cad-suite/bin/alias"
        self.assertTrue(alias.is_symlink())
        self.assertEqual(alias.read_bytes(), b"abc")

    def test_traversal_devices_duplicate_and_link_parent_rejected(self):
        attempts = [
            [("oss-cad-suite/../../escape", b"bad", "file")],
            [("/oss-cad-suite/escape", b"bad", "file")],
            [("oss-cad-suite/device", "", "fifo")],
            [("oss-cad-suite/a", b"one", "file"), ("oss-cad-suite/a", b"two", "file")],
            [("oss-cad-suite/a", "bin", "symlink"), ("oss-cad-suite/a/engine", b"bad", "file")],
            [("oss-cad-suite/a", "../../escape", "symlink")],
        ]
        for entries in attempts:
            with self.subTest(entries=entries):
                archive = self.archive(entries)
                output = self.root / "output"
                with self.assertRaises(ValueError):
                    acquire.extract_archive(archive, output)
                self.assertFalse(output.exists())
        self.assertFalse((self.root / "escape").exists())

    def test_symlink_chain_dotdot_cannot_escape(self):
        self.require_symlink_permission()
        archive = self.archive([("oss-cad-suite/c/b", "..", "symlink"), ("oss-cad-suite/a", "c/b/../outside", "symlink")])
        with self.assertRaisesRegex(ValueError, "chain"):
            acquire.extract_archive(archive, self.root / "output")
        self.assertFalse((self.root / "output").exists())

    def test_archive_limits_are_enforced(self):
        archive = self.archive([("oss-cad-suite/a", b"abcd", "file"), ("oss-cad-suite/b", b"e", "file")])
        with self.assertRaisesRegex(ValueError, "size"):
            acquire.extract_archive(archive, self.root / "output", max_bytes=3)
        with self.assertRaisesRegex(ValueError, "count"):
            acquire.extract_archive(archive, self.root / "output", max_members=1)

    def test_native_environment_removes_pythonhome_and_preserves_parent(self):
        suite = self.root / "suite"
        environment = acquire.native_environment(suite, "linux-x64", {"PATH": "old", "PYTHONHOME": "wrong", "OTHER": "kept"})
        self.assertNotIn("PYTHONHOME", environment)
        self.assertEqual(environment["OTHER"], "kept")
        self.assertEqual(environment["PATH"].split(os.pathsep)[:2], [str(suite / "bin"), str(suite / "py3bin")])
        self.assertEqual(environment["GHDL_PREFIX"], str(suite / "lib/ghdl"))

    def test_install_never_overwrites_existing_version(self):
        lock = json.loads((REPO / "toolchains/native-lock.json").read_text())
        destination = self.root / f"oss-cad-suite-{lock['suite']['release']}-linux-x64"
        destination.mkdir()
        marker = destination / "user-file"; marker.write_text("preserve")
        with self.assertRaisesRegex(ValueError, "overwrite"):
            acquire.acquire(lock, "linux-x64", self.root)
        self.assertEqual(marker.read_text(), "preserve")

    def test_probe_requires_bundled_python_and_windows_environment_is_explicit(self):
        with self.assertRaisesRegex(ValueError, "bundled Python"):
            acquire.suite_python(self.root)
        (self.root / "lib").mkdir()
        (self.root / "lib/python3.exe").write_bytes(b"fake-header")
        self.assertEqual(acquire.suite_python(self.root), str(self.root / "lib/python3.exe"))
        environment = acquire.native_environment(self.root, "windows-arm64", {"PATH": ""})
        self.assertEqual(environment["PYTHON_EXECUTABLE"], str(self.root / "lib/python3.exe"))
        self.assertEqual(environment["OPENFPGALOADER_SOJ_DIR"], str(self.root / "share/openFPGALoader"))

    def test_json_output_does_not_suppress_actionable_acquisition_errors(self):
        archive, output = self.root / "invalid.tgz", self.root / "report.json"
        archive.write_bytes(b"wrong-size")
        stderr, stdout = io.StringIO(), io.StringIO()
        arguments = ["acquire_toolchain.py", "--target", "linux-x64", "--directory", str(self.root / "install"), "--archive", str(archive), "--output", str(output)]
        with mock.patch("sys.argv", arguments), contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
            result = acquire.main()
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(output.read_text())["state"], "failed")
        self.assertIn("size differs", stderr.getvalue())
        self.assertEqual(stdout.getvalue(), "")

    def test_explicit_tool_prefix_does_not_replace_bundled_probe_python(self):
        suite, external = self.root / "suite", self.root / "external"
        (suite / "py3bin").mkdir(parents=True); external.mkdir()
        (suite / "py3bin/python3").write_bytes(b"suite-python")
        (external / "python3").write_bytes(b"other-python")
        environment = acquire.native_environment(suite, "darwin-arm64", {"PATH": "old"}, tool_bins=[external])
        self.assertEqual(environment["PATH"].split(os.pathsep)[0], str(external.resolve()))
        self.assertEqual(acquire.suite_python(suite), str(suite / "py3bin/python3"))

    @unittest.skipIf(os.name == "nt", "Mac Yosys overlay executes POSIX upstream launchers")
    def test_explicit_overlay_survives_suite_launcher_path_reset(self):
        suite, external = self.root / "suite", self.root / "external-yosys"
        (suite / "bin").mkdir(parents=True)
        launcher = suite / "bin/yosys"
        original = b"#!/bin/sh\nprintf original"
        launcher.write_bytes(original); launcher.chmod(0o755)
        external.write_bytes(b"#!/bin/sh\nprintf external"); external.chmod(0o755)
        records = acquire.overlay_tools(suite, {"yosys": external})
        self.assertEqual((suite / ".vwb-overlays/originals/yosys").read_bytes(), original)
        self.assertEqual(records[0]["original_sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(records[0]["external_sha256"], hashlib.sha256(external.read_bytes()).hexdigest())
        self.assertTrue(records[0]["externally_managed"])
        self.assertFalse(records[0]["relocatable"])
        # The upstream Python wrapper prepends suite/bin again, defeating any
        # external PATH prefix. Exercise that reset and a real tool subprocess.
        wrapper = suite / "bin/python-wrapper"
        wrapper.write_text('#!/bin/sh\nPATH="' + str(suite / "bin") + ':$PATH"\nexport PATH\nexec yosys\n')
        wrapper.chmod(0o755)
        result = subprocess.run([str(wrapper)], env={**os.environ, "PATH": str(external.parent)}, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, "external")

    def test_overlay_failure_restores_original_launcher(self):
        suite, external = self.root / "suite", self.root / "external-yosys"
        (suite / "bin").mkdir(parents=True)
        launcher = suite / "bin/yosys"
        launcher.write_bytes(b"original")
        external.write_bytes(b"external"); external.chmod(0o755)
        with mock.patch.object(Path, "symlink_to", side_effect=OSError("symlink unavailable")):
            with self.assertRaisesRegex(OSError, "symlink unavailable"):
                acquire.overlay_tools(suite, {"yosys": external})
        self.assertEqual(launcher.read_bytes(), b"original")
        self.assertFalse((suite / ".vwb-overlays/originals/yosys").exists())

    def test_supplement_tar_root_directory_and_native_bytes_are_preserved(self):
        archive = self.root / 'mac.tgz'
        with tarfile.open(archive, 'w:gz') as output:
            member = tarfile.TarInfo('.')
            member.type = tarfile.DIRTYPE
            output.addfile(member)
            member = tarfile.TarInfo('./bin/ghdl')
            member.mode = 0o4755
            member.size = 6
            output.addfile(member, io.BytesIO(b'native'))
        destination = self.root / 'supplement'
        acquire.extract_supplement(archive, destination)
        binary = destination / 'bin/ghdl'
        self.assertEqual(binary.read_bytes(), b'native')
        self.assertFalse(binary.stat().st_mode & 0o6000)
        with self.assertRaisesRegex(ValueError, 'fresh'):
            acquire.extract_supplement(archive, destination)

    def test_supplement_tar_rejects_links_and_cleans_failed_install(self):
        for kind in ('symlink', 'hardlink', 'fifo'):
            with self.subTest(kind=kind):
                archive = self.archive([('bin/tool', b'bytes', 'file'), ('bin/link', 'tool', kind)])
                target = self.root / 'supplement'
                with self.assertRaisesRegex(ValueError, 'link or special'):
                    acquire.extract_supplement(archive, target)
                self.assertFalse(target.exists())

    def test_supplement_zip_rejects_traversal_links_and_duplicate_canonical_names(self):
        for entries in [[('../outside', b'bad')], [('a/./b', b'one'), ('a/b', b'two')], [('C:/outside', b'bad')]]:
            with self.subTest(entries=entries):
                archive = self.root / 'supplement.zip'
                with zipfile.ZipFile(archive, 'w') as package:
                    for name, value in entries:
                        package.writestr(name, value)
                with self.assertRaisesRegex(ValueError, 'unsafe'):
                    acquire.extract_supplement(archive, self.root / 'extract')
                self.assertFalse((self.root / 'extract').exists())
        with zipfile.ZipFile(archive, 'w') as package:
            member = zipfile.ZipInfo('alias')
            member.external_attr = 0o120777 << 16
            package.writestr(member, 'outside')
        with self.assertRaisesRegex(ValueError, 'link or special'):
            acquire.extract_supplement(archive, self.root / 'extract')

    def test_supplement_download_revalidates_cached_bytes_and_restricts_source(self):
        cache = self.root / 'cache'; cache.mkdir()
        cached = cache / 'pinned.zip'; cached.write_bytes(b'valid')
        asset = {'name': cached.name, 'url': 'https://github.com/ghdl/ghdl/releases/download/v6.0.0/pinned.zip',
                 'size_bytes': 5, 'sha256': hashlib.sha256(b'valid').hexdigest()}
        self.assertEqual(acquire.download_supplement(asset, cache), cached)
        cached.write_bytes(b'wrong')
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            acquire.download_supplement(asset, cache)
        with self.assertRaisesRegex(ValueError, 'locked upstream'):
            acquire.download_supplement({**asset, 'url': 'https://unrelated.example/pinned.zip'}, cache)

    def test_external_arm_ghdl_uses_only_its_alias_and_matching_prefix(self):
        self.require_symlink_permission()
        binary = self.root / 'os-bin/ghdl-llvm'; binary.parent.mkdir()
        binary.write_bytes(b'OS GHDL'); binary.chmod(0o755)
        prefix = self.root / 'os-lib/ghdl/llvm/vhdl'; prefix.mkdir(parents=True)
        environment = {'PATH': 'suite-first', 'GHDL_PREFIX': 'wrong-suite-libraries'}
        lock = {'supplemental_candidates': {}}
        result, records, interpreter = acquire.supplement_environment(lock, 'linux-arm64', self.root / 'supplements', environment,
            external_ghdl=binary, ghdl_prefix=prefix)
        self.assertEqual(result['GHDL_PREFIX'], str(prefix))
        alias = Path(result['PATH'].split(os.pathsep)[0]) / 'ghdl'
        self.assertEqual(alias.resolve(), binary.resolve())
        self.assertEqual(result['PATH'].split(os.pathsep)[1:], ['suite-first'])
        self.assertEqual(records[0]['executable_sha256'], hashlib.sha256(b'OS GHDL').hexdigest())
        self.assertFalse(records[0]['relocatable'])
        self.assertIsNone(interpreter)
        self.assertEqual(environment['GHDL_PREFIX'], 'wrong-suite-libraries')

    def test_windows_wheels_require_explicit_matching_msvc_cpython_before_install(self):
        source = self.root / 'python.exe'; source.write_bytes(b'python')
        for metadata in [{'implementation': 'CPython', 'version': [3, 11, 6], 'compiler': 'GCC'},
                         {'implementation': 'CPython', 'version': [3, 12, 10], 'compiler': 'GCC'},
                         {'implementation': 'PyPy', 'version': [3, 12, 10], 'compiler': 'MSC'}]:
            with mock.patch.object(acquire, 'binary_architecture', return_value={'architectures': ['x64']}), \
                 mock.patch.object(acquire, 'run_process', return_value=type('Result', (), {'returncode': 0, 'stdout': json.dumps(metadata)})()), \
                 mock.patch.object(acquire, 'download_supplement') as download:
                with self.assertRaisesRegex(ValueError, 'explicit external x64 MSVC CPython 3.12'):
                    acquire.prepare_windows_cocotb({'wheels': []}, source, self.root / 'runtime')
                download.assert_not_called()

    def test_windows_supplement_environment_selects_the_explicit_venv_and_dll_dirs(self):
        lock = {'supplemental_candidates': {'ghdl-windows-x64': {'prefix': 'lib/ghdl'},
                'graphviz-windows-x64': {}, 'cocotb-windows-cpython312': {}}}
        base = self.root / 'supplements'
        ghdl = base / 'ghdl/bin/ghdl.exe'; ghdl.parent.mkdir(parents=True)
        (base / 'ghdl/lib/ghdl').mkdir(parents=True)
        dot = base / 'graphviz/bin/dot.exe'
        python = base / 'venv/Scripts/python.exe'
        packages = [(ghdl, {'directory': str(base / 'ghdl')}), (dot, {'directory': str(base / 'graphviz')})]
        with mock.patch.object(acquire, 'acquire_supplement', side_effect=packages), \
             mock.patch.object(acquire, 'prepare_windows_cocotb', return_value=(python, {'relocatable': False})):
            environment, records, selected = acquire.supplement_environment(lock, 'windows-arm64', base,
                {'PATH': 'suite', 'PYTHON_EXECUTABLE': 'wrong-suite-python'}, windows_python=Path('explicit.exe'))
        self.assertEqual(selected, str(python))
        self.assertEqual(environment['PYTHON_EXECUTABLE'], str(python))
        self.assertEqual(environment['VIRTUAL_ENV'], str(base / 'venv'))
        self.assertEqual(environment['GHDL_PREFIX'], str(base / 'ghdl/lib/ghdl'))
        self.assertEqual(environment['PATH'].split(os.pathsep), [str(python.parent), str(ghdl.parent), str(base / 'ghdl/lib'), str(dot.parent), 'suite'])
        self.assertEqual(len(records), 3)

    def test_windows_cocotb_install_is_offline_local_and_records_arm_translation(self):
        source = self.root / 'external-python.exe'; source.write_bytes(b'CPython')
        directory = self.root / 'tools'; directory.mkdir()
        wheel = directory / 'locked.whl'; wheel.write_bytes(b'wheel')
        runtime = {'implementation': 'CPython', 'version': [3, 12, 10], 'compiler': 'MSC v.1943'}
        commands = []
        def execute(command, **options):
            commands.append(command)
            if command[1:3] == ['-m', 'venv']:
                (Path(command[3]) / 'Scripts').mkdir(parents=True)
            return type('Result', (), {'returncode': 0, 'stdout': json.dumps(runtime), 'stderr': ''})()
        with mock.patch.object(acquire, 'binary_architecture', return_value={'format': 'pe', 'architectures': ['x64']}), \
             mock.patch.object(acquire, 'host_platform', return_value={'architecture': 'arm64', 'system': 'windows'}), \
             mock.patch.object(acquire, 'run_process', side_effect=execute), \
             mock.patch.object(acquire, 'download_supplement', return_value=wheel):
            python, record = acquire.prepare_windows_cocotb({'wheels': [{'name': 'locked.whl'}]}, source, directory)
        self.assertEqual(python, directory / 'windows-cocotb-python/Scripts/python.exe')
        install = commands[-1]
        self.assertEqual(install[:4], [str(python), '-m', 'pip', 'install'])
        self.assertIn('--no-index', install)
        self.assertIn('--no-deps', install)
        self.assertEqual(install[-1], str(wheel))
        self.assertEqual(record['compatibility']['mechanism'], 'windows-11-x64-emulation')
        self.assertFalse(record['compatibility']['verified'])
        self.assertFalse(record['relocatable'])
        self.assertEqual(record['workflow_support'], 'not-tested')


if __name__ == "__main__":
    unittest.main()
