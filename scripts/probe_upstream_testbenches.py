#!/usr/bin/env python3
"""Run unchanged original upstream verification flows through ``vwb flow``.

Unlike the supplemental directed corpus, these flows retain the upstream
testbench, firmware, driver, generators and result reporting. Required flows
never pass through skips, unavailable tools or elaboration-only substitutes.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from companion.process import run_process
from companion.flows import ENVIRONMENT_KEYS, load_flows
from scripts.acquire_upstream_hdl import acquire

REGISTRIES = ('stream-serial.json', 'core-flows.json', 'nvdla-flows.json')


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024): value.update(block)
    return value.hexdigest()


def load_registry():
    flows = []
    ids = set()
    for name in REGISTRIES:
        data = json.loads((REPO / 'upstream/benchmarks' / name).read_text())
        if data.get('schema_version') != 1: raise ValueError('Unknown upstream verification registry schema')
        for flow in data['flows']:
            if flow['id'] in ids: raise ValueError('Duplicate upstream flow ID')
            ids.add(flow['id'])
            if flow.get('status') not in {'required', 'external_required'}:
                raise ValueError('Every upstream flow must explicitly declare its verification status')
            flows.append(flow)
    designs = {(flow['project'], flow['design_top']) for flow in flows if flow['status'] == 'required'}
    if len(designs) < 20: raise ValueError('At least 20 distinct original upstream designs must be required')
    return flows


def aggregate_results(required, selected, results, only=None):
    """Validate result identities and verification verdicts before claiming coverage."""
    required_ids = [flow['id'] for flow in required]
    selected_ids = [flow['id'] for flow in selected]
    result_ids = [item.get('id') for item in results]
    canonical = {flow['id']: flow for flow in required}
    selected_map = {flow['id']: flow for flow in selected}
    required_designs = {(flow['project'], flow['design_top']) for flow in required if flow['status'] == 'required'}
    errors = []
    if len(set(required_ids)) != len(required_ids) or any(flow['status'] != 'required' for flow in required):
        errors.append('Required registry contains duplicate IDs or an external flow')
    if len(required_designs) < 20:
        errors.append('Required registry does not cover 20 distinct original designs')
    if (len(set(selected_ids)) != len(selected_ids) or set(selected_ids) - set(required_ids)
            or any(flow.get('status') != 'required' for flow in selected)):
        errors.append('Selected flows must be unique required flow IDs')
    if only is not None and (len(set(only)) != len(only) or set(only) != set(selected_ids)):
        errors.append('Executed selection differs from its explicit --only IDs')
    for flow in selected:
        expected = canonical.get(flow['id'])
        if expected is None or (flow.get('project'), flow.get('design_top')) != (expected['project'], expected['design_top']):
            errors.append('Selected flow identity differs from the required registry: ' + flow['id'])
    if len(set(result_ids)) != len(result_ids):
        errors.append('Duplicate upstream result IDs')
    missing = set(selected_ids) - set(result_ids)
    unexpected = set(result_ids) - set(selected_ids)
    if missing:
        errors.append('Missing upstream results: ' + ', '.join(sorted(missing)))
    if unexpected:
        errors.append('Unexpected upstream results: ' + ', '.join(sorted(map(str, unexpected))))
    executed_designs = set()
    verified = []
    junit_cases = 0
    for item in results:
        flow = selected_map.get(item.get('id'))
        if flow is None:
            verified.append(False)
            continue
        if (item.get('project'), item.get('design_top')) != (flow['project'], flow['design_top']):
            errors.append('Result project or design differs from its selected flow: ' + flow['id'])
            verified.append(False)
            continue
        executed_designs.add((flow['project'], flow['design_top']))
        verdict = item.get('verdict') or {}
        expected = flow['expected']
        valid = (item.get('state') == 'passed' and item.get('returncode') == 0
                 and item.get('timed_out') is False and not item.get('original_files_changed')
                 and verdict.get('passed') is True and verdict.get('kind') == expected['kind'])
        if expected['kind'] == 'junit':
            counters = [verdict.get(name) for name in ('tests', 'failures', 'errors', 'skipped')]
            counts_valid = all(type(value) is int and value >= 0 for value in counters)
            if counts_valid:
                junit_cases += counters[0]
            valid = (valid and counts_valid and counters[0] >= expected.get('minimum_tests', 1)
                     and counters[1:] == [0, 0, 0])
        elif expected['kind'] == 'log':
            valid = valid and verdict.get('missing_patterns') == []
        else:
            valid = False
        verified.append(valid)
    identities_valid = not errors and len(results) == len(selected)
    complete = (not only and identities_valid and set(selected_ids) == set(required_ids))
    passed = identities_valid and bool(results) and all(verified)
    return {'scope': 'complete_required_upstream_flows' if complete else 'selected_subset',
            'required_flows': len(required), 'required_distinct_designs': len(required_designs),
            'executed_flows': len(results), 'executed_distinct_designs': len(executed_designs),
            'passed': passed, 'complete_required_flows_passed': complete and passed,
            'junit_cases': junit_cases, 'result_identities_valid': identities_valid,
            'identity_errors': errors}


def original_files(root):
    result = {}
    for parent, directories, filenames in os.walk(root, followlinks=False):
        for name in directories:
            path = Path(parent) / name
            if path.is_symlink(): result[path.relative_to(root).as_posix()] = {'kind': 'symlink', 'target': os.readlink(path)}
        directories[:] = [name for name in directories if not (Path(parent) / name).is_symlink()]
        for name in filenames:
            path = Path(parent) / name
            result[path.relative_to(root).as_posix()] = ({'kind': 'symlink', 'target': os.readlink(path)}
                                                       if path.is_symlink() else {'kind': 'file', 'sha256': digest(path)})
    return result


def run_case(flow, project, environment, python, destination):
    root = destination / flow['id']
    shutil.copytree(project['root'], root, symlinks=True)
    before = original_files(root)
    manifest = root / '.vwb-flows.json'
    configured = configure_flow(flow, environment, root)
    manifest.write_text(json.dumps({'schema_version': 1, 'flows': [configured],
        'source_files': [name for name, record in before.items() if record['kind'] == 'file']}, indent=2) + '\n')
    # Validate the real staged project before handing it to the actual CLI.
    load_flows(root)
    command = [python, str(REPO / 'vwb.py'), '--root', str(root), '--color', 'never',
               'flow', flow['id'], '--timeout', str(flow.get('timeout_seconds', 300)), '--json']
    result = run_process(command, cwd=root, env=environment, timeout=flow.get('timeout_seconds', 300) + 30,
                         max_output_bytes=16_000_000)
    try:
        native = json.loads(result.stdout)
    except json.JSONDecodeError:
        native = {'state': 'failed', 'error': 'Native VWB flow did not return a JSON result',
                  'output_excerpt': (result.stdout + '\n' + result.stderr)[-12000:]}
    after = original_files(root)
    changed = [name for name, record in before.items() if after.get(name) != record]
    log = '\n'.join('\n'.join([str(step.get('argv')), step.get('output', '')]) for step in native.get('steps', []))
    if native.get('output_excerpt'): log += '\n' + native['output_excerpt']
    if result.stderr: log += '\n' + result.stderr
    log_path = destination / (flow['id'] + '.log'); log_path.write_text(log)
    verdict = native.get('verdict') or {}
    verified = verdict.get('passed') is True and verdict.get('kind') == flow['expected']['kind']
    if flow['expected']['kind'] == 'junit':
        verified = (verified and verdict.get('tests', 0) >= flow['expected'].get('minimum_tests', 1)
                    and all(verdict.get(name) == 0 for name in ('failures', 'errors', 'skipped')))
    state = 'passed' if result.returncode == 0 and not result.timed_out and native.get('state') == 'passed' and verified and not changed else 'failed'
    return {'id': flow['id'], 'project': flow['project'], 'design_top': flow['design_top'],
            'kind': 'original_upstream_testbench', 'entrypoint': flow['entrypoint'],
            'driver': flow['driver'], 'simulator': flow['simulator'], 'state': state,
            'repository': project['repository'], 'revision': project['revision'],
            'archive_sha256': project['archive']['sha256'], 'upstream_tree_sha256': project['tree_sha256'],
            'original_files_changed': changed, 'original_file_count': len(before),
            'original_files_sha256': hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
            'command': command, 'returncode': result.returncode, 'timed_out': result.timed_out,
            'native_state': native.get('state'), 'native_error': native.get('error'),
            'verdict': native.get('verdict'), 'dependencies': native.get('dependencies'),
            'duration_seconds': native.get('duration_seconds'), 'log': str(log_path), 'log_sha256': digest(log_path),
            'project_root': str(root), 'manifest_sha256': digest(manifest)}


def configure_flow(flow, environment, root):
    """Retain the original entrypoint and activate its pinned local tools in VWB."""
    activated = {name: value for name, value in environment.items() if name in ENVIRONMENT_KEYS}
    for name, directory in (('XDG_CONFIG_HOME', 'config'), ('XDG_CACHE_HOME', 'cache'), ('XDG_DATA_HOME', 'data')):
        activated[name] = str(root / '.vwb-native' / directory)
        Path(activated[name]).mkdir(parents=True, exist_ok=True)
    return dict(flow, environment=activated, unset_environment=[
        'PYTHONHOME', 'PYTHONPATH', 'LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH', 'LIBRARY_PATH',
        'GPI_USERS', 'PYGPI_USERS', 'PYGPI_ENTRY_POINT'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--acquisition', type=Path, required=True)
    parser.add_argument('--toolchain-acquisition', type=Path, required=True)
    parser.add_argument('--test-tools', type=Path, required=True)
    parser.add_argument('--riscv-acquisition', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, default=REPO / '.upstream-evidence')
    parser.add_argument('--only', action='append')
    parser.add_argument('--stage-project', help='Stage one verified upstream project for the CLI/browser without running it')
    parser.add_argument('--stage-directory', type=Path)
    args = parser.parse_args()
    flows = load_registry()
    required = [flow for flow in flows if flow['status'] == 'required']
    if args.only and (len(set(args.only)) != len(args.only) or set(args.only) - {flow['id'] for flow in required}):
        parser.error('--only requires unique, known required flow IDs')
    selected = [flow for flow in required if not args.only or flow['id'] in args.only]
    if bool(args.stage_project) != bool(args.stage_directory) or (args.stage_project and args.only):
        parser.error('--stage-project requires --stage-directory and cannot be combined with --only')
    acquisition = json.loads(args.acquisition.read_text())
    tools = json.loads(args.test_tools.read_text())
    riscv = json.loads(args.riscv_acquisition.read_text())
    suite = json.loads(args.toolchain_acquisition.read_text())
    if acquisition.get('lock_sha256') != digest(REPO / 'toolchains/upstream-hdl-lock.json'):
        raise ValueError('Original upstream acquisition is from a different source lock')
    if tools.get('lock_sha256') != digest(REPO / 'toolchains/upstream-python-lock.json'):
        raise ValueError('Verification profile does not match the current dependency lock')
    if args.stage_project:
        if args.stage_project not in {flow['project'] for flow in flows}:
            parser.error('Unknown upstream project to stage')
        selected = [flow for flow in flows if flow['project'] == args.stage_project]
    needed = {flow['project'] for flow in selected}
    directories = {Path(acquisition['projects'][name]['root']).parents[1] for name in needed}
    for directory in directories:
        group = [name for name in needed if Path(acquisition['projects'][name]['root']).parents[1] == directory]
        verified = acquire(directory, projects=group, offline=True)
        for name in group:
            if verified['projects'][name]['tree_sha256'] != acquisition['projects'][name]['tree_sha256']:
                raise ValueError('Original upstream source tree identity changed')
    environment = os.environ.copy()
    for name in ('PYTHONHOME', 'PYTHONPATH', 'LD_LIBRARY_PATH', 'GPI_USERS', 'PYGPI_USERS', 'PYGPI_ENTRY_POINT'):
        environment.pop(name, None)
    environment.update(suite['environment'])
    environment.update(tools.get('environment', {}))
    environment.update(tools.get('native_environment', {}))
    python = tools['python']
    def flow_environment(flow):
        result = environment.copy()
        profile = flow.get('environment_profile')
        if profile and profile != 'nvdla': result.update(tools['profiles'][profile]['environment'])
        result['PATH'] = riscv['bin_directory'] + os.pathsep + result['PATH']
        return result
    if args.stage_project:
        root = args.stage_directory.absolute()
        shutil.copytree(acquisition['projects'][args.stage_project]['root'], root, symlinks=True)
        originals = original_files(root)
        manifest = {'schema_version': 1, 'flows': [configure_flow(flow, flow_environment(flow), root) for flow in selected],
                    'source_files': [name for name, record in originals.items() if record['kind'] == 'file']}
        (root / '.vwb-flows.json').write_text(json.dumps(manifest, indent=2) + '\n')
        load_flows(root)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({'schema_version': 1, 'project': args.stage_project,
            'root': str(root), 'flow_ids': [flow['id'] for flow in selected],
            'execution_state': 'staged_not_executed'}, indent=2) + '\n')
        print('Staged original upstream project: ' + str(root))
        return 0
    args.work_dir.mkdir(parents=True, exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix='upstream-benches-', dir=args.work_dir)).resolve()
    results = []
    for flow in selected:
        case_env = flow_environment(flow)
        try:
            item = run_case(flow, acquisition['projects'][flow['project']], case_env, python, destination)
        except (OSError, ValueError, KeyError) as error:
            item = {'id': flow['id'], 'project': flow['project'], 'design_top': flow['design_top'], 'state': 'failed', 'error': str(error)}
        results.append(item)
        print(flow['id'] + ': ' + item['state'], flush=True)
    report = {'schema_version': 1, **aggregate_results(required, selected, results, args.only),
              'flows': results, 'external_flows': [dict(flow, execution_state='not_run_external_requirements') for flow in flows if flow['status'] != 'required'],
              'source_sha256': {name: digest(REPO / name) for name in ['vwb.py', 'companion/flows.py', 'scripts/probe_upstream_testbenches.py', 'toolchains/upstream-python-lock.json']},
              'registry_sha256': {name: digest(REPO / 'upstream/benchmarks' / name) for name in REGISTRIES},
              'toolchain_archive': suite['archive'], 'python_profiles': {name: value['packages'] for name, value in tools['profiles'].items()},
              'riscv_compiler': {'version': riscv['version'], 'archive': riscv['archive']}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(f"Original upstream testbenches: {sum(item['state'] == 'passed' for item in results)}/{len(results)} flows; {report['executed_distinct_designs']} designs; {report['junit_cases']} JUnit cases")
    return int(not report['passed'])


if __name__ == '__main__':
    raise SystemExit(main())
