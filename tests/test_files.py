"""Real filesystem checks for exclusive lifecycle operations and durable trash."""
from pathlib import Path
import json
import os
import stat
import tempfile
import unittest
from unittest.mock import patch

from companion import files as file_module
from companion.files import FileOperations, PRIVATE, _rename_exclusive
from companion.projects import ProjectError, ProjectGrant, revision


class FileOperationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        # Canonicalize /var on macOS and short aliases on Windows like the grant.
        self.root = Path(self.directory.name).resolve(strict=True)
        (self.root / 'src').mkdir()
        (self.root / 'test').mkdir()
        self.project = ProjectGrant(self.root)
        self.files = FileOperations(self.project)
        (self.root / 'src/a.sv').write_bytes(b'\xef\xbb\xbfmodule a;\r\nendmodule\r\n')

    def tearDown(self):
        self.directory.cleanup()

    def error(self, status, operation, *args):
        with self.assertRaises(ProjectError) as caught:
            operation(*args)
        self.assertEqual(caught.exception.status, status, str(caught.exception))
        return caught.exception

    def payload(self, path='src/a.sv', **extra):
        return {'path': path, 'revision': self.files.entry(path)['revision'], **extra}

    def trash(self, path='src/a.sv'):
        return self.files.trash(self.payload(path))

    def folder(self, identifier):
        return self.root / PRIVATE / identifier

    def test_create_file_and_empty_directory_exact_utf8(self):
        value = self.files.create({'path': 'src/μ.sv', 'kind': 'file', 'content': '\ufeffé\r\n'})
        self.assertEqual((self.root / value['path']).read_bytes(), '\ufeffé\r\n'.encode())
        self.assertEqual(value['revision'], self.project.read(value['path'])['revision'])
        self.assertEqual(value['bytes'], 7)
        folder = self.files.create({'path': 'src/empty', 'kind': 'directory'})
        self.assertEqual(folder['kind'], 'directory')
        self.assertEqual(folder['file_count'], 0)
        self.assertEqual(folder['bytes'], 0)
        self.assertTrue((self.root / 'src/empty').is_dir())

    def test_create_requires_parent_and_never_overwrites(self):
        original = (self.root / 'src/a.sv').read_bytes()
        self.error(409, self.files.create, {'path': 'src/a.sv', 'kind': 'file', 'content': 'replacement'})
        self.error(409, self.files.create, {'path': 'src', 'kind': 'directory'})
        self.error(404, self.files.create, {'path': 'absent/a.sv', 'kind': 'file'})
        self.assertEqual((self.root / 'src/a.sv').read_bytes(), original)

    def test_missing_configured_source_roots_and_ancestors_can_be_created(self):
        grant = ProjectGrant(self.root, src_dir='rtl/source', test_dir='verification')
        files = FileOperations(grant)
        files.create({'path': 'rtl', 'kind': 'directory'})
        files.create({'path': 'rtl/source', 'kind': 'directory'})
        files.create({'path': 'verification', 'kind': 'directory'})
        self.assertTrue((self.root / 'rtl/source').is_dir())
        self.error(403, files.trash, {'path': 'rtl', 'revision': files.entry('rtl')['revision']})

    def test_file_and_directory_moves_preserve_bytes_modes_and_empty_dirs(self):
        folder = self.root / 'src/group'
        folder.mkdir()
        (folder / 'empty').mkdir()
        (folder / 'b.sv').write_bytes(b'\x00\xff\r\n')
        (folder / 'b.sv').chmod(0o640)
        mode = stat.S_IMODE((folder / 'b.sv').stat().st_mode)
        before = self.files.entry('src/group')
        moved = self.files.move(self.payload('src/group', destination='test/moved'))
        self.assertEqual(moved['previous_path'], 'src/group')
        self.assertEqual(moved['revision'], before['revision'])
        self.assertEqual(moved['file_count'], 1)
        self.assertEqual((self.root / 'test/moved/b.sv').read_bytes(), b'\x00\xff\r\n')
        self.assertEqual(stat.S_IMODE((self.root / 'test/moved/b.sv').stat().st_mode), mode)
        self.assertTrue((self.root / 'test/moved/empty').is_dir())
        file_move = self.files.move(self.payload(destination='src/b.sv'))
        self.assertEqual(file_move['revision'], self.project.read('src/b.sv')['revision'])

    def test_directory_revision_covers_names_bytes_and_empty_directories(self):
        initial = self.files.entry('src')['revision']
        (self.root / 'src/empty').mkdir()
        empty = self.files.entry('src')['revision']
        self.assertNotEqual(initial, empty)
        (self.root / 'src/empty').rename(self.root / 'src/other')
        renamed = self.files.entry('src')['revision']
        self.assertNotEqual(empty, renamed)
        (self.root / 'src/a.sv').write_bytes(b'new bytes')
        self.assertNotEqual(renamed, self.files.entry('src')['revision'])

    def test_stale_revision_refuses_move_and_trash(self):
        expected = self.payload(destination='src/b.sv')
        (self.root / 'src/a.sv').write_bytes(b'outside edit')
        self.error(409, self.files.move, expected)
        expected.pop('destination')
        self.error(409, self.files.trash, expected)
        self.assertEqual((self.root / 'src/a.sv').read_bytes(), b'outside edit')
        self.assertFalse((self.root / PRIVATE).exists())

    def test_open_document_heads_are_checked_even_with_fresh_entry_revision(self):
        old = self.project.read('src/a.sv')['revision']
        (self.root / 'src/a.sv').write_bytes(b'external')
        documents = [{'path': 'src/a.sv', 'revision': old}]
        self.error(409, self.files.move, self.payload(destination='src/b.sv', documents=documents))
        self.error(409, self.files.trash, self.payload(documents=documents))
        current = [{'path': 'src/a.sv', 'revision': revision(b'external')}]
        self.files.move(self.payload(destination='src/b.sv', documents=current))
        self.assertTrue((self.root / 'src/b.sv').is_file())

    def test_document_heads_must_be_unique_bounded_affected_files(self):
        head = {'path': 'src/a.sv', 'revision': self.project.read('src/a.sv')['revision']}
        for documents in ([head, head], [dict(head, path='test/unrelated')], [head] * 65,
                          [{'path': 'src/a.sv', 'revision': 'bad'}], 'bad'):
            self.error(400, self.files.trash, self.payload(documents=documents))

    def test_move_rejects_existing_empty_destination_and_self_descendants(self):
        (self.root / 'src/group').mkdir()
        (self.root / 'test/existing').mkdir()
        self.error(409, self.files.move, self.payload('src/group', destination='test/existing'))
        self.error(400, self.files.move, self.payload('src/group', destination='src/group/child'))
        self.error(409, self.files.move, self.payload(destination='src/a.sv'))
        self.error(404, self.files.move, self.payload(destination='absent/a.sv'))

    def test_config_build_source_and_test_roots_are_protected(self):
        (self.root / '.vwb.json').write_text('{}')
        (self.root / '.vwb').mkdir()
        (self.root / '.vwb/result').write_text('result')
        for path in ('src', 'test', '.vwb.json', '.vwb', '.vwb/result'):
            self.error(403, self.files.trash, self.payload(path))
            self.error(403, self.files.move, self.payload(path, destination='moved'))
        self.error(403, self.files.create, {'path': '.vwb/new', 'kind': 'file'})
        self.error(403, self.files.create, {'path': '.vwb.json', 'kind': 'file'})
        self.error(400, self.files.entry, '.')

    def test_nested_build_ancestor_protected_and_root_source_allows_children(self):
        (self.root / 'generated').mkdir()
        files = FileOperations(ProjectGrant(self.root, src_dir='.', build_dir='generated/build'))
        self.error(403, files.trash, {'path': 'generated', 'revision': files.entry('generated')['revision']})
        files.create({'path': 'standalone.sv', 'kind': 'file'})
        self.assertTrue((self.root / 'standalone.sv').exists())

    def test_portable_case_collisions_refused_on_all_hosts(self):
        self.error(409, self.files.create, {'path': 'src/A.sv', 'kind': 'file'})
        self.error(409, self.files.move, self.payload(destination='src/A.sv'))
        self.error(409, self.files.entry, 'SRC/a.sv')
        # Creating case-colliding existing entries only works on case-sensitive hosts.
        second = self.root / 'src/A.sv'
        if not second.exists():
            second.write_bytes(b'second')
            self.error(409, self.files.entry, 'src')

    def test_composed_unicode_names_are_accessible_and_normalization_collisions_refused(self):
        item = self.files.create({'path': 'src/café.sv', 'kind': 'file', 'content': 'accent'})
        self.assertEqual(self.files.entry('src/café.sv')['revision'], item['revision'])
        self.error(409, self.files.create, {'path': 'src/cafe\u0301.sv', 'kind': 'file'})

    def test_private_git_unsafe_and_nonportable_paths_rejected(self):
        for path in ('../escape', '/absolute', '.git/config', 'src/../escape',
                     '.vwb-trash/owner.json', '.VWB-TRASH/anything'):
            self.error(403, self.files.create, {'path': path, 'kind': 'file'})
        for path in ('src/CON.txt', 'src/trailing.', 'src/a\\b', 'src/a:b'):
            self.error(400, self.files.create, {'path': path, 'kind': 'file'})

    def test_file_creation_limits_and_payload_validation(self):
        with patch.object(file_module, 'MAX_FILE_BYTES', 3):
            self.error(413, self.files.create, {'path': 'src/new', 'kind': 'file', 'content': 'éé'})
        for value in ({'path': 'src/new', 'kind': 'other'},
                      {'path': 'src/new', 'kind': 'directory', 'content': ''},
                      {'path': 'src/new', 'kind': 'file', 'content': '\ud800'},
                      {'path': 'src/new', 'kind': 'file', 'extra': True}):
            self.error(400, self.files.create, value)
        self.error(400, self.files.restore, {'id': '../escape'})

    def test_snapshot_subtree_entry_and_byte_limits(self):
        with patch.object(file_module, 'MAX_FILES', 1):
            self.error(413, self.files.entry, 'src')
        with patch.object(file_module, 'MAX_SNAPSHOT_BYTES', 2):
            self.error(413, self.files.entry, 'src/a.sv')

    def test_long_original_descendants_are_rejected_before_private_prefix_relocation(self):
        folder = self.root / 'src/group'
        folder.mkdir()
        current = folder
        try:
            for index in range(10):
                current = current / (str(index) + 'a' * 96)
                current.mkdir()
            target = current / 'file'
            target.write_bytes(b'keep exact bytes')
        except OSError:
            self.skipTest('host does not support this long-path fixture')
        relative = target.relative_to(self.root).as_posix()
        self.assertLessEqual(len(relative), 1024)
        before = self.files.entry('src/group')
        self.error(400, self.files.trash, {'path': 'src/group', 'revision': before['revision']})
        self.assertEqual(target.read_bytes(), b'keep exact bytes')
        self.assertFalse((self.root / PRIVATE).exists())
        self.error(400, self.files.move, {'path': 'src/group', 'destination': 'src/' + 'b' * 100,
                                         'revision': before['revision']})
        self.assertEqual(target.read_bytes(), b'keep exact bytes')
        self.assertFalse((self.root / 'src' / ('b' * 100)).exists())

    def test_trash_cumulative_subtree_entry_limit_includes_empty_directories(self):
        for name in ('one', 'two'):
            folder = self.root / 'src' / name
            folder.mkdir()
            (folder / 'empty').mkdir()
        with patch.object(file_module, 'MAX_FILES', 3):
            self.trash('src/one')
            self.error(413, self.files.trash, self.payload('src/two'))
        self.assertTrue((self.root / 'src/two/empty').is_dir())

    @unittest.skipUnless(hasattr(os, 'pathconf'), 'POSIX native path limits only')
    def test_native_path_byte_expansion_rejected_before_rename(self):
        source = self.root / 'src/group'
        source.mkdir()
        (source / ('é' * 20)).write_bytes(b'unchanged')
        before = self.files.entry('src/group')
        future = self.root / PRIVATE / ('0' * 32) / 'payload' / ('é' * 20)
        bound = len(os.fsencode(future)) - 1
        with patch.object(file_module.os, 'pathconf', return_value=bound):
            self.error(400, self.files.trash, {'path': 'src/group', 'revision': before['revision']})
        self.assertEqual((source / ('é' * 20)).read_bytes(), b'unchanged')
        self.assertFalse((self.root / PRIVATE).exists())

    def test_hardlinks_rejected_anywhere_in_subtree(self):
        try:
            os.link(self.root / 'src/a.sv', self.root / 'test/link')
        except OSError:
            self.skipTest('hardlinks unavailable')
        for path in ('src/a.sv', 'src'):
            self.error(403, self.files.entry, path)
            self.error(403, self.files.trash, {'path': path, 'revision': '0' * 64})

    def test_symlinks_rejected_anywhere_in_subtree_and_destination_parents(self):
        try:
            (self.root / 'src/link').symlink_to(self.root / 'test', target_is_directory=True)
        except OSError:
            self.skipTest('symlinks unavailable')
        self.error(403, self.files.entry, 'src')
        self.error(403, self.files.create, {'path': 'src/link/new', 'kind': 'file'})

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX named pipes only')
    def test_special_files_are_rejected_without_reading(self):
        os.mkfifo(self.root / 'src/pipe')
        self.error(403, self.files.entry, 'src')

    def test_trash_restore_survives_restart_with_binary_modes_and_empty_dirs(self):
        (self.root / 'src/group').mkdir()
        (self.root / 'src/group/empty').mkdir()
        target = self.root / 'src/group/blob'
        target.write_bytes(b'\x00\xff\r\n')
        target.chmod(0o640)
        mode = stat.S_IMODE(target.stat().st_mode)
        before = self.files.entry('src/group')
        trashed = self.trash('src/group')
        self.assertEqual(trashed['state'], 'trashed')
        self.assertFalse((self.root / 'src/group').exists())
        restarted = FileOperations(ProjectGrant(self.root))
        self.assertEqual(restarted.trash_list()['entries'][0]['revision'], before['revision'])
        restored = restarted.restore({'id': trashed['id']})
        self.assertEqual(restored, before)
        self.assertEqual(target.read_bytes(), b'\x00\xff\r\n')
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), mode)
        self.assertTrue((self.root / 'src/group/empty').is_dir())
        self.assertEqual(restarted.trash_list(), {'entries': []})

    def test_restore_refuses_existing_original_or_missing_parent_and_keeps_payload(self):
        item = self.trash()
        original = self.root / 'src/a.sv'
        original.write_bytes(b'new occupant')
        self.error(409, self.files.restore, {'id': item['id']})
        self.assertEqual(original.read_bytes(), b'new occupant')
        self.assertTrue((self.folder(item['id']) / 'payload').exists())
        original.unlink()
        (self.root / 'src').rmdir()
        self.error(404, self.files.restore, {'id': item['id']})
        (self.root / 'src').mkdir()
        self.files.restore({'id': item['id']})

    def test_modified_payload_or_manifest_is_visible_and_never_restored(self):
        item = self.trash()
        payload = self.folder(item['id']) / 'payload'
        payload.write_bytes(b'corrupted')
        self.assertEqual(self.files.trash_list()['entries'][0]['state'], 'invalid')
        self.error(409, self.files.restore, {'id': item['id']})
        self.assertEqual(payload.read_bytes(), b'corrupted')
        manifest_path = self.folder(item['id']) / 'manifest.json'
        wrapper = json.loads(manifest_path.read_bytes())
        wrapper['manifest']['entry']['path'] = 'src/other'
        manifest_path.write_text(json.dumps(wrapper))
        self.assertEqual(self.files.trash_list()['entries'][0]['state'], 'invalid')
        self.error(409, self.files.restore, {'id': item['id']})

    def test_unknown_store_is_never_adopted_or_deleted(self):
        store = self.root / PRIVATE
        store.mkdir()
        (store / 'user-data').write_bytes(b'keep me')
        self.error(409, self.files.trash, self.payload())
        self.assertEqual((store / 'user-data').read_bytes(), b'keep me')
        self.assertTrue((self.root / 'src/a.sv').exists())

    @unittest.skipIf(os.name == 'nt', 'POSIX permissions only')
    def test_trash_store_and_metadata_are_private(self):
        store = self.root / PRIVATE
        store.mkdir(mode=0o755)
        item = self.trash()
        folder = self.folder(item['id'])
        for path in (store, folder, store / 'owner.json', folder / 'manifest.json'):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o077, 0)
        store.chmod(0o755)
        self.error(409, self.files.trash_list)

    def test_wrong_owner_marker_rejected_and_ambiguous_entry_visible(self):
        item = self.trash()
        unknown = self.root / PRIVATE / ('f' * 32)
        unknown.mkdir()
        (unknown / 'unknown-payload').write_bytes(b'never remove unverified data')
        self.assertEqual(len(self.files.trash_list()['entries']), 2)
        self.assertTrue(any(entry['state'] == 'invalid' for entry in self.files.trash_list()['entries']))
        (self.root / 'src/new').write_bytes(b'new')
        self.error(409, self.files.trash, self.payload('src/new'))
        marker = self.root / PRIVATE / 'owner.json'
        value = json.loads(marker.read_bytes())
        value['project'] = '0' * 64
        marker.write_text(json.dumps(value))
        self.error(409, self.files.trash_list)
        self.assertTrue((self.folder(item['id']) / 'payload').exists())

    def test_retention_count_bytes_and_entries_are_bounded_without_purge(self):
        with patch.object(file_module, 'MAX_TRASH', 1):
            item = self.trash()
            (self.root / 'src/second').write_bytes(b'2')
            self.error(413, self.files.trash, self.payload('src/second'))
            self.assertTrue((self.folder(item['id']) / 'payload').exists())
        before = (self.folder(item['id']) / 'payload').stat().st_size
        with patch.object(file_module, 'MAX_SNAPSHOT_BYTES', before):
            self.error(413, self.files.trash, self.payload('src/second'))
        with patch.object(file_module, 'MAX_FILES', 1):
            # Store has two children (owner and entry); bounded inspection rejects.
            self.error(413, self.files.trash, self.payload('src/second'))
        self.assertTrue((self.root / 'src/second').exists())

    def test_exclusive_native_rename_refuses_late_file_and_empty_directory(self):
        for kind in ('file', 'directory'):
            source = self.root / ('from-' + kind)
            destination = self.root / ('to-' + kind)
            if kind == 'file':
                source.write_bytes(b'source')
                destination.write_bytes(b'occupant')
            else:
                source.mkdir()
                destination.mkdir()
            with self.assertRaises(FileExistsError):
                _rename_exclusive(source, destination)
            self.assertTrue(source.exists())
            self.assertTrue(destination.exists())
            if kind == 'file':
                self.assertEqual(destination.read_bytes(), b'occupant')

    def test_move_late_destination_race_never_clobbers(self):
        actual = file_module._rename_exclusive
        def arrive(source, destination):
            destination.write_bytes(b'arrived after checks')
            actual(source, destination)
        with patch.object(file_module, '_rename_exclusive', side_effect=arrive):
            self.error(409, self.files.move, self.payload(destination='src/b.sv'))
        self.assertEqual((self.root / 'src/b.sv').read_bytes(), b'arrived after checks')
        self.assertTrue((self.root / 'src/a.sv').exists())

    def test_create_late_destination_race_never_clobbers_or_leaves_temp(self):
        actual = file_module._rename_exclusive
        def arrive(source, destination):
            destination.write_bytes(b'arrived')
            actual(source, destination)
        with patch.object(file_module, '_rename_exclusive', side_effect=arrive):
            self.error(409, self.files.create, {'path': 'src/new', 'kind': 'file', 'content': 'ours'})
        self.assertEqual((self.root / 'src/new').read_bytes(), b'arrived')
        self.assertEqual(list((self.root / 'src').glob('.vwb-create-*')), [])

    def test_restore_late_destination_race_never_clobbers_and_keeps_payload(self):
        item = self.trash()
        actual = file_module._rename_exclusive
        def arrive(source, destination):
            destination.write_bytes(b'arrived')
            actual(source, destination)
        with patch.object(file_module, '_rename_exclusive', side_effect=arrive):
            self.error(409, self.files.restore, {'id': item['id']})
        self.assertEqual((self.root / 'src/a.sv').read_bytes(), b'arrived')
        self.assertTrue((self.folder(item['id']) / 'payload').exists())
        self.assertEqual(self.files.trash_list()['entries'][0]['state'], 'interrupted')

    def test_trash_crash_after_rename_retains_verified_payload_across_restart(self):
        actual = self.files._save_manifest
        def fail(folder, manifest):
            if manifest['state'] == 'trashed':
                raise OSError('power failure boundary')
            return actual(folder, manifest)
        with patch.object(self.files, '_save_manifest', side_effect=fail):
            self.error(400, self.files.trash, self.payload())
        restarted = FileOperations(ProjectGrant(self.root))
        item = restarted.trash_list()['entries'][0]
        self.assertEqual(item['state'], 'interrupted')
        self.assertTrue(item['restorable'])
        self.assertFalse((self.root / 'src/a.sv').exists())
        restarted.restore({'id': item['id']})
        self.assertTrue((self.root / 'src/a.sv').exists())

    def test_pre_rename_interruption_retains_original_and_explicit_acknowledgement(self):
        with patch.object(file_module, '_rename_exclusive', side_effect=OSError('interrupted')):
            self.error(400, self.files.trash, self.payload())
        item = self.files.trash_list()['entries'][0]
        self.assertEqual(item['state'], 'interrupted')
        self.assertTrue(item['restorable'])
        self.assertTrue((self.root / 'src/a.sv').exists())
        self.files.restore({'id': item['id']})
        self.assertEqual(self.files.trash_list(), {'entries': []})

    def test_restore_crash_after_rename_can_be_acknowledged_without_deleting_original(self):
        item = self.trash()
        actual = self.files._save_manifest
        def fail(folder, manifest):
            if manifest['state'] == 'restored':
                raise OSError('interrupted after rename')
            return actual(folder, manifest)
        with patch.object(self.files, '_save_manifest', side_effect=fail):
            self.error(400, self.files.restore, {'id': item['id']})
        original = (self.root / 'src/a.sv').read_bytes()
        restarted = FileOperations(ProjectGrant(self.root))
        record = restarted.trash_list()['entries'][0]
        self.assertEqual(record['state'], 'interrupted')
        self.assertTrue(record['restorable'])
        restarted.restore({'id': item['id']})
        self.assertEqual((self.root / 'src/a.sv').read_bytes(), original)
        self.assertEqual(restarted.trash_list(), {'entries': []})

    def test_changed_original_is_not_acknowledged_after_interrupted_restore(self):
        item = self.trash()
        actual = self.files._save_manifest
        def fail(folder, manifest):
            if manifest['state'] == 'restored':
                raise OSError('interrupted')
            return actual(folder, manifest)
        with patch.object(self.files, '_save_manifest', side_effect=fail):
            self.error(400, self.files.restore, {'id': item['id']})
        (self.root / 'src/a.sv').write_bytes(b'external changed original')
        self.assertFalse(self.files.trash_list()['entries'][0]['restorable'])
        self.error(409, self.files.restore, {'id': item['id']})
        self.assertEqual((self.root / 'src/a.sv').read_bytes(), b'external changed original')

    def test_empty_record_after_cleanup_interruption_is_removed_without_deleting_payloads(self):
        item = self.trash()
        folder = self.folder(item['id'])
        actual = file_module._sync_directory
        def fail(path):
            if Path(path) == folder and not (folder / 'manifest.json').exists():
                raise OSError('interrupted before empty folder removal')
            return actual(path)
        with patch.object(file_module, '_sync_directory', side_effect=fail):
            self.error(400, self.files.restore, {'id': item['id']})
        self.assertTrue((self.root / 'src/a.sv').exists())
        self.assertTrue(folder.is_dir())
        self.assertEqual(list(folder.iterdir()), [])
        self.assertEqual(self.files.trash_list()['entries'][0]['state'], 'invalid')
        (self.root / 'src/new').write_bytes(b'new data')
        second = self.trash('src/new')
        self.assertFalse(folder.exists())
        self.assertEqual([entry['id'] for entry in self.files.trash_list()['entries']], [second['id']])
        self.assertEqual((self.folder(second['id']) / 'payload').read_bytes(), b'new data')

    def test_new_store_parent_is_synced_before_source_rename_on_retry(self):
        actual_sync = file_module._sync_directory
        actual_rename = file_module._rename_exclusive
        calls = []
        failed = False
        def sync(path):
            nonlocal failed
            calls.append(('sync', Path(path)))
            if Path(path) == self.root and not failed:
                failed = True
                raise OSError('parent barrier failed')
            return actual_sync(path)
        def rename(source, destination):
            calls.append(('rename', Path(source)))
            return actual_rename(source, destination)
        with patch.object(file_module, '_sync_directory', side_effect=sync), patch.object(file_module, '_rename_exclusive', side_effect=rename):
            self.error(400, self.files.trash, self.payload())
            calls.clear()
            self.trash()
        renamed = next(index for index, event in enumerate(calls) if event[0] == 'rename')
        self.assertIn(('sync', self.root), calls[:renamed])

    def test_unsupported_exclusive_rename_fails_closed(self):
        if os.name == 'nt':
            self.skipTest('Windows os.rename is already exclusive')
        with patch.object(file_module.ctypes, 'CDLL', return_value=object()):
            self.error(501, self.files.move, self.payload(destination='src/b.sv'))
        self.assertTrue((self.root / 'src/a.sv').exists())
        self.assertFalse((self.root / 'src/b.sv').exists())


if __name__ == '__main__':
    unittest.main()
