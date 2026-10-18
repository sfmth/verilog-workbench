#!/usr/bin/env python3
"""Run real semantic LSP fixture probes; missing servers never count as passes.

Example: python scripts/probe_editor.py --server
  'systemverilog=["/opt/slang-server"]' --server
  'python=["basedpyright-langserver","--stdio"]'

Custom commands are local operator configuration and must be JSON argv arrays.
The probe copies its fixtures to temporary folders and never modifies projects.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import shutil
import sys
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import url2pathname

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from companion.lsp import (LspClient, LspError, LspResponseError,
    preview_workspace_edit, snapshot_files, semantic_references_with_definition)
from companion.process import run_process

DEFAULTS = {
    "systemverilog": ["slang-server"],
    "vhdl": ["vhdl_ls"],
    "python": ["basedpyright-langserver", "--stdio"],
}
SOURCES = {
    "systemverilog": "https://github.com/hudson-trading/slang-server/releases/tag/v0.3.0",
    "vhdl": "https://github.com/VHDL-LS/rust_hdl/releases/tag/v0.88.0",
    "python": "https://www.npmjs.com/package/basedpyright/v/1.40.2",
}
METHODS = {
    "hover": ("hoverProvider", "textDocument/hover"),
    "definition": ("definitionProvider", "textDocument/definition"),
    "references": ("referencesProvider", "textDocument/references"),
    "rename": ("renameProvider", "textDocument/rename"),
    "completion": ("completionProvider", "textDocument/completion"),
}


def site(root, spec):
    filename, snippet, token = spec
    lines = (root / filename).read_text(encoding="utf-8").splitlines()
    matches = [(index, line) for index, line in enumerate(lines) if snippet in line]
    if len(matches) != 1:
        raise ValueError(f"Fixture query must be unique: {spec}")
    line, text = matches[0]
    character = text.index(token, text.index(snippet))
    return {"uri": (root / filename).as_uri(), "line": line, "character": character}


def expected_preview(root, case):
    expected = {p.as_uri(): p.read_text(encoding="utf-8")
                for p in (root / f for f in case["files"])}
    sites = [site(root, spec) for spec in case["rename_sites"]]
    for target, spec in sorted(zip(sites, case["rename_sites"]),
                               key=lambda item: (item[0]["uri"], item[0]["line"], item[0]["character"]),
                               reverse=True):
        lines = expected[target["uri"]].splitlines(keepends=True)
        line = lines[target["line"]]
        start = target["character"]
        lines[target["line"]] = line[:start] + case["new_name"] + line[start + len(spec[2]):]
        expected[target["uri"]] = "".join(lines)
    return expected, sites


def locations(result):
    if isinstance(result, dict):
        result = [result]
    def canonical(uri):
        parsed = urlsplit(uri)
        return Path(url2pathname(parsed.path)).resolve().as_uri() if parsed.scheme == "file" else uri
    return [{"uri": canonical(loc.get("uri", loc.get("targetUri"))),
             "range": loc.get("range", loc.get("targetSelectionRange"))}
            for loc in result or []]


def check_result(name, result, root, case, snapshots, versions):
    if name == "hover":
        return bool(result and result.get("contents")), {"nonempty": bool(result)}
    if name == "completion":
        items = result.get("items", []) if isinstance(result, dict) else result or []
        labels = [item.get("label", "") for item in items]
        token = case["query"][2]
        return token in labels, {"labels": labels[:30], "expected_label": token}
    if name == "definition":
        found = locations(result)
        uri = (root / case["definition_file"]).as_uri()
        return any(loc["uri"] == uri for loc in found), {"locations": found}
    if name == "references":
        found = locations(result)
        _, expected = expected_preview(root, case)
        # Exact symbol positions exclude unrelated same-spelled symbols.
        actual_positions = {(x["uri"], x["range"]["start"]["line"], x["range"]["start"]["character"])
                            for x in found}
        expected_positions = {(x["uri"], x["line"], x["character"]) for x in expected}
        return actual_positions == expected_positions, {"locations": found,
            "expected_reference_count": len(expected_positions)}
    proposed = preview_workspace_edit(root, result, snapshots, versions)
    expected, _ = expected_preview(root, case)
    actual = {uri: proposed[uri]["text"] if uri in proposed else raw.decode("utf-8")
              for uri, raw in snapshots.items() if uri in expected}
    return actual == expected, {"files": sorted(proposed),
        "edit_count": sum(p["edit_count"] for p in proposed.values()),
        "exact_expected_text": actual == expected,
        "note": "In-memory preview only; stale bytes/versions and path escapes rejected."}


def verify_renamed_fixture(language, root, case, proposed):
    """Compile/execute only an isolated disposable copy after a valid preview."""
    commands = {
        "systemverilog": ["iverilog", "-g2012", "-t", "null", "-s", "top", "-s", "other", *case["files"]],
        "vhdl": ["ghdl", "-a", "--std=08", *case["files"]],
        "python": [sys.executable, "-c", "import compileall, model, other; "
            "assert compileall.compile_dir('.', quiet=1); "
            f"assert model.{case['new_name']}(1) == 2; "
            "assert other.result == 3; assert other.shadow(5) == 4"],
    }
    command = commands[language]
    if not shutil.which(command[0]):
        return {"status": "not_run", "reason": "Native fixture compiler is absent", "command": command}
    with tempfile.TemporaryDirectory(prefix="vwb-refactor-check-μ ") as directory:
        target = Path(directory) / language
        shutil.copytree(root, target)
        for uri, value in proposed.items():
            relative = Path(url2pathname(urlsplit(uri).path)).relative_to(root)
            (target / relative).write_bytes(value["text"].encode("utf-8"))
        result = run_process(command, cwd=target, timeout=20, max_output_bytes=4096)
    return {"status": "passed" if result.returncode == 0 else "failed",
        "command": command, "returncode": result.returncode,
        "stdout": result.stdout, "stderr": result.stderr,
        "scope": "HDL post-edit compilation; Python syntax and helper behavior. Not full simulation/Cocotb execution."}


def probe(language, command, timeout, refactor_command=None):
    executable = shutil.which(command[0])
    report = {"language": language, "command": command,
              "candidate_source": SOURCES[language], "status": "absent", "checks": {}}
    if not executable:
        report["reason"] = "Language server executable not installed"
        return report
    report["executable"] = executable
    report["executable_sha256"] = hashlib.sha256(Path(executable).read_bytes()).hexdigest()
    case = json.loads((REPO / "editor-fixtures/cases.json").read_text())[language]
    with tempfile.TemporaryDirectory(prefix="vwb-editor-μ ") as directory:
        root = Path(directory).resolve() / language
        shutil.copytree(REPO / "editor-fixtures" / language, root)
        snapshots = snapshot_files(root)
        versions = {(root / f).as_uri(): 1 for f in case["files"]}
        client = None
        try:
            with LspClient([executable, *command[1:]], root, timeout=timeout) as client:
                initialized = client.initialize()
                report["server_info"] = initialized.get("serverInfo", {})
                capabilities = initialized.get("capabilities", {})
                report["capabilities"] = capabilities
                responses = {}
                rename_result = None
                for filename in case["files"]:
                    client.open_document(root / filename, language)
                target = site(root, case["query"])
                params = {"textDocument": {"uri": target["uri"]},
                          "position": {k: target[k] for k in ("line", "character")}}
                # Indexing can lag didOpen; retry a real definition request until
                # it resolves or reaches the bounded indexing deadline.
                deadline = time.monotonic() + timeout
                if capabilities.get("definitionProvider"):
                    while time.monotonic() < deadline:
                        ready = client.request("textDocument/definition", params)
                        if ready:
                            break
                        time.sleep(0.1)
                for name, (capability, method) in METHODS.items():
                    if not capabilities.get(capability):
                        report["checks"][name] = {"status": "unsupported",
                                                 "reason": "Not advertised by initialize"}
                        continue
                    arguments = {**params}
                    if name == "references":
                        arguments["context"] = {"includeDeclaration": True}
                    if name == "rename":
                        arguments["newName"] = case["new_name"]
                    if name == "completion":
                        cursor = site(root, case["completion"])
                        arguments = {"textDocument": {"uri": cursor["uri"]},
                            "position": {"line": cursor["line"], "character": cursor["character"]},
                            "context": {"triggerKind": 1}}
                    try:
                        result = client.request(method, arguments)
                        responses[name] = result
                        if name == "rename":
                            rename_result = result
                        success, details = check_result(name, result, root, case, snapshots, versions)
                        report["checks"][name] = {"status": "passed" if success else "failed", **details}
                    except (LspError, TimeoutError, KeyError, ValueError) as exc:
                        status = "unsupported" if isinstance(exc, LspResponseError) and exc.error.get("code") == -32601 else "failed"
                        report["checks"][name] = {"status": status, "reason": str(exc)}
                if language == "systemverilog" and refactor_command:
                    report["primary_checks"] = report["checks"].copy()
                    try:
                        alternate_path = shutil.which(refactor_command[0])
                        if not alternate_path:
                            raise LspError("Refactor service executable is absent")
                        with LspClient([alternate_path, *refactor_command[1:]], root, timeout=timeout) as refactor:
                            alternate_init = refactor.initialize()
                            report["refactor_service"] = {
                                "command": refactor_command,
                                "server_info": alternate_init.get("serverInfo", {}),
                                "capabilities": alternate_init.get("capabilities", {}),
                                "executable_sha256": hashlib.sha256(Path(alternate_path).read_bytes()).hexdigest()}
                            for filename in case["files"]:
                                refactor.open_document(root / filename, language)
                            # Require both services to resolve exactly the same
                            # semantic declaration in the identical project.
                            alternate_definition = refactor.request("textDocument/definition", params)
                            def location_key(loc):
                                return (loc["uri"], json.dumps(loc["range"], sort_keys=True))
                            same_definition = {location_key(x) for x in locations(alternate_definition)} == {
                                location_key(x) for x in locations(responses.get("definition"))}
                            if not same_definition:
                                raise LspError("Language services disagree about the semantic definition")
                            alternate_refs = refactor.request("textDocument/references", {
                                **params, "context": {"includeDeclaration": True}})
                            if {location_key(x) for x in locations(alternate_refs)} != {
                                location_key(x) for x in locations(responses.get("references"))}:
                                raise LspError("Language services disagree about reference uses")
                            merged = semantic_references_with_definition(root, snapshots,
                                responses.get("references"), responses.get("definition"))
                            good, details = check_result("references", merged, root, case, snapshots, versions)
                            report["checks"]["references"] = {"status": "passed" if good else "failed",
                                "route": "slang references + independent slang semantic definition; Verible agrees", **details}
                            renamed = refactor.request("textDocument/rename", {**params, "newName": case["new_name"]})
                            rename_result = renamed
                            good, details = check_result("rename", renamed, root, case, snapshots, versions)
                            report["checks"]["rename"] = {"status": "passed" if good else "failed",
                                "route": "Verible only; slang rename is disabled", **details}
                            report["routing"] = {"scope": "M0 probe only; production bridge is not implemented",
                                "same_definition": same_definition,
                                "same_reference_uses": True,
                                "source_filelist_sha256": hashlib.sha256((root / "verible.filelist").read_bytes()).hexdigest()}
                    except (LspError, OSError, TimeoutError, ValueError, KeyError) as exc:
                        report["routing"] = {"status": "failed", "reason": str(exc)}
                        report["checks"]["rename"] = {"status": "failed", "reason": str(exc)}
                if report["checks"]["rename"]["status"] == "passed":
                    proposed = preview_workspace_edit(root, rename_result, snapshots, versions)
                    report["post_rename_validation"] = verify_renamed_fixture(language, root, case, proposed)
                    if report["post_rename_validation"]["status"] == "failed":
                        report["checks"]["rename"]["status"] = "failed"
                report["diagnostic_notification_count"] = sum(
                    x.get("method") == "textDocument/publishDiagnostics" for x in client.notifications)
                report["status"] = "passed" if all(x["status"] == "passed"
                    for x in report["checks"].values()) else "blocked"
                report["stderr_tail"] = list(client.stderr)[-8:]
        except (LspError, OSError, TimeoutError, ValueError) as exc:
            report["status"] = "failed"
            report["reason"] = str(exc)
            if client:
                report["stderr_tail"] = list(client.stderr)[-8:]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", action="append", default=[], metavar="LANGUAGE=JSON_ARGV")
    parser.add_argument("--language", action="append", choices=DEFAULTS)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--sv-refactor-server", metavar="JSON_ARGV",
                        help="Probe Verible semantic rename alongside slang in the identical fixture")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    commands = DEFAULTS.copy()
    for value in args.server:
        language, encoded = value.split("=", 1)
        command = json.loads(encoded)
        if language not in commands or not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
            parser.error("--server must name a supported language and JSON argv list")
        commands[language] = command
    if not 0 < args.timeout <= 120:
        parser.error("timeout must be in (0, 120]")
    refactor_command = json.loads(args.sv_refactor_server) if args.sv_refactor_server else None
    if refactor_command is not None and (not isinstance(refactor_command, list) or not refactor_command
            or not all(isinstance(x, str) for x in refactor_command)):
        parser.error("--sv-refactor-server must be a nonempty JSON argv list")
    report = {"recorded_at": datetime.now(timezone.utc).isoformat(),
        "host": {"system": platform.system(), "machine": platform.machine(),
                 "python": platform.python_version()},
        "scope": "Real local stdio servers; small Unicode/space-path semantic fixtures. No browser integration or six-platform certification.",
        "services": [probe(lang, commands[lang], args.timeout, refactor_command) for lang in args.language or commands]}
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if all(s["status"] == "passed" for s in report["services"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
