#!/usr/bin/env python3
"""Acquire the small pinned Perl libraries used by NVDLA's original generator.

This only downloads and extracts checked archives. No package installer or
upstream build script is executed. The complete tree is reverified on reuse.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import urllib.request

try:
    from .acquire_upstream_hdl import archive_manifest, digest, extract, verify_tree
except ImportError:  # Direct execution from scripts/.
    from acquire_upstream_hdl import archive_manifest, digest, extract, verify_tree


DEFAULT_LOCK = Path(__file__).resolve().parents[1] / 'toolchains/upstream-native-dependencies.json'
ALLOWED = {
    'perl-xml-simple': 'https://cpan.metacpan.org/authors/id/G/GR/GRANTM/XML-Simple-2.25.tar.gz',
    'perl-yaml': 'https://cpan.metacpan.org/authors/id/I/IN/INGY/YAML-1.31.tar.gz',
}


def acquire(directory, lock=DEFAULT_LOCK, *, offline=False):
    lock = Path(lock)
    document = json.loads(lock.read_text(encoding='utf-8'))
    artifacts = document.get('artifacts', [])
    if document.get('schema_version') != 1 or len(artifacts) != len(ALLOWED):
        raise ValueError('Invalid native dependency lock')
    names = set()
    for item in artifacts:
        name = item.get('id')
        if (name in names or name not in ALLOWED or item.get('url') != ALLOWED[name]
                or type(item.get('size')) is not int or not 0 < item['size'] <= 1024 * 1024
                or not re.fullmatch(r'[0-9a-f]{64}', item.get('sha256', ''))
                or not re.fullmatch(r'[A-Za-z0-9_.-]+', item.get('root', ''))
                or item.get('library_subdirectory') != 'lib'):
            raise ValueError('Invalid, unbounded, or duplicate native dependency')
        names.add(name)
    destination = Path(directory).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    report = {'schema_version': 1, 'lock_sha256': digest(lock), 'artifacts': {}, 'environment': {}}
    libraries = []
    for item in artifacts:
        name = item['id']
        archive = destination / (item['root'] + '.tar.gz')
        if not archive.exists():
            if offline:
                raise ValueError('Missing offline native dependency: ' + name)
            with urllib.request.urlopen(item['url'], timeout=60) as source:
                body = source.read(item['size'] + 1)
            if len(body) != item['size'] or hashlib.sha256(body).hexdigest() != item['sha256']:
                raise ValueError('Native dependency archive integrity failure: ' + name)
            with tempfile.NamedTemporaryFile(dir=destination, delete=False) as staged:
                staged.write(body)
                temporary = Path(staged.name)
            temporary.replace(archive)
        if archive.is_symlink() or archive.stat().st_size != item['size'] or digest(archive) != item['sha256']:
            raise ValueError('Cached native dependency integrity failure: ' + name)
        manifest = archive_manifest(archive, item['root'])
        target = destination / item['root']
        if target.is_symlink():
            raise ValueError('Native dependency root may not be a symbolic link')
        if not target.exists():
            with tempfile.TemporaryDirectory(prefix='native-dependency-', dir=destination) as folder:
                staged = Path(folder) / 'tree'
                extract(archive, staged, item['root'], manifest)
                verify_tree(staged, manifest)
                shutil.move(str(staged), str(target))
        verify_tree(target, manifest)
        library = target / 'lib'
        if not library.is_dir() or library.is_symlink():
            raise ValueError('Missing native dependency library: ' + name)
        libraries.append(str(library))
        report['artifacts'][name] = dict(item, path=str(target), archive_path=str(archive))
    report['environment']['PERL5LIB'] = os.pathsep.join(libraries)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', default='.upstream-tools/native')
    parser.add_argument('--lock', type=Path, default=DEFAULT_LOCK)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        report = acquire(args.directory, args.lock, offline=args.offline)
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + '\n')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print('Acquired %d pinned native dependencies' % len(report['artifacts']))


if __name__ == '__main__':
    main()
