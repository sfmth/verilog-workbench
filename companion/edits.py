"""Revision-checked, recoverable batches of trusted language-server edits.

The project lock serializes companion operations. Replacing several files is
not a filesystem-wide atomic operation: outside programs can still race it.
Every replacement and restoration checks its expected bytes before proceeding.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
import time

from .projects import MAX_FILE_BYTES, ProjectError, is_link, revision

MAX_EDIT_FILES = 32
MAX_EDIT_BYTES = 8 * 1024 * 1024
MAX_CACHED_BYTES = 32 * 1024 * 1024
MAX_PLANS = 16
PLAN_LIFETIME = 300
RECOVERY_DIRECTORY = "editor-recovery"
_MAX_MANIFEST_BYTES = 256 * 1024
_OWNER = ".owner"


def _sync_directory(path):
    # Windows does not provide the POSIX directory-fsync primitive.
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class EditTransactions:
    """Short-lived plans; only IDs from this instance authorize applying edits.

    ``preview`` is an internal trusted-LSP API. HTTP callers may supply an ID
    to ``apply`` or ``undo``, never a replacement file body. Undo expires with
    the session; an interrupted write's private journal survives restarts.
    """

    def __init__(self, project):
        self.project = project
        self._plans = {}
        self._undos = {}
        self._directory = project.build_path / RECOVERY_DIRECTORY
        self._pending = self._directory / "pending"

    def _target(self, value):
        target = self.project.path(value)
        if target == self.project.build_path or self.project.build_path in target.parents:
            raise ProjectError(403, "generated artifacts are read-only")
        return target

    def _source_bytes(self, value):
        target = self._target(value)
        try:
            info = target.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ProjectError(403, "edit targets must be ordinary, unlinked files")
            descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as handle:
                opened = os.fstat(handle.fileno())
                if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
                    raise ProjectError(409, "file changed while opening edit target")
                data = handle.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise ProjectError(413, "file exceeds 2 MiB limit")
            data.decode("utf-8")
            return data, stat.S_IMODE(info.st_mode)
        except FileNotFoundError as exc:
            raise ProjectError(404, "edit target does not exist") from exc
        except UnicodeError as exc:
            raise ProjectError(415, "only UTF-8 text can be edited") from exc
        except OSError as exc:
            raise ProjectError(409, "cannot safely read edit target") from exc

    def _check(self, item, expected):
        data, _ = self._source_bytes(item["path"])
        if revision(data) != item[expected]:
            raise ProjectError(409, "file changed outside this edit; preview again")
        return data

    def _prune(self):
        now = time.monotonic()
        for cache in (self._plans, self._undos):
            for key in list(cache):
                if cache[key]["expires_at"] <= now:
                    del cache[key]
        while (sum(len(cache) for cache in (self._plans, self._undos)) >= MAX_PLANS
               or sum(item["size"] for cache in (self._plans, self._undos)
                      for item in cache.values()) > MAX_CACHED_BYTES - MAX_EDIT_BYTES):
            candidates = [(value["expires_at"], cache, key)
                          for cache in (self._plans, self._undos) for key, value in cache.items()]
            if not candidates:
                break
            _, cache, key = min(candidates, key=lambda entry: entry[0])
            del cache[key]

    def _lookup(self, cache, identifier):
        if not isinstance(identifier, str) or len(identifier) > 128:
            raise ProjectError(400, "edit identifier must be a string")
        item = cache.get(identifier)
        if item is None:
            raise ProjectError(404, "edit identifier is unknown or already consumed")
        if item["expires_at"] <= time.monotonic():
            del cache[identifier]
            raise ProjectError(410, "edit identifier expired; preview again")
        return item

    def _assert_ready(self):
        if self.status()["recovery_required"]:
            raise ProjectError(409, "an interrupted edit requires explicit recovery")

    def preview(self, files):
        with self.project.lock:
            self._assert_ready()
            if not isinstance(files, list) or not 1 <= len(files) <= MAX_EDIT_FILES:
                raise ProjectError(400, "an edit must contain 1 to 32 existing files")
            items, seen, seen_inodes, total = [], set(), set(), 0
            for entry in files:
                if not isinstance(entry, dict) or set(entry) - {"path", "content", "revision", "edit_count"}:
                    raise ProjectError(400, "invalid edit file")
                value, content, expected = (entry.get(key) for key in ("path", "content", "revision"))
                target = self._target(value)
                identity = os.path.normcase(str(target))
                if identity in seen:
                    raise ProjectError(400, "duplicate edit target")
                seen.add(identity)
                if not isinstance(content, str):
                    raise ProjectError(400, "edit content must be UTF-8 text")
                try:
                    after = content.encode("utf-8")
                except UnicodeError as exc:
                    raise ProjectError(400, "edit content must be valid UTF-8") from exc
                if len(after) > MAX_FILE_BYTES:
                    raise ProjectError(413, "file exceeds 2 MiB limit")
                before, mode = self._source_bytes(value)
                info = target.lstat()
                inode = (info.st_dev, info.st_ino)
                if inode in seen_inodes:
                    raise ProjectError(400, "duplicate edit target")
                seen_inodes.add(inode)
                before_revision = revision(before)
                if expected != before_revision:
                    raise ProjectError(409, "edit base revision is stale")
                count = entry.get("edit_count", 1)
                if type(count) is not int or not 1 <= count <= 100000:
                    raise ProjectError(400, "invalid edit count")
                total += len(before) + len(after)
                if total > MAX_EDIT_BYTES:
                    raise ProjectError(413, "edit before/after contents exceed 8 MiB")
                items.append({"path": target.relative_to(self.project.root).as_posix(),
                              "before": before.decode("utf-8"), "after": content,
                              "before_revision": before_revision, "after_revision": revision(after),
                              "mode": mode, "edit_count": count})
            self._prune()
            identifier = secrets.token_urlsafe(24)
            self._plans[identifier] = {"files": items, "size": total,
                                       "expires_at": time.monotonic() + PLAN_LIFETIME}
            return {"id": identifier, "files": [{key: value for key, value in item.items() if key != "mode"}
                                                for item in items], "expires_in": PLAN_LIFETIME}

    def _private_directory(self, path):
        self.project.path(path.relative_to(self.project.root).as_posix())
        info = path.stat()
        if not stat.S_ISDIR(info.st_mode):
            raise ProjectError(409, "edit recovery location is not a directory")
        if os.name != "nt" and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise ProjectError(409, "edit recovery directory must be private and user-owned")

    def _private_bytes(self, path, limit):
        self.project.path(path.relative_to(self.project.root).as_posix())
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ProjectError(409, "invalid edit recovery file")
        if os.name != "nt" and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise ProjectError(409, "edit recovery files must be private and user-owned")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            if (info.st_dev, info.st_ino) != (os.fstat(handle.fileno()).st_dev, os.fstat(handle.fileno()).st_ino):
                raise ProjectError(409, "edit recovery file changed while opening")
            data = handle.read(limit + 1)
        if len(data) > limit:
            raise ProjectError(413, "edit recovery file exceeds limit")
        return data

    def _write_private(self, path, data):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())

    def _prepare_build(self):
        build = self.project.path(self.project.build_dir)
        marker = build / ".vwb-root"
        if marker.exists() or is_link(marker):
            self.project.path(marker.relative_to(self.project.root).as_posix())
            try:
                if marker.stat().st_nlink != 1:
                    raise ProjectError(409, "build ownership marker must not be linked")
                data = self.project._bytes(marker).decode("utf-8")
                try:
                    value = json.loads(data)
                except ValueError:
                    lines = data.splitlines()
                    if len(lines) != 2 or not lines[0].startswith("Verilog Work Bench ") or lines[1] != f"project={self.project.root}":
                        raise ProjectError(409, "build directory belongs to another project")
                else:
                    relative = value.get("project_relative") if isinstance(value, dict) else None
                    if (not isinstance(value, dict) or value.get("schema") != 1
                            or not isinstance(relative, str) or not relative or Path(relative).is_absolute()
                            or (build / relative).resolve() != self.project.root):
                        raise ProjectError(409, "invalid build ownership marker")
            except (UnicodeError, OSError) as exc:
                raise ProjectError(409, "cannot read build ownership marker") from exc
        else:
            if build.exists() and (not build.is_dir() or any(build.iterdir())):
                raise ProjectError(409, "build directory is not owned by vwb; choose an empty directory")
            # Persist every newly-created directory entry, not just the final
            # build's contents. Otherwise a crash could preserve source writes
            # while losing the ancestor entry that makes their journal reachable.
            missing, ancestor = [], build
            while not ancestor.exists():
                missing.append(ancestor)
                ancestor = ancestor.parent
            if ancestor != self.project.root:
                # A prior mkdir may have succeeded before its parent fsync
                # failed. Retrying must also persist that now-existing entry.
                _sync_directory(ancestor.parent)
            for directory in reversed(missing):
                self.project.path(directory.relative_to(self.project.root).as_posix())
                directory.mkdir()
                _sync_directory(directory.parent)
            self.project.path(self.project.build_dir)
            owner = {"schema": 1, "vwb_version": "editor-recovery", "project_relative": os.path.relpath(self.project.root, build)}
            self._write_private(marker, (json.dumps(owner) + "\n").encode("utf-8"))
            _sync_directory(build)

    def _recovery_directory(self, *, create=False):
        owner = {"schema": 1, "project": self.project.summary()["id"]}
        if not self._directory.exists() and not is_link(self._directory):
            if not create:
                return False
            self._directory.mkdir(mode=0o700)
            self._write_private(self._directory / _OWNER, json.dumps(owner).encode("utf-8"))
            _sync_directory(self._directory)
            _sync_directory(self.project.build_path)
        # Existing recovery data is usable only under the same validated VWB
        # build grant. This validation never adopts a nonempty unowned build.
        self._prepare_build()
        self._private_directory(self._directory)
        if json.loads(self._private_bytes(self._directory / _OWNER, 4096)) != owner:
            raise ProjectError(409, "edit recovery directory belongs to another project")
        if set(path.name for path in self._directory.iterdir()) - {_OWNER, "pending"}:
            raise ProjectError(409, "unexpected files in edit recovery directory")
        return True

    def _write_manifest(self, manifest):
        temporary = self._pending / "manifest.tmp"
        if temporary.exists() or is_link(temporary):
            self._private_bytes(temporary, _MAX_MANIFEST_BYTES)
            temporary.unlink()
        self._write_private(temporary, json.dumps(manifest).encode("utf-8"))
        os.replace(temporary, self._pending / "manifest.json")
        _sync_directory(self._pending)

    def _journal(self, items):
        self._prepare_build()
        self._recovery_directory(create=True)
        self._pending.mkdir(mode=0o700)
        metadata = []
        for index, item in enumerate(items):
            for field in ("before", "after"):
                self._write_private(self._pending / f"{index}.{field}", item[field].encode("utf-8"))
            metadata.append({key: item[key] for key in ("path", "before_revision", "after_revision", "mode", "edit_count")})
        manifest = {"schema": 1, "project": self.project.summary()["id"], "state": "pending", "files": metadata}
        self._write_manifest(manifest)
        _sync_directory(self._directory)
        return manifest

    def _load_journal(self):
        self._private_directory(self._pending)
        names = set(path.name for path in self._pending.iterdir())
        allowed = {"manifest.json", "manifest.tmp", *(f"{i}.{field}" for i in range(MAX_EDIT_FILES) for field in ("before", "after"))}
        if names - allowed:
            raise ProjectError(409, "unexpected files in edit journal")
        for name in names:
            self._private_bytes(self._pending / name, MAX_FILE_BYTES if name[0].isdigit() else _MAX_MANIFEST_BYTES)
        if "manifest.json" not in names:
            # Project writes start only after the complete manifest is fsynced.
            return {"state": "preparing", "files": []}
        manifest = json.loads(self._private_bytes(self._pending / "manifest.json", _MAX_MANIFEST_BYTES))
        if (not isinstance(manifest, dict) or manifest.get("schema") != 1
                or manifest.get("project") != self.project.summary()["id"]
                or manifest.get("state") not in {"pending", "restored", "committed"}
                or not isinstance(manifest.get("files"), list)
                or not 1 <= len(manifest["files"]) <= MAX_EDIT_FILES):
            raise ProjectError(409, "invalid edit recovery manifest")
        files, total, seen = [], 0, set()
        for index, item in enumerate(manifest["files"]):
            if (not isinstance(item, dict) or set(item) != {"path", "before_revision", "after_revision", "mode", "edit_count"}
                    or type(item["mode"]) is not int or not 0 <= item["mode"] <= 0o7777
                    or type(item["edit_count"]) is not int or not 1 <= item["edit_count"] <= 100000):
                raise ProjectError(409, "invalid edit recovery entry")
            target = self._target(item["path"])
            identity = os.path.normcase(str(target))
            if identity in seen:
                raise ProjectError(409, "duplicate edit recovery target")
            seen.add(identity)
            current = dict(item)
            for field in ("before", "after"):
                digest = item[field + "_revision"]
                if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                    raise ProjectError(409, "invalid edit recovery revision")
                if manifest["state"] == "pending":
                    data = self._private_bytes(self._pending / f"{index}.{field}", MAX_FILE_BYTES)
                    if revision(data) != digest:
                        raise ProjectError(409, "edit recovery backup failed revision check")
                    current[field] = data.decode("utf-8")
                    total += len(data)
            files.append(current)
        if total > MAX_EDIT_BYTES:
            raise ProjectError(413, "edit journal exceeds 8 MiB")
        return {**manifest, "files": files}

    def status(self):
        with self.project.lock:
            try:
                if not self._recovery_directory() or not (self._pending.exists() or is_link(self._pending)):
                    return {"recovery_required": False, "files": []}
                manifest = self._load_journal()
                return {"recovery_required": True, "files": [
                    {key: item[key] for key in ("path", "before_revision", "after_revision")}
                    for item in manifest["files"]], "message": "an interrupted edit requires explicit recovery"}
            except ProjectError as exc:
                return {"recovery_required": True, "files": [], "message": str(exc)}
            except (OSError, ValueError, UnicodeError):
                return {"recovery_required": True, "files": [], "message": "edit recovery journal is incomplete or unsafe"}

    def _stage(self, item, field):
        target = self._target(item["path"])
        descriptor, temporary = tempfile.mkstemp(prefix=".vwb-save-", dir=target.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(item[field].encode("utf-8"))
                handle.flush()
                os.chmod(temporary, item["mode"])
                os.fsync(handle.fileno())
            return Path(temporary)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def _replace_file(self, target, staged):
        """Separate hook for deterministic write-failure/race tests."""
        os.replace(staged, target)
        _sync_directory(target.parent)

    def _cleanup(self):
        # Validate every name and link before deleting any retained evidence.
        self._load_journal()
        for path in list(self._pending.iterdir()):
            if path.name != "manifest.json":
                path.unlink()
        (self._pending / "manifest.json").unlink(missing_ok=True)
        self._pending.rmdir()
        _sync_directory(self._directory)

    def _restore(self, items, *, best_effort=False):
        # Explicit recovery preflights the whole batch. Automatic rollback can
        # still restore its other writes while retaining an outside edit and
        # the journal containing the unresolved conflict.
        conflicts = set()
        for item in items:
            try:
                data, _ = self._source_bytes(item["path"])
                if revision(data) not in {item["before_revision"], item["after_revision"]}:
                    conflicts.add(item["path"])
            except ProjectError:
                conflicts.add(item["path"])
        if conflicts and not best_effort:
            raise ProjectError(409, "external changes prevent recovery; retained backups require resolution")
        for item in reversed(items):
            if item["path"] in conflicts:
                continue
            try:
                data, _ = self._source_bytes(item["path"])
                if revision(data) == item["before_revision"]:
                    continue
                staged = self._stage(item, "before")
                try:
                    self._check(item, "after_revision")
                    self._replace_file(self._target(item["path"]), staged)
                finally:
                    staged.unlink(missing_ok=True)
            except (ProjectError, OSError):
                if not best_effort:
                    raise
                conflicts.add(item["path"])
        if conflicts:
            raise ProjectError(409, "external changes prevent recovery; retained backups require resolution")
        for item in items:
            self._check(item, "before_revision")

    def _execute(self, items):
        for item in items:
            self._check(item, "before_revision")
        staged = []
        manifest = None
        try:
            for item in items:
                staged.append(self._stage(item, "after"))
            # Preflight the whole batch again before creating durable recovery.
            for item in items:
                self._check(item, "before_revision")
            manifest = self._journal(items)
            for item, temporary in zip(items, staged):
                self._check(item, "before_revision")
                self._replace_file(self._target(item["path"]), temporary)
            for item in items:
                self._check(item, "after_revision")
            manifest["state"] = "committed"
            self._write_manifest(manifest)
        except Exception as exc:
            if manifest is not None:
                try:
                    self._restore(items, best_effort=True)
                    manifest["state"] = "restored"
                    self._write_manifest(manifest)
                    self._cleanup()
                except Exception as rollback:
                    raise ProjectError(409, "edit interrupted; recovery required; backups retained") from rollback
            if isinstance(exc, ProjectError):
                raise
            raise ProjectError(500, "edit failed; project changes were rolled back") from exc
        finally:
            for temporary in staged:
                temporary.unlink(missing_ok=True)
        # A completed manifest makes cleanup restartable without reverting a
        # successfully committed batch or requiring already-deleted backups.
        try:
            self._cleanup()
        except (ProjectError, OSError, ValueError):
            pass
        return {"files": [{"path": item["path"], "revision": item["after_revision"], "content": item["after"]}
                          for item in items]}

    def apply(self, plan_id):
        with self.project.lock:
            self._assert_ready()
            plan = self._lookup(self._plans, plan_id)
            for item in plan["files"]:
                self._check(item, "before_revision")
            del self._plans[plan_id]
            result = self._execute(plan["files"])
            self._prune()
            undo_id = secrets.token_urlsafe(24)
            self._undos[undo_id] = {**plan, "expires_at": time.monotonic() + PLAN_LIFETIME}
            return {**result, "undo_id": undo_id}

    def undo(self, undo_id):
        with self.project.lock:
            self._assert_ready()
            plan = self._lookup(self._undos, undo_id)
            files = [{**item, "before": item["after"], "after": item["before"],
                      "before_revision": item["after_revision"], "after_revision": item["before_revision"]}
                     for item in plan["files"]]
            for item in files:
                self._check(item, "before_revision")
            del self._undos[undo_id]
            return self._execute(files)

    def recover(self):
        with self.project.lock:
            try:
                if not self._recovery_directory() or not (self._pending.exists() or is_link(self._pending)):
                    return {"files": [], "recovery_required": False}
                manifest = self._load_journal()
                if manifest["state"] == "pending":
                    self._restore(manifest["files"])
                    disk_manifest = {**manifest, "state": "restored", "files": [
                        {key: item[key] for key in ("path", "before_revision", "after_revision", "mode", "edit_count")}
                        for item in manifest["files"]]}
                    self._write_manifest(disk_manifest)
                files = [self.project.read(item["path"]) for item in manifest["files"]]
                self._cleanup()
                return {"files": [{key: item[key] for key in ("path", "revision", "content")} for item in files],
                        "recovery_required": False}
            except ProjectError:
                raise
            except (OSError, ValueError, UnicodeError) as exc:
                raise ProjectError(409, "edit recovery could not finish; backups retained") from exc
