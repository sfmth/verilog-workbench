#!/usr/bin/env python3
"""Validate and assemble the static development preview for GitHub Pages.

All shipped URLs are relative to the document/module that owns them. Static
directory entry points and fragment navigation therefore survive reloads under
the GitHub Pages project prefix without a server rewrite or an SPA 404 shim.
"""
from __future__ import annotations

import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import posixpath
import re
import shutil
import tempfile
from urllib.parse import unquote, urlsplit


REPO = Path(__file__).resolve().parents[1]
MANIFEST = "vwb-web-build.json"
ENTRY_POINTS = ("index.html", "workspace/index.html", "m0/index.html")


class BuildError(ValueError):
    """A local asset cannot be served safely from the Pages project path."""


class _HTMLReferences(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "base":
            raise BuildError("HTML base URLs are unsupported; use document-relative URLs")
        for name in ("href", "src", "poster", "data"):
            if values.get(name):
                self.references.append(values[name])
        if tag == "meta" and (values.get("http-equiv") or "").lower() == "refresh":
            match = re.search(r"(?:^|;)\s*url\s*=\s*(.+)", values.get("content") or "", re.I)
            if match:
                self.references.append(match[1].strip(" '\""))

    handle_startendtag = handle_starttag


def _without_js_comments(source: str) -> str:
    # Preserve strings so URL literals containing // are not treated as comments.
    tokens = re.compile(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|//[^\n]*|/\*[\s\S]*?\*/''')
    return tokens.sub(lambda match: " " if match[0].startswith(("//", "/*")) else match[0], source)


def _js_references(source: str) -> list[tuple[str, bool]]:
    source = _without_js_comments(source)
    patterns = (
        # Static imports/re-exports, including multiline named imports.
        (r'''\b(?:import|export)\s+(?:(?:[^;"']*?)\s+from\s*)?["']([^"']+)["']''', True),
        (r'''\bimport\s*\(\s*["']([^"']+)["']\s*\)''', True),
        # Workers, WASM and other assets loaded relative to this module.
        (r'''\bnew\s+URL\s*\(\s*["']([^"']+)["']\s*,\s*import\.meta\.url\s*\)''', False),
    )
    return [(match[1], module) for pattern, module in patterns for match in re.finditer(pattern, source)]


def _css_references(source: str) -> list[str]:
    source = re.sub(r"/\*[\s\S]*?\*/", "", source)
    references = re.findall(r'''url\(\s*["']?([^)'"\s]+)["']?\s*\)''', source)
    references.extend(re.findall(r'''@import\s+["']([^"']+)["']''', source))
    return references


def _resolve_reference(root: Path, owner: Path, reference: str, *, module: bool = False) -> None:
    reference = reference.strip()
    parsed = urlsplit(reference)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return
    decoded = unquote(parsed.path)
    if "\\" in decoded or decoded.startswith("/"):
        raise BuildError(f"{owner}: root-relative/ambiguous URL is not Pages-safe: {reference}")
    if module and not decoded.startswith(("./", "../")):
        raise BuildError(f"{owner}: bare module specifier needs a bundled relative URL: {reference}")
    relative = posixpath.normpath(posixpath.join(owner.parent.as_posix(), decoded))
    if relative == ".." or relative.startswith("../"):
        raise BuildError(f"{owner}: URL escapes the web directory: {reference}")
    target = root / relative
    if not target.resolve().is_relative_to(root):
        raise BuildError(f"{owner}: URL follows a symlink outside the web directory: {reference}")
    if target.is_dir():
        target /= "index.html"
    if not target.is_file():
        raise BuildError(f"{owner}: missing local URL target: {reference}")


def validate_web(source: Path) -> list[Path]:
    source = source.resolve()
    for entry in ENTRY_POINTS:
        if not (source / entry).is_file():
            raise BuildError(f"Missing static entry point: {entry}")
    entries = sorted(source.rglob("*"))
    for path in entries:
        relative = path.relative_to(source)
        if path.is_symlink() or not path.resolve().is_relative_to(source):
            raise BuildError(f"Symlinked assets are unsupported: {relative}")
    files = [path for path in entries if path.is_file()]
    for path in files:
        relative = path.relative_to(source)
        if path.suffix == ".html":
            parser = _HTMLReferences()
            parser.feed(path.read_text(encoding="utf-8"))
            for reference in parser.references:
                _resolve_reference(source, relative, reference)
        elif path.suffix in (".js", ".mjs"):
            for reference, module in _js_references(path.read_text(encoding="utf-8")):
                _resolve_reference(source, relative, reference, module=module)
        elif path.suffix == ".css":
            for reference in _css_references(path.read_text(encoding="utf-8")):
                _resolve_reference(source, relative, reference)
    return files


def build_web(source: Path, output: Path, base_path: str = "/verilog-workbench/") -> dict:
    source, output = source.resolve(), output.resolve()
    if not re.fullmatch(r"/(?:[A-Za-z0-9_-]+/)*", base_path):
        raise BuildError("The Pages base path must be / or slash-delimited plain path segments")
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise BuildError("Build output and web source directories must not overlap")
    files = validate_web(source)
    if output.exists() and (not output.is_dir() or (any(output.iterdir()) and not (output / MANIFEST).is_file())):
        raise BuildError(f"Refusing to replace output that is not a previous VWB web build: {output}")
    manifest = {
        "schema": "vwb.web-build/1", "status": "development-preview", "base_path": base_path,
        "entry_points": [base_path + entry.removesuffix("index.html") for entry in ENTRY_POINTS],
        "routing": "static-directories-and-fragments",
        "files": {path.relative_to(source).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in files},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".vwb-web-", dir=output.parent) as temporary:
        staging = Path(temporary) / "site"
        shutil.copytree(source, staging)
        (staging / ".nojekyll").touch()
        (staging / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        previous = Path(temporary) / "previous"
        if output.exists():
            output.rename(previous)
        try:
            staging.rename(output)
        except OSError:
            if previous.exists():
                previous.rename(output)
            raise
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPO / "dist/web")
    parser.add_argument("--base-path", default="/verilog-workbench/")
    args = parser.parse_args()
    try:
        manifest = build_web(REPO / "web", args.output, args.base_path)
    except (BuildError, OSError) as error:
        parser.exit(1, f"Web build failed: {error}\n")
    print(f"Built {len(manifest['files'])} static files for {manifest['base_path']} in {args.output.resolve()}")


if __name__ == "__main__":
    main()
