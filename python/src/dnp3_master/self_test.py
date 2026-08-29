"""Deterministic loopback smoke test for a packaged or freshly built host."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import queue
import socket
import subprocess
import sys
import tempfile
import threading
from typing import Sequence

from . import __version__
from .client import Dnp3MasterClient
from .models import (
    CrobCommand,
    HostProcessConfig,
    LabSafetyConfig,
    ReadHeader,
    TcpConnectionConfig,
)


def _unused_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        return int(reservation.getsockname()[1])


def _terminate_process(process: subprocess.Popen[str]) -> None:
    """Best-effort bounded cleanup for a helper that failed during startup."""

    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2.0)


def _start_outstation(executable: Path, port: int) -> subprocess.Popen[str]:
    process = subprocess.Popen(
        [str(executable), "--port", str(port)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert process.stdout is not None
    ready_queue: queue.Queue[str] = queue.Queue(maxsize=1)
    reader = threading.Thread(
        target=lambda: ready_queue.put(process.stdout.readline()),
        name=f"dnp3-self-test-outstation-{process.pid}",
        daemon=True,
    )
    reader.start()
    try:
        line = ready_queue.get(timeout=5.0)
    except queue.Empty as error:
        _terminate_process(process)
        stderr = process.stderr.read() if process.stderr is not None else ""
        raise RuntimeError(f"local outstation did not become ready: {stderr}") from error
    try:
        ready = json.loads(line)
    except json.JSONDecodeError as error:
        _terminate_process(process)
        raise RuntimeError(f"local outstation returned invalid readiness data: {line!r}") from error
    if ready != {"ready": True, "port": port}:
        _terminate_process(process)
        raise RuntimeError(f"unexpected local outstation readiness data: {ready!r}")
    return process


def _stop_outstation(process: subprocess.Popen[str]) -> None:
    if process.poll() is None and process.stdin is not None:
        process.stdin.write("shutdown\n")
        process.stdin.flush()
    try:
        process.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        _terminate_process(process)
    stderr = process.stderr.read() if process.stderr is not None else ""
    if process.returncode != 0:
        raise RuntimeError(
            f"local outstation exited with code {process.returncode}: {stderr}"
        )


def run_self_test(host_executable: Path, outstation_executable: Path) -> dict[str, object]:
    host = host_executable.expanduser().resolve(strict=False)
    outstation = outstation_executable.expanduser().resolve(strict=False)
    if not host.is_file():
        raise FileNotFoundError(f"native host does not exist: {host}")
    if not outstation.is_file():
        raise FileNotFoundError(f"local test outstation does not exist: {outstation}")

    port = _unused_local_port()
    process = _start_outstation(outstation, port)
    incident_directory = tempfile.TemporaryDirectory(
        prefix="dnp3-self-test-safety-incidents-"
    )
    client = Dnp3MasterClient(
        HostProcessConfig(
            executable=host,
            safety_incident_directory=Path(incident_directory.name),
            startup_timeout=5.0,
            request_timeout=6.0,
            shutdown_timeout=3.0,
        )
    )
    cleanup_error: Exception | None = None
    try:
        client.start()
        client.connect(
            TcpConnectionConfig(
                host="127.0.0.1",
                port=port,
                connect_timeout=3.0,
                retry_min=0.05,
                retry_max=0.2,
                master_address=1,
                outstation_address=1024,
                safety=LabSafetyConfig(
                    operator_id="local-self-test",
                    dut_id="bundled-loopback-outstation",
                    allow_state_change=True,
                ),
            )
        )
        integrity = client.integrity_poll(timeout=3.0)
        analog = client.read([ReadHeader.range16(30, 0, 0, 0)], timeout=3.0)
        command = client.direct_operate(
            [CrobCommand(index=0, operation="latch_on")], timeout=3.0
        )
        stats = client.get_stats()
        client.disconnect()
        return {
            "ok": True,
            "framework_version": __version__,
            "backend": client.hello_info["backend"],
            "backend_version": client.hello_info["backend_version"],
            "integrity_measurements": len(integrity.measurements),
            "analog_value": analog.measurements[0].value,
            "command_points": len(command.point_results),
            "command_all_success": command.all_success,
            "stats_scope": stats["scope"],
        }
    finally:
        diagnostics = client.close()
        if diagnostics.cleanup_error is not None:
            cleanup_error = RuntimeError(diagnostics.cleanup_error)
        try:
            _stop_outstation(process)
        except Exception as error:
            cleanup_error = cleanup_error or error
        incident_directory.cleanup()
        if cleanup_error is not None and sys.exc_info()[0] is None:
            raise cleanup_error


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a loopback DNP3 read/control self-test without a real EMS"
    )
    parser.add_argument("--host-exe", required=True, type=Path)
    parser.add_argument("--outstation-exe", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        result = run_self_test(arguments.host_exe, arguments.outstation_exe)
    except Exception as error:
        print(f"SELF-TEST FAILED: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
