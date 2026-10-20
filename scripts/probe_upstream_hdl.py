#!/usr/bin/env python3
"""Run the required pinned upstream design corpus through the real VWB CLI.

Every default case is mandatory. A selected subset is reported as a subset,
never as a complete corpus pass. No upstream build scripts are executed.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from companion.process import run_process
from scripts.acquire_upstream_hdl import acquire, load_lock

DEFAULT_MANIFEST = REPO / 'toolchains/upstream-designs.json'


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def relative_path(value):
    if not isinstance(value, str):
        raise ValueError(f'Expected normalized relative path: {value!r}')
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or '..' in path.parts or '\\' in value or ':' in value
            or str(path) != value or any(ord(character) < 32 for character in value)):
        raise ValueError(f'Expected normalized relative path: {value!r}')
    return path


def repository_file(value):
    path = (REPO / relative_path(value)).resolve(strict=True)
    if not path.is_relative_to(REPO.resolve()) or not path.is_file():
        raise ValueError('Corpus recipe or assertion bench must remain in the repository')
    return path


def load_designs(path=DEFAULT_MANIFEST):
    manifest = json.loads(Path(path).read_text(encoding='utf-8'))
    if (not isinstance(manifest, dict) or manifest.get('schema_version') != 1
            or type(manifest.get('minimum_designs')) is not int or manifest['minimum_designs'] < 20):
        raise ValueError('Corpus must require at least 20 designs')
    if not isinstance(manifest.get('designs', []), list) or not isinstance(manifest.get('recipe_files', []), list):
        raise ValueError('Corpus designs and recipe files must be lists')
    designs = list(manifest.get('designs', []))
    for name in manifest.get('recipe_files', []):
        recipe = repository_file(name)
        records = json.loads(recipe.read_text(encoding='utf-8'))
        records = records.get('designs') if isinstance(records, dict) else records
        if not isinstance(records, list):
            raise ValueError('Corpus recipe must contain a list of designs')
        designs.extend(records)
    identities, tops = set(), set()
    for case in designs:
        if not isinstance(case, dict):
            raise ValueError('Corpus design must be an object')
        identity = case.get('id', '')
        pair = (case.get('project'), case.get('top'))
        if (not isinstance(identity, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', identity)
                or identity in identities):
            raise ValueError(f'Duplicate or invalid design identity/top: {identity}')
        if (not isinstance(pair[0], str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', pair[0])
                or not isinstance(pair[1], str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$]*', pair[1])
                or pair in tops):
            raise ValueError(f'Duplicate or invalid design identity/top: {identity}')
        identities.add(identity)
        tops.add(pair)
        if case.get('kind') not in ('functional_simulation', 'lint_elaboration'):
            raise ValueError(f'Unknown coverage kind: {identity}')
        if (case.get('simulator') not in ('icarus', 'verilator')
                or not isinstance(case.get('sources'), list) or not case['sources']):
            raise ValueError(f'Missing engine or sources: {identity}')
        if case['kind'] == 'lint_elaboration' and case['simulator'] != 'verilator':
            raise ValueError(f'Static elaboration requires Verilator: {identity}')
        for source in case['sources']:
            relative_path(source)
        if not isinstance(case.get('include_dirs', []), list):
            raise ValueError(f'Include directories must be a list: {identity}')
        for directory in case.get('include_dirs', []):
            relative_path(directory)
        for key in ('defines', 'verilator_args'):
            values = case.get(key, [])
            if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
                raise ValueError(f'{key} must contain nonempty string arguments: {identity}')
        if type(case.get('negative_control', False)) is not bool:
            raise ValueError(f'Negative control flag must be boolean: {identity}')
        if ('negative_control_error' in case and (not isinstance(case['negative_control_error'], str)
                                                  or not case['negative_control_error'])):
            raise ValueError(f'Expected negative-control assertion must be a nonempty string: {identity}')
        if case['kind'] == 'functional_simulation':
            relative_path(case.get('bench'))
            if (not isinstance(case.get('bench_top'), str)
                    or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$]*', case['bench_top'])):
                raise ValueError(f'Missing assertion bench top: {identity}')
        elif case.get('negative_control'):
            raise ValueError(f'Negative assertion controls require functional simulation: {identity}')
    if len(designs) < manifest['minimum_designs']:
        raise ValueError(f"Only {len(designs)} designs; require {manifest['minimum_designs']}")
    return manifest, designs


def command_for(case):
    if case['kind'] == 'functional_simulation':
        command = ['test', case['top'], '--test', 'test/' + Path(case['bench']).name,
                   '--test-top', case['bench_top'], '--test-language', 'verilog', '--simulator', case['simulator']]
    else:
        command = ['lint', case['top'], '--linter', case['simulator']]
    for name in case.get('defines', []):
        command.extend(['--define', name])
    for name in case.get('include_dirs', []):
        command.extend(['--include', 'src/' + name])
    for value in case.get('verilator_args', []):
        command.append('--verilator-arg=' + value)
    return command


def inspect_result(case, result, log):
    if result.timed_out:
        return False, 'VWB exceeded its bounded process-tree timeout'
    if result.returncode != 0:
        return False, 'VWB returned a nonzero exit status'
    if case['kind'] == 'functional_simulation':
        marker = 'VWB_UPSTREAM_PASS ' + case['id']
        if not any(line.strip() == marker for line in log.splitlines()):
            return False, 'Missing assertion completion marker: ' + marker
        return True, 'Directed HDL assertions completed; this is not exhaustive verification'
    return True, 'Verilator parsed and elaborated the selected top; functional behavior is not claimed'


def inspect_negative_control(case, result, log):
    """Require an actual simulator assertion diagnostic, not a compiler error."""
    expected = case.get('negative_control_error', 'mismatch')
    fatal = any(expected in line and (
        line.startswith('FATAL: ') or
        (re.search(r'(?:^|\s)%(?:Fatal|Error): ', line) and 'Assertion failed' in line)
    ) for line in log.splitlines())
    return (not result.timed_out and result.returncode != 0 and fatal
            and 'VWB_UPSTREAM_PASS ' + case['id'] not in log)


def stage_case(case, project, destination):
    source_root = Path(project['root']).resolve(strict=True)
    selected = {}
    for pattern in case['sources']:
        relative_path(pattern)
        paths = sorted(source_root.glob(pattern))
        files = [path for path in paths if path.is_file()]
        if not files:
            raise ValueError(f"Source pattern matched no files: {case['id']}: {pattern}")
        for path in files:
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(source_root):
                raise ValueError('Selected source escapes its upstream tree')
            name = path.relative_to(source_root).as_posix()
            selected[name] = path
    for name, path in selected.items():
        target = destination / 'src' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    (destination / 'test').mkdir(parents=True, exist_ok=True)
    if case.get('bench'):
        bench = repository_file(case['bench'])
        shutil.copyfile(bench, destination / 'test' / bench.name)
    return {name: sha256(path) for name, path in selected.items()}


def run_corpus(designs, acquisition, destination, python, environment, timeout, only=None):
    if only is not None:
        if not only or len(set(only)) != len(only):
            raise ValueError('Selected design IDs must be nonempty and unique')
        if set(only) - {case['id'] for case in designs}:
            raise ValueError('Unknown selected design IDs')
    selected = designs if only is None else [case for case in designs if case['id'] in only]
    results = []
    for case in selected:
        started = time.monotonic()
        item = {key: case[key] for key in ('id', 'project', 'top', 'kind', 'simulator')}
        root = destination / case['id']
        try:
            root.mkdir()
            project = acquisition['projects'][case['project']]
            item.update(repository=project['repository'], revision=project['revision'], license=project['license'],
                        archive_sha256=project['archive']['sha256'], tree_sha256=project['tree_sha256'])
            item['source_sha256'] = stage_case(case, project, root)
            if case.get('bench'):
                item['bench_sha256'] = sha256(REPO / case['bench'])
            argv = [python, str(REPO / 'vwb.py'), '--root', str(root), '--color', 'never', '--verbose', *command_for(case)]
            item['command'] = argv
            result = run_process(argv, cwd=root, env=environment, timeout=timeout, max_output_bytes=4_000_000)
            log = result.stdout + '\n' + result.stderr
            log_path = destination / (case['id'] + '.log')
            log_path.write_text(log, encoding='utf-8')
            passed, note = inspect_result(case, result, log)
            item.update(state='passed' if passed else 'failed', note=note, returncode=result.returncode,
                        log=str(log_path), log_sha256=sha256(log_path))
            if not passed:
                item['log_excerpt'] = log[-6000:]
            if passed and case.get('negative_control'):
                negative_argv = [*argv, '--plusarg', 'VWB_NEGATIVE_CONTROL']
                negative = run_process(negative_argv, cwd=root, env=environment, timeout=timeout, max_output_bytes=4_000_000)
                negative_log = negative.stdout + '\n' + negative.stderr
                negative_path = destination / (case['id'] + '-negative.log')
                negative_path.write_text(negative_log, encoding='utf-8')
                expected = case.get('negative_control_error', 'mismatch')
                rejected = inspect_negative_control(case, negative, negative_log)
                item['negative_control'] = dict(passed=rejected, returncode=negative.returncode,
                                                expected_error=expected, command=negative_argv,
                                                log=str(negative_path), log_sha256=sha256(negative_path))
                if not rejected:
                    item.update(state='failed', note='Injected expected-value mismatch was not rejected by the assertion bench',
                                log_excerpt=negative_log[-6000:])
        except (OSError, ValueError, KeyError) as error:
            item.update(state='failed', error=str(error))
        item['duration_seconds'] = round(time.monotonic() - started, 3)
        results.append(item)
        print(f"{case['id']}: {item['state']} ({item['duration_seconds']}s)", flush=True)
    complete = only is None and len(results) == len(designs)
    return {'schema_version': 1, 'scope': 'complete_corpus' if complete else 'selected_subset',
            'required_designs': len(designs), 'executed_designs': len(results),
            'coverage_counts': dict(Counter(case['kind'] for case in selected)),
            'passed': bool(results) and all(item['state'] == 'passed' for item in results),
            'complete_corpus_passed': complete and bool(results) and all(item['state'] == 'passed' for item in results),
            'designs': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument('--acquisition', type=Path, required=True)
    parser.add_argument('--toolchain-acquisition', type=Path)
    parser.add_argument('--work-dir', type=Path, default=REPO / '.upstream-evidence')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--only', action='append')
    parser.add_argument('--timeout', type=float, default=180)
    args = parser.parse_args()
    if not 0 < args.timeout <= 900:
        parser.error('--timeout must be greater than zero and at most 900 seconds')
    manifest, designs = load_designs(args.manifest)
    acquisition = json.loads(args.acquisition.read_text(encoding='utf-8'))
    # Recheck pinned archives and the complete extracted trees, even on reuse.
    load_lock()
    if acquisition.get('lock_sha256') != sha256(REPO / 'toolchains/upstream-hdl-lock.json'):
        raise ValueError('Acquisition does not match the current upstream lock')
    needed = {case['project'] for case in designs if not args.only or case['id'] in args.only}
    for directory in {Path(acquisition['projects'][identity]['root']).parents[1] for identity in needed}:
        group = [identity for identity in needed if Path(acquisition['projects'][identity]['root']).parents[1] == directory]
        verified = acquire(directory, projects=group, offline=True)
        for identity in group:
            previous = acquisition['projects'][identity]
            checked = verified['projects'][identity]
            if checked['tree_sha256'] != previous['tree_sha256'] or checked['root'] != previous['root']:
                raise ValueError('Acquisition source tree identity changed')
    environment = os.environ.copy()
    for name in ('PYTHONHOME', 'PYTHONPATH', 'LD_LIBRARY_PATH'):
        environment.pop(name, None)
    python = sys.executable
    toolchain = None
    if args.toolchain_acquisition:
        toolchain = json.loads(args.toolchain_acquisition.read_text(encoding='utf-8'))
        environment.update(toolchain['environment'])
        python = toolchain.get('probe', {}).get('python') or str(Path(toolchain['suitePath']) / 'py3bin/python3')
    args.work_dir.mkdir(parents=True, exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix='corpus-', dir=args.work_dir)).resolve()
    report = run_corpus(designs, acquisition, destination, python, environment, args.timeout, args.only)
    report.update(minimum_designs=manifest['minimum_designs'], manifest_sha256=sha256(args.manifest),
                  recipe_sha256={name: sha256(REPO / name) for name in manifest.get('recipe_files', [])},
                  lock_sha256=acquisition['lock_sha256'], evidence_directory=str(destination),
                  toolchain_archive=toolchain.get('archive') if toolchain else None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f"Upstream designs: {sum(item['state'] == 'passed' for item in report['designs'])}/{report['executed_designs']} passed; {report['scope']}")
    return int(not report['passed'])


if __name__ == '__main__':
    raise SystemExit(main())
