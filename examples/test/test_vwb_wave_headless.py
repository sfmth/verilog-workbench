"""Headless waveform modes preserve the existing GUI defaults and archives."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import vwb


class HeadlessWaveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        (self.root/'src').mkdir()
        (self.root/'test').mkdir()
        (self.root/'src/dut.v').write_text('module dut; endmodule\n')
        (self.root/'test/test_dut.v').write_text('module test_dut; dut instance_under_test(); initial $finish; endmodule\n')
        self.workbench=vwb.Workbench(root=self.root,src_dir=self.root/'src',test_dir=self.root/'test',build_dir=self.root/'.vwb')
        self.workbench.prepare_build_dir()
        self.wave=self.root/'.vwb/sim/dut.vcd'
        self.wave.parent.mkdir()
        self.wave.write_text('$enddefinitions $end\n#0\n')
        self.parser=vwb.make_parser()

    def tearDown(self):
        self.temporary.cleanup()

    def capture(self, *extra):
        args=self.parser.parse_args(['wave','dut','--test-language','verilog','--wave-format','vcd',*extra])
        with mock.patch.object(self.workbench,'run_tests',return_value=(True,[self.wave])):
            return vwb.command_wave(self.workbench,args)

    def test_no_view_is_opt_in_and_default_capture_still_opens_gui(self):
        self.assertFalse(self.parser.parse_args(['wave','dut']).no_view)
        self.assertTrue(self.parser.parse_args(['wave','dut','--no-view']).no_view)
        with mock.patch.object(self.workbench,'open_waveform',return_value=(0,self.wave.with_suffix('.gtkw'))) as opened:
            self.assertEqual(self.capture(),0)
        opened.assert_called_once_with(self.wave,explicit_save=None,legacy_dut='dut')

    def test_headless_capture_archives_a_self_contained_wave_without_viewer(self):
        with mock.patch.object(self.workbench,'open_waveform',side_effect=AssertionError('headless mode launched a viewer')):
            self.assertEqual(self.capture('--no-view','--tag','known-good'),0)
        saved=self.workbench._read_saved_wave('known-good')
        self.assertEqual(saved.waveform.read_bytes(),self.wave.read_bytes())
        self.assertIsNone(saved.layout)
        metadata=json.loads((saved.directory/'metadata.json').read_text())
        self.assertEqual(metadata['wave_format'],'vcd')
        self.assertEqual(metadata['test_top'],'test_dut')

    def test_headless_list_and_load_return_project_relative_metadata_without_simulating(self):
        self.capture('--no-view','--tag','known-good')
        self.wave.unlink()
        with mock.patch.object(self.workbench,'run_tests',side_effect=AssertionError('management reran simulation')), \
             mock.patch.object(self.workbench,'open_waveform',side_effect=AssertionError('management launched a viewer')), \
             mock.patch.object(self.workbench,'sync_saved_wave_layout',side_effect=AssertionError('headless load modified a saved layout')):
            output=io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(vwb.command_wave(self.workbench,self.parser.parse_args(['wave','--list-saved','--no-view','--json'])),0)
            listed=json.loads(output.getvalue())
            self.assertEqual([item['tag'] for item in listed],['known-good'])
            output=io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(vwb.command_wave(self.workbench,self.parser.parse_args(['wave','--load','known-good','--no-view','--json'])),0)
            loaded=json.loads(output.getvalue())
            self.assertEqual(loaded,listed[0])
            self.assertFalse(Path(loaded['directory']).is_absolute())
            self.assertTrue((self.root/loaded['directory']/loaded['waveform']).is_file())

    def test_gui_load_still_opens_and_syncs_the_layout(self):
        self.capture('--no-view','--tag','known-good')
        saved=self.workbench._read_saved_wave('known-good')
        layout=saved.waveform.with_suffix('.gtkw')
        with mock.patch.object(self.workbench,'open_waveform',return_value=(0,layout)) as opened, \
             mock.patch.object(self.workbench,'sync_saved_wave_layout') as synchronized:
            self.assertEqual(vwb.command_wave(self.workbench,self.parser.parse_args(['wave','--load','known-good'])),0)
        opened.assert_called_once_with(saved.waveform,explicit_save=None)
        synchronized.assert_called_once_with('known-good',layout)

    def test_failed_simulation_is_not_archived_and_json_remains_management_only(self):
        args=self.parser.parse_args(['wave','dut','--test-language','verilog','--no-view','--tag','failed'])
        with mock.patch.object(self.workbench,'run_tests',return_value=(False,[self.wave])), \
             mock.patch.object(self.workbench,'open_waveform',side_effect=AssertionError('failed run opened viewer')):
            self.assertEqual(vwb.command_wave(self.workbench,args),1)
        self.assertFalse((self.root/'.vwb/saved-waves/failed').exists())
        for argv in [['wave','--json'],['wave','--load','known-good','--json']]:
            with self.subTest(argv=argv),self.assertRaisesRegex(vwb.VWBError,'--json requires'):
                vwb.command_wave(self.workbench,self.parser.parse_args(argv))


if __name__=='__main__':
    unittest.main()
