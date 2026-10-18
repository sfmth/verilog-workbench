import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import vwb


class CocotbStartupTests(unittest.TestCase):
    def workbench(self, root):
        (root / 'src').mkdir()
        (root / 'test').mkdir()
        return vwb.Workbench(root, root/'src', root/'test', root/'build')

    def test_new_config_supplies_python_before_pygpi_entry(self):
        with tempfile.TemporaryDirectory() as temporary:
            wb = self.workbench(Path(temporary))
            values = {'--python-bin':'/runtime/python', '--libpython':'/runtime/libpython.so',
                      '--pygpi-entry-point':'/runtime/libcocotb.so,initialize'}
            def run(command, **kwargs):
                return subprocess.CompletedProcess(command, 0, values[command[-1]], '')
            with patch.dict(os.environ, {}, clear=True), patch.object(wb, 'require_tool', return_value='cocotb-config'), patch.object(wb, 'run', side_effect=run):
                wb._load_cocotb_runtime()
            self.assertEqual(wb._cocotb_runtime['GPI_USERS'], '/runtime/libpython.so;/runtime/libcocotb.so,initialize')

    def test_old_config_does_not_invent_startup_library(self):
        with tempfile.TemporaryDirectory() as temporary:
            wb = self.workbench(Path(temporary))
            with patch.dict(os.environ, {}, clear=True), patch.object(wb, 'require_tool', return_value='cocotb-config'), patch.object(wb, 'run', return_value=subprocess.CompletedProcess([], 2, '', 'unknown option')):
                wb._load_cocotb_runtime()
            self.assertNotIn('GPI_USERS', wb._cocotb_runtime)

    def test_explicit_gpi_user_configuration_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            wb = self.workbench(Path(temporary))
            with patch.dict(os.environ, {'GPI_USERS':'custom.so,entry'}), patch.object(wb, 'require_tool', return_value='cocotb-config'), patch.object(wb, 'run', return_value=subprocess.CompletedProcess([], 0, '/runtime/python', '')) as run:
                wb._load_cocotb_runtime()
            self.assertNotIn('GPI_USERS', wb._cocotb_runtime)
            self.assertFalse(any(call.args[0][-1]=='--pygpi-entry-point' for call in run.call_args_list))


if __name__ == '__main__':
    unittest.main()
