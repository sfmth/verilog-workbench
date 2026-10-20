"""Explicit project grants and revision-checked UTF-8 file operations."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile
import threading

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_FILES = 10000
MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024


class ProjectError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def revision(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_link(path):
    """Also reject Windows junctions/reparse points on supported Python builds."""
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    except FileNotFoundError:
        return False


class ProjectGrant:
    """A folder approved by the person starting the native process.

    This is a file-access boundary, not an execution sandbox: trusted project
    tests, HDL tool scripts, and formal configurations execute with user rights.
    """
    def __init__(self, root, *, src_dir=None, test_dir=None, build_dir=None):
        self.root = Path(root).expanduser().resolve(strict=True)
        if not self.root.is_dir():
            raise ProjectError(400, "project must be a directory")
        self.lock = threading.RLock()
        config = {}
        config_path = self.root / ".vwb.json"
        if config_path.exists() or is_link(config_path):
            self.path(".vwb.json")
            try:
                config = json.loads(self._bytes(config_path).decode("utf-8"))
            except (ValueError, UnicodeError) as exc:
                raise ProjectError(400, "invalid project configuration") from exc
            if not isinstance(config, dict):
                raise ProjectError(400, "project configuration must be an object")
        self.src_dir = src_dir if src_dir is not None else config.get("src_dir", "src")
        self.test_dir = test_dir if test_dir is not None else config.get("test_dir", "test")
        self.build_dir = build_dir if build_dir is not None else config.get("build_dir", ".vwb")
        for value in (self.src_dir, self.test_dir, self.build_dir):
            self.path(value, allow_root=True)
        self.build_path = self.path(self.build_dir)
        if self.build_path == self.root:
            raise ProjectError(400, "build directory must be below the project")
        for value in (self.src_dir, self.test_dir):
            source = self.path(value, allow_root=True)
            if source == self.build_path or self.build_path in source.parents:
                raise ProjectError(400, "source and test directories cannot be inside build directory")

    def summary(self):
        # Scope browser recovery to the canonical grant, including two folders
        # with the same display name. Never expose the absolute host path.
        identity = hashlib.sha256(os.fsencode(os.path.normcase(str(self.root)))).hexdigest()
        return {"id": identity, "name": self.root.name, "src_dir": self.src_dir,
                "test_dir": self.test_dir, "build_dir": self.build_dir}

    def path(self, value, *, allow_root=False, private=False):
        if not isinstance(value, str) or not value or len(value) > 1024:
            raise ProjectError(400, "path must be a nonempty project-relative string")
        try:
            value.encode('utf-8')
        except UnicodeError as exc:
            raise ProjectError(400, 'path must be valid UTF-8') from exc
        if "\\" in value or ":" in value or any(ord(c) < 32 for c in value):
            raise ProjectError(400, "unsupported path spelling")
        pure = PurePosixPath(value)
        if any(part.casefold() == '.vwb-trash' for part in pure.parts) and not private:
            raise ProjectError(403, 'project trash is private; use the restore operation')
        if pure.is_absolute() or any(part in ("..", "") for part in pure.parts):
            raise ProjectError(403, "path must stay inside the granted project")
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                    *(f"LPT{i}" for i in range(1, 10))}
        if any(part.endswith((".", " ")) or part.split(".")[0].upper() in reserved for part in pure.parts):
            raise ProjectError(400, "path is not portable across supported operating systems")
        if any(part.casefold() == ".git" for part in pure.parts):
            raise ProjectError(403, ".git access is not allowed")
        target = self.root
        for part in pure.parts:
            # Refuse even internal links: their destination can change while a
            # browser is editing. Local concurrent mutation is not sandboxed.
            target = target / part
            if is_link(target):
                raise ProjectError(403, "symlink access is not allowed")
        try:
            relative = target.resolve().relative_to(self.root)
        except ValueError as exc:
            raise ProjectError(403, "path leaves the granted project") from exc
        if any(part.casefold() == ".git" for part in relative.parts):
            raise ProjectError(403, ".git access is not allowed")
        if target == self.root and not allow_root:
            raise ProjectError(400, "a file or subdirectory path is required")
        return target

    def _bytes(self, path):
        try:
            if not path.is_file():
                raise ProjectError(404, "file not found")
            with path.open("rb") as handle:
                data = handle.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise ProjectError(413, "file exceeds 2 MiB limit")
            return data
        except OSError as exc:
            raise ProjectError(400, "cannot read project file") from exc

    def read(self, value):
        with self.lock:
            target = self.path(value)
            if self.private_editor_path(target):
                raise ProjectError(403, 'editor recovery data is private')
            data = self._bytes(target)
            try:
                content = data.decode("utf-8")
            except UnicodeError as exc:
                raise ProjectError(415, "only UTF-8 text can be edited") from exc
            return {"path": value, "content": content, "revision": revision(data), "encoding": "utf-8"}

    def save(self, value, content, expected):
        if not isinstance(content, str):
            raise ProjectError(400, "content must be a UTF-8 string")
        if expected is not None and (not isinstance(expected, str) or len(expected) != 64
                                     or any(c not in "0123456789abcdef" for c in expected)):
            raise ProjectError(400, "revision must be SHA-256 or null for a new file")
        try:
            data = content.encode("utf-8")
        except UnicodeError as exc:
            raise ProjectError(400, "content is not valid UTF-8") from exc
        if len(data) > MAX_FILE_BYTES:
            raise ProjectError(413, "file exceeds 2 MiB limit")
        with self.lock:
            target = self.path(value)
            if self.build_path == target or self.build_path in target.parents:
                raise ProjectError(403, "generated artifacts are read-only")
            if not target.parent.is_dir():
                raise ProjectError(404, "parent directory does not exist")
            def check():
                self.path(value)
                current = revision(self._bytes(target)) if target.exists() else None
                if current != expected:
                    raise ProjectError(409, "file changed outside this editor; reload before saving")
            check()
            mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o600
            descriptor, temporary = tempfile.mkstemp(prefix=".vwb-save-", dir=target.parent)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary, mode)
                check()
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return {"path": value, "revision": revision(data), "size": len(data)}

    def private_editor_path(self, target):
        private = self.build_path / 'editor-recovery'
        return target == private or private in target.parents

    def inventory(self, *, artifacts=False):
        base = self.build_path if artifacts else self.root
        files, directories = [], []
        if not base.exists():
            return {"files": files, "directories": directories, "truncated": False}
        self.path(base.relative_to(self.root).as_posix(), allow_root=True)
        for directory, dirs, names in os.walk(base, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d.casefold() != ".git"
                             and d.casefold() != '.vwb-trash'
                             and not is_link(Path(directory) / d)
                             and not self.private_editor_path(Path(directory) / d)
                             and (artifacts or Path(directory) / d != self.build_path))
            for name in dirs:
                directories.append({'path': (Path(directory) / name).relative_to(self.root).as_posix()})
                if len(files) + len(directories) >= MAX_FILES:
                    return {'files': files, 'directories': directories, 'truncated': True}
            for name in sorted(names):
                path = Path(directory) / name
                if name.casefold() == ".git" or name.startswith(".vwb-save-") or is_link(path):
                    continue
                try:
                    info = path.stat()
                    if not stat.S_ISREG(info.st_mode):
                        continue
                    files.append({"path": path.relative_to(self.root).as_posix(), "size": info.st_size})
                except OSError:
                    continue
                if len(files) + len(directories) >= MAX_FILES:
                    return {"files": files, "directories": directories, "truncated": True}
        return {"files": files, "directories": directories, "truncated": False}

    def source_revision(self):
        """Hash source/test names and bytes, with a bounded cost for the proof.

        No source is returned to the caller. Changes while a job is running
        invalidate its results even if the process itself returned success.
        """
        digest = hashlib.sha256()
        visited = set()
        total = 0
        with self.lock:
            for directory_name in (self.src_dir, self.test_dir):
                base = self.path(directory_name, allow_root=True)
                digest.update((directory_name + "\0").encode("utf-8"))
                if not base.exists():
                    digest.update(b"missing\0")
                    continue
                if not base.is_dir():
                    raise ProjectError(400, "source and test paths must be directories")
                for directory, dirs, names in os.walk(base, followlinks=False):
                    dirs[:] = sorted(d for d in dirs if d.casefold() != ".git"
                                     and d.casefold() != '.vwb-trash'
                                     and Path(directory) / d != self.build_path)
                    for name in dirs:
                        if is_link(Path(directory) / name):
                            raise ProjectError(403, "source and test directories cannot contain links")
                    for name in sorted(names):
                        if name.casefold() == ".git" or name.startswith(".vwb-save-"):
                            continue
                        relative = (Path(directory) / name).relative_to(self.root).as_posix()
                        if relative in visited:
                            continue
                        target = self.path(relative)
                        if not target.is_file():
                            continue
                        visited.add(relative)
                        if len(visited) > MAX_FILES:
                            raise ProjectError(413, "source snapshot exceeds 10000 files")
                        encoded_path = relative.encode("utf-8")
                        digest.update(len(encoded_path).to_bytes(4, "big") + encoded_path)
                        content_digest = hashlib.sha256()
                        with target.open("rb") as handle:
                            while True:
                                chunk = handle.read(65536)
                                if not chunk:
                                    break
                                total += len(chunk)
                                if total > MAX_SNAPSHOT_BYTES:
                                    raise ProjectError(413, "source snapshot exceeds 64 MiB")
                                content_digest.update(chunk)
                        digest.update(content_digest.digest())
        return digest.hexdigest()
