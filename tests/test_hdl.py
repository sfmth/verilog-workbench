"""Typed native HDL job options, path boundaries and real headless waveform jobs."""
import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from companion.projects import ProjectError, ProjectGrant
from companion.server import CompanionState


class HdlJobTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name).resolve()
        for name in ('src', 'test', 'include headers'):
            (self.root / name).mkdir()
        (self.root / 'src/dut.sv').write_text('module dut(input a, output b); assign b=a; endmodule\n')
        (self.root / 'test/test_dut.py').write_text('import cocotb\n@cocotb.test()\nasync def selected_case(dut): pass\n')
        (self.root / 'test/selected bench.sv').write_text('''`include "values.vh"
module wrong_top; initial $fatal(1, "WRONG_TOP"); endmodule
module selected_top;
  reg a; wire b; integer value;
  dut instance_under_test(a,b);
  initial begin
    a=1; #1;
    if (b !== 1) $fatal(1, "DUT_FAILED");
    if (!$value$plusargs("VALUE=%d",value)) $fatal(1, "PLUSARG_MISSING");
    if (value != `EXPECTED || value != `INCLUDE_VALUE) $fatal(1, "VALUE_FAILED");
    $display("EXPLICIT_HDL_OK"); $finish;
  end
endmodule
''')
        (self.root / 'include headers/values.vh').write_text('`define INCLUDE_VALUE 7\n')
        self.state = CompanionState(ProjectGrant(self.root), 'https://sfmth.github.io')

    def tearDown(self):
        self.state.close()
        self.directory.cleanup()

    def argv(self, command='test', **options):
        return self.state.job_argv(command, options)

    def test_simulation_options_map_to_exact_native_cli_flags(self):
        args = self.argv(modules=['dut'], test='test/test_dut.py', test_language='cocotb',
                         testcase='selected_case', seed=0, waves=True, wave_format='vcd', max_array_words=0,
                         gate_level=True, keep_going=True, defines=['WIDTH=4', 'FEATURE'], includes=['include headers'],
                         plusargs=['+VALUE=7'])
        self.assertEqual(args[args.index('--test')+1], str(self.root / 'test/test_dut.py'))
        self.assertEqual(args[args.index('--testcase')+1], 'selected_case')
        self.assertEqual(args[args.index('--seed')+1], '0')
        self.assertEqual(args[args.index('--max-array-words')+1], '0')
        for value in ['--gate-level', '--waves', '--keep-going', '--define=WIDTH=4', '--define=FEATURE',
                      '--include='+str(self.root / 'include headers'), '--plusarg=+VALUE=7']:
            self.assertIn(value, args)

    def test_explicit_hdl_top_and_all_compilation_consumers(self):
        args = self.argv(modules=['dut'], test='test/selected bench.sv', test_top='selected_top', test_language='verilog')
        self.assertEqual(args[args.index('--test-top')+1], 'selected_top')
        for command, extra in [('test', {}), ('lint', {}), ('synth', {'module':'dut'}), ('fpga', {'module':'dut', 'board':'icebreaker'})]:
            args = self.argv(command, defines=['WIDTH=4'], includes=['.'], **extra)
            self.assertIn('--define=WIDTH=4', args)
            self.assertIn('--include='+str(self.root), args)

    def test_simulator_is_a_typed_choice_for_test_and_wave_only(self):
        for command in ('test', 'wave'):
            for simulator in ('auto', 'icarus', 'verilator', 'ghdl'):
                args = self.argv(command, simulator=simulator)
                self.assertEqual(args[args.index('--simulator') + 1], simulator)
            for simulator in ('sh', '--help', ['verilator'], None, 1):
                with self.subTest(command=command, simulator=simulator), self.assertRaises(ProjectError):
                    self.argv(command, simulator=simulator)
        for command in ('lint', 'synth', 'doctor'):
            with self.assertRaises(ProjectError):
                self.argv(command, simulator='verilator')
        with self.assertRaises(ProjectError):
            self.argv('wave', list_saved=True, simulator='auto')

    def test_wave_modes_are_headless_and_management_is_not_simulation(self):
        args = self.argv('wave', modules=['dut'], list_saved=True)
        self.assertEqual(args[-3:], ['--no-view', '--list-saved', '--json'])
        self.assertNotIn('--verbose',args)
        args = self.argv('wave', modules=['dut'], load='known-good')
        self.assertIn('--no-view', args)
        self.assertEqual(args[-3:], ['--load', 'known-good', '--json'])
        self.assertNotIn('--verbose',args)
        args = self.argv('wave', modules=['dut'], tag='known-good', replace_tag=True)
        self.assertIn('--no-view', args)
        self.assertIn('--replace-tag', args)
        self.assertLess(args.index('--verbose'),args.index('wave'))
        for command in ('list','doctor'):
            self.assertNotIn('--verbose',self.argv(command))

    def test_invalid_scalar_list_and_conflicting_options_fail_before_spawn(self):
        invalid = [
            {'test_top':'--help'}, {'test_top':{}}, {'testcase':'a,b'}, {'testcase':'pkg.name'},
            {'testcase':'a\n'}, {'seed':True}, {'max_array_words':True}, {'max_array_words':-1},
            {'max_array_words':1000001}, {'gate_level':1}, {'keep_going':'yes'},
            {'defines':'NAME=1'}, {'defines':['--output=/tmp/out']}, {'defines':['NAME\n=1']},
            {'defines':['NAME=\ud800']}, {'defines':['NAME='+('a'*513)]}, {'defines':['A']*33},
            {'includes':{}}, {'plusargs':['-o/tmp/out']}, {'plusargs':['+VALUE\n=1']},
            {'test':'test/selected bench.sv', 'test_language':'vhdl'},
            {'test':'test/selected bench.sv', 'modules':['dut','other']},
            {'test':'test/test_dut.py', 'test_top':'bench'},
            {'test_language':'cocotb', 'test_top':'bench'},
            {'test_language':'verilog', 'seed':1}, {'test':'test/selected bench.sv', 'testcase':'chosen'},
            {'compile_args':['-o/tmp/out']}, {'sim_args':['-mplugin']},
        ]
        for options in invalid:
            with self.subTest(options=options), self.assertRaises(ProjectError):
                self.state.job_argv('test', options)
        self.assertEqual(self.state.jobs, {})
        with self.assertRaises(ProjectError):
            self.argv('lint', modules=['dut'], all=True)

    def test_project_input_paths_reject_escape_links_generated_and_wrong_kind(self):
        (self.root / '.vwb').mkdir()
        (self.root / '.vwb/generated.sv').write_text('module generated; endmodule')
        (self.root / 'test/not_hdl.txt').write_text('not HDL')
        for path in ['../outside.sv', '/tmp/outside.sv', '.git/config', '.vwb/generated.sv',
                     'test/not_hdl.txt', 'test', 'test/missing.sv']:
            with self.subTest(path=path), self.assertRaises(ProjectError):
                self.argv(test=path)
        for path in ['../', '.vwb', 'test/test_dut.py', 'missing']:
            with self.subTest(path=path), self.assertRaises(ProjectError):
                self.argv(includes=[path])
        try:
            (self.root / 'test/linked.sv').symlink_to(self.root / 'test/selected bench.sv')
            (self.root / 'linked-dir').symlink_to(self.root / 'include headers', target_is_directory=True)
        except (OSError, NotImplementedError):
            return
        for options in [{'test':'test/linked.sv'}, {'includes':['linked-dir']}]:
            with self.subTest(options=options), self.assertRaises(ProjectError):
                self.state.job_argv('test', options)

    def test_saved_wave_validation_rejects_path_and_mode_confusion(self):
        invalid = [{'tag':'../escape'}, {'load':'../escape'}, {'tag':'-option'}, {'tag':'x'*129}, {'tag':'CON'}, {'tag':'trailing.'},
                   {'replace_tag':True}, {'replace_tag':1}, {'list_saved':'true'}, {'waves':False},
                   {'load':'one', 'tag':'two'}, {'load':'one', 'modules':['a','b']},
                   {'load':'one', 'test_language':'auto'}, {'list_saved':True, 'wave_format':'vcd'},
                   {'list_saved':True, 'waves':True}, {'save':'layout.gtkw'}, {'view':'sh'}]
        for options in invalid:
            with self.subTest(options=options), self.assertRaises(ProjectError):
                self.state.job_argv('wave', options)

    def run_job(self, command, options):
        job = self.state.start_job({'command':command, 'options':options, 'timeout':30})
        deadline = time.monotonic()+35
        while job['status']=='running' and time.monotonic()<deadline:
            time.sleep(.01)
            job=self.state.job_result(job['id'])
        self.assertEqual(job['status'], 'passed', job)
        return job

    @unittest.skipUnless(shutil.which('iverilog') and shutil.which('vvp'), 'real Icarus tools required')
    def test_real_explicit_hdl_top_defines_include_plusargs_and_headless_saved_wave(self):
        options={'modules':['dut'], 'test':'test/selected bench.sv', 'test_top':'selected_top',
                 'test_language':'verilog', 'defines':['EXPECTED=7'], 'includes':['include headers'],
                 'plusargs':['+VALUE=7'], 'wave_format':'vcd'}
        job=self.run_job('test', options)
        self.assertIn('RTL PASS',job['stdout'])
        self.assertIn('EXPLICIT_HDL_OK',job['stdout'])
        job=self.run_job('wave',dict(options,tag='known-good'))
        self.assertIn('RTL PASS',job['stdout'])
        self.assertIn('EXPLICIT_HDL_OK',job['stdout'])
        archive=self.root/'.vwb/saved-waves/known-good/dut.vcd'
        self.assertTrue(archive.is_file())
        self.assertIn(b'$enddefinitions',archive.read_bytes())
        listed=json.loads(self.run_job('wave',{'list_saved':True})['stdout'])
        self.assertEqual([entry['tag'] for entry in listed],['known-good'])
        loaded=json.loads(self.run_job('wave',{'load':'known-good'})['stdout'])
        self.assertEqual(loaded['tag'],'known-good')
        self.assertEqual(loaded['waveform'],'dut.vcd')
        self.assertFalse(Path(loaded['directory']).is_absolute())
        self.assertNotIn('EXPLICIT_HDL_OK',json.dumps(loaded))


if __name__ == '__main__':
    unittest.main()
