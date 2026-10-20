"""Pinned developer editor archive boundaries, never semantic-server evidence."""
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile

from scripts.acquire_editor_tools import extract


class EditorArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def tar(self, entries):
        archive = self.root / 'tool.tgz'
        with tarfile.open(archive, 'w:gz') as package:
            for entry, content in entries:
                entry.size = len(content)
                package.addfile(entry, io.BytesIO(content))
        return archive

    def test_dot_root_directory_and_executable_bytes_are_preserved(self):
        directory = tarfile.TarInfo('.'); directory.type = tarfile.DIRTYPE
        executable = tarfile.TarInfo('./tool/server'); executable.mode = 0o755
        archive = self.tar([(directory, b''), (executable, b'original bytes')])
        extract(archive, self.root / 'out')
        self.assertEqual((self.root / 'out/tool/server').read_bytes(), b'original bytes')

    def test_traversal_and_canonical_duplicate_entries_are_rejected(self):
        for names in [('outside/../../escape',), ('./server', 'server')]:
            with self.subTest(names=names):
                archive = self.tar([(tarfile.TarInfo(name), b'x') for name in names])
                with tempfile.TemporaryDirectory(dir=self.root) as directory, self.assertRaises(ValueError):
                    extract(archive, Path(directory) / 'out')
        self.assertFalse((self.root / 'escape').exists())

    def test_tar_links_and_devices_are_rejected(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE):
            entry = tarfile.TarInfo('server'); entry.type = kind; entry.linkname = '../escape'
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir=self.root) as directory:
                with self.assertRaises(ValueError):
                    extract(self.tar([(entry, b'')]), Path(directory) / 'out')

    def test_zip_link_and_existing_destinations_are_rejected(self):
        archive = self.root / 'tool.zip'
        with zipfile.ZipFile(archive, 'w') as package:
            entry = zipfile.ZipInfo('server'); entry.external_attr = 0o120777 << 16
            package.writestr(entry, '../escape')
        with self.assertRaises(ValueError):
            extract(archive, self.root / 'out')
        with self.assertRaises(FileExistsError):
            extract(archive, self.root / 'out')
