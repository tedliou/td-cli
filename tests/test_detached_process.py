"""Real Windows jobs prove survival; checking creation flags cannot prove it."""

import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes

import pytest


class _Limits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64),
        ("job_time", ctypes.c_int64),
        ("flags", wintypes.DWORD),
        ("minimum", ctypes.c_size_t),
        ("maximum", ctypes.c_size_t),
        ("active", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority", wintypes.DWORD),
        ("scheduling", wintypes.DWORD),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _Limits),
        ("io", ctypes.c_uint64 * 6),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process", ctypes.c_size_t),
        ("peak_job", ctypes.c_size_t),
    ]


@pytest.mark.skipif(os.name != "nt", reason="Windows job semantics")
@pytest.mark.parametrize("allow_breakaway", [True, False])
def test_child_survives_caller_job_even_when_breakaway_is_forbidden(tmp_path, allow_breakaway):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    job = kernel.CreateJobObjectW(None, None)
    limits = _ExtendedLimits()
    limits.basic.flags = 0x2000 | (0x800 if allow_breakaway else 0)  # KILL_ON_CLOSE, BREAKAWAY_OK
    assert kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))
    result = tmp_path / "result.json"
    marker = tmp_path / "child ran.txt"
    script = """
import sys,time,json
from pathlib import Path
from td_cli.processes import launch_detached,LaunchError
sys.stdin.readline()
try:
    pid=launch_detached([sys.executable,'-c',
        "from pathlib import Path; import time; Path("+repr(sys.argv[2])+").write_text('running'); time.sleep(20)"],
        cwd=Path.cwd(),hidden=True)
    output={'pid':pid}
except LaunchError as e:
    output={'error':str(e)}
temporary=Path(sys.argv[1]).with_suffix(".tmp")
temporary.write_text(json.dumps(output))
temporary.replace(sys.argv[1])
time.sleep(20)
"""
    errors = (tmp_path / "caller-stderr.txt").open("w+")
    caller = subprocess.Popen(
        [sys.executable, "-c", script, str(result), str(marker)],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=errors,
        creationflags=subprocess.CREATE_BREAKAWAY_FROM_JOB | subprocess.CREATE_NO_WINDOW,
    )
    child_handle = None
    try:
        assert kernel.AssignProcessToJobObject(job, int(caller._handle))
        caller.stdin.write(b"go\n")
        caller.stdin.flush()
        # The launcher has a 10-second deadline; observe its result before closing the job.
        deadline = time.monotonic() + 15
        while not result.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        errors.flush()
        errors.seek(0)
        assert result.exists(), {"caller_exit": caller.poll(), "stderr": errors.read()}
        output = json.loads(result.read_text())
        assert "pid" in output, output
        child_handle = kernel.OpenProcess(0x100001, False, output["pid"])
        assert child_handle
        kernel.CloseHandle(job)
        job = None
        caller.wait(timeout=5)
        if child_handle:
            assert kernel.WaitForSingleObject(child_handle, 300) == 258  # Still running.
    finally:
        if job:
            kernel.CloseHandle(job)
        if child_handle:
            kernel.TerminateProcess(child_handle, 0)
            kernel.CloseHandle(child_handle)
        if caller.poll() is None:
            caller.kill()
        caller.wait(timeout=5)
        caller.stdin.close()
        errors.close()
