"""Integrity controls for the original NVDLA generator's scoped Perl libraries."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location('acquire_upstream_native', SCRIPTS / 'acquire_upstream_native.py')
native = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native)
sys.path.pop(0)


class NativeDependencyIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.cache = self.root / 'cache'
        self.cache.mkdir()
        self.lock = self.root / 'lock.json'
        self.records = []
        for name, url in native.ALLOWED.items():
            root = 'XML-Simple-2.25' if name == 'perl-xml-simple' else 'YAML-1.31'
            archive = self.cache / (root + '.tar.gz')
            with tarfile.open(archive, 'w:gz') as package:
                for directory in [root, root + '/lib']:
                    info = tarfile.TarInfo(directory)
                    info.type = tarfile.DIRTYPE
                    package.addfile(info)
                data = b'package Dependency; 1;\n'
                info = tarfile.TarInfo(root + '/lib/Dependency.pm')
                info.size = len(data)
                package.addfile(info, io.BytesIO(data))
            self.records.append({'id': name, 'url': url, 'root': root,
                                 'library_subdirectory': 'lib',
                                 'size': archive.stat().st_size,
                                 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()})
        self.write_lock()

    def write_lock(self):
        self.lock.write_text(json.dumps({'schema_version': 1, 'artifacts': self.records}))

    def acquire(self):
        return native.acquire(self.cache, self.lock, offline=True)

    def test_offline_acquisition_produces_explicit_perl_environment(self):
        result = self.acquire()
        self.assertEqual(set(result['artifacts']), set(native.ALLOWED))
        self.assertEqual(len(result['environment']['PERL5LIB'].split(os.pathsep)), 2)
        self.assertEqual(self.acquire(), result)

    def test_modified_extracted_library_is_rejected_on_reuse(self):
        result = self.acquire()
        library = Path(next(iter(result['artifacts'].values()))['path']) / 'lib/Dependency.pm'
        library.write_text('tampered')
        with self.assertRaises(ValueError):
            self.acquire()

    def test_archive_tampering_and_missing_offline_archive_fail(self):
        record = self.records[0]
        archive = self.cache / (record['root'] + '.tar.gz')
        archive.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'integrity'):
            self.acquire()
        archive.unlink()
        with self.assertRaisesRegex(ValueError, 'Missing offline'):
            self.acquire()

    def test_duplicate_untrusted_or_unbounded_lock_is_rejected(self):
        for field, value in [('url', 'https://example.com/package.tar.gz'),
                             ('root', '../outside'), ('size', 1024 * 1024 + 1)]:
            original = self.records[0][field]
            self.records[0][field] = value
            self.write_lock()
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.acquire()
            self.records[0][field] = original
        self.records[1] = dict(self.records[0])
        self.write_lock()
        with self.assertRaises(ValueError):
            self.acquire()


if __name__ == '__main__':
    unittest.main()
