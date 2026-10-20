"""Bounded semantic editor queries against locally configured stdio LSP servers.

HTTP callers provide document snapshots, never process commands or server options.
Every query owns fresh language-server processes; dirty text exists only in those
processes. Rename returns a validated in-memory preview and never writes files.
"""
from __future__ import annotations

import hashlib
import json
import keyword
import os
from pathlib import Path
import re
import shutil
import threading
import time
from urllib.parse import urlsplit
from urllib.request import url2pathname

from .lsp import LspClient, LspError, _offset, preview_workspace_edit
from .projects import MAX_FILE_BYTES, MAX_FILES, MAX_SNAPSHOT_BYTES, ProjectError, is_link, revision

MAX_DOCUMENTS = 64
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_RESULTS = 200
MAX_COMPLETION_CANDIDATES = 10000
MAX_TEXT = 16384
TIMEOUT = 8.0
EXTENSIONS = {".sv": "systemverilog", ".svh": "systemverilog", ".v": "systemverilog",
              ".vh": "systemverilog", ".vhd": "vhdl", ".vhdl": "vhdl",
              ".py": "python", ".pyi": "python"}
CONFIG_NAMES = {".vwb.json", "slang.json", ".slang", "slang.f", "verible.filelist",
                "vhdl_ls.toml", "pyrightconfig.json", "basedpyrightconfig.json", "pyproject.toml"}
DEFAULTS = {"systemverilog": ["slang-server"], "sv_refactor": ["verible-verilog-ls"],
            "vhdl": ["vhdl_ls"], "python": ["basedpyright-langserver", "--stdio"]}
METHODS = {"completion": "completionProvider", "hover": "hoverProvider",
           "definition": "definitionProvider", "references": "referencesProvider",
           "rename": "renameProvider"}


def _provided(value):
    return value is True or isinstance(value, dict)


def _error(message, status=400):
    raise ProjectError(status, message)


def _position(value):
    if (not isinstance(value, dict) or set(value) != {"line", "character"}
            or any(type(value[k]) is not int or not 0 <= value[k] <= 2147483647
                   for k in ("line", "character"))):
        _error("position must contain nonnegative UTF-16 line and character integers")
    return {k: value[k] for k in ("line", "character")}


def _range(text, value):
    if not isinstance(value, dict):
        raise LspError("Language server returned an invalid range")
    start, end = _position(value.get("start")), _position(value.get("end"))
    if _offset(text, end) < _offset(text, start):
        raise LspError("Language server returned an inverted range")
    return {"start": start, "end": end}


def _plain(value):
    """Return plain text only. Consumers must use textContent, never innerHTML."""
    if isinstance(value, str):
        return re.sub(r"<[^>]*>", "", value)[:MAX_TEXT]
    if isinstance(value, list):
        return "\n\n".join(filter(None, (_plain(x) for x in value[:32])))[:MAX_TEXT]
    if isinstance(value, dict):
        return _plain(value.get("value", ""))
    return ""


def _snippet(value):
    """Flatten supported LSP snippets to text, refusing variables/nested syntax."""
    if not isinstance(value, str) or len(value) > MAX_TEXT:
        raise LspError("Oversized or invalid completion insertion")
    output, cursor = [], 0
    while cursor < len(value):
        char = value[cursor]
        if char == "\\" and cursor + 1 < len(value) and value[cursor + 1] in "\\$},|":
            output.append(value[cursor + 1]); cursor += 2; continue
        if char != "$":
            output.append(char); cursor += 1; continue
        match = re.match(r"\$(?:\d+|\{\d+(?::([^{}]*?)|\|([^{}]*?)\|)?\})", value[cursor:])
        if not match:
            raise LspError("Completion snippet uses unsupported variables or nested placeholders")
        default, choices = match.groups()
        output.append(default if default is not None else choices.split(",")[0] if choices else "")
        cursor += len(match.group(0))
    return "".join(output)


class EditorService:
    def __init__(self, project, config=None):
        self.project = project
        self._lock = threading.Lock()
        self._closed = False
        if config is not None and not isinstance(config, dict):
            raise ValueError("editor configuration must be a local language mapping")
        if config and set(config) - set(DEFAULTS):
            raise ValueError("unknown language in local editor configuration")
        self.config = {}
        for language, default in DEFAULTS.items():
            entry = (config or {}).get(language, {"command": default})
            if entry is None or entry is False:
                continue
            if not isinstance(entry, dict) or set(entry) - {"command", "initialization_options", "configuration"}:
                raise ValueError("invalid local language-server configuration")
            command = entry.get("command", default)
            if (not isinstance(command, list) or not command
                    or any(not isinstance(x, str) or not x or "\0" in x for x in command)):
                raise ValueError("local language-server command must be a nonempty argv array")
            for key in ("initialization_options", "configuration"):
                if not isinstance(entry.get(key, {}), dict):
                    raise ValueError(f"{key} must be an object")
            # Resolve executable once from trusted local settings; browser payload
            # fields cannot affect this selection.
            executable = shutil.which(command[0])
            self.config[language] = {**entry, "command": [executable, *command[1:]] if executable else None}

    def capabilities(self):
        languages = {}
        for language in ("systemverilog", "vhdl", "python"):
            available = bool(self.config.get(language, {}).get("command"))
            operations = {name: {"available": available,
                "reason": "Server capabilities are checked on each query" if available
                          else "Local language server is not installed or configured"}
                for name in METHODS}
            if language == "systemverilog":
                secondary = bool(self.config.get("sv_refactor", {}).get("command"))
                operations["rename"] = {"available": available and secondary,
                    "reason": "Requires per-query slang/Verible definition and reference agreement"
                              if available and secondary else
                              "SystemVerilog rename requires both slang-server and Verible; slang rename is disabled"}
            languages[language] = {"available": available, "operations": operations,
                                   "server": Path(self.config[language]["command"][0]).name if available else None,
                                   "semantic_status": "unproved for this project until queried"}
        return {"languages": languages, "position_encoding": "utf-16",
                "source": "local-language-servers", "limits": {"documents": MAX_DOCUMENTS,
                "file_bytes": MAX_FILE_BYTES, "document_bytes": MAX_DOCUMENT_BYTES,
                "results": MAX_RESULTS, "timeout_seconds": TIMEOUT}}

    def _path(self, value):
        target = self.project.path(value)
        if target == self.project.build_path or self.project.build_path in target.parents:
            _error("generated artifacts cannot be queried or edited", 403)
        return target

    def _documents(self, payload):
        documents = payload.get("documents")
        if not isinstance(documents, list) or not 1 <= len(documents) <= MAX_DOCUMENTS:
            _error("documents must contain between 1 and 64 snapshots")
        output, total = {}, 0
        for item in documents:
            if not isinstance(item, dict) or set(item) != {"path", "content", "revision", "version"}:
                _error("each document requires path, content, revision and version")
            path = self._path(item["path"])
            relative = path.relative_to(self.project.root).as_posix()
            if relative in output:
                _error("duplicate document path")
            content, expected, version = item["content"], item["revision"], item["version"]
            if not isinstance(content, str):
                _error("document content must be UTF-8 text")
            if type(version) is not int or not 1 <= version <= 2147483647:
                _error("document version must be a positive integer")
            if expected is not None and (not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected)):
                _error("document revision must be a SHA-256 digest or null for a new file")
            try:
                encoded = content.encode("utf-8")
            except UnicodeError:
                _error("document content must be valid UTF-8")
            total += len(encoded)
            if len(encoded) > MAX_FILE_BYTES or total > MAX_DOCUMENT_BYTES:
                _error("editor document snapshot exceeds byte limit", 413)
            disk = self.project._bytes(path) if path.exists() else None
            if not path.parent.is_dir():
                _error("document parent directory does not exist", 404)
            if (revision(disk) if disk is not None else None) != expected:
                _error("document changed on disk; reload before querying", 409)
            output[relative] = {**item, "path": relative, "uri": path.as_uri(), "disk": disk}
        return output

    def _capture(self, documents, deadline=None):
        """Capture real source context; no invented file lists or project changes."""
        snapshots, total, count = {}, 0, 0
        excluded = {".git", ".m0-tools", ".venv", "venv", "node_modules", "__pycache__",
                    ".pytest_cache", ".tox", ".mypy_cache", ".ruff_cache"}
        for directory, dirs, names in os.walk(self.project.root, followlinks=False):
            dirs[:] = sorted(name for name in dirs if name.casefold() not in excluded
                             and not is_link(Path(directory) / name)
                             and Path(directory) / name != self.project.build_path)
            for name in sorted(names):
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError("semantic editor source snapshot timed out")
                path = Path(directory) / name
                if name.casefold() == ".git" or name.startswith(".vwb-save-") or is_link(path) or not path.is_file():
                    continue
                count += 1
                if count > MAX_FILES:
                    _error("editor project inventory exceeds file limit", 413)
                if path.suffix.lower() not in EXTENSIONS and name not in CONFIG_NAMES:
                    continue
                relative = path.relative_to(self.project.root).as_posix()
                path = self._path(relative)
                data = self.project._bytes(path)
                total += len(data)
                if total > MAX_SNAPSHOT_BYTES:
                    _error("editor project source snapshot exceeds limit", 413)
                try:
                    data.decode("utf-8")
                except UnicodeError:
                    _error("editor source context must be UTF-8", 415)
                snapshots[path.as_uri()] = data
        digest = hashlib.sha256()
        for uri, data in sorted(snapshots.items()):
            digest.update(uri.encode("utf-8") + b"\0" + hashlib.sha256(data).digest())
        # Non-source browser tabs are revision-checked, but cannot alter the
        # public semantic-context fingerprint used for preview/apply checks.
        for doc in documents.values():
            if doc["disk"] is not None:
                snapshots[doc["uri"]] = doc["disk"]
        return snapshots, digest.hexdigest()

    def project_fingerprint(self):
        """Hash current source/config context for preview/apply freshness checks."""
        with self.project.lock:
            return self._capture({})[1]

    def _location(self, value, texts):
        if not isinstance(value, dict):
            raise LspError("Language server returned an invalid location")
        uri = value.get("uri", value.get("targetUri"))
        if not isinstance(uri, str):
            raise LspError("Language server returned an invalid URI")
        parsed = urlsplit(uri)
        if parsed.scheme != "file" or parsed.netloc not in ("", "localhost") or parsed.query or parsed.fragment:
            raise LspError("Language server returned a nonlocal URI")
        path = Path(url2pathname(parsed.path))
        try:
            relative = path.relative_to(self.project.root).as_posix()
        except ValueError:
            raise LspError("Language server returned a path outside the project") from None
        checked = self._path(relative)
        canonical = checked.as_uri()
        if canonical not in texts:
            raise LspError("Language server returned an unknown source file")
        region = value.get("range", value.get("targetSelectionRange"))
        return {"path": checked.relative_to(self.project.root).as_posix(),
                "range": _range(texts[canonical], region)}

    def _locations(self, values, texts, *, bounded=True):
        if values is None:
            values = []
        if isinstance(values, dict):
            values = [values]
        if not isinstance(values, list) or len(values) > 10000:
            raise LspError("Language server returned invalid or excessive locations")
        unique = {json.dumps(loc, sort_keys=True): loc for loc in
                  (self._location(value, texts) for value in values)}
        output = [unique[key] for key in sorted(unique)]
        return {"locations": output[:MAX_RESULTS] if bounded else output,
                "truncated": bounded and len(output) > MAX_RESULTS}

    def _complete(self, result, active, position):
        # LSP label/insertText-only items inherit the editor's current word
        # range. Compute it in Unicode code points, then return UTF-16 units.
        offset = _offset(active["content"], position)
        left = right = offset
        def word(char):
            return char == "$" or char == "_" or char.isalnum() or ("a" + char).isidentifier()
        while left > 0 and word(active["content"][left - 1]):
            left -= 1
        while right < len(active["content"]) and word(active["content"][right]):
            right += 1
        line_start = max(active["content"].rfind("\n", 0, offset),
                         active["content"].rfind("\r", 0, offset)) + 1
        fallback = {"start": {"line": position["line"], "character": len(active["content"][line_start:left].encode("utf-16-le")) // 2},
                    "end": {"line": position["line"], "character": len(active["content"][line_start:right].encode("utf-16-le")) // 2}}
        values = result.get("items", []) if isinstance(result, dict) else result or []
        if not isinstance(values, list):
            raise LspError("Language server returned invalid completion items")
        defaults = result.get("itemDefaults", {}) if isinstance(result, dict) else {}
        if not isinstance(defaults, dict):
            defaults = {}
        if len(values) > MAX_COMPLETION_CANDIDATES:
            raise LspError("Language server completion inventory exceeds 10000 items")
        # Some servers (notably vhdl_ls) return their complete symbol inventory
        # and expect the editor to filter it. Never truncate that inventory
        # before selecting candidates for the actual identifier prefix.
        insensitive = EXTENSIONS.get(Path(active["path"]).suffix.lower()) == "vhdl"
        fold = str.casefold if insensitive else lambda value: value
        prefix = fold(active["content"][left:offset])
        current_word = fold(active["content"][left:right])
        candidates = []
        for index, item in enumerate(values):
            if not isinstance(item, dict) or not isinstance(item.get("label"), str):
                continue
            label = item["label"][:1024]
            filtering = item.get("filterText", label)
            if not isinstance(filtering, str):
                filtering = label
            names = (fold(filtering[:1024]), fold(label))
            if prefix and not any(name.startswith(prefix) for name in names):
                continue
            sorting = item.get("sortText", label)
            sorting = sorting[:1024] if isinstance(sorting, str) else label
            rank = (0 if current_word and current_word in names else 1,
                    0 if prefix and prefix in names else 1, fold(sorting), fold(label), index)
            candidates.append((rank, item))
        candidates.sort(key=lambda entry: entry[0])
        items = []
        for _, item in candidates:
            if len(items) >= MAX_RESULTS:
                break
            if not isinstance(item, dict) or not isinstance(item.get("label"), str):
                continue
            try:
                edit = item.get("textEdit", {})
                if not isinstance(edit, dict):
                    continue
                region = edit.get("range", edit.get("replace", defaults.get("editRange")))
                if isinstance(region, dict) and "replace" in region:
                    region = region["replace"]
                region = _range(active["content"], region or fallback)
                insertion = edit.get("newText", item.get("textEditText", item.get("insertText", item["label"])))
                if not isinstance(insertion, str) or len(insertion) > MAX_TEXT:
                    continue
                snippet = item.get("insertTextFormat", defaults.get("insertTextFormat")) == 2
                if snippet:
                    insertion = _snippet(insertion)
                additional = item.get("additionalTextEdits", [])
                if not isinstance(additional, list) or len(additional) > 32:
                    continue
                edits = []
                offsets = [(_offset(active["content"], region["start"]), _offset(active["content"], region["end"]))]
                for extra in additional:
                    extra_range = _range(active["content"], extra["range"])
                    if not isinstance(extra.get("newText"), str) or len(extra["newText"]) > MAX_TEXT:
                        raise LspError("Invalid additional completion edit")
                    offsets.append((_offset(active["content"], extra_range["start"]), _offset(active["content"], extra_range["end"])))
                    edits.append({"range": extra_range, "new_text": extra["newText"]})
                offsets.sort()
                if any(b[0] < a[1] or b[0] == a[0] for a, b in zip(offsets, offsets[1:])):
                    raise LspError("Overlapping completion edits")
                items.append({"label": item["label"][:1024], "detail": _plain(item.get("detail", "")),
                              "insert_text": insertion, "range": region, "additional_edits": edits,
                              "snippet_flattened": snippet, "insert_text_format": 1})
            except (LspError, ProjectError, KeyError, TypeError, ValueError):
                continue  # Unsupported snippets/edits cannot be safely offered.
        return {"items": items, "is_incomplete": bool(result.get("isIncomplete")) if isinstance(result, dict) else False,
                "truncated": len(candidates) > MAX_RESULTS}

    def _diagnostics(self, client, documents, texts):
        output = []
        by_uri = {doc["uri"]: doc for doc in documents.values()}
        for note in client.notifications:
            if note.get("method") != "textDocument/publishDiagnostics":
                continue
            params = note.get("params", {})
            doc = by_uri.get(params.get("uri"))
            # Unversioned diagnostics cannot prove that they describe dirty text.
            if not doc or params.get("version") != doc["version"]:
                continue
            items = []
            diagnostics = params.get("diagnostics", [])
            if not isinstance(diagnostics, list):
                continue
            for item in diagnostics[:MAX_RESULTS]:
                try:
                    location = self._location({"uri": doc["uri"], "range": item["range"]}, texts)
                    items.append({"range": location["range"], "message": _plain(item.get("message", "")),
                                  "severity": item.get("severity") if type(item.get("severity")) is int else None,
                                  "code": str(item.get("code", ""))[:128]})
                except (LspError, ProjectError, KeyError, TypeError):
                    continue
            output.append({"path": doc["path"], "version": doc["version"], "items": items})
        # At most one current diagnostic set per document.
        return list({item["path"]: item for item in output}.values())[:MAX_DOCUMENTS]

    def query(self, payload):
        if not self._lock.acquire(blocking=False):
            _error("another semantic editor query is in progress", 429)
        clients = []
        deadline = time.monotonic() + TIMEOUT - 0.75
        def remaining():
            value = deadline - time.monotonic()
            if value <= 0:
                raise TimeoutError("semantic editor query exceeded 8 second limit")
            return value
        try:
            if self._closed:
                _error("editor service is closed", 503)
            if not isinstance(payload, dict) or set(payload) - {"operation", "path", "position", "documents", "new_name"}:
                _error("unsupported editor query fields")
            operation = payload.get("operation")
            if operation not in METHODS:
                _error("unsupported editor operation")
            active_path = self._path(payload.get("path"))
            language = EXTENSIONS.get(active_path.suffix.lower())
            if language is None:
                _error("no semantic editor configured for this file type", 422)
            available = self.capabilities()["languages"][language]["operations"][operation]
            if not available["available"]:
                _error(available["reason"], 503)
            position = _position(payload.get("position"))
            with self.project.lock:
                documents = self._documents(payload)
                relative = active_path.relative_to(self.project.root).as_posix()
                if relative not in documents:
                    _error("the active path must have a document snapshot")
                active = documents[relative]
                _offset(active["content"], position)
                if operation == "rename" and any(doc["disk"] is None or doc["content"].encode("utf-8") != doc["disk"]
                                                  for doc in documents.values()):
                    _error("save all documents before semantic rename", 409)
                snapshots, fingerprint = self._capture(documents, deadline)
                source_revision = self.project.source_revision()
            texts = {uri: data.decode("utf-8") for uri, data in snapshots.items()}
            texts.update({doc["uri"]: doc["content"] for doc in documents.values()})
            versions = {uri: 1 for uri in texts}
            versions.update({doc["uri"]: doc["version"] for doc in documents.values()})
            def start(route):
                entry = self.config[route]
                client = LspClient(entry["command"], self.project.root, timeout=remaining(),
                                   configuration=entry.get("configuration"))
                clients.append(client)
                initialized = client.initialize(entry.get("initialization_options"))
                capabilities = initialized.get("capabilities", {})
                client.timeout = remaining()
                # Open every bounded source file in the relevant language so
                # multi-file project context does not depend on browser tabs.
                for uri, text in texts.items():
                    if EXTENSIONS.get(Path(url2pathname(urlsplit(uri).path)).suffix.lower()) != language:
                        continue
                    client.timeout = remaining()
                    client.notify("textDocument/didOpen", {"textDocument": {"uri": uri,
                        "languageId": language, "version": versions.get(uri, 1), "text": text}})
                return client, capabilities
            def request(client, method, arguments):
                client.timeout = remaining()
                return client.request(method, arguments, timeout=remaining())
            primary, capabilities = start(language)
            if not _provided(capabilities.get(METHODS[operation])):
                # The SystemVerilog rename path intentionally never asks slang
                # to rename, so only the alternate server needs renameProvider.
                if not (operation == "rename" and language == "systemverilog"):
                    _error("language server does not advertise this operation", 422)
            params = {"textDocument": {"uri": active["uri"]}, "position": position}
            if operation == "completion":
                result = self._complete(request(primary, "textDocument/completion", {**params,
                    "context": {"triggerKind": 1}}), active, position)
            elif operation == "hover":
                value = request(primary, "textDocument/hover", params)
                if value is not None and not isinstance(value, dict):
                    raise LspError("Language server returned invalid hover content")
                result = {"text": _plain((value or {}).get("contents", ""))}
                if value and value.get("range"):
                    result["range"] = _range(active["content"], value["range"])
            elif operation in ("definition", "references"):
                arguments = {**params, **({"context": {"includeDeclaration": True}} if operation == "references" else {})}
                locations = request(primary, "textDocument/" + operation, arguments)
                if operation == "references" and language == "systemverilog":
                    definitions = self._locations(request(primary, "textDocument/definition", params), texts,
                                                  bounded=False)["locations"]
                    if len(definitions) != 1:
                        _error("semantic definition is missing or ambiguous; cannot include declaration", 422)
                    references = self._locations(locations, texts, bounded=False)["locations"]
                    # Slang omits the declaration even with includeDeclaration.
                    # Merge the independently resolved semantic definition only.
                    combined = {json.dumps(loc, sort_keys=True): loc for loc in references + definitions}
                    values = [combined[key] for key in sorted(combined)]
                    result = {"locations": values[:MAX_RESULTS], "truncated": len(values) > MAX_RESULTS}
                else:
                    result = self._locations(locations, texts)
            else:
                new_name = payload.get("new_name")
                valid = isinstance(new_name, str) and len(new_name) <= 128
                if valid:
                    valid = (new_name.isidentifier() and not keyword.iskeyword(new_name)) if language == "python" else bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", new_name))
                    if language == "vhdl":
                        valid = bool(re.fullmatch(r"[A-Za-z](?:[A-Za-z0-9]|_(?=[A-Za-z0-9]))*", new_name))
                if not valid:
                    _error("new_name must be a valid unescaped language identifier")
                rename_client, rename_capabilities = primary, capabilities
                agreed = None
                if language == "systemverilog":
                    rename_client, rename_capabilities = start("sv_refactor")
                    definitions = [self._locations(request(c, "textDocument/definition", params), texts, bounded=False)["locations"]
                                   for c in (primary, rename_client)]
                    if len(definitions[0]) != 1 or definitions[0] != definitions[1]:
                        _error("slang and Verible disagree on the semantic definition; rename disabled", 422)
                    refs = [self._locations(request(c, "textDocument/references", {**params,
                            "context": {"includeDeclaration": True}}), texts, bounded=False)["locations"]
                            for c in (primary, rename_client)]
                    if refs[0] != refs[1]:
                        _error("slang and Verible disagree on semantic references; rename disabled", 422)
                    agreed = {json.dumps(loc, sort_keys=True) for loc in refs[0] + definitions[0]}
                if not _provided(rename_capabilities.get("renameProvider")):
                    _error("language server does not advertise semantic rename", 422)
                provider = rename_capabilities["renameProvider"]
                if isinstance(provider, dict) and provider.get("prepareProvider"):
                    prepared = request(rename_client, "textDocument/prepareRename", params)
                    if prepared is None:
                        _error("language server rejected rename at this position", 422)
                    if isinstance(prepared, dict) and not prepared.get("defaultBehavior"):
                        _range(active["content"], prepared.get("range", prepared))
                # A hierarchical document-symbol result can prove a same-scope
                # collision. Reject that evidence rather than relying on a
                # rename server that may merely generate identifier edits.
                if _provided(capabilities.get("documentSymbolProvider")) and _provided(capabilities.get("definitionProvider")):
                    targets = self._locations(request(primary, "textDocument/definition", params), texts,
                                              bounded=False)["locations"]
                    if len(targets) == 1:
                        target = targets[0]
                        target_uri = (self.project.root / target["path"]).as_uri()
                        symbols = request(primary, "textDocument/documentSymbol", {"textDocument": {"uri": target_uri}})
                        def find_siblings(nodes):
                            if not isinstance(nodes, list) or len(nodes) > 10000:
                                return None
                            for node in nodes:
                                if not isinstance(node, dict):
                                    continue
                                if node.get("selectionRange") == target["range"]:
                                    return nodes, node
                                found = find_siblings(node.get("children", []))
                                if found:
                                    return found
                            return None
                        found = find_siblings(symbols)
                        if found:
                            siblings, original = found
                            fold = str.casefold if language == "vhdl" else lambda value: value
                            if any(node is not original and isinstance(node.get("name"), str)
                                   and fold(node["name"]) == fold(new_name) for node in siblings):
                                _error("rename would collide with an existing symbol in the same scope", 422)
                edit = request(rename_client, "textDocument/rename", {**params, "newName": new_name})
                # Normalize every edit URI through the project path boundary
                # BEFORE preview_workspace_edit resolves symlinks.
                changes = []
                if isinstance(edit, dict):
                    changes += list(edit.get("changes", {}).items())
                    for change in edit.get("documentChanges", []):
                        if not isinstance(change, dict) or "textDocument" not in change:
                            raise LspError("Rename file resource operations are not supported")
                        changes.append((change["textDocument"]["uri"], change["edits"]))
                for uri, edits in changes:
                    for item in edits:
                        location = self._location({"uri": uri, "range": item["range"]}, texts)
                        if not isinstance(item.get("newText"), str) or item["newText"] != new_name:
                            raise LspError("Rename must contain only explicit identifier replacements")
                        if agreed is not None and json.dumps(location, sort_keys=True) not in agreed:
                            raise LspError("Verible rename contains edits outside agreed semantic references")
                proposed = preview_workspace_edit(self.project.root, edit, snapshots, versions)
                if agreed is not None:
                    actual = {json.dumps(self._location({"uri": uri, "range": item["range"]}, texts), sort_keys=True)
                              for uri, edits in changes for item in edits}
                    if actual != agreed:
                        raise LspError("Verible rename does not cover all agreed semantic references")
                files = [{"path": Path(url2pathname(urlsplit(uri).path)).relative_to(self.project.root).as_posix(),
                          "content": value["text"], "revision": value["before_sha256"],
                          "edit_count": value["edit_count"]} for uri, value in sorted(proposed.items())]
                if (len(files) > MAX_DOCUMENTS or any(len(f["content"].encode("utf-8")) > MAX_FILE_BYTES for f in files)
                        or sum(len(f["content"].encode("utf-8")) for f in files) > MAX_DOCUMENT_BYTES):
                    _error("rename preview exceeds document snapshot limit", 413)
                result = {"files": files, "edit_count": sum(f["edit_count"] for f in files),
                          "collision_check": "Language-server rename validation; no general collision-safety proof"}
            remaining()
            with self.project.lock:
                fresh = self._documents(payload)
                _, current = self._capture(fresh, deadline)
                if current != fingerprint or self.project.source_revision() != source_revision:
                    _error("project source changed during semantic query; retry", 409)
            return {"operation": operation, "language": language, "path": relative,
                    "version": active["version"], "revision": active["revision"],
                    "source_revision": source_revision, "project_fingerprint": fingerprint, "result": result,
                    "diagnostics": self._diagnostics(primary, documents, texts)}
        except ProjectError:
            raise
        except TimeoutError as exc:
            raise ProjectError(504, "semantic editor query timed out") from exc
        except (LspError, OSError, ValueError, KeyError, TypeError, UnicodeError, RecursionError) as exc:
            # Native paths/server output stay local; browser receives a concise
            # error without stderr, argv, or arbitrary server error data.
            raise ProjectError(422, "semantic editor query failed: " + str(exc)[:512].replace(str(self.project.root), "<project>")) from exc
        finally:
            for client in reversed(clients):
                # Per-request servers have no reusable state. Stop owned trees
                # directly rather than adding independent shutdown timeouts.
                client._cleanup_tree()
                client.close()
            self._lock.release()

    def close(self):
        with self._lock:
            self._closed = True
