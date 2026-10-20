"""Missing Cocotb execution evidence must not diagnose a proven HDL failure."""
import contextlib
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import vwb

PASS = '<testsuite tests="1"><testcase name="executed"/></testsuite>'
FAIL = '<testsuite tests="1" failures="1"><testcase name="executed"><failure message="assertion"/></testcase></testsuite>'


class CocotbResultsDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        (self.root/'src').mkdir(); (self.root/'test').mkdir()
        (self.root/'src/dut.v').write_text('module dut; endmodule\n')
        self.test = self.root/'test/test_dut.py'
        self.test.write_text('import cocotb\n@cocotb.test()\nasync def check(dut): pass\n')
        self.workbench = vwb.Workbench(self.root,self.root/'src',self.root/'test',self.root/'build')

    def tearDown(self):
        self.temporary.cleanup()

    def execute(self, xml_stages, *, gate=False):
        xml = iter(xml_stages)
        def environment(spec, directory, args, language):
            directory.mkdir(parents=True,exist_ok=True)
            results = directory/'results.xml'
            content = next(xml)
            if content is not None: results.write_text(content)
            return {}, results
        args = vwb.make_parser().parse_args(['test','dut','--test-language','cocotb',*(['--gate-level'] if gate else [])])
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(self.workbench,'require_tool'), \
             mock.patch.object(self.workbench,'cocotb_library',return_value=('/library','cocotb_vpi')), \
             mock.patch.object(self.workbench,'_compile_simulation',return_value=(True,None,self.root/'compiled.vvp')), \
             mock.patch.object(self.workbench,'_cocotb_environment',side_effect=environment), \
             mock.patch.object(self.workbench,'gate_netlist',return_value=self.root/'gate.v'), \
             mock.patch.object(self.workbench,'run',return_value=subprocess.CompletedProcess(['simulator'],1,'','startup or test failure')), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            status = vwb.command_test(self.workbench,args)
        return status, output.getvalue(), errors.getvalue()

    def test_missing_invalid_empty_and_unexecuted_results_are_incomplete_and_still_fail(self):
        for xml in [None,'<broken','<testsuite tests="0"/>',
                    '<testsuite tests="1"><testcase><skipped/></testcase></testsuite>',
                    '<testsuite tests="1" skipped="1"><testcase/></testsuite>',
                    '<testsuite errors="-1"><testcase/></testsuite>',
                    '<testsuite tests="bad"><testcase/></testsuite>']:
            with self.subTest(xml=xml):
                status,output,errors = self.execute([xml])
                self.assertEqual(status,1)
                self.assertIn('RTL FAIL',output)
                self.assertIn('Your code: NOT FULLY CHECKED',output)
                self.assertIn('Your setup: NEEDS ATTENTION',output)
                self.assertNotIn('Your setup: OK',output)
                self.assertIn('no executed Cocotb test results',errors)
                self.assertIn('Python dependencies/imports',output)

    def test_executed_assertion_failure_remains_a_code_failure(self):
        status,output,errors = self.execute([FAIL])
        self.assertEqual(status,1)
        self.assertIn('Your code: FAIL',output)
        self.assertIn('Your setup: OK',output)
        self.assertNotIn('NEEDS ATTENTION',output)
        self.assertNotIn('no executed Cocotb',errors)

    def test_real_failure_in_one_stage_is_preserved_when_another_stage_has_no_results(self):
        for stages in [[FAIL,None],[None,FAIL]]:
            with self.subTest(stages=stages):
                status,output,errors = self.execute(stages,gate=True)
                self.assertEqual(status,1)
                self.assertIn('Your code: FAIL',output)
                self.assertIn('Your setup: NEEDS ATTENTION',output)
                self.assertIn('Failed test runs:',output)
                self.assertIn('no executed Cocotb test results',errors)

    def test_execution_evidence_counts_namespaced_failures_and_respects_nested_skip_counts(self):
        path = self.root/'results.xml'
        for xml,executed in [
            (PASS,True),(FAIL,True),
            ('<j:testsuite xmlns:j="urn:junit" tests="1"><j:testcase><j:error/></j:testcase></j:testsuite>',True),
            ('<testsuite><testsuite tests="1" skipped="1"><testcase/></testsuite></testsuite>',False),
            ('<testsuites><testsuite tests="1" skipped="1"><testcase/></testsuite><testsuite tests="1"><testcase/></testsuite></testsuites>',True),
            ('<testsuite errors="-1"><testcase/></testsuite>',False),
        ]:
            with self.subTest(xml=xml):
                path.write_text(xml)
                self.assertEqual(self.workbench._results_executed(path),executed)


if __name__=='__main__':
    unittest.main()
