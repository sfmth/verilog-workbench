#!/usr/bin/env python3
"""Build a relocatable companion including its own CPython, using only stdlib.

The build host needs Python 3.12+. The resulting application does not need
system Python, pip, Docker or WSL. Only pinned, hash-verified runtime archives
are downloaded; no installer or downloaded shell script is executed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "packaging" / "runtimes.json"
MAX_RUNTIME_FILES = 100_000
MAX_RUNTIME_BYTES = 1024 * 1024 * 1024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest() -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("schema") != 1:
        raise ValueError("Unsupported runtime manifest")
    for target in manifest["targets"].values():
        parsed = urllib.parse.urlsplit(target["url"])
        if parsed.scheme != "https" or parsed.netloc != "github.com":
            raise ValueError("Only official HTTPS GitHub runtime assets are allowed")
        if not parsed.path.startswith("/astral-sh/python-build-standalone/releases/download/"):
            raise ValueError("Unexpected runtime asset repository")
        if len(target["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in target["sha256"]):
            raise ValueError("Invalid pinned runtime digest")
    return manifest


def verify_archive(path: Path, runtime: dict) -> None:
    if path.stat().st_size != runtime["size"]:
        raise ValueError(f"Runtime archive size does not match manifest: {path}")
    if sha256(path) != runtime["sha256"]:
        raise ValueError(f"Runtime archive SHA-256 does not match manifest: {path}")


def download_runtime(runtime: dict, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    name = urllib.parse.unquote(urllib.parse.urlsplit(runtime["url"]).path.rsplit("/", 1)[1])
    path = cache_dir / name
    if path.exists():
        verify_archive(path, runtime)
        return path
    descriptor, temporary = tempfile.mkstemp(prefix="download-", suffix=".part", dir=cache_dir)
    temporary_path = Path(temporary)
    try:
        request = urllib.request.Request(runtime["url"], headers={"User-Agent": "VWB-native-bundle/1"})
        with os.fdopen(descriptor, "wb") as output, urllib.request.urlopen(request, timeout=60) as response:
            size = 0
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > runtime["size"]:
                    raise ValueError("Runtime download exceeds pinned size")
                output.write(chunk)
        verify_archive(temporary_path, runtime)
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return path


def extract_runtime(archive: Path, destination: Path) -> Path:
    """Reject traversal, devices, external links, and excessive expanded data."""
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        if len(members) > MAX_RUNTIME_FILES or sum(m.size for m in members) > MAX_RUNTIME_BYTES:
            raise ValueError("Runtime archive exceeds extraction limits")
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "python":
                raise ValueError(f"Unsafe runtime archive member: {member.name}")
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                raise ValueError(f"Unsupported runtime archive member: {member.name}")
            # Preflight *all* links before extracting anything. data_filter also
            # checks resolved paths at extraction time to prevent link pivots.
            tarfile.data_filter(member, str(destination))
        source.extractall(destination, members=members, filter="data")
    runtime = destination / "python"
    if not runtime.is_dir():
        raise ValueError("Expected python/ runtime archive root")
    return runtime


def validate_runtime(runtime: Path, python_path: str) -> None:
    runtime = runtime.resolve(strict=True)
    if not runtime.is_dir() or not (runtime / python_path).is_file():
        raise ValueError(f"Runtime needs {python_path}")
    if not any(p.name.lower().startswith("license") for p in runtime.rglob("*")):
        raise ValueError("Runtime has no retained license notice")
    for path in runtime.rglob("*"):
        if path.is_symlink() and not path.resolve().is_relative_to(runtime):
            raise ValueError(f"Runtime symlink leaves runtime directory: {path}")
        if not path.is_symlink() and not path.is_file() and not path.is_dir():
            raise ValueError(f"Unsupported runtime entry: {path}")


def copy_application(repo: Path, destination: Path) -> list[dict]:
    """Allowlist source; never sweep a workspace, tests, .git, or user files."""
    destination.mkdir()
    package_sources = [p for p in (repo / "companion").rglob("*.py")
                       if not any(part.startswith(".") or part in {"test", "tests", "__pycache__"}
                                  for part in p.relative_to(repo / "companion").parts)]
    sources = [repo / "vwb.py", *sorted(package_sources)]
    if not (repo / "companion" / "__main__.py").is_file():
        raise ValueError("Companion module entry point is missing")
    inventory = []
    for source in sources:
        relative = source.relative_to(repo)
        if source.is_symlink() or any((repo / parent).is_symlink() for parent in relative.parents):
            raise ValueError(f"Application source must not be a symlink: {relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        inventory.append({"path": relative.as_posix(), "sha256": sha256(target)})
    shutil.copy2(repo / "packaging" / "launch.py", destination / "_launch.py")
    return inventory


def write_launchers(bundle: Path, windows: bool) -> None:
    for name, module in (("vwb-companion", "companion"), ("vwb", "vwb")):
        if windows:
            content = f'@echo off\r\n"%~dp0runtime\\python.exe" -I "%~dp0app\\_launch.py" {module} %*\r\n'
            content += 'exit /b %errorlevel%\r\n'
            (bundle / f"{name}.cmd").write_bytes(content.encode("utf-8"))
        else:
            content = '#!/bin/sh\nset -eu\nROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd)\n'
            content += f'exec "$ROOT/runtime/bin/python3" -I "$ROOT/app/_launch.py" {module} "$@"\n'
            launcher = bundle / name
            launcher.write_text(content, encoding="utf-8")
            launcher.chmod(0o755)


def make_archive(bundle: Path, windows: bool) -> Path:
    if windows:
        output = bundle.with_suffix(".zip")
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(bundle.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(bundle.parent))
    else:
        output = bundle.with_suffix(".tar.gz")
        with tarfile.open(output, "w:gz") as archive:
            archive.add(bundle, arcname=bundle.name)
    return output


def build_bundle(target: str, output_dir: Path, runtime_dir: Path | None = None,
                 runtime_archive: Path | None = None, download: bool = False,
                 cache_dir: Path | None = None, archive: bool = False,
                 repo: Path = REPO) -> tuple[Path, Path | None]:
    manifest = load_manifest()
    runtime_info = manifest["targets"][target]
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle = output_dir / f"vwb-companion-{target}"
    if bundle.exists():
        raise ValueError(f"Output already exists; choose a fresh output directory: {bundle}")
    if runtime_dir and (output_dir == runtime_dir.resolve() or output_dir.is_relative_to(runtime_dir.resolve())):
        raise ValueError("Output directory must be outside runtime directory")
    with tempfile.TemporaryDirectory(prefix=".vwb-build-", dir=output_dir) as temporary:
        stage = Path(temporary)
        provenance = {"provider": "caller-supplied", "archive_verified": False}
        if not runtime_dir:
            if download:
                runtime_archive = download_runtime(runtime_info, cache_dir or Path.home() / ".cache" / "vwb-native")
            if not runtime_archive:
                raise ValueError("Choose --download-runtime, --runtime-archive, or --runtime-dir")
            verify_archive(runtime_archive, runtime_info)
            runtime_dir = extract_runtime(runtime_archive, stage / "extracted")
            provenance = {"provider": manifest["provider"], "python_version": manifest["python_version"],
                          "release": manifest["release"], "archive_verified": True,
                          "url": runtime_info["url"], "sha256": runtime_info["sha256"]}
        validate_runtime(runtime_dir, runtime_info["python"])
        work = stage / bundle.name
        work.mkdir()
        shutil.copytree(runtime_dir, work / "runtime", symlinks=True)
        inventory = copy_application(repo, work / "app")
        for filename in ("LICENSE.txt", "README.md"):
            shutil.copy2(repo / filename, work / filename)
        shutil.copy2(repo / "packaging" / "THIRD_PARTY_NOTICES.md", work / "THIRD_PARTY_NOTICES.md")
        # Include the complete packaging source and manifest alongside app source.
        shutil.copy2(repo / "scripts" / "build_native_bundle.py", work / "build_native_bundle.py")
        (work / "packaging").mkdir()
        for filename in ("launch.py", "runtimes.json", "THIRD_PARTY_NOTICES.md"):
            shutil.copy2(repo / "packaging" / filename, work / "packaging" / filename)
        write_launchers(work, target.startswith("windows"))
        (work / "bundle.json").write_text(json.dumps({"schema": 1, "target": target,
            "runtime": provenance, "application_sources": inventory,
            "source_url": "https://github.com/sfmth/verilog-workbench/tree/web-port-plan",
            "toolchains_included": False}, indent=2) + "\n", encoding="utf-8")
        work.replace(bundle)
    return bundle, make_archive(bundle, target.startswith("windows")) if archive else None


def main(argv: list[str] | None = None) -> int:
    manifest = load_manifest()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=manifest["targets"], required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--download-runtime", action="store_true")
    source.add_argument("--runtime-archive", type=Path)
    source.add_argument("--runtime-dir", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--archive", action="store_true", help="Also create .zip (Windows) or .tar.gz (POSIX)")
    args = parser.parse_args(argv)
    try:
        bundle, archive = build_bundle(args.target, args.output_dir, args.runtime_dir,
            args.runtime_archive, args.download_runtime, args.cache_dir, args.archive)
    except (OSError, ValueError, tarfile.TarError, urllib.error.URLError) as exc:
        parser.exit(1, f"Bundle failed: {exc}\n")
    print(json.dumps({"bundle": str(bundle), "archive": str(archive) if archive else None}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
