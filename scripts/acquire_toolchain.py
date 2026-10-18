#!/usr/bin/env python3
"""Acquire an exact locked native suite; acquisition never proves workflows."""
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
import urllib.request

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from companion.process import run_process
from companion.toolchains import binary_architecture, host_platform

LOCK = REPO / "toolchains" / "native-lock.json"
MAX_MEMBERS = 150_000
MAX_UNPACKED_BYTES = 8 * 1024**3


def verify_archive(path: Path, asset: dict) -> None:
    if path.stat().st_size != asset["size_bytes"]:
        raise ValueError("archive size differs from the pinned release asset")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != asset["sha256"]:
        raise ValueError("archive SHA-256 differs from the pinned release asset")


def download_archive(asset: dict, cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / asset["name"]
    if destination.exists():
        verify_archive(destination, asset)
        return destination
    if not asset["url"].startswith("https://github.com/YosysHQ/oss-cad-suite-build/releases/download/"):
        raise ValueError("download URL is not an upstream pinned suite release")
    handle, filename = tempfile.mkstemp(prefix=".suite-download-", dir=cache)
    temporary = Path(filename)
    try:
        size = 0
        with os.fdopen(handle, "wb") as output, urllib.request.urlopen(asset["url"], timeout=30) as response:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > asset["size_bytes"]:
                    raise ValueError("download exceeded pinned archive size")
                output.write(chunk)
        verify_archive(temporary, asset)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def clean_name(value: str) -> str:
    if "\\" in value or "\0" in value:
        raise ValueError("archive path contains a platform-dependent separator")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or path.parts[0] != "oss-cad-suite" or any(part in ("..", ":") or ":" in part for part in path.parts):
        raise ValueError(f"archive path escapes the expected suite root: {value}")
    return str(path)


def internal_link(member: tarfile.TarInfo, name: str) -> str:
    value = member.linkname
    if not value or "\\" in value or "\0" in value or PurePosixPath(value).is_absolute() or ":" in value:
        raise ValueError("archive link target is not a portable internal relative path")
    parts = list(PurePosixPath(name).parent.parts) if member.issym() else []
    for part in PurePosixPath(value).parts:
        if part == "..":
            if len(parts) <= 1:
                raise ValueError("archive link escapes suite root")
            parts.pop()
        elif part != ".":
            parts.append(part)
    if not parts or parts[0] != "oss-cad-suite":
        raise ValueError("archive link escapes suite root")
    return str(PurePosixPath(*parts))


def extract_archive(archive: Path, directory: Path, *, max_members: int = MAX_MEMBERS, max_bytes: int = MAX_UNPACKED_BYTES) -> None:
    """Extract into a fresh staging directory, with links installed last.

    Reject devices/FIFOs, traversal, duplicate files, link-parent writes and
    external symlinks. Hardlinks must point to an archived regular file. Strip
    owner/setuid metadata; preserve only ordinary executable permissions.
    """
    if directory.exists() or directory.is_symlink():
        raise ValueError("extraction destination must be fresh")
    directory.mkdir(mode=0o700, parents=True)
    try:
        with tarfile.open(archive, mode="r:gz") as package:
            members = {}
            total = 0
            for count, member in enumerate(package, start=1):
                if count > max_members:
                    raise ValueError("archive exceeds the member-count bound")
                name = clean_name(member.name)
                if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                    raise ValueError("archive contains a device or unsupported entry")
                if name in members:
                    if member.isdir() and members[name].isdir():
                        continue
                    raise ValueError("archive contains a duplicate path")
                total += member.size
                if member.size < 0 or total > max_bytes:
                    raise ValueError("archive exceeds the unpacked-size bound")
                members[name] = member
            links = {name: internal_link(member, name) for name, member in members.items() if member.issym() or member.islnk()}
            for name, member in members.items():
                for parent in PurePosixPath(name).parents:
                    ancestor = members.get(str(parent))
                    if ancestor and not ancestor.isdir():
                        raise ValueError("archive entry has a non-directory or link parent")
                if member.islnk():
                    target = members.get(links[name])
                    if target is None or not target.isfile():
                        raise ValueError("hardlink target is not an archived regular file")
            for name, member in members.items():
                target = directory.joinpath(*PurePosixPath(name).parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = package.extractfile(member)
                    if source is None:
                        raise ValueError("regular archive entry has no data")
                    with source, target.open("xb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                    target.chmod(0o755 if member.mode & 0o111 else 0o644)
            for name, member in members.items():
                target = directory.joinpath(*PurePosixPath(name).parts)
                if member.islnk():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.link(directory.joinpath(*PurePosixPath(links[name]).parts), target)
                elif member.issym():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    internal_target = directory.joinpath(*PurePosixPath(links[name]).parts)
                    target.symlink_to(member.linkname, target_is_directory=internal_target.is_dir())
            # Lexically internal link chains can still escape when '..'
            # follows another symlink. Validate the finished graph after all
            # regular writes, while no archive operation can follow a link.
            suite_root = (directory / "oss-cad-suite").resolve()
            for name, member in members.items():
                if member.issym():
                    target = directory.joinpath(*PurePosixPath(name).parts)
                    try:
                        target.resolve().relative_to(suite_root)
                    except (OSError, ValueError, RuntimeError) as exc:
                        raise ValueError("archive symlink chain escapes suite root or loops") from exc
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def native_environment(suite: Path, target: str, base: dict | None = None, *, tool_bins: list[Path] | None = None) -> dict:
    environment = dict(os.environ if base is None else base)
    environment.pop("PYTHONHOME", None)
    directories = [suite / "bin", suite / "py3bin"]
    if target.startswith("windows-"):
        # Windows' loader needs bundled DLLs as well as executable directories.
        directories.insert(1, suite / "lib")
    directories = [*(path.resolve() for path in tool_bins or []), *directories]
    environment.update(PATH=os.pathsep.join([*(str(path) for path in directories), environment.get("PATH", "")]),
                       VIRTUAL_ENV=str(suite), VERILATOR_ROOT=str(suite / "share" / "verilator"),
                       GHDL_PREFIX=str(suite / "lib" / "ghdl"))
    if target.startswith("windows-"):
        # Literal upstream environment.bat settings needed by native Python
        # and board data; no arbitrary activation script is evaluated.
        environment.update(YOSYSHQ_ROOT=str(suite) + os.sep,
                           PYTHON_EXECUTABLE=str(suite / "lib" / "python3.exe"),
                           SSL_CERT_FILE=str(suite / "etc" / "cacert.pem"),
                           OPENFPGALOADER_SOJ_DIR=str(suite / "share" / "openFPGALoader"))
    return environment


def suite_python(suite: Path) -> str:
    for relative in ("py3bin/python3", "bin/python3", "lib/python3.exe", "bin/python3.exe", "bin/python.exe"):
        candidate = suite / relative
        if candidate.is_file():
            return str(candidate)
    raise ValueError("suite has no bundled Python runtime; system Python is not a substitute for simulator runtime compatibility")


def overlay_tools(suite: Path, overlays: dict[str, Path]) -> list[dict]:
    """Explicit, receipted external Yosys override, resilient to PATH resets.

    The original launcher is retained. An absolute external symlink makes this
    installation dependent on that separately managed package and nonportable.
    No downloaded archive bytes are changed or relabelled as the external tool.
    """
    records = []
    for name, source in overlays.items():
        if name != "yosys":
            raise ValueError("only the explicitly supported yosys overlay is accepted")
        external = source.resolve(strict=True)
        if not external.is_file() or not os.access(external, os.X_OK):
            raise ValueError(f"external {name} is not an executable file: {external}")
        launcher = suite / "bin" / name
        if not launcher.is_file():
            raise ValueError(f"suite has no original {name} launcher to preserve")
        backup = suite / ".vwb-overlays" / "originals" / name
        backup.parent.mkdir(parents=True, exist_ok=True)
        record = {"tool": name, "original_launcher": f"bin/{name}",
                  "original_sha256": hashlib.sha256(launcher.read_bytes()).hexdigest(),
                  "original_symlink": os.readlink(launcher) if launcher.is_symlink() else None,
                  "original_backup": str(backup.relative_to(suite)),
                  "external_executable": str(external),
                  "external_sha256": hashlib.sha256(external.read_bytes()).hexdigest(),
                  "external_binary": binary_architecture(external),
                  "externally_managed": True, "relocatable": False,
                  "reason": "Explicit native Tcl-enabled Yosys candidate; suite wrappers prepend suite/bin to PATH."}
        launcher.rename(backup)
        try:
            launcher.symlink_to(external)
        except BaseException:
            backup.rename(launcher)
            raise
        records.append(record)
    return records


def acquire(lock: dict, target: str, directory: Path, *, archive: Path | None = None, cache: Path | None = None, tool_bins: list[Path] | None = None, overlays: dict[str, Path] | None = None) -> tuple[dict, dict]:
    route = lock["targets"][target]
    asset = lock["suite"]["assets"][route["archive_target"]]
    directory = directory.resolve()
    destination = directory / f"oss-cad-suite-{lock['suite']['release']}-{route['archive_target']}"
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"refusing to overwrite existing tool package: {destination}")
    archive = archive.resolve() if archive is not None else download_archive(asset, cache or directory / "cache")
    verify_archive(archive, asset)
    directory.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".suite-install-", dir=directory))
    stage.rmdir()
    try:
        extract_archive(archive, stage)
        if not (stage / "oss-cad-suite" / "bin").is_dir():
            raise ValueError("archive is missing expected suite/bin")
        overlay_records = overlay_tools(stage / "oss-cad-suite", overlays or {})
        receipt = {"release": lock["suite"]["release"], "archive": asset["name"], "sha256": asset["sha256"], "size_bytes": asset["size_bytes"],
                   "external_tool_overlays": overlay_records, "relocatable": not bool(overlay_records)}
        (stage / "vwb-toolchain-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        os.rename(stage, destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    suite = destination / "oss-cad-suite"
    environment = native_environment(suite, target, tool_bins=tool_bins)
    report = {"schema_version": "vwb.toolchain-acquisition/1", "state": "acquired", "target": target,
              "suitePath": str(suite), "archive": receipt, "binaryArchitecture": route["binary_architecture"],
              "compatibility": route["translation"], "workflowSupport": "not-tested",
              "external_tool_bins": [str(path.resolve()) for path in tool_bins or []],
              "external_tool_overlays": receipt["external_tool_overlays"], "relocatable": receipt["relocatable"],
              "blockers": route.get("blockers", []), "environment": {key: environment[key] for key in ("PATH", "VIRTUAL_ENV", "VERILATOR_ROOT", "GHDL_PREFIX", "YOSYSHQ_ROOT", "PYTHON_EXECUTABLE", "SSL_CERT_FILE", "OPENFPGALOADER_SOJ_DIR") if key in environment}}
    return report, environment


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=json.loads(LOCK.read_text())["targets"], default=host_platform()["target"])
    parser.add_argument("--directory", type=Path, required=True, help="versioned install parent; existing package is never overwritten")
    parser.add_argument("--archive", type=Path, help="reuse this exact locked archive instead of downloading")
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--tool-bin", action="append", type=Path, default=[], help="explicit external tool directory placed before suite tools; bundled probe Python remains fixed (repeatable)")
    parser.add_argument("--overlay-tool", action="append", default=[], metavar="yosys=ABS_EXE", help="explicit external Yosys symlink inside suite/bin, with original backup and integrity receipt; installation becomes nonportable")
    parser.add_argument("--output", type=Path, help="write acquisition JSON here")
    parser.add_argument("--probe-output", type=Path, help="run actual native CLI probe; missing/failed workflow returns 1")
    parser.add_argument("--probe-work-dir", type=Path, help="retain probe projects/logs/artifacts beneath this parent (requires --probe-output)")
    args = parser.parse_args()
    if args.probe_work_dir and not args.probe_output:
        parser.error("--probe-work-dir requires --probe-output")
    report = {}
    try:
        for directory in args.tool_bin:
            if not directory.is_dir():
                raise ValueError(f"external tool directory does not exist: {directory}")
        overlays = {}
        for value in args.overlay_tool:
            name, separator, path = value.partition("=")
            if not separator or name != "yosys" or name in overlays or not Path(path).is_absolute():
                raise ValueError("--overlay-tool requires one explicit yosys=ABS_EXE path")
            overlays[name] = Path(path)
        report, environment = acquire(json.loads(LOCK.read_text()), args.target, args.directory, archive=args.archive, cache=args.cache_dir, tool_bins=args.tool_bin, overlays=overlays)
        status = 0
        if args.probe_output:
            if args.target != host_platform()["target"]:
                raise ValueError("cannot execute a different host target; cross-platform archives are acquisition-only")
            interpreter = suite_python(Path(report["suitePath"]))
            command = [interpreter, str(REPO / "scripts" / "probe_toolchain.py"), "--execute", "--output", str(args.probe_output.resolve())]
            if args.probe_work_dir:
                command.extend(["--work-dir", str(args.probe_work_dir.resolve())])
            result = run_process(command,
                                 cwd=REPO, env=environment, timeout=900, max_output_bytes=32000)
            report["probe"] = {"path": str(args.probe_output.resolve()), "python": interpreter, "returncode": result.returncode,
                               "state": "passed" if result.returncode == 0 else "failed", "log_excerpt": (result.stdout or "") + (result.stderr or ""),
                               "work_directory_parent": str(args.probe_work_dir.resolve()) if args.probe_work_dir else None}
            status = int(result.returncode != 0)
    except (OSError, ValueError, tarfile.TarError) as exc:
        report, status = {**report, "schema_version": "vwb.toolchain-acquisition/1", "state": "failed", "error": str(exc)}, 1
    text = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    print(f"Toolchain acquisition {args.target}: {report['state']}", file=sys.stderr)
    if report.get("error"):
        print(report["error"], file=sys.stderr)
    if "probe" in report:
        print(f"Native workflow probe: {report['probe']['state']} (exit {report['probe']['returncode']})", file=sys.stderr)
        if report["probe"].get("log_excerpt"):
            print(report["probe"]["log_excerpt"][-16000:], file=sys.stderr)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
