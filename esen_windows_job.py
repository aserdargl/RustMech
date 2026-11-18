"""Start a Windows process tree inside an aggregate committed-memory limit."""

from contextlib import ExitStack, contextmanager
import ctypes
from ctypes import wintypes
import os
import subprocess


CREATE_SUSPENDED = 0x00000004
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x0200
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9


class BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
        ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
        ("max_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
        ("scheduling", wintypes.DWORD),
    ]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", BasicLimits), ("io_counters", ctypes.c_ulonglong * 6),
        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t),
    ]


class JobProcess:
    def __init__(self, handle, kill, winapi):
        self.handle = handle
        self.kill = kill
        self.winapi = winapi
        self.returncode = None

    def poll(self):
        if self.winapi.WaitForSingleObject(self.handle, 0) == self.winapi.WAIT_OBJECT_0:
            self.returncode = self.winapi.GetExitCodeProcess(self.handle)
        return self.returncode

    def wait(self):
        self.winapi.WaitForSingleObject(self.handle, self.winapi.INFINITE)
        return self.poll()


@contextmanager
def limited_process(command, *, cwd, env, stdout, stderr, memory_mib):
    if os.name != "nt":
        raise ValueError("Local memory limits are available on Windows.")
    import _winapi
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    def api(name, result, *args):
        function = getattr(kernel, name)
        function.restype = result
        function.argtypes = args
        return function

    handle = wintypes.HANDLE
    dword = wintypes.DWORD
    create_job = api("CreateJobObjectW", handle, ctypes.c_void_p, wintypes.LPCWSTR)
    configure = api("SetInformationJobObject", wintypes.BOOL, handle, ctypes.c_int, ctypes.c_void_p, dword)
    assign = api("AssignProcessToJobObject", wintypes.BOOL, handle, handle)
    terminate = api("TerminateJobObject", wintypes.BOOL, handle, wintypes.UINT)
    resume = api("ResumeThread", dword, handle)

    with ExitStack() as cleanup:
        job = create_job(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        cleanup.callback(_winapi.CloseHandle, job)
        limits = ExtendedLimits()
        limits.basic.flags = JOB_OBJECT_LIMIT_JOB_MEMORY | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        limits.job_memory = memory_mib * 1024**2
        if not configure(job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())

        startup = subprocess.STARTUPINFO(dwFlags=subprocess.STARTF_USESTDHANDLES)
        with ExitStack() as inherited:
            stdin = inherited.enter_context(open(os.devnull, "rb"))
            current = _winapi.GetCurrentProcess()
            handles = []
            for stream in (stdin, stdout, stderr):
                duplicate = _winapi.DuplicateHandle(
                    current, msvcrt.get_osfhandle(stream.fileno()), current, 0, True,
                    _winapi.DUPLICATE_SAME_ACCESS,
                )
                inherited.callback(_winapi.CloseHandle, duplicate)
                handles.append(duplicate)
            startup.hStdInput, startup.hStdOutput, startup.hStdError = handles
            startup.lpAttributeList = {"handle_list": handles}
            process, thread, _, _ = _winapi.CreateProcess(
                None, subprocess.list2cmdline(command), None, None, True,
                subprocess.CREATE_NO_WINDOW | CREATE_SUSPENDED, env, os.fspath(cwd), startup,
            )
            cleanup.callback(_winapi.CloseHandle, process)
            cleanup.callback(_winapi.CloseHandle, thread)
            # Attach before the launcher can create any local MPI ranks.
            if not assign(job, process):
                error = ctypes.WinError(ctypes.get_last_error())
                _winapi.TerminateProcess(process, 1)
                _winapi.WaitForSingleObject(process, _winapi.INFINITE)
                raise error
            if resume(thread) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())

        def kill():
            if not terminate(job, 1):
                raise ctypes.WinError(ctypes.get_last_error())

        yield JobProcess(process, kill, _winapi)
