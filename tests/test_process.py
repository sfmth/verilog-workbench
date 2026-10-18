"""Real native process tests; these also run on Windows/macOS CI."""

import contextlib
import io
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import vwb
from companion import process as runner


def process_running(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(api.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = Path(f"/proc/{pid}/stat")
    if stat.exists():
        return stat.read_text().rsplit(")", 1)[1].split()[0] != "Z"
    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
    )
    return bool(state.stdout.strip()) and not state.stdout.lstrip().startswith("Z")


class ProcessTests(unittest.TestCase):
    def test_darwin_waitid_fallback_does_not_require_os_wait_constants(self):
        import ctypes
        from types import SimpleNamespace

        class SigInfo(ctypes.Structure):
            _fields_ = [("si_pid", ctypes.c_int)]

        observed = []

        def waitid(kind, pid, information, flags):
            observed.append((kind, pid, flags))
            ctypes.cast(information, ctypes.POINTER(SigInfo)).contents.si_pid = pid
            return 0

        with (
            mock.patch.object(runner, "os", SimpleNamespace(name="posix")),
            mock.patch.object(runner.sys, "platform", "darwin"),
            mock.patch.object(runner, "_darwin_waitid_api", return_value=(waitid, SigInfo)),
        ):
            self.assertTrue(runner._exit_pending(SimpleNamespace(pid=12345, returncode=None)))
        self.assertEqual(observed, [(1, 12345, 0x25)])

    def assert_stopped(self, pids):
        end = time.monotonic() + 2
        while any(process_running(pid) for pid in pids) and time.monotonic() < end:
            time.sleep(0.02)
        self.assertFalse([pid for pid in pids if process_running(pid)], "tool descendants survived")

    def test_argument_boundaries_unicode_environment_and_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "project with spaces λ"
            folder.mkdir()
            code = (
                "import os, sys, json; "
                "os.write(1, json.dumps([os.getcwd(), sys.argv[1:], os.environ['VWB_TEST']], "
                "ensure_ascii=False).encode('utf-8')); "
                "os.write(2, 'stderr π'.encode('utf-8'))"
            )
            environment = dict(os.environ, VWB_TEST="hello β")
            result = runner.run_process(
                [sys.executable, "-c", code, "one two", "λ", "$(not-a-shell)"],
                cwd=folder, env=environment,
            )
            import json
            self.assertEqual(result.returncode, 0)
            actual_cwd, arguments, variable = json.loads(result.stdout)
            self.assertEqual(Path(actual_cwd).resolve(), folder.resolve())
            self.assertEqual(arguments, ["one two", "λ", "$(not-a-shell)"])
            self.assertEqual(variable, "hello β")
            self.assertEqual(result.stderr, "stderr π")
            self.assertFalse(result.cancelled or result.timed_out)

    def test_no_capture_and_tool_exit_status(self):
        result = runner.run_process([sys.executable, "-c", "raise SystemExit(7)"], capture=False)
        self.assertEqual(result.returncode, 7)
        self.assertIsNone(result.stdout)
        self.assertIsNone(result.stderr)

    def test_output_drains_both_streams_with_a_bounded_tail(self):
        result = runner.run_process(
            [sys.executable, "-c",
             "import os; os.write(1, b'a'*3000000+b'END'); os.write(2, b'b'*3000000+b'ERR')"],
            max_output_bytes=4096, timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(result.stdout), 4096)
        self.assertEqual(len(result.stderr), 4096)
        self.assertTrue(result.stdout.endswith("END"))
        self.assertTrue(result.stderr.endswith("ERR"))
        self.assertTrue(result.stdout_truncated and result.stderr_truncated)

    def test_pre_cancelled_event_never_launches_a_process(self):
        event = threading.Event()
        event.set()
        with mock.patch.object(runner.subprocess, "Popen") as launch:
            result = runner.run_process([sys.executable, "-c", "pass"], cancel_event=event)
        launch.assert_not_called()
        self.assertEqual(result.returncode, 130)
        self.assertTrue(result.cancelled)

    def make_tree(self, root, *, leader_exits=False):
        # The grandchild ignores TERM on POSIX, retains the pipes, and would
        # write a marker after the cancellation deadline if cleanup failed.
        grandchild = root / "grandchild.py"
        grandchild.write_text(
            "import os, signal, time\n"
            "from pathlib import Path\n"
            "if os.name != 'nt': signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "Path('grandchild.pid').write_text(str(os.getpid()))\n"
            "print('grandchild ready', flush=True)\n"
            "time.sleep(3)\n"
            "Path('survived').write_text('bad')\n",
            encoding="utf-8",
        )
        child = root / "child.py"
        child.write_text(
            "import os, subprocess, sys, time\n"
            "from pathlib import Path\n"
            "Path('child.pid').write_text(str(os.getpid()))\n"
            "subprocess.Popen([sys.executable, 'grandchild.py'])\n"
            "time.sleep(30)\n", encoding="utf-8",
        )
        leader = (
            "import subprocess, sys, time; "
            "subprocess.Popen([sys.executable, 'child.py']); "
            + ("" if leader_exits else "time.sleep(30)")
        )
        return [sys.executable, "-c", leader]

    def test_timeout_kills_descendants_even_after_launcher_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = runner.run_process(self.make_tree(root, leader_exits=True), cwd=root, timeout=1.5)
            self.assertTrue((root / "grandchild.pid").exists(), "grandchild did not start")
            pids = [int((root / name).read_text()) for name in ("child.pid", "grandchild.pid")]
            self.assert_stopped(pids)
            self.assertEqual(result.returncode, 124)
            self.assertTrue(result.timed_out)
            self.assertFalse(result.cancelled)
            self.assertIn("grandchild ready", result.stdout)
            self.assertFalse((root / "survived").exists())

    def test_cancel_event_kills_running_tool_and_descendants(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            event = threading.Event()

            def cancel_when_ready():
                deadline = time.monotonic() + 8
                while not (root / "grandchild.pid").exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                event.set()

            thread = threading.Thread(target=cancel_when_ready)
            thread.start()
            result = runner.run_process(self.make_tree(root), cwd=root, timeout=10, cancel_event=event)
            thread.join()
            self.assertTrue((root / "grandchild.pid").exists(), "grandchild did not start")
            pids = [int((root / name).read_text()) for name in ("child.pid", "grandchild.pid")]
            self.assert_stopped(pids)
            self.assertEqual(result.returncode, 130)
            self.assertTrue(result.cancelled)
            self.assertFalse(result.timed_out)

    def test_successful_launcher_does_not_leave_redirected_descendant(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "child.py").write_text(
                "import os, time\nfrom pathlib import Path\n"
                "Path('child.pid').write_text(str(os.getpid()))\n"
                "time.sleep(30)\n", encoding="utf-8",
            )
            leader = (
                "import subprocess, sys, time; from pathlib import Path; "
                "subprocess.Popen([sys.executable, 'child.py'], "
                "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
                "deadline=time.monotonic()+5\n"
                "while not Path('child.pid').exists() and time.monotonic()<deadline: time.sleep(.02)\n"
            )
            result = runner.run_process([sys.executable, "-c", leader], cwd=root, timeout=10)
            self.assertEqual(result.returncode, 0)
            self.assertTrue((root / "child.pid").exists(), "child never started")
            self.assert_stopped([int((root / "child.pid").read_text())])

    @unittest.skipIf(os.name == "nt", "POSIX PID ownership during group cleanup")
    def test_completed_leader_stays_unreaped_until_group_cleanup(self):
        launched = []
        original_launch = runner.subprocess.Popen
        original_signal = os.killpg
        signals = []

        def launch(*args, **kwargs):
            process = original_launch(*args, **kwargs)
            launched.append(process)
            return process

        def signal_owned_group(pid, signum):
            self.assertIsNone(launched[-1].returncode, "leader was reaped before group cleanup")
            self.assertTrue(runner._exit_pending(launched[-1]))
            signals.append(signum)
            return original_signal(pid, signum)

        with mock.patch.object(runner.subprocess, "Popen", side_effect=launch):
            with mock.patch.object(runner.os, "killpg", side_effect=signal_owned_group):
                for _ in range(8):
                    result = runner.run_process([sys.executable, "-c", "pass"])
                    self.assertEqual(result.returncode, 0)
        self.assertEqual(signals, [signal.SIGTERM, signal.SIGKILL] * 8)

    @unittest.skipIf(os.name == "nt", "POSIX separately owned CLI/tool sessions")
    def test_companion_cancellation_unwinds_cli_owned_tool_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "test").mkdir()
            (root / "src" / "dut.v").write_text("module dut; endmodule\n")
            (root / "proof.sby").write_text("[options]\nmode prove\n")
            self.make_tree(root)
            tools = root / "bin"
            tools.mkdir()
            fake_sby = tools / "sby"
            fake_sby.write_text(
                f"#!{sys.executable}\n"
                "import os, signal, subprocess, sys, time\nfrom pathlib import Path\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "Path('tool.pid').write_text(str(os.getpid()))\n"
                "subprocess.Popen([sys.executable, 'child.py'])\n"
                "time.sleep(30)\n", encoding="utf-8",
            )
            fake_sby.chmod(0o755)
            event = threading.Event()

            def cancel_when_ready():
                deadline = time.monotonic() + 8
                while not (root / "grandchild.pid").exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                event.set()

            thread = threading.Thread(target=cancel_when_ready)
            thread.start()
            environment = dict(
                os.environ, PATH=str(tools) + os.pathsep + os.environ.get("PATH", ""),
                PYTHONPATH=str(Path(vwb.__file__).parent) + os.pathsep + os.environ.get("PYTHONPATH", ""),
            )
            # Bundled Python launchers may prepend their own tool directory to
            # PATH. Select this test's native tool explicitly while retaining
            # the real CLI entrypoint, signal handler and Workbench runner.
            bootstrap = (
                "import vwb; original=vwb.Workbench.run; "
                "vwb.Workbench.run=lambda self, argv, **kw: original(self, "
                f"[{str(fake_sby)!r} if str(argv[0])=='sby' else argv[0], *argv[1:]], **kw); "
                "raise SystemExit(vwb._cli_main())"
            )
            result = runner.run_process(
                [sys.executable, "-c", bootstrap, "--root", root,
                 "--src-dir", "src", "--test-dir", "test", "formal", "proof.sby"],
                cwd=root, env=environment, cancel_event=event, timeout=10,
            )
            thread.join()
            self.assertTrue((root / "grandchild.pid").exists(), result.stderr)
            self.assertEqual(result.returncode, 130)
            self.assertTrue(result.cancelled)
            self.assertIn("interrupted", result.stderr)
            self.assert_stopped([
                int((root / name).read_text())
                for name in ("tool.pid", "child.pid", "grandchild.pid")
            ])

    def test_keyboard_interrupt_cleans_tree_and_propagates(self):
        launched = []
        original_launch = runner.subprocess.Popen
        original_sleep = time.sleep
        interrupted = False

        def interrupt_once(seconds):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                raise KeyboardInterrupt
            return original_sleep(seconds)

        def launch(*args, **kwargs):
            process = original_launch(*args, **kwargs)
            launched.append(process)
            return process

        with mock.patch.object(runner.subprocess, "Popen", side_effect=launch):
            with mock.patch.object(runner.time, "sleep", side_effect=interrupt_once):
                with self.assertRaises(KeyboardInterrupt):
                    runner.run_process([sys.executable, "-c", "import time; time.sleep(30)"])
        self.assertEqual(len(launched), 1)
        self.assertIsNotNone(launched[0].returncode)

    @unittest.skipUnless(os.name == "nt", "Windows containment failure path")
    def test_windows_containment_failure_kills_suspended_process(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "should-not-run"
            with mock.patch.object(runner._WindowsJob, "assign_and_resume", side_effect=OSError("assignment failed")):
                with self.assertRaisesRegex(OSError, "assignment failed"):
                    runner.run_process([sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"])
            self.assertFalse(marker.exists())

    def test_real_exit_124_is_not_mistaken_for_timeout(self):
        result = runner.run_process([sys.executable, "-c", "raise SystemExit(124)"])
        self.assertEqual(result.returncode, 124)
        self.assertFalse(result.timed_out)

    def test_capture_startup_failure_cleans_launched_process(self):
        launched = []
        original_launch = runner.subprocess.Popen

        def launch(*args, **kwargs):
            process = original_launch(*args, **kwargs)
            launched.append(process)
            return process

        with mock.patch.object(runner.subprocess, "Popen", side_effect=launch):
            with mock.patch.object(runner, "_Output", side_effect=RuntimeError("reader failed")):
                with self.assertRaisesRegex(RuntimeError, "reader failed"):
                    runner.run_process([sys.executable, "-c", "import time; time.sleep(30)"])
        self.assertIsNotNone(launched[0].returncode)
        self.assertTrue(launched[0].stdout.closed)
        self.assertTrue(launched[0].stderr.closed)

    def test_cli_quiet_capture_dry_run_and_timeout_compatibility(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbench = vwb.Workbench(root, root / "src", root / "test", root / ".vwb", require_project_dirs=False)
            output, errors = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                result = workbench.run([sys.executable, "-c", "print('quiet-success')"])
            self.assertEqual(result.returncode, 0)
            self.assertEqual(output.getvalue() + errors.getvalue(), "")
            captured = workbench.run([sys.executable, "-c", "print('captured')"], capture=True)
            self.assertEqual(captured.stdout.strip(), "captured")
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors):
                result = workbench.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.05)
            self.assertEqual(result.returncode, 124)
            self.assertIn("timed out", errors.getvalue())
            workbench.dry_run = True
            with mock.patch.object(vwb, "run_process") as launch:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(workbench.run(["does-not-exist"]).returncode, 0)
            launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
