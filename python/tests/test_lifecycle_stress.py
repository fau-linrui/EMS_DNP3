from __future__ import annotations

import ctypes
from ctypes import wintypes
import gc
import os
from pathlib import Path
import threading

import pytest

from dnp3_master import Dnp3MasterClient, HostProcessConfig


def current_windows_handle_count() -> int | None:
    if os.name != "nt":
        return None
    count = wintypes.DWORD()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetProcessHandleCount.restype = wintypes.BOOL
    if not kernel32.GetProcessHandleCount(kernel32.GetCurrentProcess(), ctypes.byref(count)):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(count.value)


@pytest.mark.lifecycle_stress
def test_repeated_hello_shutdown_has_no_live_process_or_handle_growth() -> None:
    iterations = int(os.environ.get("DNP3_LIFECYCLE_ITERATIONS", "0"))
    if iterations <= 0:
        pytest.skip("set DNP3_LIFECYCLE_ITERATIONS to run lifecycle stress")

    configured = os.environ.get("DNP3_MASTER_HOST_EXE")
    assert configured, "DNP3_MASTER_HOST_EXE must identify the built native host"
    config = HostProcessConfig(
        executable=Path(configured),
        startup_timeout=2.0,
        request_timeout=2.0,
        shutdown_timeout=1.0,
        diagnostic_tail_bytes=4096,
    )

    with Dnp3MasterClient(config) as warmup:
        assert warmup.hello_info["backend"] == "opendnp3"
    gc.collect()
    baseline_handles = current_windows_handle_count()
    baseline_threads = threading.active_count()
    retained_clients: list[Dnp3MasterClient] = []

    for _ in range(iterations):
        client = Dnp3MasterClient(config)
        with client:
            assert client.hello_info["backend"] == "opendnp3"
        diagnostics = client.diagnostics
        assert diagnostics.returncode == 0
        assert diagnostics.cleanup_error is None
        assert not client.is_running
        retained_clients.append(client)

    gc.collect()
    final_handles = current_windows_handle_count()
    final_threads = threading.active_count()
    print(
        f"lifecycle_iterations={iterations} "
        f"handles={baseline_handles}->{final_handles} "
        f"threads={baseline_threads}->{final_threads}"
    )
    assert final_threads <= baseline_threads
    if baseline_handles is not None and final_handles is not None:
        assert final_handles <= baseline_handles + 8
    retained_clients.clear()
