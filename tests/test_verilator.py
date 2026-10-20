"""Verilator engine contracts and opt-in real timed/VPI/trace acceptance."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import vwb


class SimulatorContractTests(unittest.TestCase):
    def test_test_and_wave_engine_choices_preserve_auto_default(self):
        parser = vwb.make_parser()
        for command in ('test', 'wave'):
            self.assertEqual(parser.parse_args([command]).simulator, 'auto')
            for engine in ('auto', 'icarus', 'verilator', 'ghdl'):
                self.assertEqual(parser.parse_args([command, '--simulator', engine]).simulator, engine)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser.parse_args([command, '--simulator', 'shell'])

    def test_saved_wave_management_rejects_even_explicit_default_engine(self):
        args = vwb.make_parser().parse_args(['wave', '--list-saved', '--simulator', 'auto'])
        self.assertIn('--simulator', vwb.wave_management_overrides(args))

    def test_temporary_compile_preserves_bytes_outputs_and_cleans_up_on_cancellation(self):
        with tempfile.TemporaryDirectory(prefix='vwb Verilator spaces ') as directory:
            root = Path(directory).resolve(); (root / 'src').mkdir(); (root / 'test').mkdir()
            source = root / 'src/dut.v'; source.write_bytes(b'module dut; endmodule\r\n')
            wb = vwb.Workbench(root, root / 'src', root / 'test', root / 'build')
            work = root / 'build/sim/case'; work.mkdir(parents=True)
            command = ['verilator', '--binary', '--Mdir', 'obj_dir', '-I' + str(source.parent), source]
            staging = []
            def compile(actual, *, cwd):
                output = Path(actual[actual.index('--Mdir') + 1]); staging.append(output.parent)
                self.assertEqual(cwd, work)
                self.assertFalse(any(c.isspace() for c in str(output)))
                self.assertEqual(actual[-1], source)
                output.mkdir(); (output / 'vwb_sim').write_bytes(b'actual compiler output')
                return subprocess.CompletedProcess(actual, 0, '', '')
            with patch.object(wb, 'run', side_effect=compile):
                self.assertEqual(wb._compile_verilator(command, work).returncode, 0)
            self.assertFalse(staging[0].exists())
            self.assertEqual((work / 'obj_dir/vwb_sim').read_bytes(), b'actual compiler output')
            metadata = json.loads((work / 'verilator_build.json').read_text())
            self.assertEqual((work / metadata['inputs'][0]['retained']).read_bytes(), source.read_bytes())
            def cancel(actual, *, cwd):
                staging.append(Path(actual[actual.index('--Mdir') + 1]).parent); raise KeyboardInterrupt()
            with patch.object(wb, 'run', side_effect=cancel), self.assertRaises(KeyboardInterrupt):
                wb._compile_verilator(command, work)
            self.assertFalse(staging[-1].exists())

    def test_engine_language_mismatch_fails_before_external_execution(self):
        for language, engine in [('vhdl', 'icarus'), ('vhdl', 'verilator'), ('verilog', 'ghdl')]:
            with self.subTest(language=language, engine=engine), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(); (root / 'src').mkdir(); (root / 'test').mkdir()
                if language == 'vhdl':
                    (root / 'src/dut.vhd').write_text('entity dut is end dut; architecture rtl of dut is begin end rtl;')
                else:
                    (root / 'src/dut.v').write_text('module dut; endmodule')
                wb = vwb.Workbench(root, root / 'src', root / 'test', root / 'build')
                spec = vwb.TestSpec('dut', 'cocotb', root / 'test/test_dut.py')
                args = vwb.make_parser().parse_args(['test', '--simulator', engine])
                with patch.object(wb, 'run') as run, self.assertRaisesRegex(vwb.VWBError, '--simulator'):
                    wb._run_rtl_test_spec(spec, args)
                run.assert_not_called()

    def test_timed_verilator_dry_run_uses_assertions_and_real_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); (root / 'src').mkdir(); (root / 'test').mkdir()
            (root / 'src/dut.sv').write_text('module dut; endmodule')
            bench = root / 'test/bench.sv'; bench.write_text('module bench; dut u(); initial #1 $finish; endmodule')
            wb = vwb.Workbench(root, root / 'src', root / 'test', root / 'build', dry_run=True)
            spec = vwb.TestSpec('dut', 'verilog', bench, 'bench')
            args = vwb.make_parser().parse_args(['test', '--simulator', 'verilator', '--waves', '--wave-format', 'fst'])
            completed = subprocess.CompletedProcess([], 0, '', '')
            with patch.object(wb, 'require_tool', return_value='tool'), patch.object(wb, 'run', return_value=completed) as run:
                passed, wave = wb._run_rtl_test_spec(spec, args)
            self.assertTrue(passed); self.assertEqual(wave.suffix, '.fst')
            command = run.call_args_list[0].args[0]
            for flag in ('--binary', '--timing', '--assert', '--trace-fst'):
                self.assertIn(flag, command)
            self.assertEqual(command[command.index('--top-module') + 1], 'bench')
            self.assertNotIn('iverilog', command)
            self.assertTrue(str(run.call_args_list[-1].args[0][0]).endswith('vwb_sim.exe' if os.name == 'nt' else 'vwb_sim'))


@unittest.skipUnless(os.environ.get('VWB_VERILATOR_TEST_ACQUISITION'), 'Set VWB_VERILATOR_TEST_ACQUISITION for real Verilator acceptance')
class ManagedVerilatorTests(unittest.TestCase):
    def setUp(self):
        acquisition = json.loads(Path(os.environ['VWB_VERILATOR_TEST_ACQUISITION']).read_text())
        self.environment = os.environ.copy()
        for name in ('LD_LIBRARY_PATH', 'PYTHONHOME', 'PYTHONPATH', 'GPI_USERS', 'PYGPI_USERS', 'PYGPI_ENTRY_POINT'):
            self.environment.pop(name, None)
        self.environment.update(acquisition['environment'])
        self.python = acquisition.get('probe', {}).get('python') or str(Path(acquisition['suitePath']) / 'py3bin/python3')
        self.temporary = tempfile.TemporaryDirectory(prefix='vwb Verilator μ spaces '); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve(); (self.root / 'src').mkdir(); (self.root / 'test').mkdir()
        (self.root / 'test/__init__.py').touch()
        (self.root / 'src/dut.sv').write_text('module dut(input logic a,b,output logic y); assign y=a^b; endmodule\n')

    def execute(self, kind, body, *, wave_format='vcd', gate=False):
        extension = 'sv' if kind == 'verilog' else 'py'
        test = self.root / f'test/check.{extension}'; test.write_text(body)
        command = [self.python, str(Path(vwb.__file__)), '--root', str(self.root), '--color', 'never', '--verbose', 'test', 'dut', '--test', str(test), '--test-language', kind, '--simulator', 'verilator', '--waves', '--wave-format', wave_format]
        if kind == 'verilog': command.extend(['--test-top', 'bench'])
        else: command.extend(['--seed', '7'])
        if gate: command.append('--gate-level')
        result = subprocess.run(command, env=self.environment, capture_output=True, text=True, timeout=120)
        return result, result.stdout + result.stderr

    def assert_wave(self, format):
        waves = list((self.root / '.vwb').rglob('dut.' + format))
        self.assertEqual(len(waves), 1); content = waves[0].read_bytes(); self.assertGreater(len(content), 100)
        if format == 'vcd':
            self.assertIn(b'$enddefinitions', content); self.assertIn(b' y ', content)
        else:
            converted = subprocess.run(['fst2vcd', str(waves[0])], env=self.environment, capture_output=True, timeout=30)
            self.assertEqual(converted.returncode, 0, converted.stderr)
            self.assertIn(b'$enddefinitions', converted.stdout)

    @staticmethod
    def bench(*, fail=False):
        return '''module bench; logic a,b; wire y; dut u(a,b,y);
initial begin a=0;b=0;#1;assert(y==0) else $fatal(1,"BAD_ZERO");
a=1;#1;assert(y==1) else $fatal(1,"BAD_ONE");
''' + ('assert(0) else $fatal(1,"VWB_VERILATOR_ASSERTION_CONTROL");\n' if fail else '$display("VWB_VERILATOR_TRUTH_PASSED");\n') + '$finish;end endmodule\n'

    @staticmethod
    def cocotb(*, fail=False):
        return '''import cocotb
from cocotb.triggers import Timer
@cocotb.test()
async def truth_table(dut):
    for value in range(4):
        dut.a.value=value>>1; dut.b.value=value&1
        await Timer(1,unit="ns")
        assert int(dut.y.value)==((value>>1)^(value&1))
''' + ('    assert False, "VWB_VERILATOR_COCOTB_CONTROL"\n' if fail else '')

    def test_real_timed_hdl_assertions_and_vcd(self):
        result, text = self.execute('verilog', self.bench())
        self.assertEqual(result.returncode, 0, text); self.assertIn('VWB_VERILATOR_TRUTH_PASSED', text)
        self.assertIn('RTL PASS', text); self.assert_wave('vcd')

    def test_real_timed_hdl_fst(self):
        result, text = self.execute('verilog', self.bench(), wave_format='fst')
        self.assertEqual(result.returncode, 0, text); self.assert_wave('fst')

    def test_real_assertion_failure_is_nonzero(self):
        result, text = self.execute('verilog', self.bench(fail=True))
        self.assertNotEqual(result.returncode, 0); self.assertIn('VWB_VERILATOR_ASSERTION_CONTROL', text)
        self.assertIn('RTL FAIL', text); self.assertNotIn('Your code: PASS', text)

    def test_real_cocotb_vpi_truth_table_and_vcd(self):
        result, text = self.execute('cocotb', self.cocotb())
        self.assertEqual(result.returncode, 0, text); self.assert_wave('vcd')
        xml = ET.parse(next((self.root / '.vwb').rglob('results.xml')))
        self.assertEqual(len(list(xml.iter('testcase'))), 1); self.assertEqual(list(xml.iter('failure')), [])

    def test_real_cocotb_failure_stays_failure_and_fst_is_readable(self):
        result, text = self.execute('cocotb', self.cocotb(fail=True), wave_format='fst')
        self.assertNotEqual(result.returncode, 0); self.assertIn('VWB_VERILATOR_COCOTB_CONTROL', text)
        xml = ET.parse(next((self.root / '.vwb').rglob('results.xml')))
        self.assertEqual(len(list(xml.iter('failure'))), 1); self.assert_wave('fst')

    def test_real_cocotb_rechecks_synthesized_gates_with_same_engine(self):
        result, text = self.execute('cocotb', self.cocotb(), gate=True)
        self.assertEqual(result.returncode, 0, text); self.assertIn('RTL PASS', text); self.assertIn('GATE PASS', text)
        self.assertEqual(len(list((self.root / '.vwb').rglob('results.xml'))), 2)


if __name__ == '__main__': unittest.main()
