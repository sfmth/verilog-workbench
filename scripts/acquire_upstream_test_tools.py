#!/usr/bin/env python3
"""Install hash-pinned upstream verification profiles outside VWB's own runtime.

Downloads are explicit, bounded and verified. Pip uses only those local artifacts
with dependency resolution and build isolation disabled. Original MyHDL VPI code
is compiled against the selected native Icarus installation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.request

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

PROFILE_NAME = re.compile(r'[a-z0-9][a-z0-9-]{0,63}\Z')
ARTIFACT_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.+-]*\Z')


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024 * 1024): result.update(chunk)
    return result.hexdigest()


def download(record, cache, offline=False):
    from urllib.parse import urlsplit
    url = urlsplit(record['url'])
    if (url.scheme != 'https' or url.hostname not in {'files.pythonhosted.org', 'cpan.metacpan.org'}
            or type(record['size']) is not int or not 0 < record['size'] <= 64 * 1024 * 1024
            or len(record['sha256']) != 64 or any(c not in '0123456789abcdef' for c in record['sha256'])):
        raise ValueError('Verification dependency requires an official URL and pinned bounded artifact')
    name = record.get('filename') or Path(url.path).name
    if not isinstance(name, str) or not ARTIFACT_NAME.fullmatch(name):
        raise ValueError('Invalid verification artifact name')
    path = cache / name
    if path.is_symlink(): raise ValueError('Dependency cache cannot contain symbolic links')
    if not path.exists():
        if offline: raise ValueError('Pinned verification artifact is not cached: ' + name)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=cache, delete=False) as output:
                temporary = Path(output.name)
                request = urllib.request.Request(record['url'], headers={'User-Agent': 'vwb-upstream-verification/1'})
                size = 0
                with urllib.request.urlopen(request, timeout=60) as response:
                    while chunk := response.read(1024 * 1024):
                        size += len(chunk)
                        if size > record['size']: raise ValueError('Verification download exceeds pinned size')
                        output.write(chunk)
            if size != record['size'] or digest(temporary) != record['sha256']:
                raise ValueError('Verification artifact integrity mismatch')
            temporary.replace(path)
        finally:
            if temporary is not None: temporary.unlink(missing_ok=True)
    if not path.is_file() or path.stat().st_size != record['size'] or digest(path) != record['sha256']:
        raise ValueError('Cached verification artifact integrity mismatch: ' + name)
    return path


def tree_files(root):
    paths = sorted(root.rglob('*'))
    if any(path.is_symlink() for path in paths):
        raise ValueError('Installed verification profiles cannot contain symbolic links')
    return {path.relative_to(root).as_posix(): digest(path) for path in paths
            if path.is_file() and not path.is_symlink() and '__pycache__' not in path.parts and path.suffix != '.pyc'
            and path != root / '.vwb-profile.json'}


def python_shims(directory, python):
    """Upstream `env python` generators must use the suite's matching runtime."""
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError('Managed Python shim directory cannot be a symbolic link')
    directory.mkdir(parents=True, exist_ok=True)
    source = '#!/bin/sh\nexec ' + shlex.quote(python) + ' "$@"\n'
    for alias in ('python', 'python3'):
        path = directory / alias
        if path.is_symlink() or (path.exists() and (not path.is_file() or path.read_text() != source)):
            raise ValueError('Managed Python shim changed: ' + alias)
        if not path.exists():
            path.write_text(source)
            path.chmod(0o755)
        if not path.stat().st_mode & 0o111:
            raise ValueError('Managed Python shim is not executable: ' + alias)
    return directory


def install_profile(name, definition, packages, directory, cache, python, base_env, lock_hash, offline):
    if not PROFILE_NAME.fullmatch(name) or not re.fullmatch(r'[0-9a-f]{64}', lock_hash):
        raise ValueError('Invalid verification profile name or lock digest')
    target = directory / 'profiles' / (name + '-' + lock_hash[:16])
    if target.parent.is_symlink(): raise ValueError('Verification profile parent cannot be a symbolic link')
    receipt_path = target / '.vwb-profile.json'
    selected = list(dict.fromkeys([*definition.get('build_packages', []), *definition['packages']]))
    artifacts = {name: download(packages[name]['artifact'], cache, offline) for name in selected}
    if target.is_symlink(): raise ValueError('Verification profile cannot be a symbolic link')
    if target.exists():
        if receipt_path.is_symlink(): raise ValueError('Verification receipt cannot be a symbolic link')
        receipt = json.loads(receipt_path.read_text())
        if receipt['lock_sha256'] != lock_hash or receipt['python'] != python or tree_files(target) != receipt['files']:
            raise ValueError('Installed verification profile changed; remove the named profile before rebuilding')
        return receipt
    target.parent.mkdir(parents=True, exist_ok=True)
    # The final installation path is deliberate: pip console-script shebangs
    # must not refer to a temporary staging name that disappears after install.
    target.mkdir()
    try:
        site = target / 'site'
        shim = python_shims(target / 'bin', python)
        environment = dict(base_env, PYTHONPATH=str(site), PYTHON_BIN=python, PYGPI_PYTHON_BIN=python,
                           PYTHONNOUSERSITE='1', PATH=str(shim) + os.pathsep + str(site / 'bin') + os.pathsep + base_env['PATH'])
        for names in (definition.get('build_packages', []), definition['packages']):
            if not names: continue
            command = [python, '-m', 'pip', 'install', '--disable-pip-version-check', '--no-index', '--no-deps',
                       '--no-build-isolation', '--no-cache-dir', '--upgrade', '--target', str(site), *[str(artifacts[name]) for name in names]]
            subprocess.run(command, env=environment, check=True, timeout=300)
        for build in definition.get('native_builds', []):
            cwd = (target / build['cwd']).resolve(strict=True)
            if not cwd.is_relative_to(target) or build['argv'] != ['make']:
                raise ValueError('Unexpected verification native build')
            for tool in build['tools']:
                if shutil.which(tool, path=environment['PATH']) is None: raise ValueError('Missing verification build tool: ' + tool)
            subprocess.run(build['argv'], cwd=cwd, env=environment, check=True, timeout=120)
            for output in build['outputs']:
                output_path = cwd / output
                if output_path.is_symlink() or not output_path.resolve().is_relative_to(cwd) or not output_path.is_file():
                    raise ValueError('Verification native plugin was not built')
            environment.update({key: value.replace('${profile}', str(target)) for key, value in build.get('environment', {}).items()})
        kept = {key: value for key, value in environment.items() if key in {'PATH', 'PYTHONPATH', 'PYTHON_BIN', 'PYGPI_PYTHON_BIN',
                'PYTHONNOUSERSITE', 'IVERILOG_VPI_MODULE_PATH', 'VIRTUAL_ENV', 'VERILATOR_ROOT', 'GHDL_PREFIX'}}
        receipt = {'schema_version': 1, 'name': name, 'python': python, 'lock_sha256': lock_hash,
                   'root': str(target), 'packages': {name: packages[name]['version'] for name in selected},
                   'environment': kept, 'files': tree_files(target)}
        receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
        return receipt
    except BaseException:
        shutil.rmtree(target)
        raise


def install_perl(directory, cache, offline):
    from scripts.acquire_upstream_native import acquire
    return acquire(directory / 'native', offline=offline)['environment']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=REPO / '.upstream-tools')
    parser.add_argument('--toolchain-acquisition', type=Path, required=True)
    parser.add_argument('--profile', action='append')
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if platform.system() != 'Linux' or platform.machine().lower() not in {'x86_64', 'amd64'}:
        parser.error('These pinned upstream verification profiles currently target Linux x64')
    lock_path = REPO / 'toolchains/upstream-python-lock.json'
    lock = json.loads(lock_path.read_text())
    if lock.get('schema_version') != 1: parser.error('Unsupported Python verification dependency lock')
    acquisition = json.loads(args.toolchain_acquisition.read_text())
    environment = os.environ.copy()
    for name in ('PYTHONHOME', 'PYTHONPATH', 'LD_LIBRARY_PATH', 'GPI_USERS', 'PYGPI_USERS', 'PYGPI_ENTRY_POINT'):
        environment.pop(name, None)
    environment.update(acquisition['environment'])
    python = acquisition.get('probe', {}).get('python') or str(Path(acquisition['suitePath']) / 'py3bin/python3')
    version = subprocess.check_output([python, '-c', 'import sys;print("%d.%d"%sys.version_info[:2])'], env=environment, text=True).strip()
    if version != '3.11': parser.error('Pinned verification wheels require the suite CPython 3.11')
    directory = args.directory.resolve(); directory.mkdir(parents=True, exist_ok=True)
    cache = directory / 'python-artifacts'; cache.mkdir(exist_ok=True)
    if cache.is_symlink(): parser.error('Verification artifact cache cannot be a symbolic link')
    common_shims = python_shims(directory / 'python-bin', python)
    native_environment = {'PATH': str(common_shims) + os.pathsep + environment['PATH'], 'PYTHON_BIN': python,
                          'PYGPI_PYTHON_BIN': python, 'PYTHONNOUSERSITE': '1'}
    native_environment.update(install_perl(directory, cache, args.offline))
    environment.update(native_environment)
    report = {'schema_version': 1, 'lock_sha256': digest(lock_path), 'python': python, 'profiles': {},
              'native_environment': native_environment}
    for name in args.profile or list(lock['profiles']):
        if name not in lock['profiles']: parser.error('Unknown verification Python profile')
        receipt = install_profile(name, lock['profiles'][name], lock['packages'], directory, cache, python, environment, report['lock_sha256'], args.offline)
        report['profiles'][name] = {key: value for key, value in receipt.items() if key != 'files'}
        report['profiles'][name]['tree_sha256'] = hashlib.sha256(json.dumps(receipt['files'], sort_keys=True).encode()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print('Acquired upstream verification profiles: ' + ', '.join(report['profiles']))


if __name__ == '__main__':
    main()
