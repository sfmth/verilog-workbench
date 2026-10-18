"""Bounded stdio LSP experiments; not a browser-facing RPC or edit executor.

The service command is supplied by trusted local configuration, never by an
HTTP client. Workspace edits are previewed in memory, with no writes to disk.
"""
from __future__ import annotations

from collections import deque
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
from urllib.parse import urlsplit
from urllib.request import url2pathname

from .process import _WindowsJob, _terminate_tree


class LspError(RuntimeError):
    pass


class LspResponseError(LspError):
    def __init__(self, error):
        self.error = error
        super().__init__(f"LSP {error.get('code')}: {error.get('message')}")


def read_message(stream, max_bytes=16 * 1024 * 1024):
    """Read byte-counted LSP framing, including partial pipe reads."""
    headers = {}
    total = 0
    while True:
        line = stream.readline(8193)
        if not line:
            raise EOFError("Language server closed stdout")
        total += len(line)
        if total > 8192 or not line.endswith(b"\r\n"):
            raise LspError("Malformed or oversized LSP headers")
        if line == b"\r\n":
            break
        try:
            key, value = line.decode("ascii").strip().split(":", 1)
        except (ValueError, UnicodeDecodeError) as exc:
            raise LspError("Malformed LSP header") from exc
        key = key.lower()
        if key in headers:
            raise LspError("Duplicate LSP header")
        headers[key] = value.strip()
    try:
        size = int(headers["content-length"])
    except (KeyError, ValueError) as exc:
        raise LspError("Missing/invalid Content-Length") from exc
    if not 0 < size <= max_bytes:
        raise LspError("LSP message exceeds size limit")
    chunks = bytearray()
    while len(chunks) < size:
        part = stream.read(size - len(chunks))
        if not part:
            raise EOFError("Truncated LSP body")
        chunks.extend(part)
    try:
        message = json.loads(chunks)
    except (UnicodeDecodeError, ValueError) as exc:
        raise LspError("Invalid LSP JSON") from exc
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        raise LspError("Expected JSON-RPC 2.0 object")
    return message


def encode_message(message):
    body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


class LspClient:
    """Single-request-at-a-time client with bounded wait and bounded logs.

    Responses to harmless standard server requests keep indexing moving.
    workspace/applyEdit is explicitly refused: edits require a user preview.
    """
    def __init__(self, command, root, timeout=15.0, configuration=None):
        if not command or timeout <= 0:
            raise ValueError("A local command and positive timeout are required")
        self.root = Path(root).resolve(strict=True)
        self.timeout = timeout
        self.configuration = configuration or {}
        self.messages = queue.Queue(maxsize=256)
        self.notifications = deque(maxlen=64)
        self.stderr = deque(maxlen=64)
        self._next_id = 0
        self._writes = queue.Queue(maxsize=16)
        self._write_failed = False
        self.initialized = False
        self.closed = False
        self._job = _WindowsJob() if os.name == "nt" else None
        kwargs = {"start_new_session": True} if self._job is None else {
            "creationflags": 0x00000004 | 0x00000200}
        try:
            self.process = subprocess.Popen(list(command), cwd=self.root,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                **kwargs)
            if self._job:
                self._job.assign_and_resume(self.process)
        except BaseException:
            if hasattr(self, "process"):
                self.process.kill()
                self.process.wait()
                for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                    stream.close()
            if self._job:
                self._job.close()
            raise
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._writer = threading.Thread(target=self._write_loop, daemon=True)
        self._writer.start()
        self._reader.start()
        self._stderr_reader.start()

    def _read_loop(self):
        try:
            while True:
                message = read_message(self.process.stdout)
                if self.messages.full():
                    raise LspError("Language server flooded response queue")
                self.messages.put_nowait(message)
        except (EOFError, LspError, OSError) as exc:
            try:
                self.messages.put_nowait(exc)
            except queue.Full:
                pass

    def _read_stderr(self):
        while True:
            line = self.process.stderr.readline(2048)
            if not line:
                break
            self.stderr.append(line.decode("utf-8", errors="replace").rstrip())

    def _write_loop(self):
        while True:
            task = self._writes.get()
            if task is None:
                return
            data, completed, errors = task
            try:
                self.process.stdin.write(data)
                self.process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                errors.append(exc)
            finally:
                completed.set()

    def _send(self, message, timeout=None):
        if self.closed:
            raise LspError("Language server client is closed")
        if self._write_failed:
            raise LspError("Language server write channel has failed")
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        data = encode_message(message)
        if len(data) > 16 * 1024 * 1024:
            raise LspError("Outgoing LSP message exceeds size limit")
        completed = threading.Event()
        errors = []
        try:
            self._writes.put((data, completed, errors), timeout=max(0, deadline - time.monotonic()))
            if not completed.wait(timeout=max(0, deadline - time.monotonic())):
                raise queue.Full
        except queue.Full:
            self._write_failed = True
            # Closing a Python buffered pipe can itself block behind write().
            # Kill its owned reader first, so the writer unblocks before close.
            _terminate_tree(self.process, self._job)
            raise TimeoutError("Timed out writing to language server") from None
        if errors:
            self._write_failed = True
            raise LspError("Language server closed stdin") from errors[0]

    def notify(self, method, params=None):
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _server_request(self, message, timeout=None):
        method = message["method"]
        if method == "workspace/configuration":
            result = []
            for item in message.get("params", {}).get("items", []):
                value = self.configuration
                for part in item.get("section", "").split("."):
                    value = value.get(part, {}) if isinstance(value, dict) else {}
                result.append(value)
        elif method == "workspace/workspaceFolders":
            result = [{"uri": self.root.as_uri(), "name": self.root.name}]
        elif method in ("client/registerCapability", "client/unregisterCapability",
                        "window/workDoneProgress/create"):
            result = None
        elif method == "workspace/applyEdit":
            result = {"applied": False, "failureReason": "Preview-only M0 client"}
        else:
            self._send({"jsonrpc": "2.0", "id": message["id"], "error": {
                "code": -32601, "message": "Client method not supported"}}, timeout=timeout)
            return
        self._send({"jsonrpc": "2.0", "id": message["id"], "result": result}, timeout=timeout)

    def request(self, method, params=None, timeout=None):
        self._next_id += 1
        request_id = self._next_id
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        self._send({"jsonrpc": "2.0", "id": request_id,
                    "method": method, "params": params or {}}, timeout=max(0, deadline - time.monotonic()))
        while True:
            remaining = deadline - time.monotonic()
            try:
                if remaining <= 0:
                    raise queue.Empty
                message = self.messages.get(timeout=remaining)
            except queue.Empty:
                self.notify("$/cancelRequest", {"id": request_id})
                raise TimeoutError(f"Timed out waiting for {method}") from None
            if isinstance(message, Exception):
                raise LspError(str(message)) from message
            if "method" in message:
                if "id" in message:
                    self._server_request(message, timeout=max(0, deadline - time.monotonic()))
                else:
                    self.notifications.append(message)
            elif message.get("id") == request_id:
                if "error" in message:
                    raise LspResponseError(message["error"])
                return message.get("result")
            # Late responses to cancelled requests are intentionally discarded.

    def initialize(self, options=None):
        result = self.request("initialize", {
            "processId": os.getpid(), "rootUri": self.root.as_uri(),
            "workspaceFolders": [{"uri": self.root.as_uri(), "name": self.root.name}],
            "clientInfo": {"name": "vwb-m0-probe", "version": "0.1"},
            "capabilities": {
                "general": {"positionEncodings": ["utf-16"]},
                "workspace": {"configuration": True, "workspaceFolders": True,
                    "workspaceEdit": {"documentChanges": True}},
                "textDocument": {"definition": {"linkSupport": True},
                    "completion": {"completionItem": {"snippetSupport": True}},
                    "rename": {"prepareSupport": True}}},
            "initializationOptions": options or {}})
        encoding = result.get("capabilities", {}).get("positionEncoding", "utf-16")
        if encoding != "utf-16":
            raise LspError(f"Unsupported negotiated position encoding: {encoding}")
        self.notify("initialized")
        self.initialized = True
        return result

    def open_document(self, path, language_id, version=1):
        path = Path(path).resolve(strict=True)
        path.relative_to(self.root)
        self.notify("textDocument/didOpen", {"textDocument": {
            "uri": path.as_uri(), "languageId": language_id, "version": version,
            "text": path.read_text(encoding="utf-8")}})

    def close(self):
        if self.closed:
            return
        try:
            if self.initialized and self.process.poll() is None:
                self.request("shutdown", timeout=min(self.timeout, 2.0))
                self.notify("exit")
                self.process.wait(timeout=2)
        except (LspError, TimeoutError, subprocess.TimeoutExpired):
            pass
        finally:
            self.closed = True
            # A leader can exit while its descendants retain stdout. Reap the
            # owned tree on graceful exit too, using native platform adapters.
            _terminate_tree(self.process, self._job)
            if self._job:
                self._job.close()
            self._writes.put_nowait(None)
            self._writer.join(timeout=1)
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                try:
                    stream.close()
                except BrokenPipeError:
                    pass
            self._reader.join(timeout=1)
            self._stderr_reader.join(timeout=1)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def snapshot_files(root):
    """Capture only files in a trusted fixture before requesting rename."""
    root = Path(root).resolve(strict=True)
    return {p.resolve().as_uri(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _offset(text, position):
    """Translate LSP UTF-16 positions without accepting half-surrogate offsets."""
    line, character = position["line"], position["character"]
    if type(line) is not int or type(character) is not int or min(line, character) < 0:
        raise LspError("Invalid edit position")
    lines = text.splitlines(keepends=True)
    if line == len(lines) and character == 0 and (not text or text.endswith(("\n", "\r"))):
        return len(text)
    if line >= len(lines):
        raise LspError("Edit line outside document")
    segment = lines[line].rstrip("\r\n")
    units = 0
    for index, char in enumerate(segment):
        if units == character:
            return sum(map(len, lines[:line])) + index
        units += len(char.encode("utf-16-le")) // 2
    if units == character:
        return sum(map(len, lines[:line])) + len(segment)
    raise LspError("Edit character outside document or within surrogate pair")


def preview_workspace_edit(root, edit, snapshots, document_versions=None):
    """Validate a proposed edit against disk snapshots and return changed text.

    Reject resource operations, external/symlink paths, stale bytes/versions and
    overlapping edits. This is a preview boundary, not a semantic collision
    detector or a transaction implementation. No files are written.
    """
    root = Path(root).resolve(strict=True)
    if not isinstance(edit, dict):
        raise LspError("No workspace edit returned")
    if edit.get("changeAnnotations"):
        raise LspError("Annotated edits need an interactive approval UI")
    groups = []
    for uri, edits in edit.get("changes", {}).items():
        groups.append((uri, None, edits))
    for change in edit.get("documentChanges", []):
        if "textDocument" not in change:
            raise LspError("File resource operations are not supported in M0")
        doc = change["textDocument"]
        groups.append((doc["uri"], doc.get("version"), change["edits"]))
    if not groups:
        raise LspError("Empty workspace edit")
    output = {}
    for uri, version, edits in groups:
        parsed = urlsplit(uri)
        if parsed.scheme != "file" or parsed.netloc not in ("", "localhost") or parsed.query or parsed.fragment:
            raise LspError("Edit URI is not a local file")
        path = Path(url2pathname(parsed.path)).resolve(strict=True)
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise LspError("Edit escapes authorized project") from exc
        canonical = path.as_uri()
        if canonical in output:
            raise LspError("Duplicate document edit")
        expected = snapshots.get(canonical)
        if expected is None or path.read_bytes() != expected:
            raise LspError("External edit conflict or unknown file")
        if version is not None and (document_versions or {}).get(canonical) != version:
            raise LspError("Open document version conflict")
        text = expected.decode("utf-8")
        ranges = []
        for item in edits:
            if "annotationId" in item:
                raise LspError("Annotated edit needs preview confirmation")
            start = _offset(text, item["range"]["start"])
            end = _offset(text, item["range"]["end"])
            if end < start or not isinstance(item["newText"], str):
                raise LspError("Invalid text edit")
            ranges.append((start, end, item["newText"]))
        ranges.sort()
        for previous, current in zip(ranges, ranges[1:]):
            if current[0] < previous[1] or current[0] == previous[0]:
                raise LspError("Overlapping text edits")
        for start, end, replacement in reversed(ranges):
            text = text[:start] + replacement + text[end:]
        output[canonical] = {"text": text,
            "before_sha256": hashlib.sha256(expected).hexdigest(),
            "edit_count": len(ranges)}
    return output


def semantic_references_with_definition(root, snapshots, references, definitions):
    """Include one independently resolved semantic definition in references.

    Some HDL servers ignore includeDeclaration. This probe-only adapter never
    searches text for a definition. It rejects ambiguous, out-of-root, unknown,
    and invalid locations, then deduplicates exact LSP ranges. It cannot prove
    semantic correctness of arbitrary upstream responses by itself.
    """
    root = Path(root).resolve(strict=True)
    def normalize(location):
        uri = location.get("uri", location.get("targetUri"))
        region = location.get("range", location.get("targetSelectionRange"))
        parsed = urlsplit(uri)
        if parsed.scheme != "file" or parsed.netloc not in ("", "localhost") or parsed.query or parsed.fragment:
            raise LspError("Reference URI is not a local file")
        path = Path(url2pathname(parsed.path)).resolve(strict=True)
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise LspError("Reference escapes authorized project") from exc
        canonical = path.as_uri()
        if canonical not in snapshots or path.read_bytes() != snapshots[canonical]:
            raise LspError("Unknown or stale semantic reference file")
        text = snapshots[canonical].decode("utf-8")
        if _offset(text, region["end"]) <= _offset(text, region["start"]):
            raise LspError("Empty or inverted semantic reference range")
        return {"uri": canonical, "range": region}
    def key(location):
        region = location["range"]
        return (location["uri"], region["start"]["line"], region["start"]["character"],
                region["end"]["line"], region["end"]["character"])
    if isinstance(definitions, dict):
        definitions = [definitions]
    resolved = {key(loc): loc for loc in (normalize(x) for x in definitions or [])}
    if len(resolved) != 1:
        raise LspError("Definition is missing or ambiguous; cannot merge references")
    resolved.update({key(loc): loc for loc in (normalize(x) for x in references or [])})
    return list(resolved.values())
