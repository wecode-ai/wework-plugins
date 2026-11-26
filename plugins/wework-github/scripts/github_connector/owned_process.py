"""Contain and clean up only subprocess trees created by this plugin.

POSIX children receive a new session/process group. Windows children start
suspended, join a kill-on-close Job Object, and only then resume execution.
The process leader may exit before cleanup; its group/job still owns descendants.
"""

import os
import signal
import subprocess
import sys
import threading
import time

from .errors import GitHubError

_WINDOWS = os.name == "nt"
_CREATE_SUSPENDED = 0x00000004
_CREATE_NO_WINDOW = 0x08000000
_GRACE_SECONDS = 0.25
_REAP_SECONDS = 1
_owned = {}
_lock = threading.Lock()


def _failure():
    return GitHubError(
        "gh_login_failed",
        "The GitHub authorization process could not be safely managed.",
    )


class _WindowsJob:
    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_ulonglong)
                for name in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self._kernel = kernel
        self._handle = kernel.CreateJobObjectW(None, None)
        if not self._handle:
            raise _failure()
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = (
            0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        if not kernel.SetInformationJobObject(
            self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            self.close()
            raise _failure()

    def attach(self, process):
        # Assignment requires PROCESS_SET_QUOTA | PROCESS_TERMINATE. The process
        # is still suspended, so it cannot create an uncontained descendant.
        handle = self._kernel.OpenProcess(0x0100 | 0x0001, False, process.pid)
        if not handle:
            raise _failure()
        try:
            if not self._kernel.AssignProcessToJobObject(self._handle, handle):
                raise _failure()
        finally:
            self._kernel.CloseHandle(handle)

    def resume(self, process):
        import ctypes
        from ctypes import wintypes

        class ThreadEntry(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ThreadID", wintypes.DWORD),
                ("th32OwnerProcessID", wintypes.DWORD),
                ("tpBasePri", wintypes.LONG),
                ("tpDeltaPri", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
            ]

        kernel = self._kernel
        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
        kernel.Thread32First.restype = wintypes.BOOL
        kernel.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
        kernel.Thread32Next.restype = wintypes.BOOL
        kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenThread.restype = wintypes.HANDLE
        kernel.ResumeThread.argtypes = [wintypes.HANDLE]
        kernel.ResumeThread.restype = wintypes.DWORD
        snapshot = kernel.CreateToolhelp32Snapshot(0x00000004, 0)  # TH32CS_SNAPTHREAD
        if snapshot == ctypes.c_void_p(-1).value:
            raise _failure()
        try:
            entry = ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            present = kernel.Thread32First(snapshot, ctypes.byref(entry))
            while present:
                if entry.th32OwnerProcessID == process.pid:
                    thread = kernel.OpenThread(0x0002, False, entry.th32ThreadID)
                    if not thread:
                        raise _failure()
                    try:
                        if kernel.ResumeThread(thread) == 0xFFFFFFFF:
                            raise _failure()
                        return
                    finally:
                        kernel.CloseHandle(thread)
                present = kernel.Thread32Next(snapshot, ctypes.byref(entry))
            raise _failure()
        finally:
            kernel.CloseHandle(snapshot)

    def close(self):
        if self._handle:
            handle, self._handle = self._handle, None
            if not self._kernel.CloseHandle(handle):
                raise _failure()


def _reap(process):
    try:
        process.wait(timeout=_REAP_SECONDS)
    except (subprocess.TimeoutExpired, OSError):
        raise _failure() from None


def spawn(arguments, *, popen=None, **kwargs):
    """Start an owned process; any containment failure stops it before returning."""
    popen = subprocess.Popen if popen is None else popen
    if _WINDOWS:
        kwargs["creationflags"] = (
            kwargs.get("creationflags", 0) | _CREATE_SUSPENDED | _CREATE_NO_WINDOW
        )
    else:
        kwargs["start_new_session"] = True
    try:
        process = popen(arguments, **kwargs)
    except FileNotFoundError:
        raise GitHubError(
            "gh_missing", "Install GitHub CLI (gh) on the execution device."
        ) from None
    except OSError:
        raise _failure() from None
    job = None
    try:
        if _WINDOWS:
            job = _WindowsJob()
            job.attach(process)
            job.resume(process)
        with _lock:
            _owned[process] = job
        return process
    except BaseException as error:
        with _lock:
            _owned.pop(process, None)
        if job is not None:
            try:
                job.close()
            except BaseException:  # noqa: BLE001, S110 - Continue emergency cleanup, then re-raise the original failure.
                pass
        if not _WINDOWS:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
        try:
            process.kill()
        except OSError:
            pass
        try:
            _reap(process)
        except GitHubError:
            pass
        if not isinstance(error, Exception):
            raise
        raise _failure() from None


def _signal_group(pid, value):
    deadline = time.monotonic() + _GRACE_SECONDS
    while True:
        try:
            os.killpg(pid, value)
            return
        except ProcessLookupError:
            return
        except PermissionError:
            # Darwin can retain an exiting, zombie-only group briefly and
            # report EPERM until the group disappears. Retry only this owned
            # group; persistent permission failures must still fail closed.
            if sys.platform != "darwin" or time.monotonic() >= deadline:
                raise _failure() from None
            time.sleep(0.01)
        except OSError:
            raise _failure() from None


def cleanup(process):
    """Stop the registered tree even if its leader has exited; safe to repeat."""
    with _lock:
        if process not in _owned:
            return
        job = _owned.pop(process)
    if _WINDOWS:
        job.close()  # KILL_ON_JOB_CLOSE also covers descendants after leader exit.
        _reap(process)
        return
    _signal_group(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    except OSError:
        raise _failure() from None
    # A dead group leader does not mean its descendants have released the pipes.
    _signal_group(process.pid, signal.SIGKILL)
    _reap(process)
