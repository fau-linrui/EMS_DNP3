from __future__ import annotations

import io
from pathlib import Path
import threading
import time

import pytest

from dnp3_master import (
    ClientStateError, CrobCommand, Dnp3MasterClient, HostTimeoutError,
    LabSafetyConfig, SafetyIncidentPersistenceError, TcpConnectionConfig,
)
from dnp3_master.client import _PendingWrite, _STDIN_STOP
from test_client import fake_config


@pytest.mark.parametrize("mode, timeout", [
    ("stop_reading_after_hello", 0.1), ("slow_request_io", 0.22),
])
def test_pipe_write_and_response_share_a_bounded_deadline(mode: str, timeout: float) -> None:
    client = Dnp3MasterClient(fake_config(mode, startup_timeout=3))
    client.start()
    process = client._process
    assert process is not None
    writer = client._stdin_thread
    # Protect the test runner itself if bounded writes regress.
    emergency = threading.Timer(4.0, process.kill)
    emergency.start()
    started = time.monotonic()
    try:
        with pytest.raises(HostTimeoutError):
            client.request("transport_probe", {"padding": "x" * 131072}, timeout=timeout)
        assert time.monotonic() - started < 2.5
        assert not client.is_running
        assert writer is not None and not writer.is_alive()
        with pytest.raises(ClientStateError):
            client.get_status()
    finally:
        emergency.cancel()
        emergency.join()
        client.close()
        client.close()


def test_writer_handles_short_writes_and_shuts_down_without_a_thread_leak() -> None:
    class ShortStream(io.BytesIO):
        def write(self, value: bytes) -> int:
            return super().write(value[:7])

    client = Dnp3MasterClient(fake_config("normal"))
    stream = ShortStream()
    pending = _PendingWrite(b"complete-json-line\n" * 20, threading.Event())
    client._stdin_queue.put_nowait(pending)
    writer = threading.Thread(target=client._write_stdin, args=(stream,), daemon=True)
    writer.start()
    try:
        assert pending.completed.wait(2)
        assert pending.error is None
        assert stream.getvalue() == pending.payload
    finally:
        client._io_stopping.set()
        client._stdin_queue.put_nowait(_STDIN_STOP)
        writer.join(2)
        assert not writer.is_alive()
        client.close()


@pytest.mark.parametrize("size", [True, 63, 16777217, 1024.0])
def test_request_size_configuration_is_strict(size: object) -> None:
    with pytest.raises(ValueError, match="max_request_bytes"):
        fake_config("normal", max_request_bytes=size)


def test_oversized_request_is_rejected_before_writing_and_preserves_session() -> None:
    with Dnp3MasterClient(fake_config("normal", max_request_bytes=256)) as client:
        with pytest.raises(ValueError, match="max_request_bytes"):
            client.request("transport_probe", {"padding": "x" * 257})
        assert client._request_write_started is False
        assert client.get_status()["state"] == "READY"


def _fake_control_client(tmp_path: Path) -> Dnp3MasterClient:
    # This existing fake NDJSON host has no sockets or native DNP3 backend.
    client = Dnp3MasterClient(fake_config("tcp_api", safety_incident_directory=tmp_path))
    client.start()
    client.connect(TcpConnectionConfig(
        host="127.0.0.1",
        safety=LabSafetyConfig(
            operator_id="synthetic-reviewer", dut_id="synthetic-transport-dut",
            allow_state_change=True,
        ),
    ))
    return client


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("phase", ["enqueue", "response", "validation"])
def test_interrupted_control_persists_incident_and_destroys_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interruption: type[BaseException], phase: str,
) -> None:
    client = _fake_control_client(tmp_path)
    writer = client._stdin_thread

    def interrupt(*args: object, **kwargs: object) -> None:
        raise interruption()

    original_put = client._stdin_queue.put_nowait

    def enqueue_then_interrupt(item: object) -> None:
        original_put(item)
        if isinstance(item, _PendingWrite):
            raise interruption()

    with monkeypatch.context() as injected:
        if phase == "enqueue":
            injected.setattr(client._stdin_queue, "put_nowait", enqueue_then_interrupt)
        elif phase == "response":
            injected.setattr(client._stdout_queue, "get", interrupt)
        else:
            injected.setattr(client, "_validate_command_correlation", interrupt)
        try:
            with pytest.raises(interruption) as caught:
                client.direct_operate([CrobCommand(index=0, operation="latch_on")])
            incident = client.active_safety_incident()
            assert incident is not None
            assert caught.value.incident_id == incident["incident_id"]
            assert caught.value.details["persistent_safety_lock"] is True
            assert not client.state_change_authorized
            assert not client.is_running
            assert writer is not None and not writer.is_alive()
            assert client._stdin_queue.empty()
            with pytest.raises(ClientStateError):
                client.direct_operate([CrobCommand(index=0, operation="latch_off")])
        finally:
            client.close()


def test_interruption_before_dispatch_does_not_create_false_incident(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _fake_control_client(tmp_path)
    try:
        with monkeypatch.context() as injected:
            def interrupt(*args: object) -> None:
                raise KeyboardInterrupt()
            injected.setattr(CrobCommand, "to_params", interrupt)
            with pytest.raises(KeyboardInterrupt):
                client.direct_operate([CrobCommand(index=0, operation="latch_on")])
        assert client.active_safety_incident() is None
        assert client.is_running
        assert client.state_change_authorized
    finally:
        client.close()


def test_interrupted_control_still_closes_if_incident_persistence_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _fake_control_client(tmp_path)
    def interrupt(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt()
    def fail_store(*args: object, **kwargs: object) -> None:
        raise OSError("synthetic persistence failure")
    with monkeypatch.context() as injected:
        injected.setattr(client._stdout_queue, "get", interrupt)
        injected.setattr(client._safety_incident_store, "record_uncertain", fail_store)
        try:
            with pytest.raises(SafetyIncidentPersistenceError) as caught:
                client.direct_operate([CrobCommand(index=0, operation="latch_on")])
            assert isinstance(caught.value.__cause__, KeyboardInterrupt)
            assert not client.is_running
            assert not client.state_change_authorized
        finally:
            client.close()
