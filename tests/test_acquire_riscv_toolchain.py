"""Compiler acquisition must reject changed installs and unsafe archives."""
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('acquire_riscv_toolchain', SCRIPTS / 'acquire_riscv_toolchain.py')
    acquire_riscv = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(acquire_riscv)
finally:
    sys.path.remove(str(SCRIPTS))


class CompilerAcquisitionTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        self.directory = self.root / 'tools'
        self.cache = self.directory / 'archives'
        self.cache.mkdir(parents=True)
        self.archive = self.cache / 'xpack-riscv-none-elf-gcc-14.2.0-3.tar.gz'
        self.lock = self.root / 'lock.json'
        self.record = acquire_riscv.load_lock()
        self.make_archive()

    def make_archive(self, unsafe=False):
        root = self.record['archive']['root']
        with tarfile.open(self.archive, 'w:gz') as archive:
            for name in (root, root + '/bin'):
                member = tarfile.TarInfo(name)
                member.type = tarfile.DIRTYPE
                archive.addfile(member)
            for name in ('LICENSE', 'bin/riscv-none-elf-gcc', 'bin/riscv-none-elf-objcopy', 'bin/riscv-none-elf-objdump'):
                data = b'fixture compiler or license\n'
                member = tarfile.TarInfo(root + '/' + name)
                member.size = len(data)
                member.mode = 0o755 if name.startswith('bin/') else 0o644
                archive.addfile(member, io.BytesIO(data))
            if unsafe:
                member = tarfile.TarInfo(root + '/bin/escape')
                member.type = tarfile.SYMTYPE
                member.linkname = '../../../outside'
                archive.addfile(member)
        self.record['archive']['size'] = self.archive.stat().st_size
        self.record['archive']['sha256'] = acquire_riscv.archive_tools.digest(self.archive)
        self.record['license_paths'] = ['LICENSE']
        self.lock.write_text(json.dumps(self.record), encoding='utf-8')

    def acquire(self):
        with patch.object(acquire_riscv.platform, 'system', return_value='Linux'), \
                patch.object(acquire_riscv.platform, 'machine', return_value='x86_64'):
            return acquire_riscv.acquire(self.directory, self.lock, offline=True)

    def test_offline_reuse_checks_all_installed_files(self):
        first = self.acquire()
        self.assertEqual(self.acquire(), first)
        compiler = Path(first['bin_directory']) / 'riscv-none-elf-gcc'
        compiler.write_bytes(b'changed compiler')
        with self.assertRaisesRegex(ValueError, 'Installed source file differs'):
            self.acquire()

    def test_safe_extraction_rejects_an_escaping_link(self):
        self.make_archive(unsafe=True)
        with self.assertRaisesRegex(ValueError, 'symbolic link escapes'):
            self.acquire()
        self.assertFalse((self.root / 'outside').exists())

    def test_cache_corruption_and_unofficial_url_are_not_trusted(self):
        self.archive.write_bytes(b'corrupt archive')
        with self.assertRaisesRegex(ValueError, 'integrity mismatch'):
            self.acquire()
        self.record['archive']['url'] = 'https://example.com/compiler.tar.gz'
        self.lock.write_text(json.dumps(self.record), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'official release URL'):
            self.acquire()

    def test_unsupported_host_is_explicit(self):
        with patch.object(acquire_riscv.platform, 'system', return_value='Darwin'):
            with self.assertRaisesRegex(ValueError, 'Linux x64 only'):
                acquire_riscv.acquire(self.directory, self.lock, offline=True)


if __name__ == '__main__':
    unittest.main()
