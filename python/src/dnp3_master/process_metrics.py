"""Windows process resource samples used by performance and soak reports."""

from __future__ import annotations

from dataclasses import dataclass
import os
import time
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ProcessResourceSample:
    """One same-instant host-process resource observation."""

    monotonic_ns: int
    process_cpu_seconds: float
    working_set_bytes: int
    private_bytes: int
    handle_count: int
    thread_count: int
    source: str = "windows_process_api"
    scope: str = "dnp3_master_host_process"

    def to_mapping(self) -> dict[str, Any]:
        return {
            "monotonic_ns": self.monotonic_ns,
            "process_cpu_seconds": self.process_cpu_seconds,
            "working_set_bytes": self.working_set_bytes,
            "private_bytes": self.private_bytes,
            "handle_count": self.handle_count,
            "thread_count": self.thread_count,
            "source": self.source,
            "scope": self.scope,
        }

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any]
    ) -> ProcessResourceSample:
        required = {
            "monotonic_ns",
            "process_cpu_seconds",
            "working_set_bytes",
            "private_bytes",
            "handle_count",
            "thread_count",
            "source",
            "scope",
        }
        if set(value) != required:
            raise ValueError("process resource sample fields are invalid")
        for field_name in (
            "monotonic_ns",
            "working_set_bytes",
            "private_bytes",
            "handle_count",
            "thread_count",
        ):
            if type(value[field_name]) is not int or value[field_name] < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        cpu = value["process_cpu_seconds"]
        if isinstance(cpu, bool) or not isinstance(cpu, (int, float)) or cpu < 0:
            raise ValueError("process_cpu_seconds must be non-negative")
        for field_name in ("source", "scope"):
            if not isinstance(value[field_name], str) or not value[field_name]:
                raise ValueError(f"{field_name} must be a non-empty string")
        return cls(
            monotonic_ns=value["monotonic_ns"],
            process_cpu_seconds=float(cpu),
            working_set_bytes=value["working_set_bytes"],
            private_bytes=value["private_bytes"],
            handle_count=value["handle_count"],
            thread_count=value["thread_count"],
            source=value["source"],
            scope=value["scope"],
        )


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _PROCESS_VM_READ = 0x0010
    _TH32CS_SNAPTHREAD = 0x00000004
    _ERROR_NO_MORE_FILES = 18
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class _FILETIME(ctypes.Structure):
        _fields_ = [
            ("dwLowDateTime", wintypes.DWORD),
            ("dwHighDateTime", wintypes.DWORD),
        ]

    class _PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]

    class _THREADENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _psapi = ctypes.WinDLL("psapi", use_last_error=True)

    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME),
    ]
    _kernel32.GetProcessTimes.restype = wintypes.BOOL
    _kernel32.GetProcessHandleCount.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.GetProcessHandleCount.restype = wintypes.BOOL
    _kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.Thread32First.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_THREADENTRY32),
    ]
    _kernel32.Thread32First.restype = wintypes.BOOL
    _kernel32.Thread32Next.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_THREADENTRY32),
    ]
    _kernel32.Thread32Next.restype = wintypes.BOOL
    _psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_PROCESS_MEMORY_COUNTERS_EX),
        wintypes.DWORD,
    ]
    _psapi.GetProcessMemoryInfo.restype = wintypes.BOOL


def _windows_error(operation: str) -> OSError:
    import ctypes

    code = ctypes.get_last_error()
    return OSError(code, f"{operation} failed with Windows error {code}")


def _filetime_value(value: object) -> int:
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)


class ProcessResourceSampler:
    """Own a bounded query handle and sample one Windows process."""

    def __init__(self, pid: int) -> None:
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise ValueError("pid must be a positive integer")
        if os.name != "nt":
            raise OSError(
                "ProcessResourceSampler is implemented only for the supported Windows target"
            )
        self.pid = pid
        self._handle: object | None = _kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION | _PROCESS_VM_READ,
            False,
            pid,
        )
        if not self._handle:
            raise _windows_error("OpenProcess")

    def __enter__(self) -> ProcessResourceSampler:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def sample(self) -> ProcessResourceSample:
        if self._handle is None:
            raise RuntimeError("process resource sampler is closed")
        creation = _FILETIME()
        exit_time = _FILETIME()
        kernel = _FILETIME()
        user = _FILETIME()
        if not _kernel32.GetProcessTimes(
            self._handle,
            creation,
            exit_time,
            kernel,
            user,
        ):
            raise _windows_error("GetProcessTimes")

        memory = _PROCESS_MEMORY_COUNTERS_EX()
        memory.cb = ctypes.sizeof(memory)
        if not _psapi.GetProcessMemoryInfo(
            self._handle,
            memory,
            memory.cb,
        ):
            raise _windows_error("GetProcessMemoryInfo")

        handles = wintypes.DWORD()
        if not _kernel32.GetProcessHandleCount(self._handle, handles):
            raise _windows_error("GetProcessHandleCount")

        return ProcessResourceSample(
            monotonic_ns=time.monotonic_ns(),
            process_cpu_seconds=(
                _filetime_value(kernel) + _filetime_value(user)
            )
            / 10_000_000.0,
            working_set_bytes=int(memory.WorkingSetSize),
            private_bytes=int(memory.PrivateUsage),
            handle_count=int(handles.value),
            thread_count=self._thread_count(),
        )

    def close(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is not None and not _kernel32.CloseHandle(handle):
            raise _windows_error("CloseHandle")

    def _thread_count(self) -> int:
        snapshot = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
        if snapshot == _INVALID_HANDLE_VALUE:
            raise _windows_error("CreateToolhelp32Snapshot")
        try:
            entry = _THREADENTRY32()
            entry.dwSize = ctypes.sizeof(entry)
            if not _kernel32.Thread32First(snapshot, entry):
                code = ctypes.get_last_error()
                if code == _ERROR_NO_MORE_FILES:
                    return 0
                raise _windows_error("Thread32First")
            count = 0
            while True:
                if int(entry.th32OwnerProcessID) == self.pid:
                    count += 1
                entry.dwSize = ctypes.sizeof(entry)
                if not _kernel32.Thread32Next(snapshot, entry):
                    code = ctypes.get_last_error()
                    if code != _ERROR_NO_MORE_FILES:
                        raise _windows_error("Thread32Next")
                    return count
        finally:
            if not _kernel32.CloseHandle(snapshot):
                raise _windows_error("CloseHandle(thread snapshot)")

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


__all__ = ["ProcessResourceSample", "ProcessResourceSampler"]
