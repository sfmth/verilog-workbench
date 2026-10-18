"""Enter bundled source without trusting cwd, PYTHONPATH, or site packages."""
import pathlib
import runpy
import sys

if not sys.flags.isolated:
    raise SystemExit("The bundled launcher must run Python with -I")
if len(sys.argv) < 2 or sys.argv[1] not in {"companion", "vwb"}:
    raise SystemExit("Expected companion or vwb")
module = sys.argv.pop(1)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
runpy.run_module(module, run_name="__main__", alter_sys=True)
