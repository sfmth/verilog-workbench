"""Original upstream coverage requires distinct designs and real bench verdicts."""
from contextlib import redirect_stderr
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import probe_upstream_testbenches as probe


def fixture_flow(number=0):
    return {'id': 'flow-' + str(number), 'project': 'upstream',
            'design_top': 'design_' + str(number), 'status': 'required',
            'entrypoint': 'bench.py', 'driver': 'python', 'simulator': 'icarus',
            'cwd': '.', 'argv': ['python3', 'bench.py'],
            'requirements': {'tools': ['python3']},
            'expected': {'kind': 'junit', 'paths': ['results.xml'], 'minimum_tests': 1}}


def fixture_result(flow):
    return {'id': flow['id'], 'project': flow['project'], 'design_top': flow['design_top'],
            'state': 'passed', 'returncode': 0, 'timed_out': False,
            'original_files_changed': [],
            'verdict': {'kind': 'junit', 'passed': True, 'tests': 1,
                        'failures': 0, 'errors': 0, 'skipped': 0}}


class RegistryCoverageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        (self.repo / 'upstream/benchmarks').mkdir(parents=True)
        self.path = self.repo / 'upstream/benchmarks/test.json'
        self.flows = [fixture_flow(number) for number in range(20)]

    def load(self, flows=None):
        self.path.write_text(json.dumps({'schema_version': 1, 'flows': self.flows if flows is None else flows}))
        with patch.object(probe, 'REPO', self.repo), patch.object(probe, 'REGISTRIES', ('test.json',)):
            return probe.load_registry()

    def test_twenty_distinct_required_designs_are_counted(self):
        self.assertEqual(len(self.load()), 20)

    def test_second_simulator_for_same_core_does_not_supply_missing_design(self):
        flows = deepcopy(self.flows[:19])
        duplicate_core = deepcopy(flows[0])
        duplicate_core.update(id='same-core-verilator', simulator='verilator')
        flows.append(duplicate_core)
        with self.assertRaisesRegex(ValueError, '20 distinct'):
            self.load(flows)

    def test_external_requirements_do_not_fill_required_coverage(self):
        flows = deepcopy(self.flows)
        flows[-1]['status'] = 'external_required'
        with self.assertRaisesRegex(ValueError, '20 distinct'):
            self.load(flows)

    def test_duplicate_ids_and_unclassified_flows_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.load(self.flows + [self.flows[0]])
        flows = deepcopy(self.flows)
        flows[0]['status'] = 'elaboration_only'
        with self.assertRaisesRegex(ValueError, 'verification status'):
            self.load(flows)


class AggregationTests(unittest.TestCase):
    def setUp(self):
        self.flows = [fixture_flow(number) for number in range(20)]
        self.results = [fixture_result(flow) for flow in self.flows]

    def aggregate(self, results=None, selected=None, only=None):
        return probe.aggregate_results(self.flows, self.flows if selected is None else selected,
                                       self.results if results is None else results, only)

    def assert_failed(self, report):
        self.assertFalse(report['passed'], report)
        self.assertFalse(report['complete_required_flows_passed'], report)

    def test_complete_required_results_need_all_twenty_distinct_designs(self):
        report = self.aggregate()
        self.assertTrue(report['passed'])
        self.assertTrue(report['complete_required_flows_passed'])
        self.assertEqual(report['scope'], 'complete_required_upstream_flows')
        self.assertEqual(report['required_distinct_designs'], 20)
        self.assertEqual(report['executed_distinct_designs'], 20)
        self.assertEqual(report['junit_cases'], 20)

    def test_explicit_subset_can_pass_without_claiming_complete_coverage(self):
        selected = self.flows[:1]
        report = self.aggregate(self.results[:1], selected, [selected[0]['id']])
        self.assertTrue(report['passed'])
        self.assertFalse(report['complete_required_flows_passed'])
        self.assertEqual(report['scope'], 'selected_subset')
        self.assertEqual(report['executed_distinct_designs'], 1)
        # Explicitly naming every ID is still an explicit selection, not the CI gate.
        report = self.aggregate(only=[flow['id'] for flow in self.flows])
        self.assertTrue(report['passed'])
        self.assertFalse(report['complete_required_flows_passed'])

    def test_missing_duplicate_and_wrong_project_results_never_complete(self):
        variants = {'missing': self.results[:-1],
                    'duplicate': self.results[:-1] + [deepcopy(self.results[0])]}
        wrong = deepcopy(self.results)
        wrong[-1]['project'] = 'another-upstream'
        variants['wrong_project'] = wrong
        wrong = deepcopy(self.results)
        wrong[-1]['design_top'] = 'another_design'
        variants['wrong_top'] = wrong
        for name, results in variants.items():
            with self.subTest(name=name):
                report = self.aggregate(results)
                self.assert_failed(report)
                self.assertFalse(report['result_identities_valid'])
                self.assertTrue(report['identity_errors'])

    def test_unselected_or_external_result_cannot_be_counted(self):
        external = fixture_flow(21)
        external['status'] = 'external_required'
        self.assert_failed(self.aggregate(self.results + [fixture_result(external)]))
        selected = deepcopy(self.flows)
        selected[-1] = external
        self.assert_failed(self.aggregate(self.results, selected))
        selected = deepcopy(self.flows)
        selected[-1]['status'] = 'external_required'
        self.assert_failed(self.aggregate(self.results, selected))

    def test_explicit_selection_must_match_the_executed_unique_ids(self):
        for only in (['unknown-flow'], [self.flows[0]['id'], self.flows[0]['id']]):
            with self.subTest(only=only):
                self.assert_failed(self.aggregate(self.results[:1], self.flows[:1], only))

    def test_metadata_only_or_no_junit_assertion_execution_cannot_pass(self):
        variants = [None, {}, {'passed': True, 'kind': 'log', 'missing_patterns': []},
                    {'passed': True, 'kind': 'junit', 'tests': 0, 'failures': 0, 'errors': 0, 'skipped': 0},
                    {'passed': True, 'kind': 'junit', 'tests': True, 'failures': False, 'errors': False, 'skipped': False}]
        for verdict in variants:
            with self.subTest(verdict=verdict):
                results = deepcopy(self.results)
                results[-1]['verdict'] = verdict
                self.assert_failed(self.aggregate(results))

    def test_failed_skipped_setup_error_timed_out_or_changed_sources_fail(self):
        variants = [{'state': 'blocked'}, {'returncode': 1}, {'timed_out': True},
                    {'original_files_changed': ['rtl/core.v']}]
        for changes in variants:
            with self.subTest(changes=changes):
                results = deepcopy(self.results)
                results[-1].update(changes)
                self.assert_failed(self.aggregate(results))
        for counter in ('failures', 'errors', 'skipped'):
            with self.subTest(counter=counter):
                results = deepcopy(self.results)
                results[-1]['verdict'][counter] = 1
                self.assert_failed(self.aggregate(results))

    def test_duplicate_core_variants_cannot_make_nineteen_designs_a_full_gate(self):
        self.flows[-1]['project'] = self.flows[0]['project']
        self.flows[-1]['design_top'] = self.flows[0]['design_top']
        self.results = [fixture_result(flow) for flow in self.flows]
        report = self.aggregate()
        self.assertEqual(report['required_flows'], 20)
        self.assertEqual(report['required_distinct_designs'], 19)
        self.assert_failed(report)


class NativeCaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='vwb original bench ')
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name).resolve()
        self.source = self.work / 'original'
        self.source.mkdir()
        self.destination = self.work / 'evidence'
        self.destination.mkdir()
        self.flow = fixture_flow()
        self.project = {'root': str(self.source), 'repository': 'https://github.com/upstream/project',
                        'revision': 'a' * 40, 'archive': {'sha256': 'b' * 64}, 'tree_sha256': 'c' * 64}
        self.environment = os.environ.copy()
        self.environment['PATH'] = str(Path(sys.executable).parent) + os.pathsep + os.defpath
        for name in ('PYTHONHOME', 'PYTHONPATH', 'LD_LIBRARY_PATH'):
            self.environment.pop(name, None)
        if os.name == 'nt':
            self.flow['argv'][0] = 'python'
            self.flow['requirements']['tools'] = ['python']
        else:
            # The bundled Python wrapper exports its own PYTHONHOME. Its child
            # must use that same runtime instead of a host python3 on PATH.
            shim = self.work / 'tools/bin'
            shim.mkdir(parents=True)
            (shim / 'python3').write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' "$@"\n')
            (shim / 'python3').chmod(0o755)
            self.environment['PATH'] = str(shim) + os.pathsep + self.environment['PATH']
        self.script('')
        (self.source / 'LICENSE').write_text('Original retained license\n')

    def script(self, extra):
        (self.source / 'bench.py').write_text(
            "from pathlib import Path\n"
            "Path('results.xml').write_text('<testsuite><testcase name=\"original_assertions\"/></testsuite>')\n"
            + extra, encoding='utf-8')

    def run_case(self):
        return probe.run_case(self.flow, self.project, self.environment, sys.executable, self.destination)

    def test_real_vwb_flow_preserves_original_sources_and_records_verified_junit(self):
        result = self.run_case()
        self.assertEqual(result['state'], 'passed', result)
        self.assertEqual(result['verdict']['tests'], 1)
        self.assertEqual(result['original_files_changed'], [])
        self.assertEqual(result['archive_sha256'], 'b' * 64)
        self.assertRegex(result['manifest_sha256'], r'^[0-9a-f]{64}$')
        self.assertRegex(result['log_sha256'], r'^[0-9a-f]{64}$')
        self.assertEqual((self.source / 'LICENSE').read_text(), 'Original retained license\n')

    def test_changed_original_source_cannot_pass_after_successful_bench_verdict(self):
        self.script("Path('LICENSE').write_text('changed upstream license')\n")
        result = self.run_case()
        self.assertEqual(result['native_state'], 'passed')
        self.assertEqual(result['state'], 'failed')
        self.assertIn('LICENSE', result['original_files_changed'])

    def test_real_zero_exit_without_upstream_junit_is_not_a_verification_pass(self):
        (self.source / 'bench.py').write_text("print('upstream process exited normally')\n")
        result = self.run_case()
        self.assertEqual(result['returncode'], 1)  # VWB rejects the upstream process's zero exit.
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['original_files_changed'], [])
        self.assertIn('Missing or stale', result['verdict']['error'])

    def test_original_archive_with_old_junit_does_not_prove_a_new_execution(self):
        (self.source / 'bench.py').write_text("print('no test ran')\n")
        (self.source / 'results.xml').write_text('<testsuite><testcase name="previous_run"/></testsuite>')
        result = self.run_case()
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['original_files_changed'], [])
        self.assertIn('Missing or stale', result['verdict']['error'])

    def test_original_file_replaced_by_same_content_link_is_still_a_source_change(self):
        probe_link = self.source / 'symlink-capability-check'
        try:
            probe_link.symlink_to('LICENSE')
        except OSError as error:
            self.skipTest('Symlink creation unavailable: ' + str(error))
        finally:
            probe_link.unlink(missing_ok=True)
        self.script("p=Path('LICENSE')\nPath('generated-license').write_bytes(p.read_bytes())\np.unlink()\np.symlink_to('generated-license')\n")
        result = self.run_case()
        self.assertEqual(result['native_state'], 'passed')
        self.assertEqual(result['state'], 'failed')
        self.assertIn('LICENSE', result['original_files_changed'])

    def test_changed_original_directory_link_is_not_hidden_by_equal_file_hashes(self):
        (self.source / 'linked-original').mkdir()
        (self.source / 'linked-alternative').mkdir()
        try:
            (self.source / 'original-link').symlink_to('linked-original', target_is_directory=True)
        except OSError as error:
            self.skipTest('Symlink creation unavailable: ' + str(error))
        self.script("p=Path('original-link')\np.unlink()\np.symlink_to('linked-alternative', target_is_directory=True)\n")
        result = self.run_case()
        self.assertEqual(result['native_state'], 'passed')
        self.assertEqual(result['state'], 'failed')
        self.assertIn('original-link', result['original_files_changed'])

    def test_native_metadata_claim_without_a_verdict_is_rejected(self):
        result = SimpleNamespace(stdout=json.dumps({'state': 'passed', 'steps': []}),
                                 stderr='', returncode=0, timed_out=False)
        with patch.object(probe, 'run_process', return_value=result):
            report = self.run_case()
        self.assertEqual(report['state'], 'failed')
        self.assertIsNone(report['verdict'])

    def test_timeout_flag_rejects_an_otherwise_passing_native_result(self):
        native = {'state': 'passed', 'steps': [], 'verdict': fixture_result(self.flow)['verdict']}
        result = SimpleNamespace(stdout=json.dumps(native), stderr='', returncode=0, timed_out=True)
        with patch.object(probe, 'run_process', return_value=result):
            self.assertEqual(self.run_case()['state'], 'failed')


class SelectionInputTests(unittest.TestCase):
    def test_unknown_duplicate_or_external_only_ids_fail_before_execution(self):
        flows = [fixture_flow(number) for number in range(20)]
        external = fixture_flow(21)
        external['status'] = 'external_required'
        for only in ([flows[0]['id'], flows[0]['id']], ['unknown-flow'], [external['id']]):
            with self.subTest(only=only):
                argv = ['probe', '--acquisition', 'unused', '--toolchain-acquisition', 'unused',
                        '--test-tools', 'unused', '--riscv-acquisition', 'unused', '--output', 'unused']
                for name in only:
                    argv += ['--only', name]
                with patch.object(sys, 'argv', argv), patch.object(probe, 'load_registry', return_value=flows + [external]), \
                        patch.object(probe, 'run_case') as runner, redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        probe.main()
                self.assertEqual(error.exception.code, 2)
                runner.assert_not_called()


if __name__ == '__main__':
    unittest.main()
