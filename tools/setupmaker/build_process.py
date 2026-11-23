"""Own the Windows process tree for a cancellable SetupMaker build."""

import ctypes
from ctypes import wintypes
import subprocess
import sys
from time import monotonic, sleep
from uuid import uuid4


class BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
        ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
        ("max_working_set", ctypes.c_size_t), ("process_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD),
    ]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", BasicLimits), ("io", ctypes.c_uint64 * 6),
        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
        ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t),
    ]


class Accounting(ctypes.Structure):
    _fields_ = [
        ("times", ctypes.c_int64 * 4), ("page_faults", wintypes.DWORD),
        ("total_processes", wintypes.DWORD), ("active_processes", wintypes.DWORD),
        ("terminated_processes", wintypes.DWORD),
    ]


class BuildStopped(Exception):
    """The user won the stop-or-deliver decision."""


def run_build_tree(arguments, decision):
    if decision.exists():
        raise BuildStopped

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
    ]
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job_name = f"Local\\DatenburgSetupBuild-{uuid4()}"
    job = kernel.CreateJobObjectW(None, job_name)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    child = None
    try:
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        child = subprocess.Popen([sys.executable, "-u", *arguments, job_name])
        while child.poll() is None and not decision.exists():
            sleep(0.1)
        stopped = decision.exists()
        _drain_job(kernel, job)
        if stopped:
            raise BuildStopped
        if child.wait():
            raise RuntimeError("Derleme başarısız oldu. Ayrıntılar işlem günlüğünde.")
    finally:
        kernel.CloseHandle(job)
        if child is not None:
            child.wait()


def join_build_job(name):
    # A Windows venv launcher can start Python before the launcher itself can
    # be assigned. Enroll the real interpreter before it starts any build work.
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenJobObjectW.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel.OpenJobObjectW(1, False, name)  # JOB_OBJECT_ASSIGN_PROCESS
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(job)


def _drain_job(kernel, job):
    if not kernel.TerminateJobObject(job, 1):
        raise ctypes.WinError(ctypes.get_last_error())
    deadline = monotonic() + 10
    accounting = Accounting()
    while True:
        if not kernel.QueryInformationJobObject(job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if not accounting.active_processes:
            return
        if monotonic() >= deadline:
            raise TimeoutError("Derleme alt süreçleri kapanmadı.")
        sleep(0.05)
