#!/usr/bin/env python3
"""Acquire an exact locked native suite; acquisition never proves workflows."""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from companion.process import run_process
from companion.toolchains import binary_architecture, compatibility_route, host_platform

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


def validate_link_chain(name: str, members: dict[str, tarfile.TarInfo]) -> None:
    """Resolve archive symlinks with portable POSIX semantics before writing.

    In particular, expand an intervening symlink before interpreting a later
    '..'. Windows Path.resolve may normalize that '..' earlier, so a native
    filesystem check alone cannot enforce the same archive policy on all hosts.
    """
    pending = deque(PurePosixPath(name).parts)
    resolved = []
    expansions = steps = 0
    while pending:
        steps += 1
        if steps > 10000:
            raise ValueError('archive symlink chain exceeds the resolution bound')
        part = pending.popleft()
        if part == '..':
            if len(resolved) <= 1:
                raise ValueError('archive symlink chain escapes suite root')
            resolved.pop()
            continue
        if part == '.':
            continue
        resolved.append(part)
        member = members.get(str(PurePosixPath(*resolved)))
        if member is not None and member.issym():
            expansions += 1
            if expansions > 40:
                raise ValueError('archive symlink chain loops or exceeds the resolution bound')
            resolved.pop()
            pending.extendleft(reversed(PurePosixPath(member.linkname).parts))
    if not resolved or resolved[0] != 'oss-cad-suite':
        raise ValueError('archive symlink chain escapes suite root')


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
                if member.issym():
                    validate_link_chain(name, members)
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


def download_supplement(asset: dict, cache: Path) -> Path:
    """Only exact locked upstream assets; never resolve a latest package."""
    name, url = asset['name'], asset['url']
    if Path(name).name != name or not url.startswith((
        'https://github.com/ghdl/ghdl/releases/download/',
        'https://gitlab.com/api/v4/projects/4207231/packages/generic/graphviz-releases/',
        'https://files.pythonhosted.org/packages/',
    )):
        raise ValueError('supplement is not a locked upstream asset')
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / name
    if target.exists():
        verify_archive(target, asset)
        return target
    handle, filename = tempfile.mkstemp(prefix='.supplement-download-', dir=cache)
    temporary = Path(filename)
    try:
        with os.fdopen(handle, 'wb') as output, urllib.request.urlopen(url, timeout=30) as response:
            size = 0
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > asset['size_bytes']:
                    raise ValueError('supplement exceeded its pinned size')
                output.write(chunk)
        verify_archive(temporary, asset)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def extract_supplement(archive: Path, destination: Path) -> None:
    """Fresh bounded extraction of regular files/directories; reject all links."""
    if destination.exists() or destination.is_symlink():
        raise ValueError('supplement destination must be fresh')
    destination.mkdir(parents=True)
    try:
        package = zipfile.ZipFile(archive) if zipfile.is_zipfile(archive) else tarfile.open(archive, 'r:gz')
        with package:
            entries = package.infolist() if isinstance(package, zipfile.ZipFile) else package.getmembers()
            seen, total = set(), 0
            for entry in entries:
                name = entry.filename if isinstance(package, zipfile.ZipFile) else entry.name
                path = PurePosixPath(name)
                directory = entry.is_dir() if isinstance(package, zipfile.ZipFile) else entry.isdir()
                if not path.parts and directory:
                    continue
                if len(seen) >= 10000 or not path.parts or path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name or '\0' in name or str(path) in seen:
                    raise ValueError('unsafe or oversized supplement archive')
                seen.add(str(path))
                mode = entry.external_attr >> 16 if isinstance(package, zipfile.ZipFile) else entry.mode
                if (isinstance(package, zipfile.ZipFile) and mode & 0o170000 not in (0, 0o040000, 0o100000)) or (not isinstance(package, zipfile.ZipFile) and not (entry.isdir() or entry.isfile())):
                    raise ValueError('supplement contains a link or special entry')
                size = entry.file_size if isinstance(package, zipfile.ZipFile) else entry.size
                total += size
                if size < 0 or total > 512 * 1024 * 1024:
                    raise ValueError('supplement exceeds expanded size limit')
                target = destination.joinpath(*path.parts)
                if directory:
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = package.open(entry) if isinstance(package, zipfile.ZipFile) else package.extractfile(entry)
                    with source, target.open('xb') as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                    target.chmod(0o755 if mode & 0o111 else 0o644)
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def acquire_supplement(key: str, candidate: dict, directory: Path) -> tuple[Path, dict]:
    archive = download_supplement(candidate, directory / 'cache')
    target = directory / key
    extract_supplement(archive, target)
    executable = target / candidate['entry']
    if not executable.is_file():
        raise ValueError('supplement lacks its locked executable entry')
    record = {**candidate, 'directory': str(target), 'executable': str(executable),
              'executable_sha256': hashlib.sha256(executable.read_bytes()).hexdigest(),
              'binary': binary_architecture(executable), 'workflow_support': 'not-tested'}
    (target / 'vwb-supplement-receipt.json').write_text(json.dumps(record, indent=2) + '\n')
    return executable, record


def anchor_ghdl_in_suite(executable: Path, receipt: dict, suite: Path) -> tuple[Path, dict]:
    """Keep macOS Python's @executable_path dependencies at their suite anchor.

    The Intel mcode binary imports only Apple system libraries. Its standard
    libraries remain in the independently receipted supplement via GHDL_PREFIX.
    A real copy is required: a symlink can retain the foreign executable anchor.
    """
    expected = receipt['executable_sha256']
    if hashlib.sha256(executable.read_bytes()).hexdigest() != expected:
        raise ValueError('GHDL supplement changed before runtime anchoring')
    destination = suite / 'bin' / 'ghdl'
    with executable.open('rb') as source, destination.open('xb') as output:
        shutil.copyfileobj(source, output, length=1024 * 1024)
        output.flush()
        os.fsync(output.fileno())
    destination.chmod(0o755)
    if hashlib.sha256(destination.read_bytes()).hexdigest() != expected:
        raise ValueError('GHDL runtime anchor copy differs from the verified payload')
    record = {**receipt, 'archive_executable': str(executable), 'executable': str(destination),
              'runtime_anchor': str(suite), 'runtime_anchor_kind': 'exclusive-verified-copy',
              'runtime_anchor_reason': 'Cocotb loads suite libpython whose dylib dependencies use @executable_path/../lib; the GHDL mcode executable imports only Apple system libraries.'}
    (Path(record['directory']) / 'vwb-supplement-receipt.json').write_text(json.dumps(record, indent=2) + '\n')
    return destination, record


def prepare_windows_cocotb(candidate: dict, source_python: Path, directory: Path) -> tuple[Path, dict]:
    """Explicit MSVC CPython candidate, never a wheel in the MinGW suite Python."""
    source_python = source_python.resolve(strict=True)
    binary = binary_architecture(source_python)
    query = run_process([str(source_python), '-c', 'import json,platform,sys; print(json.dumps({"implementation":platform.python_implementation(),"version":list(sys.version_info[:3]),"compiler":platform.python_compiler()}))'], timeout=15, max_output_bytes=4096)
    metadata = json.loads(query.stdout) if query.returncode == 0 else {}
    if metadata.get('implementation') != 'CPython' or metadata.get('version', [])[:2] != [3, 12] or 'MSC' not in metadata.get('compiler', '') or binary.get('architectures') != ['x64']:
        raise ValueError('Windows Cocotb requires explicit external x64 MSVC CPython 3.12')
    destination = directory / 'windows-cocotb-python'
    if destination.exists():
        raise ValueError('refusing to replace an existing Windows Cocotb environment')
    packages = [download_supplement(asset, directory / 'cache') for asset in candidate['wheels']]
    result = run_process([str(source_python), '-m', 'venv', str(destination)], timeout=120, max_output_bytes=16000)
    if result.returncode:
        raise ValueError('local Cocotb venv creation failed: ' + (result.stderr or result.stdout)[-4000:])
    python = destination / 'Scripts' / 'python.exe'
    result = run_process([str(python), '-m', 'pip', 'install', '--no-index', '--no-deps', '--disable-pip-version-check', *(str(path) for path in packages)], timeout=120, max_output_bytes=16000)
    if result.returncode:
        raise ValueError('offline pinned Cocotb wheel installation failed: ' + (result.stderr or result.stdout)[-4000:])
    record = {'source_python': str(source_python), 'source_sha256': hashlib.sha256(source_python.read_bytes()).hexdigest(),
              'binary': binary, 'runtime': metadata, 'python': str(python), 'packages': candidate['wheels'],
              'compatibility': compatibility_route(binary, host_platform()), 'external_prerequisite': True,
              'relocatable': False, 'workflow_support': 'not-tested'}
    (destination / 'vwb-cocotb-runtime.json').write_text(json.dumps(record, indent=2) + '\n')
    return python, record


def supplement_environment(lock: dict, target: str, directory: Path, environment: dict, *,
                           external_ghdl: Path | None = None, ghdl_prefix: Path | None = None,
                           windows_python: Path | None = None) -> tuple[dict, list[dict], str | None]:
    candidates = lock['supplemental_candidates']
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    directories, records, interpreter = [], [], None
    prefix = None
    if target == 'darwin-x64':
        binary, receipt = acquire_supplement('ghdl-darwin-x64', candidates['ghdl-darwin-x64'], directory)
        binary, receipt = anchor_ghdl_in_suite(binary, receipt, Path(environment['VIRTUAL_ENV']))
        directories.append(binary.parent)
        prefix = Path(receipt['directory']) / candidates['ghdl-darwin-x64']['prefix']
        records.append(receipt)
    elif target.startswith('windows-'):
        if windows_python is None:
            raise ValueError('Windows supplements require explicit --windows-cocotb-python MSVC CPython 3.12')
        for key in ('ghdl-windows-x64', 'graphviz-windows-x64'):
            binary, receipt = acquire_supplement(key, candidates[key], directory)
            directories.append(binary.parent)
            if key.startswith('ghdl'):
                prefix = Path(receipt['directory']) / candidates[key]['prefix']
                directories.append(Path(receipt['directory']) / 'lib')
            records.append(receipt)
        python, receipt = prepare_windows_cocotb(candidates['cocotb-windows-cpython312'], windows_python, directory)
        directories.insert(0, python.parent)
        interpreter = str(python)
        records.append(receipt)
    elif target == 'linux-arm64':
        if external_ghdl is None or ghdl_prefix is None:
            raise ValueError('Linux ARM64 supplements require explicit --external-ghdl and --ghdl-prefix native OS prerequisites')
        binary = external_ghdl.resolve(strict=True)
        prefix = ghdl_prefix.resolve(strict=True)
        if not binary.is_file() or not os.access(binary, os.X_OK) or not prefix.is_dir():
            raise ValueError('external GHDL executable and library prefix must exist')
        alias = directory / 'external-ghdl-bin'
        alias.mkdir()
        (alias / 'ghdl').symlink_to(binary)
        directories.append(alias)
        records.append({'provider': 'explicit-external-native-GHDL', 'executable': str(binary), 'prefix': str(prefix),
                        'executable_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
                        'binary': binary_architecture(binary), 'external_prerequisite': True,
                        'relocatable': False, 'workflow_support': 'not-tested'})
    result = dict(environment)
    result['PATH'] = os.pathsep.join([*(str(path) for path in directories), environment.get('PATH', '')])
    if prefix is not None:
        if not prefix.is_dir():
            raise ValueError('supplemental GHDL library prefix is missing')
        result['GHDL_PREFIX'] = str(prefix)
    if interpreter is not None:
        result['PYTHON_EXECUTABLE'] = interpreter
        result['VIRTUAL_ENV'] = str(Path(interpreter).parent.parent)
    return result, records, interpreter


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
    parser.add_argument('--supplementals', action='store_true', help='activate pinned supplemental native package candidates; real workflows still required')
    parser.add_argument('--external-ghdl', type=Path, help='explicit externally managed Linux ARM64 GHDL executable (requires --supplementals)')
    parser.add_argument('--ghdl-prefix', type=Path, help='matching external GHDL standard-library directory')
    parser.add_argument('--windows-cocotb-python', type=Path, help='explicit external x64 MSVC CPython 3.12; create a local pinned Cocotb venv')
    args = parser.parse_args()
    if args.probe_work_dir and not args.probe_output:
        parser.error("--probe-work-dir requires --probe-output")
    if (args.external_ghdl or args.ghdl_prefix or args.windows_cocotb_python) and not args.supplementals:
        parser.error('external supplemental prerequisites require --supplementals')
    if (args.external_ghdl or args.ghdl_prefix) and args.target != 'linux-arm64':
        parser.error('external GHDL prerequisites are currently Linux ARM64 only')
    if args.windows_cocotb_python and not args.target.startswith('windows-'):
        parser.error('--windows-cocotb-python is for Windows targets only')
    if args.supplementals and args.target == 'linux-arm64' and not (args.external_ghdl and args.ghdl_prefix):
        parser.error('Linux ARM64 supplements require --external-ghdl and --ghdl-prefix')
    if args.supplementals and args.target.startswith('windows-') and not args.windows_cocotb_python:
        parser.error('Windows supplements require --windows-cocotb-python')
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
        lock = json.loads(LOCK.read_text())
        report, environment = acquire(lock, args.target, args.directory, archive=args.archive, cache=args.cache_dir, tool_bins=args.tool_bin, overlays=overlays)
        interpreter = suite_python(Path(report['suitePath'])) if args.probe_output else None
        if args.supplementals:
            environment, packages, external_python = supplement_environment(lock, args.target, args.directory / 'supplements', environment,
                external_ghdl=args.external_ghdl, ghdl_prefix=args.ghdl_prefix, windows_python=args.windows_cocotb_python)
            interpreter = external_python or interpreter
            report.update(supplemental_packages=packages, supplemental_workflow_support='not-tested',
                          environment={key: value for key, value in environment.items() if key in report['environment']},
                          relocatable=report['relocatable'] and all(package.get('relocatable', True) for package in packages))
        status = 0
        if args.probe_output:
            if args.target != host_platform()["target"]:
                raise ValueError("cannot execute a different host target; cross-platform archives are acquisition-only")
            command = [interpreter, str(REPO / "scripts" / "probe_toolchain.py"), "--execute", "--output", str(args.probe_output.resolve())]
            if args.probe_work_dir:
                command.extend(["--work-dir", str(args.probe_work_dir.resolve())])
            result = run_process(command,
                                 cwd=REPO, env=environment, timeout=900, max_output_bytes=32000)
            report["probe"] = {"path": str(args.probe_output.resolve()), "python": interpreter, "returncode": result.returncode,
                               "state": "passed" if result.returncode == 0 else "failed", "log_excerpt": (result.stdout or "") + (result.stderr or ""),
                               "work_directory_parent": str(args.probe_work_dir.resolve()) if args.probe_work_dir else None}
            status = int(result.returncode != 0)
    except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as exc:
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
