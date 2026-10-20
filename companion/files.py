"""Bounded project file lifecycle operations and persistent, private trash.

Operations serialize with the companion's other writers. They do not implement
compare-and-swap against unrelated native processes. Publication uses the OS's
exclusive rename primitive, so an arriving destination is never overwritten.
POSIX directory/file fsync is used for durability; Windows directory flushing is
not provided by Python and remains a platform acceptance limitation.
"""
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import tempfile
import unicodedata

from .projects import MAX_FILE_BYTES, MAX_FILES, MAX_SNAPSHOT_BYTES, ProjectError, is_link, revision

MAX_TRASH = 32
PRIVATE = '.vwb-trash'
FORMAT = 'verilog-workbench-trash-v1'
IDENTIFIER = re.compile(r'^[a-f0-9]{32}$')
SHA256 = re.compile(r'^[a-f0-9]{64}$')


def _name_key(value):
    # HFS+/APFS may return decomposed names for composed browser input.
    return unicodedata.normalize('NFC', value).casefold()


def _sync_directory(path):
    if os.name == 'nt':
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _rename_exclusive(source, destination):
    """Never fall back to POSIX rename, which can overwrite a late arrival.

    Linux: include/uapi/linux/fs.h, RENAME_NOREPLACE (1 << 0).
    https://github.com/torvalds/linux/blob/master/include/uapi/linux/fs.h
    Apple: xnu/bsd/sys/stdio.h, RENAME_EXCL 0x00000004.
    https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/stdio.h
    Windows: docs.python.org/3/library/os.html#os.rename (always exclusive).
    """
    if os.name == 'nt':
        os.rename(source, destination)
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith('linux') and hasattr(libc, 'renameat2'):
        operation = libc.renameat2
        operation.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        operation.restype = ctypes.c_int
        result = operation(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    elif sys.platform == 'darwin' and hasattr(libc, 'renamex_np'):
        operation = libc.renamex_np
        operation.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        operation.restype = ctypes.c_int
        result = operation(os.fsencode(source), os.fsencode(destination), 4)
    else:
        raise ProjectError(501, 'exclusive rename is unavailable on this operating system')
    if result:
        number = ctypes.get_errno()
        if number in {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP}:
            raise ProjectError(501, 'this filesystem does not support exclusive rename')
        raise OSError(number, os.strerror(number))


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


class FileOperations:
    def __init__(self, project):
        self.project = project

    def _path(self, value, *, private=False):
        target = self.project.path(value, private=private)
        # Require the actual case of every existing component, including on a
        # case-insensitive host. Reject ambiguous siblings on case-sensitive hosts.
        parent = self.project.root
        for part in target.relative_to(self.project.root).parts:
            if parent.is_dir():
                matches = [child.name for child in parent.iterdir() if _name_key(child.name) == _name_key(part)]
                if matches and (len(matches) != 1 or unicodedata.normalize('NFC', matches[0]) != unicodedata.normalize('NFC', part)):
                    raise ProjectError(409, 'path has a case collision; use an unambiguous portable name')
            parent = parent / part
        return target

    def _relative(self, path):
        return path.relative_to(self.project.root).as_posix()

    def _mutable(self, target, *, creating=False, kind=None):
        parts = tuple(part.casefold() for part in target.relative_to(self.project.root).parts)
        if not parts or parts == ('.vwb.json',):
            raise ProjectError(403, 'project root and configuration are protected')
        build = tuple(part.casefold() for part in self.project.build_path.relative_to(self.project.root).parts)
        if parts == build[:len(parts)] or build == parts[:len(build)]:
            raise ProjectError(403, 'build directory and its ancestors are protected')
        for name in (self.project.src_dir, self.project.test_dir):
            base = self.project.path(name, allow_root=True)
            root_parts = tuple(part.casefold() for part in base.relative_to(self.project.root).parts)
            if parts == root_parts[:len(parts)]:
                if not creating or kind != 'directory':
                    raise ProjectError(403, 'source and test roots and their ancestors are protected')

    def _destination(self, value, *, creating=False, kind=None):
        target = self._path(value)
        self._mutable(target, creating=creating, kind=kind)
        if not target.parent.is_dir():
            raise ProjectError(404, 'parent directory does not exist')
        if any(_name_key(child.name) == _name_key(target.name) for child in target.parent.iterdir()):
            raise ProjectError(409, 'destination already exists or has a case collision')
        return target

    def _snapshot(self, target, *, sync=False, private=False, relocated_to=None):
        digest = hashlib.sha256()
        total = count = files = 0
        root_kind = None
        native_path_max = None
        if relocated_to is not None and hasattr(os, 'pathconf'):
            try:
                native_path_max = os.pathconf(self.project.root, 'PC_PATH_MAX')
            except (OSError, ValueError):
                pass

        def visit(path, local):
            nonlocal total, count, files, root_kind
            self.project.path(self._relative(path), private=private)
            if relocated_to is not None:
                future = relocated_to / local if local else relocated_to
                if native_path_max and native_path_max > 0 and len(os.fsencode(future)) >= native_path_max:
                    raise ProjectError(400, 'relocated entry exceeds the native filesystem path limit')
                self.project.path(self._relative(future), private=True)
            try:
                info = path.lstat()
            except FileNotFoundError as exc:
                raise ProjectError(404, 'project entry does not exist') from exc
            if is_link(path) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise ProjectError(403, 'links and special files cannot be operated on')
            if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                raise ProjectError(403, 'hard-linked files cannot be operated on')
            count += 1
            if count > MAX_FILES:
                raise ProjectError(413, 'entry exceeds 10000 filesystem entries')
            kind = 'directory' if stat.S_ISDIR(info.st_mode) else 'file'
            if root_kind is None:
                root_kind = kind
            name = local.encode('utf-8')
            digest.update(kind.encode('ascii') + len(name).to_bytes(4, 'big') + name)
            if kind == 'directory':
                children = sorted(path.iterdir(), key=lambda item: item.name)
                folded = [_name_key(child.name) for child in children]
                if len(set(folded)) != len(folded):
                    raise ProjectError(409, 'directory contains case-colliding names')
                for child in children:
                    visit(child, local + '/' + child.name if local else child.name)
                if sync:
                    _sync_directory(path)
            else:
                files += 1
                content = hashlib.sha256()
                size = 0
                # Windows fsync uses _commit/FlushFileBuffers, which requires
                # a write-capable handle. A read-only file therefore fails
                # safely before a lifecycle mutation on Windows.
                with path.open('r+b' if sync and os.name == 'nt' else 'rb') as handle:
                    opened = os.fstat(handle.fileno())
                    if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino) or opened.st_nlink != 1:
                        raise ProjectError(409, 'entry changed while being inspected')
                    for chunk in iter(lambda: handle.read(65536), b''):
                        total += len(chunk)
                        size += len(chunk)
                        if total > MAX_SNAPSHOT_BYTES:
                            raise ProjectError(413, 'entry exceeds 64 MiB')
                        content.update(chunk)
                    final = os.fstat(handle.fileno())
                    if (final.st_size, final.st_mtime_ns, final.st_ctime_ns, final.st_nlink) != (
                            opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns, opened.st_nlink):
                        raise ProjectError(409, 'entry changed while being inspected')
                    if sync:
                        os.fsync(handle.fileno())
                digest.update(size.to_bytes(8, 'big') + content.digest())
                if not local:
                    return content.hexdigest()
            return None

        file_revision = visit(target, '')
        return {'path': self._relative(target), 'kind': root_kind,
                'revision': file_revision or digest.hexdigest(), 'file_count': files,
                'bytes': total, '_entries': count}

    @staticmethod
    def _public(metadata):
        return {key: value for key, value in metadata.items() if not key.startswith('_')}

    @staticmethod
    def _payload(payload, fields, required):
        if not isinstance(payload, dict) or set(payload) - set(fields) or not set(required) <= set(payload):
            raise ProjectError(400, 'invalid file operation fields')

    def _expected(self, payload, snapshot):
        expected = payload.get('revision')
        if not isinstance(expected, str) or not SHA256.fullmatch(expected):
            raise ProjectError(400, 'revision must be SHA-256')
        if snapshot['revision'] != expected:
            raise ProjectError(409, 'entry changed outside this editor; refresh before continuing')
        documents = payload.get('documents', [])
        if not isinstance(documents, list) or len(documents) > 64:
            raise ProjectError(400, 'documents must contain at most 64 affected file revisions')
        seen = set()
        for document in documents:
            self._payload(document, ('path', 'revision'), ('path', 'revision'))
            target = self._path(document['path'])
            relative = self._relative(target)
            if relative in seen or not (relative == snapshot['path'] or relative.startswith(snapshot['path'] + '/')):
                raise ProjectError(400, 'document must be a unique affected file')
            seen.add(relative)
            if not isinstance(document['revision'], str) or not SHA256.fullmatch(document['revision']):
                raise ProjectError(400, 'document revision must be SHA-256')
            current = self._snapshot(target)
            if current['kind'] != 'file' or current['revision'] != document['revision']:
                raise ProjectError(409, 'an open file changed outside this editor; reload before continuing')

    def entry(self, path):
        with self.project.lock:
            try:
                return self._public(self._snapshot(self._path(path)))
            except OSError as exc:
                raise ProjectError(400, 'cannot inspect project entry') from exc

    def create(self, payload):
        self._payload(payload, ('path', 'kind', 'content'), ('path', 'kind'))
        kind = payload['kind']
        if kind not in ('file', 'directory') or (kind == 'directory' and 'content' in payload):
            raise ProjectError(400, 'kind must be file or directory; only files have content')
        content = payload.get('content', '')
        if not isinstance(content, str):
            raise ProjectError(400, 'content must be a UTF-8 string')
        try:
            data = content.encode('utf-8')
        except UnicodeError as exc:
            raise ProjectError(400, 'content must be valid UTF-8') from exc
        if len(data) > MAX_FILE_BYTES:
            raise ProjectError(413, 'new file exceeds 2 MiB')
        with self.project.lock:
            temporary = None
            try:
                target = self._destination(payload['path'], creating=True, kind=kind)
                if kind == 'directory':
                    target.mkdir(mode=0o700)
                    _sync_directory(target)
                else:
                    descriptor, temporary = tempfile.mkstemp(prefix='.vwb-create-', dir=target.parent)
                    with os.fdopen(descriptor, 'wb') as handle:
                        handle.write(data)
                        handle.flush()
                        os.fsync(handle.fileno())
                    self._destination(payload['path'], creating=True, kind=kind)
                    _rename_exclusive(Path(temporary), target)
                    temporary = None
                _sync_directory(target.parent)
                return self._public(self._snapshot(target))
            except FileExistsError as exc:
                raise ProjectError(409, 'destination already exists') from exc
            except OSError as exc:
                raise ProjectError(400, 'cannot create project entry') from exc
            finally:
                if temporary is not None:
                    Path(temporary).unlink(missing_ok=True)

    def move(self, payload):
        self._payload(payload, ('path', 'destination', 'revision', 'documents'), ('path', 'destination', 'revision'))
        with self.project.lock:
            try:
                source = self._path(payload['path'])
                self._mutable(source)
                destination = self._destination(payload['destination'])
                if source == destination or source in destination.parents:
                    raise ProjectError(400, 'entry cannot be moved into itself')
                before = self._snapshot(source, sync=True, relocated_to=destination)
                self._expected(payload, before)
                self._destination(payload['destination'])
                self._expected(payload, self._snapshot(source))
                _rename_exclusive(source, destination)
                _sync_directory(destination.parent)
                if source.parent != destination.parent:
                    _sync_directory(source.parent)
                return {**self._public(self._snapshot(destination)), 'previous_path': before['path']}
            except FileExistsError as exc:
                raise ProjectError(409, 'destination already exists') from exc
            except OSError as exc:
                raise ProjectError(400, 'cannot move project entry') from exc

    def _read_json(self, path, limit=16384):
        self._path(self._relative(path), private=True)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or is_link(path) or info.st_size > limit:
            raise ProjectError(409, 'trash metadata is not an ordinary bounded file')
        try:
            with path.open('rb') as handle:
                raw = handle.read(limit + 1)
            if len(raw) > limit:
                raise ValueError('too large')
            return json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise ProjectError(409, 'trash metadata is invalid') from exc

    def _write_json(self, path, value):
        descriptor, temporary = tempfile.mkstemp(prefix='.metadata-', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(_canonical(value))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            _sync_directory(path.parent)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _store(self, *, create=False):
        store = self._path(PRIVATE, private=True)
        if not store.exists():
            if not create:
                return None, None
            store.mkdir(mode=0o700)
            _sync_directory(self.project.root)
        if not store.is_dir() or is_link(store):
            raise ProjectError(409, 'trash store is not an ordinary directory')
        marker = store / 'owner.json'
        if not marker.exists():
            if not create or any(store.iterdir()):
                raise ProjectError(409, 'refusing an unowned trash store')
            if os.name != 'nt':
                store.chmod(0o700)
            self._write_json(marker, {'format': FORMAT, 'project': self.project.summary()['id'],
                                      'owner': secrets.token_hex(16)})
        if os.name != 'nt' and (stat.S_IMODE(store.stat().st_mode) & 0o077):
            raise ProjectError(409, 'trash store permissions must be private')
        value = self._read_json(marker)
        if (not isinstance(value, dict) or set(value) != {'format', 'project', 'owner'}
                or value['format'] != FORMAT or value['project'] != self.project.summary()['id']
                or not isinstance(value['owner'], str) or not IDENTIFIER.fullmatch(value['owner'])):
            raise ProjectError(409, 'trash store belongs to another project or is invalid')
        # Retry these barriers even if an earlier attempt created the store but
        # failed before its parent directory entry became durable.
        if create:
            _sync_directory(self.project.root)
            _sync_directory(store)
        return store, value['owner']

    def _manifest(self, folder, owner):
        if not IDENTIFIER.fullmatch(folder.name) or not folder.is_dir() or is_link(folder):
            raise ProjectError(409, 'trash entry directory is invalid')
        if os.name != 'nt' and (stat.S_IMODE(folder.stat().st_mode) & 0o077):
            raise ProjectError(409, 'trash entry permissions must be private')
        wrapper = self._read_json(folder / 'manifest.json')
        if not isinstance(wrapper, dict) or set(wrapper) != {'manifest', 'checksum'}:
            raise ProjectError(409, 'trash manifest is invalid')
        value = wrapper['manifest']
        if not isinstance(value, dict) or revision(_canonical(value)) != wrapper['checksum']:
            raise ProjectError(409, 'trash manifest checksum does not match')
        if set(value) != {'format', 'owner', 'id', 'state', 'entry'} or value['format'] != FORMAT or value['owner'] != owner or value['id'] != folder.name:
            raise ProjectError(409, 'trash manifest identity does not match')
        if value['state'] not in ('preparing', 'trashed', 'restoring', 'restored'):
            raise ProjectError(409, 'trash manifest state is invalid')
        entry = value['entry']
        if (not isinstance(entry, dict) or set(entry) != {'path', 'kind', 'revision', 'file_count', 'bytes', '_entries'}
                or entry['kind'] not in ('file', 'directory') or not isinstance(entry['revision'], str)
                or not SHA256.fullmatch(entry['revision'])
                or any(type(entry[key]) is not int or entry[key] < 0 for key in ('file_count', 'bytes', '_entries'))
                or entry['_entries'] < 1 or entry['_entries'] > MAX_FILES or entry['bytes'] > MAX_SNAPSHOT_BYTES
                or entry['file_count'] > entry['_entries']):
            raise ProjectError(409, 'trash entry metadata is invalid')
        target = self._path(entry['path'])
        self._mutable(target)
        if self._relative(target) != entry['path']:
            raise ProjectError(409, 'trash original path is not canonical')
        return value

    def _save_manifest(self, folder, manifest):
        self._write_json(folder / 'manifest.json', {'manifest': manifest, 'checksum': revision(_canonical(manifest))})

    @staticmethod
    def _same(snapshot, entry):
        return all(snapshot[key] == entry[key] for key in ('kind', 'revision', 'file_count', 'bytes', '_entries'))

    def _record(self, folder, owner):
        manifest = self._manifest(folder, owner)
        entry = manifest['entry']
        payload = folder / 'payload'
        result = {**self._public(entry), 'id': folder.name, 'state': manifest['state'], 'restorable': False}
        if payload.exists() or is_link(payload):
            actual = self._snapshot(payload, private=True)
            if not self._same(actual, entry):
                raise ProjectError(409, 'trash payload checksum or inventory does not match')
            result['restorable'] = True
            if manifest['state'] != 'trashed':
                result['state'] = 'interrupted'
                result['error'] = 'operation was interrupted; verified payload is retained'
        else:
            result['state'] = 'interrupted'
            original = self._path(entry['path'])
            if original.exists() and self._same(self._snapshot(original), entry) and manifest['state'] in ('preparing', 'restoring', 'restored'):
                result['restorable'] = True
                result['error'] = 'original entry is present; Restore acknowledges the interrupted operation'
            else:
                result['error'] = 'payload is missing; retained metadata requires native inspection'
        return result, manifest

    def _records(self, store, owner, *, cleanup_empty=False):
        entries = []
        if store is None:
            return entries
        children = sorted(store.iterdir(), key=lambda path: path.name)
        if len(children) > MAX_FILES:
            raise ProjectError(413, 'trash store contains too many entries')
        for folder in children:
            if folder.name == 'owner.json':
                continue
            # A crash after metadata unlink and before rmdir can leave this
            # harmless tombstone. Only remove an empty, ordinary UUID folder
            # inside our verified store, and only during a requested mutation.
            if cleanup_empty and IDENTIFIER.fullmatch(folder.name) and folder.is_dir() and not is_link(folder):
                try:
                    if not any(folder.iterdir()):
                        folder.rmdir()  # Atomic refusal if another writer adds data.
                        _sync_directory(store)
                        continue
                except OSError:
                    pass
            try:
                public, _ = self._record(folder, owner)
                entries.append(public)
            except (ProjectError, OSError):
                entries.append({'id': folder.name, 'state': 'invalid', 'restorable': False,
                                'error': 'trash entry cannot be verified; retained for native inspection'})
        return entries

    def trash_list(self):
        with self.project.lock:
            try:
                store, owner = self._store()
                return {'entries': self._records(store, owner)}
            except OSError as exc:
                raise ProjectError(409, 'cannot inspect private trash store') from exc

    def trash(self, payload):
        self._payload(payload, ('path', 'revision', 'documents'), ('path', 'revision'))
        with self.project.lock:
            try:
                source = self._path(payload['path'])
                self._mutable(source)
                # The private prefix can be longer than the original path. All
                # descendants must remain inspectable/restorable after the rename.
                future_payload = self.project.root / PRIVATE / ('0' * 32) / 'payload'
                before = self._snapshot(source, sync=True, relocated_to=future_payload)
                self._expected(payload, before)
                store, owner = self._store(create=True)
                records = self._records(store, owner, cleanup_empty=True)
                if any(record['state'] == 'invalid' for record in records):
                    raise ProjectError(409, 'unverified trash entries require native inspection before adding more')
                if len(records) >= MAX_TRASH:
                    raise ProjectError(413, 'trash retains at most 32 entries; restore an entry before adding more')
                total_bytes = total_entries = 0
                for record in records:
                    manifest = self._manifest(store / record['id'], owner)
                    total_bytes += manifest['entry']['bytes']
                    total_entries += manifest['entry']['_entries']
                if total_bytes + before['bytes'] > MAX_SNAPSHOT_BYTES or total_entries + before['_entries'] > MAX_FILES:
                    raise ProjectError(413, 'trash exceeds 64 MiB or 10000 retained filesystem entries')
                identifier = secrets.token_hex(16)
                folder = store / identifier
                folder.mkdir(mode=0o700)
                _sync_directory(store)
                manifest = {'format': FORMAT, 'owner': owner, 'id': identifier,
                            'state': 'preparing', 'entry': before}
                self._save_manifest(folder, manifest)
                self._expected(payload, self._snapshot(source))
                _rename_exclusive(source, folder / 'payload')
                _sync_directory(folder)
                _sync_directory(source.parent)
                manifest['state'] = 'trashed'
                self._save_manifest(folder, manifest)
                result, _ = self._record(folder, owner)
                return result
            except FileExistsError as exc:
                raise ProjectError(409, 'trash destination already exists') from exc
            except OSError as exc:
                raise ProjectError(400, 'trash operation was interrupted; inspect Trash before retrying') from exc

    def restore(self, payload):
        self._payload(payload, ('id',), ('id',))
        identifier = payload['id']
        if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier):
            raise ProjectError(400, 'invalid trash entry identifier')
        with self.project.lock:
            try:
                store, owner = self._store()
                if store is None or not (store / identifier).exists():
                    raise ProjectError(404, 'trash entry does not exist')
                folder = store / identifier
                public, manifest = self._record(folder, owner)
                if not public['restorable']:
                    raise ProjectError(409, 'trash entry cannot be safely restored; native inspection is required')
                entry = manifest['entry']
                source = folder / 'payload'
                if source.exists():
                    target = self._destination(entry['path'])
                    manifest['state'] = 'restoring'
                    self._save_manifest(folder, manifest)
                    self._destination(entry['path'])
                    if not self._same(self._snapshot(source, sync=True, private=True), entry):
                        raise ProjectError(409, 'trash payload changed before restore')
                    _rename_exclusive(source, target)
                    _sync_directory(target.parent)
                    _sync_directory(folder)
                else:
                    target = self._path(entry['path'])
                    if not self._same(self._snapshot(target, sync=True), entry):
                        raise ProjectError(409, 'original entry changed before restore acknowledgement')
                manifest['state'] = 'restored'
                self._save_manifest(folder, manifest)
                # Only our metadata is removed. No payload is recursively deleted.
                (folder / 'manifest.json').unlink()
                _sync_directory(folder)
                folder.rmdir()
                _sync_directory(store)
                return self._public(self._snapshot(target))
            except FileExistsError as exc:
                raise ProjectError(409, 'original destination already exists') from exc
            except OSError as exc:
                raise ProjectError(400, 'restore was interrupted; retained state is visible in Trash') from exc
