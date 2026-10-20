"""Project-owned upstream verification entrypoints and strict result oracles.

The project manifest selects its original Make/FuseSoC/Python driver. Browser
requests select a named flow and a declared simulator, never shell text.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import time
import xml.etree.ElementTree as ET

from .process import run_process

MANIFEST = '.vwb-flows.json'
DRIVERS = {'make': {'make', 'gmake'}, 'fusesoc': {'fusesoc'}, 'python': {'python', 'python3'}, 'pytest': {'python', 'python3'},
           'sbt': {'sbt'}, 'questa': {'vsim'}, 'vcs': {'vcs'}, 'xcelium': {'xrun'},
           'perl': {'perl'}, 'java': {'java'}, 'cpp': {'cpp'}, 'verilator': {'verilator'}, 'executable': set()}
SIMULATORS = {'icarus', 'verilator', 'ghdl', 'questa', 'modelsim', 'vcs', 'xcelium', 'riviera', 'activehdl'}
FLOW_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,95}\Z')
ENVIRONMENT_KEYS = {'PATH', 'PYTHONPATH', 'PYTHON_BIN', 'PYGPI_PYTHON_BIN', 'VIRTUAL_ENV', 'VERILATOR_ROOT',
                    'GHDL_PREFIX', 'IVERILOG_VPI_MODULE_PATH', 'PERL5LIB', 'PYTHONNOUSERSITE',
                    'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH',
                    'LIBRARY_PATH', 'SYSTEMC_HOME', 'UVM_HOME', 'UVMC_HOME', 'PYTHONHOME',
                    'GPI_USERS', 'PYGPI_USERS', 'PYGPI_ENTRY_POINT'}


def project_path(root, value, *, existing=False):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value or any(ord(c) < 32 for c in value):
        raise ValueError('Flow paths must be relative project paths')
    relative = PurePosixPath(value)
    if relative.is_absolute() or '..' in relative.parts or str(relative) != value:
        raise ValueError('Flow path leaves the project')
    path = root.joinpath(*relative.parts)
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError('Flow working directories and result paths cannot use symbolic links')
    if not path.resolve(strict=existing).is_relative_to(root):
        raise ValueError('Flow path leaves the project')
    return path


def _argv(value, driver):
    if (not isinstance(value, list) or not 1 <= len(value) <= 128
            or any(not isinstance(v, str) or not v or len(v) > 4096 or any(ord(c) < 32 for c in v) for v in value)
            or (driver != 'executable' and value[0] not in DRIVERS[driver])):
        raise ValueError('Flow argv must use its declared driver and ordinary argument strings')
    if driver == 'executable' and (value[0].startswith('/') or '..' in PurePosixPath(value[0]).parts or '\\' in value[0] or ':' in value[0]):
        raise ValueError('Compiled testbench executable must be a project-relative file')
    return value


def load_flows(root):
    root = Path(root).resolve(strict=True)
    path = project_path(root, MANIFEST, existing=True)
    if path.is_symlink() or path.stat().st_size > 1024 * 1024:
        raise ValueError('Flow manifest must be a regular project file smaller than 1 MiB')
    manifest = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(manifest, dict) or manifest.get('schema_version') != 1 or not isinstance(manifest.get('flows'), list) or not 1 <= len(manifest['flows']) <= 256:
        raise ValueError('Expected schema_version 1 and a nonempty flows list')
    sources = manifest.get('source_files', [])
    if not isinstance(sources, list) or len(sources) > 10000:
        raise ValueError('Flow source_files must be a bounded list of project paths')
    for source in sources:
        project_path(root, source)
    ids = set()
    for flow in manifest['flows']:
        if not isinstance(flow, dict) or not isinstance(flow.get('id'), str) or not FLOW_ID.fullmatch(flow['id']) or flow['id'] in ids:
            raise ValueError('Invalid or duplicate project flow ID')
        ids.add(flow['id'])
        driver = flow.get('driver')
        if driver not in DRIVERS:
            raise ValueError('Unknown upstream project driver')
        if flow.get('simulator') not in SIMULATORS:
            raise ValueError('Unknown flow simulator')
        environment = flow.get('environment', {})
        if (not isinstance(environment, dict) or set(environment) - ENVIRONMENT_KEYS
                or any(not isinstance(value, str) or len(value) > 32768 or any(ord(c) < 32 for c in value) for value in environment.values())):
            raise ValueError('Invalid project-owned verification tool environment')
        unset = flow.get('unset_environment', [])
        if not isinstance(unset, list) or any(not isinstance(name, str) or name not in ENVIRONMENT_KEYS for name in unset):
            raise ValueError('Invalid project-owned verification environment reset')
        cwd = project_path(root, flow.get('cwd', '.'), existing=True)
        if not cwd.is_dir():
            raise ValueError('Flow working directory must exist')
        steps = flow.get('steps', [{'argv': flow.get('argv'), 'cwd': flow.get('cwd', '.')}])
        if not isinstance(steps, list) or not 1 <= len(steps) <= 16:
            raise ValueError('Flow must contain 1 to 16 build/run steps')
        for step in steps:
            if not isinstance(step, dict):
                raise ValueError('Invalid flow step')
            step_driver = step.get('driver', driver)
            if step_driver not in DRIVERS:
                raise ValueError('Unknown flow step driver')
            _argv(step.get('argv'), step_driver)
            if not project_path(root, step.get('cwd', flow.get('cwd', '.')), existing=True).is_dir():
                raise ValueError('Flow step working directory must exist')
        expected = flow.get('expected', {})
        if not isinstance(expected, dict):
            raise ValueError('Flow requires an explicit result oracle')
        if expected.get('kind') == 'junit':
            paths = expected.get('paths')
            if not isinstance(paths, list) or not 1 <= len(paths) <= 32:
                raise ValueError('JUnit flow must declare its result paths')
            for output in paths:
                project_path(root, output)
            if len({project_path(root, output).resolve() for output in paths}) != len(paths):
                raise ValueError('JUnit result paths must be unique')
            if type(expected.get('minimum_tests', 1)) is not int or expected.get('minimum_tests', 1) < 1:
                raise ValueError('JUnit must execute at least one test')
        elif expected.get('kind') == 'log':
            patterns = expected.get('patterns')
            if not isinstance(patterns, list) or not 1 <= len(patterns) <= 32 or any(not isinstance(p, str) or not p or len(p) > 2048 for p in patterns):
                raise ValueError('Log flow must declare completion assertions or output patterns')
            for pattern in patterns:
                re.compile(pattern)
            paths = expected.get('paths', [])
            if not isinstance(paths, list) or len(paths) > 32:
                raise ValueError('Log result paths must be a bounded list')
            for output in paths:
                project_path(root, output)
        else:
            raise ValueError('Flow requires an explicit JUnit or log result oracle')
        requirements = flow.get('requirements', {})
        if not isinstance(requirements, dict):
            raise ValueError('Invalid flow dependency lists')
        for name in ('tools', 'python_modules', 'perl_modules', 'licenses'):
            values = requirements.get(name, [])
            if not isinstance(values, list) or any(not isinstance(v, str) or not v or len(v) > 256 for v in values):
                raise ValueError('Invalid flow dependency list')
        if any(not re.fullmatch(r'[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*', name) for name in requirements.get('perl_modules', [])):
            raise ValueError('Invalid Perl module dependency')
        if not isinstance(flow.get('simulators', {}), dict):
            raise ValueError('Invalid simulator presets')
        for simulator, preset in flow.get('simulators', {}).items():
            if simulator not in SIMULATORS or not isinstance(preset, dict):
                raise ValueError('Invalid simulator preset')
            for name in ('arguments', 'tools'):
                if not isinstance(preset.get(name, []), list) or any(not isinstance(v, str) or not v or any(ord(c) < 32 for c in v) for v in preset.get(name, [])):
                    raise ValueError('Invalid simulator preset arguments')
    return manifest


def dependencies(flow, environment, simulator=None):
    environment = {name: value for name, value in environment.items() if name not in flow.get('unset_environment', [])}
    environment.update(flow.get('environment', {}))
    preset = flow.get('simulators', {}).get(simulator, {}) if simulator else {}
    tools = list(dict.fromkeys([*flow.get('requirements', {}).get('tools', []), *preset.get('tools', [])]))
    missing = [tool for tool in tools if shutil.which(tool, path=environment.get('PATH')) is None]
    modules = list(flow.get('requirements', {}).get('python_modules', []))
    if modules:
        # The companion intentionally launches VWB with -I. Probe the selected
        # native tool Python in a child rather than importing profile packages
        # into that isolated trusted process.
        python = shutil.which('python3', path=environment.get('PATH')) or sys.executable
        probe = 'import importlib.util,json,sys\nmissing=[]\nfor name in json.loads(sys.argv[1]):\n try:\n  if importlib.util.find_spec(name) is None: missing.append(name)\n except (ImportError,ModuleNotFoundError,ValueError): missing.append(name)\nprint(json.dumps(missing))\n'
        try:
            result = run_process([python, '-c', probe, json.dumps(modules)], env=environment, timeout=15, max_output_bytes=16000, termination_grace=0.2)
            if result.returncode == 0:
                found = json.loads(result.stdout)
                if isinstance(found, list) and all(name in modules for name in found): modules = found
        except (OSError, ValueError):
            pass
    perl_missing = []
    for name in flow.get('requirements', {}).get('perl_modules', []):
        try:
            probe = run_process(['perl', '-M' + name, '-e', '1'], env=environment, timeout=10, max_output_bytes=8000, termination_grace=0.2)
            if probe.returncode != 0: perl_missing.append(name)
        except OSError:
            perl_missing.append(name)
    return {'missing_tools': missing, 'missing_python_modules': modules, 'missing_perl_modules': perl_missing,
            'licenses': flow.get('requirements', {}).get('licenses', []),
            'license_state': 'not_verified' if flow.get('requirements', {}).get('licenses') else 'not_required'}


def result_files(root, flow):
    return [project_path(root, path) for path in flow['expected'].get('paths', [])]


def file_signature(path):
    if not path.is_file():
        return None
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('Upstream test report exceeds 8 MiB')
    return (path.stat().st_mtime_ns, path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())


def inspect_result(root, flow, log, before, started_ns):
    expected = flow['expected']
    if expected['kind'] == 'log':
        artifacts = []
        for path in result_files(root, flow):
            signature = file_signature(path)
            if signature is None or signature == before.get(str(path)) or signature[0] < started_ns:
                return {'passed': False, 'error': 'Missing or stale upstream output: ' + str(path.relative_to(root))}
            log += '\n' + path.read_text(encoding='utf-8', errors='replace')
            artifacts.append({'path': str(path.relative_to(root)), 'bytes': signature[1], 'sha256': signature[2]})
        missing = [pattern for pattern in expected['patterns'] if re.search(pattern, log, re.MULTILINE) is None]
        return {'passed': not missing, 'missing_patterns': missing, 'kind': 'log', 'artifacts': artifacts}
    cases = failures = errors = skipped = 0
    artifacts = []
    identities = set()
    for path in result_files(root, flow):
        signature = file_signature(path)
        if signature is None or signature == before.get(str(path)) or signature[0] < started_ns:
            return {'passed': False, 'error': 'Missing or stale upstream test results: ' + str(path.relative_to(root))}
        if signature[1] > 8 * 1024 * 1024:
            return {'passed': False, 'error': 'Upstream test report exceeds 8 MiB'}
        identity = (path.stat().st_dev, path.stat().st_ino)
        if identity in identities:
            return {'passed': False, 'error': 'The same upstream result file was supplied more than once'}
        identities.add(identity)
        tree = ET.parse(path)
        nodes = list(tree.iter('testcase'))
        cases += len(nodes)
        failures += len(list(tree.iter('failure')))
        errors += len(list(tree.iter('error')))
        skipped += len(list(tree.iter('skipped')))
        # Some producers report failed setup/collection only in suite counters.
        for node in tree.iter():
            if node.tag in ('testsuite', 'testsuites'):
                if int(node.get('failures', '0')) > 0: failures = max(failures, 1)
                if int(node.get('errors', '0')) > 0: errors = max(errors, 1)
                if int(node.get('skipped', '0')) > 0: skipped = max(skipped, 1)
        artifacts.append({'path': str(path.relative_to(root)), 'bytes': signature[1], 'sha256': signature[2]})
    return {'passed': cases >= expected.get('minimum_tests', 1) and failures == errors == skipped == 0,
            'kind': 'junit', 'tests': cases, 'failures': failures, 'errors': errors, 'skipped': skipped, 'artifacts': artifacts}


def run_flow(root, flow, *, simulator=None, environment=None, timeout=300, dry_run=False, runner=run_process):
    root = Path(root).resolve(strict=True)
    environment = dict(os.environ if environment is None else environment)
    for name in flow.get('unset_environment', []): environment.pop(name, None)
    environment.update(flow.get('environment', {}))
    selected = simulator or flow['simulator']
    if selected != flow['simulator'] and selected not in flow.get('simulators', {}):
        raise ValueError('This upstream flow does not declare the requested simulator')
    deps = dependencies(flow, environment, selected)
    report = {'id': flow['id'], 'simulator': selected, 'driver': flow['driver'], 'dependencies': deps,
              'state': 'blocked' if deps['missing_tools'] or deps['missing_python_modules'] or deps['missing_perl_modules'] else 'not_run', 'steps': []}
    if report['state'] == 'blocked':
        return report
    before = {str(path): file_signature(path) for path in result_files(root, flow)}
    started_ns = time.time_ns()
    started = time.monotonic()
    logs = []
    steps = flow.get('steps', [{'argv': flow.get('argv'), 'cwd': flow.get('cwd', '.')}])
    preset = flow.get('simulators', {}).get(selected, {})
    for step in steps:
        argv = list(step['argv'])
        if selected != flow['simulator']:
            argv = [argument for argument in argv if not argument.startswith('SIM=')]
            argv.extend(preset.get('arguments', []))
        cwd = project_path(root, step.get('cwd', flow.get('cwd', '.')), existing=True)
        if step.get('driver', flow['driver']) == 'executable' and not dry_run:
            relative = (cwd.relative_to(root) / argv[0]).as_posix()
            argv[0] = str(project_path(root, relative.removeprefix('./'), existing=True))
        item = {'argv': argv, 'cwd': str(cwd.relative_to(root))}
        report['steps'].append(item)
        if dry_run:
            continue
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            report['state'] = 'timed_out'
            break
        result = runner(argv, cwd=cwd, env=environment, timeout=remaining, max_output_bytes=8_000_000, termination_grace=0.2)
        log = result.stdout + '\n' + result.stderr
        logs.append(log)
        item.update(returncode=result.returncode, timed_out=result.timed_out, output=log)
        if result.timed_out or result.returncode != 0:
            report['state'] = 'timed_out' if result.timed_out else 'failed'
            break
    else:
        if dry_run:
            report['state'] = 'dry_run'
        else:
            try:
                report['verdict'] = inspect_result(root, flow, '\n'.join(logs), before, started_ns)
                report['state'] = 'passed' if report['verdict']['passed'] else 'failed'
            except (OSError, ValueError, ET.ParseError) as error:
                report.update(state='failed', error=str(error))
    report['duration_seconds'] = round(time.monotonic() - started, 3)
    return report


def archive_result(root, build, report):
    """Publish the native report and its verified files to VWB's artifact tree."""
    root, build = Path(root).resolve(), Path(build).resolve()
    destination = build / 'flows' / report['id']
    def output(relative):
        cursor = build
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ValueError('Flow artifact paths cannot use symbolic links')
        cursor.parent.mkdir(parents=True, exist_ok=True)
        return cursor
    files = []
    for artifact in (report.get('verdict') or {}).get('artifacts', []):
        source = project_path(root, artifact['path'], existing=True)
        with source.open('rb') as stream:
            payload = stream.read(8 * 1024 * 1024 + 1)
        if len(payload) != artifact['bytes'] or hashlib.sha256(payload).hexdigest() != artifact['sha256']:
            raise ValueError('Upstream result changed before it could be archived')
        target = output(Path('flows') / report['id'] / 'results' / artifact['path'])
        target.write_bytes(payload)
        files.append(str(target.relative_to(build)))
    log = '\n'.join(str(step['argv']) + '\n' + step.get('output', '') for step in report['steps'])
    output(Path('flows') / report['id'] / 'run.log').write_text(log, encoding='utf-8')
    report['archived_artifacts'] = files
    report['report_artifact'] = str((destination / 'report.json').relative_to(build))
    output(Path('flows') / report['id'] / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
