from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys

import pytest


ABRUPT_PARENT = Path(__file__).with_name("abrupt_parent.py")
PACKAGE_SOURCE = Path(__file__).resolve().parents[1] / "src"


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object acceptance test")
def test_abrupt_python_parent_exit_kills_native_host_tree() -> None:
    configured = os.environ.get("DNP3_MASTER_HOST_EXE")
    assert configured, "DNP3_MASTER_HOST_EXE must identify the built native host"

    environment = os.environ.copy()
    existing_python_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = str(PACKAGE_SOURCE)
    if existing_python_path:
        environment["PYTHONPATH"] += os.pathsep + existing_python_path

    process = subprocess.Popen(
        [sys.executable, str(ABRUPT_PARENT), configured],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 91, stderr.decode("utf-8", errors="replace")
    host_pid = int(stdout.decode("ascii").strip())

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    synchronize = 0x00100000
    process_terminate = 0x0001
    wait_object_0 = 0
    handle = kernel32.OpenProcess(synchronize | process_terminate, False, host_pid)
    if not handle:
        assert ctypes.get_last_error() == 87
        return
    try:
        wait_result = kernel32.WaitForSingleObject(handle, 2000)
        if wait_result != wait_object_0:
            kernel32.TerminateProcess(handle, 99)
        assert wait_result == wait_object_0, (
            f"native host PID {host_pid} survived abrupt Python parent exit"
        )
    finally:
        kernel32.CloseHandle(handle)
