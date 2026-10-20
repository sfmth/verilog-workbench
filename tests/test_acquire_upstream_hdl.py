"""Safety and reproducibility checks for downloaded third-party HDL sources."""
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/acquire_upstream_hdl.py'
SPEC = importlib.util.spec_from_file_location('acquire_upstream_hdl', SCRIPT)
acquire_hdl = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acquire_hdl)


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name).resolve()
        self.revision = 'a' * 40
        self.archive_root = 'example-' + self.revision
        self.directory = self.root / 'corpus'
        self.cache = self.directory / 'archives'
        self.cache.mkdir(parents=True)
        self.archive = self.cache / ('example-' + self.revision + '.tar.gz')
        self.lock = self.root / 'lock.json'
        self.make_archive()

    def make_archive(self, extras=()):
        with tarfile.open(self.archive, 'w:gz') as archive:
            for name in (self.archive_root, self.archive_root + '/rtl'):
                entry = tarfile.TarInfo(name)
                entry.type = tarfile.DIRTYPE
                archive.addfile(entry)
            for name, body in [('LICENSE', b'Upstream license\n'), ('rtl/core.v', b'module core; endmodule\n')]:
                entry = tarfile.TarInfo(self.archive_root + '/' + name)
                entry.size = len(body)
                archive.addfile(entry, io.BytesIO(body))
            for entry in extras:
                archive.addfile(entry)
        record = {'schema_version': 1, 'projects': [{
            'id': 'example', 'repository': 'https://github.com/upstream/example', 'revision': self.revision,
            'archive': {'url': 'https://codeload.github.com/upstream/example/tar.gz/' + self.revision,
                        'size': self.archive.stat().st_size, 'sha256': acquire_hdl.digest(self.archive), 'root': self.archive_root},
            'license': {'spdx': 'ISC', 'paths': ['LICENSE']}, 'source_roots': ['rtl']}]}
        self.lock.write_text(json.dumps(record))

    def acquire(self, **kwargs):
        return acquire_hdl.acquire(self.directory, self.lock, offline=True, **kwargs)

    def test_acquire_preserves_sources_notices_and_provenance_and_reuses_offline(self):
        with patch.object(acquire_hdl.urllib.request, 'urlopen', side_effect=AssertionError('network unexpectedly used')):
            report = self.acquire()
            again = self.acquire(projects=['example'])
        project = report['projects']['example']
        self.assertEqual(report, again)
        self.assertEqual(project['revision'], self.revision)
        self.assertEqual((Path(project['root']) / 'LICENSE').read_bytes(), b'Upstream license\n')
        self.assertEqual((Path(project['root']) / 'rtl/core.v').read_bytes(), b'module core; endmodule\n')
        self.assertEqual(project['file_count'], 2)
        self.assertEqual(project['tree_sha256'], again['projects']['example']['tree_sha256'])

    def test_installed_file_modification_is_rejected_without_trusting_receipt(self):
        project = self.acquire()['projects']['example']
        root = Path(project['root'])
        (root / 'rtl/core.v').write_text('module malicious; endmodule\n')
        with self.assertRaisesRegex(ValueError, 'file differs'):
            self.acquire()

    def test_extra_installed_member_is_rejected(self):
        root = Path(self.acquire()['projects']['example']['root'])
        (root / 'extra.v').write_text('module extra; endmodule\n')
        with self.assertRaisesRegex(ValueError, 'Unexpected installed source member'):
            self.acquire()

    def test_incomplete_installed_tree_is_rejected(self):
        root = Path(self.acquire()['projects']['example']['root'])
        (root / 'rtl/core.v').unlink()
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self.acquire()

    def test_cached_archive_tamper_is_rejected(self):
        self.archive.write_bytes(self.archive.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'integrity mismatch'):
            self.acquire()

    def test_missing_archive_offline_fails_without_network(self):
        self.archive.unlink()
        with patch.object(acquire_hdl.urllib.request, 'urlopen', side_effect=AssertionError('network unexpectedly used')):
            with self.assertRaisesRegex(ValueError, 'not cached'):
                self.acquire()

    def test_download_verifies_exact_archive_before_publishing_sources(self):
        content = self.archive.read_bytes()
        self.archive.unlink()
        with patch.object(acquire_hdl.urllib.request, 'urlopen', return_value=io.BytesIO(content)):
            report = acquire_hdl.acquire(self.directory, self.lock)
        self.assertEqual(self.archive.read_bytes(), content)
        self.assertTrue((Path(report['projects']['example']['root']) / 'rtl/core.v').is_file())

    def test_wrong_hash_or_oversized_download_leaves_no_cached_or_installed_source(self):
        original = self.archive.read_bytes()
        self.archive.unlink()
        for content in [b'x' * len(original), original + b'oversized']:
            with self.subTest(size=len(content)):
                with patch.object(acquire_hdl.urllib.request, 'urlopen', return_value=io.BytesIO(content)):
                    with self.assertRaisesRegex(ValueError, 'integrity mismatch|exceeds'):
                        acquire_hdl.acquire(self.directory, self.lock)
                self.assertFalse(self.archive.exists())
                self.assertEqual(list((self.directory / 'projects').iterdir()), [])
                self.assertEqual(list(self.cache.iterdir()), [])

    def test_download_closes_writer_before_replace_or_failure_cleanup(self):
        original_content = self.archive.read_bytes()
        original_temporary = acquire_hdl.tempfile.NamedTemporaryFile
        original_replace = acquire_hdl.os.replace
        original_unlink = Path.unlink
        for content in [original_content, original_content + b'oversized']:
            self.archive.unlink(missing_ok=True)
            opened = []
            def temporary(*args, **kwargs):
                stream = original_temporary(*args, **kwargs)
                opened.append(stream)
                return stream
            def replace(source, destination):
                self.assertTrue(opened[-1].closed, 'Temporary writer must close before atomic replacement')
                return original_replace(source, destination)
            def unlink(path, *args, **kwargs):
                if opened and str(path) == opened[-1].name:
                    self.assertTrue(opened[-1].closed, 'Temporary writer must close before cleanup')
                return original_unlink(path, *args, **kwargs)
            with self.subTest(size=len(content)), \
                    patch.object(acquire_hdl.tempfile, 'NamedTemporaryFile', side_effect=temporary), \
                    patch.object(acquire_hdl.os, 'replace', side_effect=replace), \
                    patch.object(Path, 'unlink', unlink), \
                    patch.object(acquire_hdl.urllib.request, 'urlopen', return_value=io.BytesIO(content)):
                project = acquire_hdl.load_lock(self.lock)['projects'][0]
                if content == original_content:
                    acquire_hdl._archive(project, self.cache, offline=False)
                else:
                    with self.assertRaisesRegex(ValueError, 'exceeds'):
                        acquire_hdl._archive(project, self.cache, offline=False)

    def test_unknown_project_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unknown'):
            self.acquire(projects=['not-pinned'])

    def test_lock_requires_immutable_revision_url_hash_and_notice(self):
        original = json.loads(self.lock.read_text())
        for field, value in [('revision', 'main'), ('repository', 'http://github.com/upstream/example')]:
            record = json.loads(json.dumps(original))
            record['projects'][0][field] = value
            self.lock.write_text(json.dumps(record))
            with self.assertRaises(ValueError):
                acquire_hdl.load_lock(self.lock)
        for field, value in [('url', 'https://example.com/source.tar.gz'), ('sha256', 'f' * 63), ('size', 0)]:
            record = json.loads(json.dumps(original))
            record['projects'][0]['archive'][field] = value
            self.lock.write_text(json.dumps(record))
            with self.assertRaises(ValueError):
                acquire_hdl.load_lock(self.lock)
        record = json.loads(json.dumps(original))
        record['projects'][0]['license']['paths'] = []
        self.lock.write_text(json.dumps(record))
        with self.assertRaises(ValueError):
            acquire_hdl.load_lock(self.lock)

    def test_archive_traversal_duplicate_special_and_hard_link_rejected(self):
        for name, kind in [('../escape', tarfile.REGTYPE), (self.archive_root + '/rtl/core.v', tarfile.REGTYPE),
                           (self.archive_root + '/device', tarfile.CHRTYPE), (self.archive_root + '/hard', tarfile.LNKTYPE)]:
            with self.subTest(name=name, kind=kind):
                entry = tarfile.TarInfo(name)
                entry.type = kind
                entry.linkname = 'rtl/core.v'
                self.make_archive([entry])
                with self.assertRaises(ValueError):
                    self.acquire()
        self.assertFalse((self.directory / 'escape').exists())

    @unittest.skipIf(os.name == 'nt', 'Creating upstream symlinks requires Windows developer mode')
    def test_safe_internal_file_and_directory_symlinks_preserved(self):
        file_link = tarfile.TarInfo(self.archive_root + '/alias.v')
        file_link.type = tarfile.SYMTYPE
        file_link.linkname = 'rtl/core.v'
        directory_link = tarfile.TarInfo(self.archive_root + '/rtl_alias')
        directory_link.type = tarfile.SYMTYPE
        directory_link.linkname = 'rtl'
        self.make_archive([file_link, directory_link])
        project = self.acquire()['projects']['example']
        root = Path(project['root'])
        self.assertTrue((root / 'alias.v').is_symlink())
        self.assertEqual((root / 'rtl_alias/core.v').read_bytes(), (root / 'rtl/core.v').read_bytes())
        self.assertEqual(project['symbolic_links'], {'alias.v': 'rtl/core.v', 'rtl_alias': 'rtl'})
        self.assertEqual(self.acquire()['projects']['example'], project)

    def test_escaping_missing_and_cyclic_symlinks_rejected(self):
        for target in ['../outside', '/tmp/outside', 'missing', 'alias.v']:
            with self.subTest(target=target):
                entry = tarfile.TarInfo(self.archive_root + '/alias.v')
                entry.type = tarfile.SYMTYPE
                entry.linkname = target
                self.make_archive([entry])
                with self.assertRaises(ValueError):
                    self.acquire()

    def test_archive_member_through_symlink_rejected(self):
        link = tarfile.TarInfo(self.archive_root + '/linked')
        link.type = tarfile.SYMTYPE
        link.linkname = 'rtl'
        child = tarfile.TarInfo(self.archive_root + '/linked/injected.v')
        self.make_archive([link, child])
        with self.assertRaisesRegex(ValueError, 'parent'):
            self.acquire()

    @unittest.skipIf(os.name == 'nt', 'Executable mode is a POSIX source property')
    def test_installed_executable_mode_tamper_is_rejected(self):
        root = Path(self.acquire()['projects']['example']['root'])
        (root / 'rtl/core.v').chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'file differs'):
            self.acquire()

    def test_committed_lock_has_ten_official_pinned_projects(self):
        lock = acquire_hdl.load_lock()
        projects = {project['id'] for project in lock['projects']}
        self.assertTrue({'picorv32', 'ibex', 'cv32e40p', 'serv', 'nvdla',
                         'verilog-axis', 'verilog-axi', 'verilog-uart', 'verilog-i2c'}.issubset(projects))
        self.assertGreaterEqual(len(projects), 10)


if __name__ == '__main__':
    unittest.main()
