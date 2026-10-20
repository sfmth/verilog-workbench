#!/usr/bin/env python3
"""Acquire the pinned RISC-V compiler for original upstream firmware tests.

The archive and every installed member are verified on each invocation. Safe
archive graph validation, bounded extraction, and internal-link handling are
shared with the HDL source acquirer. This command never executes package scripts.
"""
import argparse
import hashlib
import json
import platform
from pathlib import Path
import re
import tarfile
import tempfile

import acquire_upstream_hdl as archive_tools


DEFAULT_LOCK = Path(__file__).resolve().parents[1] / 'toolchains/upstream-riscv-toolchain-lock.json'
MAX_ARCHIVE = 512 * 1024 * 1024


def load_lock(path=DEFAULT_LOCK):
    lock = json.loads(Path(path).read_text(encoding='utf-8'))
    if (not isinstance(lock, dict) or lock.get('schema_version') != 1
            or lock.get('id') != 'xpack-riscv-none-elf-gcc'
            or lock.get('target') != 'linux-x64'
            or lock.get('repository') != 'https://github.com/xpack-dev-tools/riscv-none-elf-gcc-xpack'
            or lock.get('toolchain_prefix') != 'riscv-none-elf-'
            or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+-[0-9]+', lock.get('version', ''))):
        raise ValueError('Unsupported RISC-V compiler identity or target')
    version = lock['version']
    archive = lock.get('archive', {})
    expected_root = 'xpack-riscv-none-elf-gcc-' + version
    expected_url = (lock['repository'] + '/releases/download/v' + version + '/'
                    + expected_root + '-linux-x64.tar.gz')
    if (not isinstance(archive, dict) or archive.get('url') != expected_url
            or archive.get('root') != expected_root
            or not re.fullmatch(r'[0-9a-f]{64}', archive.get('sha256', ''))
            or type(archive.get('size')) is not int or not 0 < archive['size'] <= MAX_ARCHIVE):
        raise ValueError('Compiler archive requires an official release URL, SHA256, and bounded size')
    if not isinstance(lock.get('license_paths'), list) or not lock['license_paths']:
        raise ValueError('Retained compiler license paths are required')
    for notice in lock['license_paths']:
        archive_tools._relative(notice)
    return lock


def acquire(directory, lock=DEFAULT_LOCK, offline=False):
    locked = load_lock(lock)
    if platform.system() != 'Linux' or platform.machine().lower() not in ('x86_64', 'amd64'):
        raise ValueError('This pinned compiler is available for Linux x64 only')
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    cache = directory / 'archives'
    installed = directory / 'riscv'
    for path in (cache, installed):
        path.mkdir(exist_ok=True)
        if path.is_symlink():
            raise ValueError('Compiler acquisition directories cannot be symbolic links')
    project = {'id': locked['id'], 'revision': locked['version'], 'archive': locked['archive']}
    archive = archive_tools._archive(project, cache, offline)
    manifest = archive_tools.archive_manifest(archive, locked['archive']['root'])
    for notice in locked['license_paths']:
        if manifest.get(notice, {}).get('kind') != 'file':
            raise ValueError('Retained compiler license is missing: ' + notice)
    for binary in ('gcc', 'objcopy', 'objdump'):
        if manifest.get('bin/' + locked['toolchain_prefix'] + binary, {}).get('kind') != 'file':
            raise ValueError('Pinned RISC-V compiler executable is missing: ' + binary)
    tree_sha256 = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    receipt = {'schema_version': 1, 'id': locked['id'], 'version': locked['version'],
               'archive': locked['archive'], 'tree_sha256': tree_sha256,
               'lock_sha256': archive_tools.digest(Path(lock)),
               'license_paths': locked['license_paths']}
    target = installed / locked['archive']['root']
    if not target.exists() and not target.is_symlink():
        with tempfile.TemporaryDirectory(dir=installed) as work:
            staged = Path(work) / 'compiler'
            archive_tools.extract(archive, staged, locked['archive']['root'], manifest)
            archive_tools.verify_tree(staged, manifest)
            (staged / archive_tools.RECEIPT).write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
            staged.rename(target)
    archive_tools.verify_tree(target, manifest)
    receipt_path = target / archive_tools.RECEIPT
    if receipt_path.is_symlink() or json.loads(receipt_path.read_text(encoding='utf-8')) != receipt:
        raise ValueError('Installed compiler receipt differs from the pinned lock')
    return dict(receipt, root=str(target), archive_path=str(archive),
                bin_directory=str(target / 'bin'), toolchain_prefix=locked['toolchain_prefix'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', type=Path, default=DEFAULT_LOCK)
    parser.add_argument('--directory', type=Path, default=Path('.upstream-tools'))
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        report = acquire(args.directory, args.lock, args.offline)
    except (ValueError, OSError, tarfile.TarError) as error:
        parser.exit(1, 'RISC-V compiler acquisition failed: ' + str(error) + '\n')
    serialized = json.dumps(report, indent=2) + '\n'
    if args.output:
        args.output.write_text(serialized, encoding='utf-8')
    print(serialized, end='')


if __name__ == '__main__':
    main()
