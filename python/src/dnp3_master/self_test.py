"""Deterministic loopback smoke test for a packaged or freshly built host."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
from typing import Sequence

from . import __version__
from .client import Dnp3MasterClient
from .local_outstation import LocalTestOutstation
from .models import (
    AnalogOutputCommand,
    CaptureConfig,
    CapturePointRange,
    CrobCommand,
    HostProcessConfig,
    LabSafetyConfig,
    ReadHeader,
    TcpConnectionConfig,
)


def run_self_test(host_executable: Path, outstation_executable: Path) -> dict[str, object]:
    host = host_executable.expanduser().resolve(strict=False)
    outstation = outstation_executable.expanduser().resolve(strict=False)
    if not host.is_file():
        raise FileNotFoundError(f"native host does not exist: {host}")
    if not outstation.is_file():
        raise FileNotFoundError(f"local test outstation does not exist: {outstation}")

    local_outstation = LocalTestOutstation(outstation).start()
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
                port=local_outstation.port,
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
        capture_started = client.begin_capture(
            CaptureConfig(
                mode="static_set",
                sources=("solicited",),
                duration_limit=3.0,
                point_ranges=(
                    CapturePointRange("binary_input", 0, 1),
                    CapturePointRange("analog_input", 0, 1),
                    CapturePointRange("binary_output_status", 0, 1),
                    CapturePointRange("analog_output_status", 0, 1),
                ),
                queue_capacity=64,
                mismatch_sample_limit=8,
            )
        )
        capture_read = client.read(
            (
                ReadHeader.all_objects(1, 2),
                ReadHeader.all_objects(30, 5),
                ReadHeader.all_objects(10, 2),
                ReadHeader.all_objects(40, 3),
            ),
            timeout=3.0,
            max_measurements=100,
            return_mode="summary",
        )
        capture_terminal = client.end_capture(
            capture_started.capture_id,
            drain_timeout=2.0,
        )
        binary_baseline = client.read(
            [ReadHeader.range16(10, 2, 0, 0)], timeout=3.0
        )
        binary_operate = client.direct_operate(
            [CrobCommand(index=0, operation="latch_on")], timeout=3.0
        )
        binary_operated = client.read(
            [ReadHeader.range16(10, 2, 0, 0)], timeout=3.0
        )
        binary_restore = client.direct_operate(
            [CrobCommand(index=0, operation="latch_off")], timeout=3.0
        )
        binary_restored = client.read(
            [ReadHeader.range16(10, 2, 0, 0)], timeout=3.0
        )
        analog_baseline = client.read(
            [ReadHeader.range16(40, 3, 0, 0)], timeout=3.0
        )
        analog_operate = client.select_and_operate(
            [AnalogOutputCommand.float32(0, 1.25)], timeout=3.0
        )
        analog_operated = client.read(
            [ReadHeader.range16(40, 3, 0, 0)], timeout=3.0
        )
        analog_restore = client.select_and_operate(
            [AnalogOutputCommand.float32(0, 0.0)], timeout=3.0
        )
        analog_restored = client.read(
            [ReadHeader.range16(40, 3, 0, 0)], timeout=3.0
        )
        snapshot = local_outstation.snapshot()
        stats = client.get_stats()
        command_results = (
            binary_operate,
            binary_restore,
            analog_operate,
            analog_restore,
        )
        feedback_reads = (
            binary_baseline,
            binary_operated,
            binary_restored,
            analog_baseline,
            analog_operated,
            analog_restored,
        )
        if any(
            result.task_status != "SUCCESS" or len(result.measurements) != 1
            for result in feedback_reads
        ):
            raise RuntimeError("loopback feedback read did not return one exact point")
        binary_feedback_cycle = [
            binary_baseline.measurements[0].value,
            binary_operated.measurements[0].value,
            binary_restored.measurements[0].value,
        ]
        analog_feedback_cycle = [
            analog_baseline.measurements[0].value,
            analog_operated.measurements[0].value,
            analog_restored.measurements[0].value,
        ]
        if integrity.task_status != "SUCCESS" or len(integrity.measurements) < 9:
            raise RuntimeError("loopback integrity poll did not return the test database")
        if (
            analog.task_status != "SUCCESS"
            or len(analog.measurements) != 1
            or analog.measurements[0].value != 123.5
        ):
            raise RuntimeError("loopback G30V5 range read did not match 123.5")
        if (
            capture_read.task_status != "SUCCESS"
            or not capture_terminal.valid
            or capture_terminal.state != "FINALIZED"
            or capture_terminal.expected_total != 8
            or capture_terminal.received_unique != 8
            or capture_terminal.missing != 0
            or capture_terminal.duplicates != 0
            or capture_terminal.unmatched_total != 0
            or capture_terminal.queue_overflow != 0
        ):
            raise RuntimeError("loopback static capture did not match its exact truth set")
        if not all(
            result.task_status == "SUCCESS"
            and result.all_success
            and len(result.point_results) == 1
            for result in command_results
        ):
            raise RuntimeError("loopback control or restore command failed")
        if binary_feedback_cycle != [False, True, False]:
            raise RuntimeError(
                f"unexpected binary feedback cycle: {binary_feedback_cycle!r}"
            )
        if analog_feedback_cycle != [0.0, 1.25, 0.0]:
            raise RuntimeError(
                f"unexpected analog feedback cycle: {analog_feedback_cycle!r}"
            )
        if snapshot.get("operation_count") != 4:
            raise RuntimeError(
                "loopback outstation did not observe exactly four operations"
            )
        if stats.get("scope") != "host_channel_and_local_queues":
            raise RuntimeError("loopback host returned an unexpected stats scope")
        client.disconnect()
        return {
            "ok": True,
            "framework_version": __version__,
            "backend": client.hello_info["backend"],
            "backend_version": client.hello_info["backend_version"],
            "integrity_measurements": len(integrity.measurements),
            "analog_value": analog.measurements[0].value,
            "capture_expected": capture_terminal.expected_total,
            "capture_received_unique": capture_terminal.received_unique,
            "capture_valid": capture_terminal.valid,
            "command_points": sum(
                len(result.point_results) for result in command_results
            ),
            "command_all_success": all(result.all_success for result in command_results),
            "binary_feedback_cycle": binary_feedback_cycle,
            "analog_feedback_cycle": analog_feedback_cycle,
            "outstation_operation_count": snapshot["operation_count"],
            "stats_scope": stats["scope"],
        }
    finally:
        diagnostics = client.close()
        if diagnostics.cleanup_error is not None:
            cleanup_error = RuntimeError(diagnostics.cleanup_error)
        try:
            local_outstation.close()
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
