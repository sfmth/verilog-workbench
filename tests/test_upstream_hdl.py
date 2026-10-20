"""Corpus acceptance guards; mocked processes test verdict handling, not HDL."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/probe_upstream_hdl.py'
SPEC = importlib.util.spec_from_file_location('probe_upstream_hdl', SCRIPT)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def result(returncode=0, stdout='', stderr='', timed_out=False):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr, timed_out=timed_out)


def design(index=0, functional=False):
    case = {'id': f'design-{index:02}', 'project': 'example', 'top': f'core_{index}',
            'sources': ['rtl/core.v'], 'include_dirs': [], 'defines': [],
            'simulator': 'icarus' if functional else 'verilator',
            'kind': 'functional_simulation' if functional else 'lint_elaboration'}
    if functional:
        case.update(bench='fixtures/bench.sv', bench_top='tb_core')
    return case


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name).resolve()
        self.path = self.root / 'manifest.json'

    def load(self, designs=None, minimum=20, recipes=None):
        manifest = {'schema_version': 1, 'minimum_designs': minimum,
                    'designs': designs if designs is not None else [design(i) for i in range(20)],
                    'recipe_files': recipes or []}
        self.path.write_text(json.dumps(manifest))
        with patch.object(probe, 'REPO', self.root):
            return probe.load_designs(self.path)

    def test_at_least_twenty_distinct_design_tops_required(self):
        self.assertEqual(len(self.load()[1]), 20)
        with self.assertRaisesRegex(ValueError, 'Only 19 designs'):
            self.load([design(i) for i in range(19)])
        with self.assertRaisesRegex(ValueError, 'at least 20'):
            self.load(minimum=19)

    def test_duplicate_identity_or_project_top_does_not_inflate_coverage(self):
        cases = [design(i) for i in range(20)]
        cases[-1]['id'] = cases[0]['id']
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.load(cases)
        cases[-1]['id'] = 'unique-id'
        cases[-1]['top'] = cases[0]['top']
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.load(cases)

    def test_same_top_name_in_two_distinct_projects_is_distinct_coverage(self):
        cases = [design(i) for i in range(20)]
        cases[-1].update(project='second-project', top=cases[0]['top'])
        self.assertEqual(len(self.load(cases)[1]), 20)

    def test_recipes_merge_into_required_count(self):
        (self.root / 'recipes.json').write_text(json.dumps([design(i) for i in range(10, 20)]))
        self.assertEqual(len(self.load([design(i) for i in range(10)], recipes=['recipes.json'])[1]), 20)

    def test_lint_engine_cannot_claim_verilator_elaboration_using_icarus(self):
        cases = [design(i) for i in range(20)]
        cases[0]['simulator'] = 'icarus'
        with self.assertRaisesRegex(ValueError, 'requires Verilator'):
            self.load(cases)

    def test_functional_bench_requires_explicit_top(self):
        cases = [design(i, functional=True) for i in range(20)]
        del cases[0]['bench_top']
        with self.assertRaisesRegex(ValueError, 'bench top'):
            self.load(cases)

    def test_untrusted_path_fields_and_option_shaped_tops_are_rejected(self):
        for field, value in [('sources', ['../outside.v']), ('include_dirs', ['/etc']),
                             ('bench', '../outside.sv'), ('top', '--help'), ('project', '../project')]:
            with self.subTest(field=field):
                cases = [design(i, functional=True) for i in range(20)]
                cases[0][field] = value
                with self.assertRaises(ValueError):
                    self.load(cases)
        with self.assertRaises(ValueError):
            self.load(recipes=['../recipes.json'])

    def test_negative_controls_are_only_for_functional_benches(self):
        cases = [design(i) for i in range(20)]
        cases[0]['negative_control'] = True
        with self.assertRaisesRegex(ValueError, 'require functional simulation'):
            self.load(cases)


class VerdictTests(unittest.TestCase):
    def test_zero_exit_without_exact_functional_completion_marker_fails(self):
        case = design(functional=True)
        for log in ['', 'Your code: PASS', 'VWB_UPSTREAM_PASS different-design',
                    'compiler echoes "VWB_UPSTREAM_PASS design-00"']:
            with self.subTest(log=log):
                self.assertFalse(probe.inspect_result(case, result(), log)[0])
        self.assertTrue(probe.inspect_result(case, result(), 'VWB_UPSTREAM_PASS design-00\n')[0])

    def test_nonzero_exit_with_completion_marker_still_fails(self):
        self.assertFalse(probe.inspect_result(design(functional=True), result(returncode=1),
                                             'VWB_UPSTREAM_PASS design-00')[0])

    def test_timeout_cannot_pass_even_with_zero_exit_and_marker(self):
        self.assertFalse(probe.inspect_result(design(functional=True), result(timed_out=True),
                                             'VWB_UPSTREAM_PASS design-00')[0])

    def test_static_elaboration_never_claims_functional_verification(self):
        passed, note = probe.inspect_result(design(), result(), 'lint output')
        self.assertTrue(passed)
        self.assertIn('functional behavior is not claimed', note)
        self.assertNotIn('assertions completed', note)

    def test_negative_control_requires_real_assertion_not_compile_failure(self):
        case = design(functional=True)
        self.assertTrue(probe.inspect_negative_control(case, result(returncode=1),
                                                      'FATAL: bench.sv:42: data mismatch\n'))
        self.assertTrue(probe.inspect_negative_control(case, result(returncode=1),
                                                      '[123] %Fatal: bench.sv:42: Assertion failed in tb: data mismatch'))
        for log, value in [('compiler error: mismatch', result(returncode=1)),
                           ('FATAL: bench.sv:42: timeout', result(returncode=1)),
                           ('FATAL: bench.sv:42: data mismatch', result()),
                           ('FATAL: bench.sv:42: data mismatch', result(returncode=1, timed_out=True)),
                           ('FATAL: bench.sv:42: data mismatch\nVWB_UPSTREAM_PASS design-00', result(returncode=1))]:
            with self.subTest(log=log, value=value):
                self.assertFalse(probe.inspect_negative_control(case, value, log))


class StagingAndSelectionTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name).resolve()
        self.source = self.root / 'upstream'
        (self.source / 'rtl').mkdir(parents=True)
        (self.source / 'rtl/core.v').write_text('module core; endmodule\n')
        self.destination = self.root / 'results'
        self.destination.mkdir()
        self.project = {'root': str(self.source), 'repository': 'https://github.com/upstream/example',
                        'revision': 'a' * 40, 'license': {'spdx': 'ISC', 'paths': ['LICENSE']},
                        'archive': {'sha256': 'b' * 64}, 'tree_sha256': 'c' * 64}
        self.acquisition = {'projects': {'example': self.project}}

    def run_corpus(self, cases, only=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return probe.run_corpus(cases, self.acquisition, self.destination,
                                    '/python', {}, 10, only=only)

    def test_staging_preserves_original_hierarchy_and_hashes(self):
        destination = self.destination / 'case'
        hashes = probe.stage_case(design(), self.project, destination)
        self.assertEqual((destination / 'src/rtl/core.v').read_bytes(), (self.source / 'rtl/core.v').read_bytes())
        self.assertEqual(hashes, {'rtl/core.v': probe.sha256(self.source / 'rtl/core.v')})

    def test_missing_source_pattern_fails_before_process_is_launched(self):
        case = design()
        case['sources'] = ['rtl/missing*.v']
        with patch.object(probe, 'run_process') as process:
            report = self.run_corpus([case])
        process.assert_not_called()
        self.assertFalse(report['passed'])
        self.assertIn('matched no files', report['designs'][0]['error'])

    def test_parent_traversal_is_blocked_before_staging(self):
        outside = self.root / 'outside.v'
        outside.write_text('module outside; endmodule\n')
        case = design()
        case['sources'] = ['../outside.v']
        with self.assertRaisesRegex(ValueError, 'normalized relative path'):
            probe.stage_case(case, self.project, self.destination / 'case')
        self.assertFalse((self.destination / 'case').exists())

    @unittest.skipIf(os.name == 'nt', 'Creating symlinks requires Windows developer mode')
    def test_source_and_bench_symlinks_cannot_escape_declared_roots(self):
        outside = self.root / 'outside.v'
        outside.write_text('module outside; endmodule\n')
        (self.source / 'rtl/link.v').symlink_to(outside)
        case = design()
        case['sources'] = ['rtl/link.v']
        with self.assertRaisesRegex(ValueError, 'escapes'):
            probe.stage_case(case, self.project, self.destination / 'case')
        repo = self.root / 'repo'
        (repo / 'fixtures').mkdir(parents=True)
        (repo / 'fixtures/bench.sv').symlink_to(outside)
        with patch.object(probe, 'REPO', repo):
            with self.assertRaisesRegex(ValueError, 'remain in the repository'):
                probe.stage_case(design(functional=True), self.project, self.destination / 'bench-case')

    def test_subset_and_explicit_all_selection_never_claim_complete_corpus(self):
        cases = [design(0), design(1)]
        with patch.object(probe, 'run_process', return_value=result()):
            report = self.run_corpus(cases, only=['design-01'])
        self.assertEqual(report['scope'], 'selected_subset')
        self.assertEqual(report['executed_designs'], 1)
        self.assertEqual(report['designs'][0]['id'], 'design-01')
        self.assertTrue(report['passed'])
        self.assertFalse(report['complete_corpus_passed'])
        self.destination = self.root / 'all-explicit'
        self.destination.mkdir()
        with patch.object(probe, 'run_process', return_value=result()):
            report = self.run_corpus(cases, only=['design-00', 'design-01'])
        self.assertFalse(report['complete_corpus_passed'])

    def test_unknown_empty_or_duplicate_selection_fails_without_running(self):
        for selection in [[], ['missing'], ['design-00', 'design-00']]:
            with self.subTest(selection=selection), patch.object(probe, 'run_process') as process:
                with self.assertRaises(ValueError):
                    self.run_corpus([design()], only=selection)
                process.assert_not_called()

    def test_one_failed_design_prevents_complete_corpus_pass(self):
        with patch.object(probe, 'run_process', side_effect=[result(), result(returncode=1)]):
            report = self.run_corpus([design(0), design(1)])
        self.assertEqual(report['scope'], 'complete_corpus')
        self.assertFalse(report['passed'])
        self.assertFalse(report['complete_corpus_passed'])

    def test_missing_assertion_bench_fails_before_execution(self):
        with patch.object(probe, 'REPO', self.root), patch.object(probe, 'run_process') as process:
            report = self.run_corpus([design(functional=True)])
        process.assert_not_called()
        self.assertFalse(report['passed'])

    def test_broken_negative_control_overrules_passing_positive(self):
        repo = self.root / 'repo'
        (repo / 'fixtures').mkdir(parents=True)
        (repo / 'fixtures/bench.sv').write_text('module tb_core; endmodule\n')
        case = design(functional=True)
        case['negative_control'] = True
        positive = result(stdout='VWB_UPSTREAM_PASS design-00')
        # A compiler failure mentioning the assertion text cannot count as a
        # successful injected assertion failure.
        negative = result(returncode=1, stdout='compiler error: mismatch')
        with patch.object(probe, 'REPO', repo), patch.object(probe, 'run_process', side_effect=[positive, negative]):
            report = self.run_corpus([case])
        self.assertFalse(report['passed'])
        self.assertFalse(report['designs'][0]['negative_control']['passed'])


if __name__ == '__main__':
    unittest.main()
