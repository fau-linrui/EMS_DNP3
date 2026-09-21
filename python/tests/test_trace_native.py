"""Read-only/local-simulator trace regression; never connects to an EMS address."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import time

import pytest

from dnp3_master import (
    AnalogOutputCommand, CrobCommand, Dnp3MasterClient, HostCommandError,
    HostProcessConfig, ReadHeader, TcpConnectionConfig, TraceConfig,
    TraceIncompleteError, ProcessResourceSampler,
)
from dnp3_master.local_outstation import LocalTestOutstation


TC_APP_PROTOCOL_TRACE_LOCAL_001 = "TC_APP_PROTOCOL_TRACE_LOCAL_001"


@contextmanager
def local_stack(*, point_count=2):
    host = os.environ.get("DNP3_MASTER_HOST_EXE")
    outstation_exe = os.environ.get("DNP3_TEST_OUTSTATION_EXE")
    if not host or not outstation_exe:
        pytest.skip("run scripts/test.ps1 to supply the built local executables")
    with LocalTestOutstation(Path(outstation_exe), point_count=point_count) as outstation:
        with Dnp3MasterClient(HostProcessConfig(executable=Path(host))) as client:
            connection = TcpConnectionConfig(
                host="127.0.0.1", port=outstation.port, connect_timeout=3,
                retry_min=0.05, retry_max=0.2, simulator=True,
            )
            yield client, outstation, connection


def drain(client, *, max_records=256, require_complete=True):
    # Bounded even if a producer keeps refilling the queue: no endless drain.
    batches = []
    for _ in range(2048):
        batch = client.read_trace(max_records=max_records, require_complete=require_complete)
        batches.append(batch)
        if batch.summary.queued_records == 0:
            return batches
    pytest.fail("trace did not drain within the local test batch budget")


def test_trace_default_off_and_strict_session_boundaries():
    with local_stack() as (client, _, connection):
        assert "trace.read" in client.hello_info["supported_commands"]
        assert client.get_status()["trace"]["state"] == "IDLE"
        client.connect(connection)
        assert client.read((ReadHeader.all_objects(1, 2),)).summary["received_total"] == 2
        assert client.get_stats()["trace"]["last_sequence"] == 0
        with pytest.raises(HostCommandError) as failure:
            client.start_trace()
        assert failure.value.code == "INVALID_STATE"
        client.disconnect()
        client.start_trace()
        assert client.read_trace().timed_out
        client.connect(connection)
        with pytest.raises(HostCommandError) as failure:
            client.stop_trace()
        assert failure.value.code == "INVALID_STATE"
        client.disconnect()
        assert client.stop_trace().state == "STOPPED"
        assert client.stop_trace().state == "STOPPED"
        drain(client)


def test_trace_reads_controls_unsolicited_confirm_and_one_record_batches():
    assert TC_APP_PROTOCOL_TRACE_LOCAL_001
    with local_stack() as (client, outstation, connection):
        client.start_trace()
        client.connect(connection)
        result = client.read(tuple(ReadHeader.all_objects(g, v) for g, v in
                                   ((1, 2), (30, 5), (10, 2), (40, 3))))
        assert result.summary["received_total"] == 8
        control = client.direct_operate((CrobCommand(
            index=0, operation="latch_on", on_time_ms=0, off_time_ms=0,
        ),))
        assert control.all_success
        assert client.direct_operate((AnalogOutputCommand.float32(0, -12.5),)).all_success
        feedback = client.read((ReadHeader.all_objects(40, 3),))
        assert any(point.index == 0 and point.value == -12.5 for point in feedback.measurements)
        assert client.enable_unsolicited((1, 2)).task_status == "SUCCESS"
        outstation.update_binary_input(True, timestamp_ms=1700000000201)
        outstation.update_analog_input(123.5, timestamp_ms=1700000000202)
        received = set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and len(received) < 2:
            events = client.wait_unsolicited(wait_timeout=0.1)
            received.update(point.kind for point in events.measurements)
        assert {"binary_input", "analog_input"} <= received
        assert client.disable_unsolicited((1, 2)).task_status == "SUCCESS"
        client.disconnect()
        client.stop_trace()
        batches = drain(client, max_records=1)
        frames = [frame for batch in batches for frame in batch.frames]
        applications = [app for batch in batches for app in batch.applications]
        assert {frame.direction for frame in frames} == {"RX", "TX"}
        assert all(frame.crc_valid and frame.raw_bytes[:2] == b"\x05\x64" for frame in frames)
        assert {1, 5, 20, 21} <= {app.header["function_code"] for app in applications
                                if app.direction == "TX"}
        assert {129, 130} <= {app.header["function_code"] for app in applications
                             if app.direction == "RX"}
        assert any(app.direction == "TX" and app.header["function_code"] == 0
                   for app in applications), "automatic Confirm must be observable"
        def values(direction, function, group, variation):
            return [value for app in applications
                    if app.direction == direction and app.header["function_code"] == function
                    for obj in app.objects if (obj["group"], obj["variation"]) == (group, variation)
                    for value in obj["values"]]

        crob = values("TX", 5, 12, 1)[0]
        assert crob["index"] == 0 and crob["operation"] == "LATCH_ON"
        assert (crob["count"], crob["on_time_ms"], crob["off_time_ms"]) == (1, 0, 0)
        assert values("TX", 5, 41, 3)[0]["value"] == -12.5
        assert any(value["value"] is True and value["time_ms"] == 1700000000201
                   for value in values("RX", 130, 2, 2))
        assert any(value["value"] == 123.5 and value["time_ms"] == 1700000000202
                   for value in values("RX", 130, 32, 7))
        for batch in batches:
            json.dumps(batch.to_dict(), allow_nan=False)
        assert client.get_status()["trace"]["dropped_records"] == 0


def test_trace_reassembles_transport_segments_and_preserves_read_results():
    with local_stack(point_count=200) as (client, _, connection):
        client.start_trace()
        client.connect(connection)
        result = client.read((ReadHeader.all_objects(30, 5),), max_measurements=201)
        assert result.summary["received_total"] == 200
        # Consume during an active session as well as after disconnect.
        batches = drain(client, max_records=3)
        client.disconnect()
        client.stop_trace()
        batches += drain(client, max_records=3)
        applications = [app for batch in batches for app in batch.applications]
        assert any(app.direction == "RX" and len(app.frame_sequences) > 1
                   for app in applications)
        assert any(len(app.raw_bytes) > 250 for app in applications if app.direction == "RX")
        assert all(batch.complete for batch in batches)


def test_trace_overflow_is_explicit_and_does_not_break_dnp3_reads():
    with local_stack() as (client, _, connection):
        client.start_trace(TraceConfig(queue_capacity=1))
        client.connect(connection)
        assert client.read((ReadHeader.all_objects(1, 2),)).summary["received_total"] == 2
        with pytest.raises(TraceIncompleteError) as failure:
            client.read_trace()
        assert failure.value.batch.summary.dropped_records > 0
        assert client.is_running
        assert client.read((ReadHeader.all_objects(30, 5),)).summary["received_total"] == 2
        client.disconnect()
        assert not client.stop_trace().complete
        assert not drain(client, require_complete=False)[-1].complete
        # An explicitly new trace restores diagnostics; old loss is not hidden.
        second = client.start_trace()
        assert second.trace_id == "trace-2" and second.complete
        client.connect(connection)
        client.read((ReadHeader.all_objects(1, 2),))
        client.disconnect()
        client.stop_trace()
        assert all(batch.complete for batch in drain(client))


@pytest.mark.skipif(os.name != "nt", reason="Windows native resource acceptance")
def test_repeated_trace_sessions_release_native_handles_and_threads():
    with local_stack() as (client, _, connection):
        def cycle():
            client.start_trace(TraceConfig(queue_capacity=2048))
            client.connect(connection)
            client.read((ReadHeader.all_objects(30, 5),))
            client.disconnect()
            client.stop_trace()
            assert all(batch.complete for batch in drain(client))

        cycle()  # Warm up the pinned stack before measuring steady-state ownership.
        assert client.pid is not None
        with ProcessResourceSampler(client.pid) as sampler:
            baseline = sampler.sample()
            for _ in range(20):
                cycle()
            final = sampler.sample()
        assert final.handle_count <= baseline.handle_count + 2
        assert final.thread_count <= baseline.thread_count
