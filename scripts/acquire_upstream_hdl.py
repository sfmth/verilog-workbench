#!/usr/bin/env python3
"""Acquire immutable, integrity-checked upstream HDL sources without running them.

The retained source archives and extracted trees include upstream licenses and
notices. Internal relative symbolic links are preserved after validating their
targets; archive members are never extracted through a link. Sources are not
edited, built, or executed by this command.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import tarfile
import tempfile
import urllib.request


DEFAULT_LOCK = Path(__file__).resolve().parents[1] / 'toolchains/upstream-hdl-lock.json'
MAX_ARCHIVE = 256 * 1024 * 1024
MAX_EXPANDED = 2 * 1024 * 1024 * 1024
MAX_ENTRIES = 100_000
RECEIPT = '.vwb-upstream-hdl.json'


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            result.update(chunk)
    return result.hexdigest()


def _relative(value):
    if not isinstance(value, str):
        raise ValueError('Expected a normalized relative source path')
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or any(ord(character) < 32 for character in value)
            or '..' in path.parts or '\\' in value or ':' in value
            or str(path) != value or value == '.'):
        raise ValueError('Expected a normalized relative source path')
    return path


def load_lock(path=DEFAULT_LOCK):
    lock = json.loads(Path(path).read_text(encoding='utf-8'))
    if (not isinstance(lock, dict) or lock.get('schema_version') != 1
            or not isinstance(lock.get('projects'), list) or not lock['projects']):
        raise ValueError('Unsupported or empty upstream HDL lock')
    seen = set()
    for project in lock['projects']:
        if not isinstance(project, dict):
            raise ValueError('Upstream project must be an object')
        name = project.get('id', '')
        revision = project.get('revision', '')
        repository = project.get('repository', '')
        if (not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', name) or name in seen
                or not re.fullmatch(r'[0-9a-f]{40}', revision)
                or not re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository)):
            raise ValueError('Invalid or duplicate upstream project identity')
        seen.add(name)
        archive = project.get('archive', {})
        expected_url = repository.replace('https://github.com/', 'https://codeload.github.com/') + '/tar.gz/' + revision
        if (not isinstance(archive, dict) or archive.get('url') != expected_url
                or not re.fullmatch(r'[0-9a-f]{64}', archive.get('sha256', ''))
                or type(archive.get('size')) is not int or not 0 < archive['size'] <= MAX_ARCHIVE):
            raise ValueError('Upstream archive must pin a full revision, SHA256, and bounded size')
        if len(_relative(archive.get('root', '')).parts) != 1:
            raise ValueError('Archive root must be one directory')
        license_record = project.get('license', {})
        if (not isinstance(license_record, dict) or not isinstance(license_record.get('spdx'), str)
                or not isinstance(license_record.get('paths'), list) or not license_record['paths']):
            raise ValueError('Upstream license and retained notice paths are required')
        for value in license_record['paths']:
            _relative(value)
        if not isinstance(project.get('source_roots', []), list):
            raise ValueError('Source roots must be a list of relative paths')
        for value in project.get('source_roots', []):
            _relative(value)
    return lock


def archive_manifest(archive, root):
    """Validate the complete archive and hash regular files without extracting."""
    manifest = {}
    total = 0
    with tarfile.open(archive, 'r:gz') as package:
        for member in package:
            path = _relative(member.name.rstrip('/'))
            if path.parts[0] != root:
                raise ValueError('Archive member is outside its pinned root')
            relative = '/'.join(path.parts[1:])
            if not relative:
                if not member.isdir():
                    raise ValueError('Archive root must be a directory')
                continue
            if relative in manifest or len(manifest) >= MAX_ENTRIES or relative == RECEIPT:
                raise ValueError('Duplicate, reserved, or excessive archive entries')
            if member.isdir():
                manifest[relative] = {'kind': 'directory'}
            elif member.isfile():
                total += member.size
                if member.size < 0 or total > MAX_EXPANDED:
                    raise ValueError('Expanded source archive exceeds limit')
                checksum = hashlib.sha256()
                stream = package.extractfile(member)
                with stream:
                    while chunk := stream.read(1024 * 1024):
                        checksum.update(chunk)
                manifest[relative] = {'kind': 'file', 'size': member.size, 'sha256': checksum.hexdigest(),
                                      'executable': bool(member.mode & 0o111)}
            elif member.issym():
                target = member.linkname
                if not target or target.startswith('/') or '\\' in target or ':' in target or '\x00' in target:
                    raise ValueError('Unsafe source symbolic link')
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(relative), target))
                if resolved == '..' or resolved.startswith('../'):
                    raise ValueError('Source symbolic link escapes its project')
                manifest[relative] = {'kind': 'symlink', 'target': target, 'resolved': resolved}
            else:
                raise ValueError('Source archive contains a hard link or special file')
    if not manifest:
        raise ValueError('Empty source archive')
    # Parents must be directories, so no file can be written through a link.
    for name in manifest:
        for parent in PurePosixPath(name).parents:
            if str(parent) != '.' and manifest.get(str(parent), {}).get('kind') != 'directory':
                raise ValueError('Archive parent is missing or is not a directory')
    for name, record in manifest.items():
        if record['kind'] != 'symlink':
            continue
        target = record['resolved']
        visited = {name}
        while target != '.':
            if target in visited:
                raise ValueError('Cyclic source symbolic link')
            visited.add(target)
            value = manifest.get(target)
            if not value:
                raise ValueError('Source symbolic link target is missing')
            if value['kind'] != 'symlink':
                break
            target = value['resolved']
    return manifest


def extract(archive, destination, root, manifest):
    destination.mkdir()
    with tarfile.open(archive, 'r:gz') as package:
        for member in package:
            relative = '/'.join(PurePosixPath(member.name.rstrip('/')).parts[1:])
            if not relative:
                continue
            record = manifest[relative]
            target = destination.joinpath(*PurePosixPath(relative).parts)
            if record['kind'] == 'directory':
                target.mkdir(parents=True, exist_ok=True)
            elif record['kind'] == 'file':
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.extractfile(member) as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output)
                target.chmod(0o755 if record['executable'] else 0o644)
    for name, record in manifest.items():
        if record['kind'] == 'symlink':
            destination.joinpath(*PurePosixPath(name).parts).symlink_to(
                record['target'], target_is_directory=(destination / record['resolved']).is_dir())


def verify_tree(root, manifest):
    """Compare every installed member with the verified archive, including extras."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Installed source project is not an ordinary directory')
    actual = set()
    for directory, directories, files in os.walk(root, followlinks=False):
        for entry in directories + files:
            path = Path(directory) / entry
            name = path.relative_to(root).as_posix()
            if name == RECEIPT:
                continue
            actual.add(name)
            record = manifest.get(name)
            if not record:
                raise ValueError('Unexpected installed source member: ' + name)
            if record['kind'] == 'symlink':
                if not path.is_symlink() or os.readlink(path) != record['target']:
                    raise ValueError('Installed source link differs: ' + name)
                try:
                    resolved = path.resolve(strict=True)
                except (OSError, RuntimeError) as error:
                    raise ValueError('Invalid installed source link: ' + name) from error
                if not resolved.is_relative_to(root):
                    raise ValueError('Installed source link escapes project: ' + name)
            elif path.is_symlink():
                raise ValueError('Unexpected installed source link: ' + name)
            elif record['kind'] == 'directory':
                if not path.is_dir():
                    raise ValueError('Installed source directory differs: ' + name)
            elif (not path.is_file() or path.stat().st_size != record['size'] or digest(path) != record['sha256']
                  or (os.name != 'nt' and bool(path.stat().st_mode & 0o111) != record['executable'])):
                raise ValueError('Installed source file differs: ' + name)
    if actual != set(manifest):
        raise ValueError('Installed source tree is incomplete')


def _archive(project, cache, offline):
    record = project['archive']
    path = cache / (project['id'] + '-' + project['revision'] + '.tar.gz')
    if not path.exists():
        if offline:
            raise ValueError('Pinned source archive is not cached: ' + project['id'])
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as output:
            temporary = Path(output.name)
            try:
                size = 0
                request = urllib.request.Request(record['url'], headers={'User-Agent': 'verilog-workbench-upstream-acquisition/1'})
                with urllib.request.urlopen(request, timeout=60) as response:
                    while chunk := response.read(1024 * 1024):
                        size += len(chunk)
                        if size > record['size']:
                            raise ValueError('Upstream download exceeds its pinned size')
                        output.write(chunk)
                output.flush()
                if size != record['size'] or digest(temporary) != record['sha256']:
                    raise ValueError('Upstream source archive integrity mismatch: ' + project['id'])
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
    if (path.is_symlink() or not path.is_file() or path.stat().st_size != record['size']
            or digest(path) != record['sha256']):
        raise ValueError('Cached upstream source archive integrity mismatch: ' + project['id'])
    return path


def acquire(directory, lock=DEFAULT_LOCK, projects=None, offline=False):
    locked = load_lock(lock)
    requested = set(projects or [p['id'] for p in locked['projects']])
    known = {p['id'] for p in locked['projects']}
    if not requested or requested - known:
        raise ValueError('Unknown or empty upstream project selection: ' + ', '.join(sorted(requested - known)))
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    cache = directory / 'archives'
    cache.mkdir(exist_ok=True)
    if cache.is_symlink():
        raise ValueError('Source cache cannot be a symbolic link')
    installed = directory / 'projects'
    installed.mkdir(exist_ok=True)
    if installed.is_symlink():
        raise ValueError('Source install directory cannot be a symbolic link')
    report = {'schema_version': 1, 'lock_sha256': digest(Path(lock)), 'projects': {}}
    for project in locked['projects']:
        name = project['id']
        if name not in requested:
            continue
        archive = _archive(project, cache, offline)
        manifest = archive_manifest(archive, project['archive']['root'])
        for notice in project['license']['paths']:
            if manifest.get(notice, {}).get('kind') != 'file':
                raise ValueError('Pinned upstream license/notice is missing: ' + notice)
        for source in project.get('source_roots', []):
            if source not in manifest:
                raise ValueError('Pinned upstream source root is missing: ' + source)
        tree_sha256 = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        target = installed / (name + '-' + project['revision'])
        receipt = {'schema_version': 1, 'id': name, 'repository': project['repository'], 'revision': project['revision'],
                   'archive': project['archive'], 'license': project['license'], 'tree_sha256': tree_sha256,
                   'file_count': sum(p['kind'] == 'file' for p in manifest.values()),
                   'symbolic_links': {p: v['target'] for p, v in manifest.items() if v['kind'] == 'symlink'}}
        if not target.exists() and not target.is_symlink():
            with tempfile.TemporaryDirectory(dir=installed) as work:
                staged = Path(work) / 'source'
                extract(archive, staged, project['archive']['root'], manifest)
                verify_tree(staged, manifest)
                (staged / RECEIPT).write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
                staged.rename(target)
        verify_tree(target, manifest)
        if (target / RECEIPT).is_symlink() or json.loads((target / RECEIPT).read_text(encoding='utf-8')) != receipt:
            raise ValueError('Installed source receipt differs: ' + name)
        report['projects'][name] = dict(receipt, root=str(target), archive_path=str(archive),
                                      source_roots=project.get('source_roots', []))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', type=Path, default=DEFAULT_LOCK)
    parser.add_argument('--directory', type=Path, default=Path('.upstream-hdl'))
    parser.add_argument('--project', action='append', help='Acquire selected project IDs; default: all pinned projects')
    parser.add_argument('--offline', action='store_true', help='Require all immutable archives to be present in the cache')
    parser.add_argument('--output', type=Path, help='Also save the complete provenance report as JSON')
    args = parser.parse_args()
    try:
        report = acquire(args.directory, args.lock, args.project, args.offline)
    except (ValueError, OSError, tarfile.TarError) as error:
        parser.exit(1, 'Upstream HDL acquisition failed: ' + str(error) + '\n')
    serialized = json.dumps(report, indent=2) + '\n'
    if args.output:
        args.output.write_text(serialized, encoding='utf-8')
    print(serialized, end='')


if __name__ == '__main__':
    main()
