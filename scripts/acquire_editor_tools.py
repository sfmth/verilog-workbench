#!/usr/bin/env python3
"""Acquire pinned Linux x86-64 editor services for development/CI evidence.

This is not a six-platform installer. Archives retain their upstream notices;
the generated operator config is local and never accepted from the browser.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile

ASSETS = {
    'systemverilog': ('https://github.com/hudson-trading/slang-server/releases/download/v0.3.0/slang-server-linux-x64.tar.gz', 'sha256', '29ad91f245f100337c35ce27dba711d5f0ea50ebc70e73c78e2b27400a87ae62', 'slang-server'),
    'sv_refactor': ('https://github.com/chipsalliance/verible/releases/download/v0.0-4296-g0f262651/verible-v0.0-4296-g0f262651-linux-static-x86_64.tar.gz', 'sha256', '8569defb891d2316067613ea00442af28a7a09d405d95b54c0c91f9942d26635', 'verible-verilog-ls'),
    'vhdl': ('https://github.com/VHDL-LS/rust_hdl/releases/download/v0.88.0/vhdl_ls-x86_64-unknown-linux-gnu.zip', 'sha256', '8cba241c6df50c9d6d8c2bd7e8ddd592b15c54f5b7656c440265de25bc3335f3', 'vhdl_ls'),
    'python': ('https://registry.npmjs.org/basedpyright/-/basedpyright-1.40.2.tgz', 'sha512', base64.b64decode('9uld8+5tZC2Gxf8nIynyunK0N4xn01W1ApzLMXDAcuA8uJq2QSfldYn9gSbxn9MWR8wmHDo7GaEHLhlnLFHaJA==').hex(), 'pyright-langserver.js'),
}
MAX_ARCHIVE = 128 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024


def digest(path, algorithm='sha256'):
    value = hashlib.new(algorithm)
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def extract(archive, destination):
    """Fresh extraction, regular files only, bounded entries and expanded bytes."""
    destination.mkdir()
    total = 0
    seen = set()
    package = zipfile.ZipFile(archive) if zipfile.is_zipfile(archive) else tarfile.open(archive, 'r:gz')
    with package:
        entries = package.infolist() if isinstance(package, zipfile.ZipFile) else package.getmembers()
        for entry in entries:
            name = entry.filename if isinstance(package, zipfile.ZipFile) else entry.name
            path = PurePosixPath(name)
            directory = entry.is_dir() if isinstance(package, zipfile.ZipFile) else entry.isdir()
            if not path.parts and directory:
                continue
            name = str(path)
            if len(seen) >= 10000 or not path.parts or path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name or name in seen:
                raise ValueError('Unsafe or oversized editor archive')
            seen.add(name)
            mode = (entry.external_attr >> 16) if isinstance(package, zipfile.ZipFile) else entry.mode
            if (isinstance(package, zipfile.ZipFile) and (mode & 0o170000) not in (0, 0o040000, 0o100000)) or (not isinstance(package, zipfile.ZipFile) and not (entry.isdir() or entry.isfile())):
                raise ValueError('Editor archives cannot contain links or special files')
            total += entry.file_size if isinstance(package, zipfile.ZipFile) else entry.size
            if total > MAX_EXPANDED:
                raise ValueError('Editor archive exceeds expanded limit')
            target = destination.joinpath(*path.parts)
            if directory:
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = package.open(entry) if isinstance(package, zipfile.ZipFile) else package.extractfile(entry)
            with source, target.open('xb') as output:
                shutil.copyfileobj(source, output)
            target.chmod(0o755 if mode & 0o111 else 0o644)


def acquire(directory):
    if platform.system() != 'Linux' or platform.machine().lower() not in ('x86_64', 'amd64'):
        raise ValueError('Pinned acquisition is currently Linux x86-64 development/CI only')
    node = shutil.which('node')
    if not node:
        raise ValueError('The Python language-service package needs the developer/CI Node runtime')
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    cache = directory / 'archives'
    cache.mkdir(exist_ok=True)
    config = {}
    for language, (url, algorithm, expected, executable) in ASSETS.items():
        archive = cache / url.rsplit('/', 1)[1]
        if not archive.exists():
            with tempfile.NamedTemporaryFile(dir=cache, delete=False) as output:
                temporary = Path(output.name)
                try:
                    size = 0
                    with urllib.request.urlopen(url, timeout=30) as response:
                        while chunk := response.read(1024 * 1024):
                            size += len(chunk)
                            if size > MAX_ARCHIVE:
                                raise ValueError('Editor archive exceeds download limit')
                            output.write(chunk)
                    output.flush()
                    if digest(temporary, algorithm) != expected:
                        raise ValueError('Editor archive integrity mismatch: ' + language)
                    os.replace(temporary, archive)
                finally:
                    temporary.unlink(missing_ok=True)
        if archive.stat().st_size > MAX_ARCHIVE or digest(archive, algorithm) != expected:
            raise ValueError('Cached editor archive integrity mismatch: ' + language)
        target = directory / language
        receipt = target / '.vwb-editor-tool.json'
        if not target.exists():
            with tempfile.TemporaryDirectory(dir=directory) as work:
                staged = Path(work) / 'tool'
                extract(archive, staged)
                matches = [p for p in staged.rglob(executable) if p.is_file()]
                if len(matches) != 1:
                    raise ValueError('Expected one language-server entry point: ' + language)
                entry = matches[0]
                if language != 'python':
                    entry.chmod(0o755)
                (staged / receipt.name).write_text(json.dumps({'url': url, 'archive_digest': expected, 'algorithm': algorithm,
                    'entry': entry.relative_to(staged).as_posix(), 'entry_sha256': digest(entry)}, indent=2) + '\n')
                staged.rename(target)
        record = json.loads(receipt.read_text())
        entry = (target / record['entry']).resolve()
        if not entry.is_relative_to(target) or record['archive_digest'] != expected or digest(entry) != record['entry_sha256']:
            raise ValueError('Installed editor tool differs from its receipt: ' + language)
        command = [node, str(entry), '--stdio'] if language == 'python' else [str(entry)]
        if language == 'sv_refactor':
            command.append('--lsp_enable_hover')
        config[language] = {'command': command}
    output = directory / 'editor-config.json'
    output.write_text(json.dumps(config, indent=2) + '\n')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path('.m0-tools/editor'))
    args = parser.parse_args()
    print(acquire(args.directory))
