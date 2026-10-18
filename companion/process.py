"""Native tool execution with owned process trees and cancellable jobs.

No shell is involved. POSIX jobs have their own process group. Windows jobs
start suspended, enter a kill-on-close Job Object, then resume: a fast launcher
cannot spawn unowned children between process creation and job assignment.
"""

from __future__ import annotations

import os
import errno
import functools
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Mapping, Sequence


class _WindowsJob:
    """Windows 10+ containment using documented Win32 calls, without pywin32."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        pointer = ctypes.c_void_p
        dword = wintypes.DWORD
        size = ctypes.c_size_t

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", dword),
                ("MinimumWorkingSetSize", size),
                ("MaximumWorkingSetSize", size),
                ("ActiveProcessLimit", dword),
                ("Affinity", size),
                ("PriorityClass", dword),
                ("SchedulingClass", dword),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", size),
                ("JobMemoryLimit", size),
                ("PeakProcessMemoryUsed", size),
                ("PeakJobMemoryUsed", size),
            ]

        class ThreadEntry(ctypes.Structure):
            _fields_ = [
                ("dwSize", dword), ("cntUsage", dword),
                ("th32ThreadID", dword), ("th32OwnerProcessID", dword),
                ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG),
                ("dwFlags", dword),
            ]

        self.ThreadEntry = ThreadEntry
        signatures = {
            "CreateJobObjectW": ([pointer, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, pointer, dword], wintypes.BOOL),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "OpenProcess": ([dword, wintypes.BOOL, dword], wintypes.HANDLE),
            "CreateToolhelp32Snapshot": ([dword, dword], wintypes.HANDLE),
            "Thread32First": ([wintypes.HANDLE, ctypes.POINTER(ThreadEntry)], wintypes.BOOL),
            "Thread32Next": ([wintypes.HANDLE, ctypes.POINTER(ThreadEntry)], wintypes.BOOL),
            "OpenThread": ([dword, wintypes.BOOL, dword], wintypes.HANDLE),
            "ResumeThread": ([wintypes.HANDLE], dword),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes = arguments
            function.restype = result

        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign_and_resume(self, process: subprocess.Popen) -> None:
        ctypes = self.ctypes
        # Assignment needs SET_QUOTA | TERMINATE. Open our own handle rather
        # than depending on CPython's private Popen handle representation.
        handle = self.api.OpenProcess(0x0100 | 0x0001, False, process.pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self.api.AssignProcessToJobObject(self.handle, handle):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self.api.CloseHandle(handle)

        # Popen closes the initial thread handle. A suspended new process has
        # one initial thread, which we reopen through the documented snapshot
        # API before resuming it. No application code has run yet.
        snapshot = self.api.CreateToolhelp32Snapshot(0x00000004, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = self.ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            present = self.api.Thread32First(snapshot, ctypes.byref(entry))
            while present:
                if entry.th32OwnerProcessID == process.pid:
                    thread = self.api.OpenThread(0x0002, False, entry.th32ThreadID)
                    if not thread:
                        raise ctypes.WinError(ctypes.get_last_error())
                    try:
                        if self.api.ResumeThread(thread) == 0xFFFFFFFF:
                            raise ctypes.WinError(ctypes.get_last_error())
                    finally:
                        self.api.CloseHandle(thread)
                    return
                entry.dwSize = ctypes.sizeof(entry)
                present = self.api.Thread32Next(snapshot, ctypes.byref(entry))
            raise OSError("could not find the suspended tool's initial thread")
        finally:
            self.api.CloseHandle(snapshot)

    def terminate(self) -> None:
        if self.handle and not self.api.TerminateJobObject(self.handle, 130):
            raise self.ctypes.WinError(self.ctypes.get_last_error())

    def close(self) -> None:
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


class _Output:
    def __init__(self, stream, limit: int | None) -> None:
        self.stream = stream
        self.limit = limit
        self.buffer = bytearray()
        self.truncated = False
        self.thread = threading.Thread(target=self._drain, daemon=True)
        self.thread.start()

    def _drain(self) -> None:
        try:
            while True:
                chunk = self.stream.read(65536)
                if not chunk:
                    return
                self.buffer.extend(chunk)
                if self.limit is not None and len(self.buffer) > self.limit:
                    del self.buffer[:len(self.buffer) - self.limit]
                    self.truncated = True
        finally:
            self.stream.close()

    def text(self) -> str:
        # Match subprocess text-mode universal newlines on every host.
        return self.buffer.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


@functools.lru_cache(maxsize=1)
def _darwin_waitid_api():
    """Darwin exposes waitid in libc before Python exposed it in 3.13."""
    import ctypes

    class SigValue(ctypes.Union):
        _fields_ = [("integer", ctypes.c_int), ("pointer", ctypes.c_void_p)]

    # Darwin's public <sys/signal.h> siginfo_t, for both supported 64-bit ABIs.
    class SigInfo(ctypes.Structure):
        _fields_ = [
            ("si_signo", ctypes.c_int), ("si_errno", ctypes.c_int),
            ("si_code", ctypes.c_int), ("si_pid", ctypes.c_int),
            ("si_uid", ctypes.c_uint), ("si_status", ctypes.c_int),
            ("si_addr", ctypes.c_void_p), ("si_value", SigValue),
            ("si_band", ctypes.c_long), ("padding", ctypes.c_ulong * 7),
        ]

    waitid = ctypes.CDLL(None, use_errno=True).waitid
    waitid.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.POINTER(SigInfo), ctypes.c_int]
    waitid.restype = ctypes.c_int
    return waitid, SigInfo


def _exit_pending(process: subprocess.Popen) -> bool:
    """Observe termination while retaining the child PID until group cleanup."""
    if process.returncode is not None:
        return True
    if os.name == "nt":
        return process.poll() is not None
    if hasattr(os, "waitid"):
        options = os.WEXITED | os.WNOHANG | os.WNOWAIT
        return os.waitid(os.P_PID, process.pid, options) is not None
    if sys.platform == "darwin":
        import ctypes
        waitid, SigInfo = _darwin_waitid_api()
        information = SigInfo()
        # Public Darwin <sys/wait.h>: Python <=3.12 can omit these constants
        # along with os.waitid, so do not depend on the os module exporting them.
        options = 0x00000004 | 0x00000001 | 0x00000020  # WEXITED | WNOHANG | WNOWAIT
        while waitid(1, process.pid, ctypes.byref(information), options) != 0:  # P_PID
            error = ctypes.get_errno()
            if error != errno.EINTR:
                raise OSError(error, os.strerror(error))
        return information.si_pid == process.pid
    raise OSError("non-reaping child-status observation is unavailable on this host")


def _darwin_group_has_no_live_members(pgid: int) -> bool:
    """Confirm Darwin's EPERM came from a group containing only zombies.

    XNU can return EPERM when a group exists but has no signalable members.
    See killpg1's SZOMB exclusion and nfound handling in Apple's source:
    https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/kern_sig.c
    Keep the leader unreaped to reserve its PID, and independently inspect the
    group before accepting that outcome. An unavailable or malformed census
    must preserve the original signal error.
    """
    try:
        census = subprocess.run(
            ["/bin/ps", "-axo", "pid=,pgid=,stat="],
            capture_output=True, text=True, encoding="ascii", errors="strict",
            timeout=2, env=dict(os.environ, LC_ALL="C"),
        )
        if census.returncode:
            return False
        rows = census.stdout.splitlines()
        if not rows:
            return False
        retained_leader = False
        for row in rows:
            fields = row.split()
            if len(fields) != 3:
                return False
            pid, group = int(fields[0]), int(fields[1])
            if pid < 0 or group < 0:
                return False
            if group == pgid and not fields[2].startswith("Z"):
                return False
            if pid == pgid and group == pgid and fields[2].startswith("Z"):
                retained_leader = True
        return retained_leader
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
        return False


def _signal_group(pgid: int, signum: int) -> None:
    try:
        os.killpg(pgid, signum)
    except ProcessLookupError:
        pass
    except PermissionError:
        if sys.platform != "darwin" or not _darwin_group_has_no_live_members(pgid):
            raise


def _terminate_tree(
    process: subprocess.Popen, job: _WindowsJob | None, grace: float = 0.2
) -> None:
    if job is not None:
        job.terminate()
        process.wait()
        return
    # A previous cleanup has reaped the leader and released its PID. Repeated
    # cleanup must not signal a process group that could now belong to others.
    if process.returncode is not None:
        return
    _signal_group(process.pid, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while not _exit_pending(process) and time.monotonic() < deadline:
        time.sleep(0.01)
    # The leader can exit while a descendant ignores TERM and retains a pipe.
    # Always kill the remaining group, even if the leader's exit was observed.
    # Reap only after the final signal, keeping its PID reserved throughout.
    _signal_group(process.pid, signal.SIGKILL)
    process.wait()


def run_process(
    argv: Sequence[str | Path],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    capture: bool = True,
    timeout: float | None = None,
    cancel_event: threading.Event | None = None,
    max_output_bytes: int | None = None,
    termination_grace: float = 1.0,
) -> subprocess.CompletedProcess[str]:
    """Run an argv without a shell; timeout=124 and cancellation=130.

    ``cancel_event`` may be set from another thread. Timeout/cancellation kills
    descendants as well as the launcher. KeyboardInterrupt also kills the tree,
    then propagates to the caller. Native launch errors propagate as OSError.
    Captured output is UTF-8 with replacement for malformed bytes. Optionally
    retain only the last ``max_output_bytes`` bytes of each output stream while
    draining, so a chatty browser job cannot consume unbounded capture memory.
    ``termination_grace`` gives a cooperative wrapper time to clean its own
    native-tool sessions before the parent group receives SIGKILL. Nested VWB
    tool runs use a shorter grace period than the outer companion job.

    The CompletedProcess also has boolean ``timed_out``, ``cancelled``,
    ``stdout_truncated`` and ``stderr_truncated`` metadata, distinguishing a
    cancellation from a tool that itself returns 124 or 130.
    """
    if isinstance(argv, (str, bytes)) or not argv:
        raise ValueError("argv must be a nonempty sequence of arguments")
    if timeout is not None and timeout < 0:
        raise ValueError("timeout must be nonnegative")
    if max_output_bytes is not None and max_output_bytes < 0:
        raise ValueError("max_output_bytes must be nonnegative")
    if termination_grace < 0:
        raise ValueError("termination_grace must be nonnegative")
    arguments = [str(item) for item in argv]
    cancelled = bool(cancel_event is not None and cancel_event.is_set())
    timed_out = False
    outputs: list[_Output] = []
    process = None
    job = None
    returncode = 130 if cancelled else 0

    try:
        if not cancelled:
            options = {"start_new_session": True}
            if os.name == "nt":
                job = _WindowsJob()
                # CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP. No shell and no
                # console control handlers or third-party process utilities.
                options = {"creationflags": 0x00000004 | 0x00000200}
            process = subprocess.Popen(
                arguments, cwd=cwd, env=env,
                stdout=subprocess.PIPE if capture else None,
                stderr=subprocess.PIPE if capture else None,
                **options,
            )
            try:
                if job is not None:
                    job.assign_and_resume(process)
            except BaseException:
                # A suspended process must never survive a containment failure.
                process.kill()
                process.wait()
                if process.stdout is not None:
                    process.stdout.close()
                    process.stderr.close()
                raise
            try:
                if capture:
                    outputs.append(_Output(process.stdout, max_output_bytes))
                    outputs.append(_Output(process.stderr, max_output_bytes))
                deadline = None if timeout is None else time.monotonic() + timeout
                while True:
                    cancelled = bool(cancel_event is not None and cancel_event.is_set())
                    timed_out = deadline is not None and time.monotonic() >= deadline
                    if cancelled or timed_out:
                        _terminate_tree(process, job, termination_grace)
                        returncode = 130 if cancelled else 124
                        break
                    if _exit_pending(process) and not any(
                        output.thread.is_alive() for output in outputs
                    ):
                        # A successful launcher can leave children whose
                        # stdout/stderr were redirected elsewhere. Keep job
                        # ownership consistent with Windows kill-on-close.
                        _terminate_tree(process, job, termination_grace)
                        returncode = process.returncode
                        break
                    time.sleep(0.02)
            except BaseException:
                _terminate_tree(process, job, termination_grace)
                raise
            finally:
                for output in outputs:
                    output.thread.join()
                # A capture thread may fail to start (including interruption)
                # after Popen succeeds. Close any pipe without a drain owner.
                if capture:
                    for stream in (process.stdout, process.stderr):
                        if stream is not None and not stream.closed:
                            stream.close()
    finally:
        if job is not None:
            job.close()

    result = subprocess.CompletedProcess(
        arguments, returncode,
        outputs[0].text() if outputs else ("" if capture else None),
        outputs[1].text() if outputs else ("" if capture else None),
    )
    result.timed_out = timed_out and not cancelled
    result.cancelled = cancelled
    result.stdout_truncated = bool(outputs and outputs[0].truncated)
    result.stderr_truncated = bool(outputs and outputs[1].truncated)
    return result
