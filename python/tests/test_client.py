from __future__ import annotations

import os
from pathlib import Path
import sys
import time

import pytest

from dnp3_master import (
    ClientStateError,
    Dnp3MasterClient,
    HostCommandError,
    HostExitedError,
    HostProcessConfig,
    HostProtocolError,
    HostStartError,
    HostTimeoutError,
    TcpConnectionConfig,
)


FAKE_HOST = Path(__file__).with_name("fake_host.py")


def fake_config(mode: str, **overrides: object) -> HostProcessConfig:
    values: dict[str, object] = {
        "executable": Path(sys.executable),
        "arguments": (str(FAKE_HOST), "--mode", mode),
        "startup_timeout": 1.0,
        "request_timeout": 1.0,
        "shutdown_timeout": 0.2,
        "diagnostic_tail_bytes": 4096,
    }
    values.update(overrides)
    return HostProcessConfig(**values)


def test_context_manager_performs_hello_and_graceful_shutdown() -> None:
    client = Dnp3MasterClient(fake_config("normal"))
    with client as running:
        assert running is client
        assert client.is_running
        assert client.hello_info["backend"] == "none"
        assert client.get_status()["state"] == "READY"
        pid = client.pid

    diagnostics = client.diagnostics
    assert pid is not None
    assert diagnostics.pid == pid
    assert diagnostics.returncode == 0
    assert diagnostics.cleanup_error is None
    assert not client.is_running
    if os.name == "nt":
        assert diagnostics.job_object_assigned is True


def test_host_command_error_is_typed_and_process_remains_usable() -> None:
    with Dnp3MasterClient(fake_config("normal")) as client:
        with pytest.raises(HostCommandError) as captured:
            client.request("connect")
        assert captured.value.code == "UNSUPPORTED_BY_BACKEND"
        assert captured.value.details == {"source": "fake_host"}
        assert client.get_status()["state"] == "READY"


def test_tcp_helpers_use_validated_protocol_parameters() -> None:
    connection = TcpConnectionConfig(
        host="127.0.0.1",
        port=20001,
        connect_timeout=0.25,
        retry_min=0.05,
        retry_max=0.5,
        master_address=7,
        outstation_address=8,
        keep_alive_timeout=2.0,
    )
    with Dnp3MasterClient(fake_config("tcp_api")) as client:
        connected = client.connect(connection)
        assert connected["state"] == "CONNECTED"
        assert connected["received"] == connection.to_params()
        assert client.get_status()["state"] == "CONNECTED"

        waited = client.wait_event(wait_timeout=0.025, max_events=3)
        assert waited["received"] == {"timeout_ms": 25, "max_events": 3}
        assert waited["timed_out"] is True

        disconnected = client.disconnect()
        assert disconnected["state"] == "READY"
        with pytest.raises(HostCommandError) as captured:
            client.disconnect()
        assert captured.value.code == "NOT_CONNECTED"


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"host": ""}, "host"),
        ({"host": "bad host"}, "host"),
        ({"host": "127.0.0.1", "port": 0}, "port"),
        ({"host": "127.0.0.1", "master_address": 65520}, "master_address"),
        (
            {
                "host": "127.0.0.1",
                "master_address": 9,
                "outstation_address": 9,
            },
            "must differ",
        ),
        ({"host": "127.0.0.1", "connect_timeout": 0.049}, "connect_timeout"),
        (
            {"host": "127.0.0.1", "retry_min": 2.0, "retry_max": 1.0},
            "retry_min",
        ),
        ({"host": "127.0.0.1", "keep_alive_timeout": 0.5}, "keep_alive"),
    ],
)
def test_tcp_connection_config_rejects_invalid_values(
    kwargs: dict[str, object], field: str
) -> None:
    with pytest.raises(ValueError, match=field):
        TcpConnectionConfig(**kwargs)


def test_tcp_connection_config_accepts_documented_boundaries() -> None:
    connection = TcpConnectionConfig(
        host="example.invalid",
        port=65535,
        connect_timeout=0.05,
        retry_min=0.01,
        retry_max=300.0,
        master_address=0,
        outstation_address=65519,
        keep_alive_timeout=86400.0,
    )
    assert connection.to_params()["link"] == {
        "master_address": 0,
        "outstation_address": 65519,
        "keep_alive_timeout_ms": 86400000,
    }


def test_startup_timeout_terminates_process_and_preserves_diagnostics() -> None:
    client = Dnp3MasterClient(
        fake_config("silent", startup_timeout=0.1, shutdown_timeout=0.1)
    )
    with pytest.raises(HostTimeoutError) as captured:
        client.start()

    assert not client.is_running
    assert captured.value.diagnostics is not None
    assert captured.value.diagnostics.returncode is not None
    first = client.close()
    second = client.close()
    assert first == second


def test_request_timeout_terminates_process_and_rejects_reuse() -> None:
    client = Dnp3MasterClient(fake_config("hang_on_status", request_timeout=0.1))
    client.start()
    with pytest.raises(HostTimeoutError, match="get_status"):
        client.get_status()
    assert not client.is_running
    with pytest.raises(ClientStateError, match="closed"):
        client.get_status()


def test_abnormal_exit_reports_code_and_stderr_tail() -> None:
    client = Dnp3MasterClient(fake_config("exit_23"))
    with pytest.raises(HostExitedError) as captured:
        client.start()

    diagnostics = captured.value.diagnostics
    assert diagnostics is not None
    assert diagnostics.returncode == 23
    assert "controlled-exit-23" in diagnostics.stderr_tail


@pytest.mark.parametrize(
    ("mode", "message_fragment"),
    [
        ("invalid_json", "valid JSON"),
        ("wrong_id", "response id"),
        ("schema_v2", "schema_version"),
        ("duplicate_key", "duplicate"),
    ],
)
def test_protocol_violation_terminates_process(
    mode: str,
    message_fragment: str,
) -> None:
    client = Dnp3MasterClient(fake_config(mode))
    with pytest.raises(HostProtocolError, match=message_fragment):
        client.start()
    assert not client.is_running
    assert client.diagnostics.returncode is not None


def test_stderr_is_drained_continuously_and_tail_is_bounded() -> None:
    client = Dnp3MasterClient(fake_config("stderr_flood"))
    client.start()
    diagnostics = client.close()

    assert diagnostics.returncode == 0
    assert diagnostics.stderr_tail.endswith("E" * 64)
    assert len(diagnostics.stderr_tail.encode("utf-8")) <= 4096


def test_unsolicited_stdout_flood_is_bounded_and_terminates_host() -> None:
    client = Dnp3MasterClient(fake_config("stdout_flood"))
    try:
        client.start()
    except HostProtocolError as error:
        assert "queue" in str(error)
    else:
        with pytest.raises(HostProtocolError):
            client.get_status()
    assert not client.is_running
    assert len(client.diagnostics.stdout_tail.encode("utf-8")) <= 4096


def test_unresponsive_shutdown_is_killed_and_close_is_idempotent() -> None:
    client = Dnp3MasterClient(fake_config("ignore_shutdown"))
    client.start()
    first = client.close()
    second = client.close()

    assert first == second
    assert first.returncode is not None
    assert first.cleanup_error is not None
    assert "timed out" in first.cleanup_error.lower()
    assert not client.is_running


def test_process_that_exits_while_idle_is_reported_during_cleanup() -> None:
    client = Dnp3MasterClient(fake_config("exit_after_hello"))
    client.start()
    time.sleep(0.1)
    diagnostics = client.close()

    assert diagnostics.returncode == 37
    assert diagnostics.cleanup_error is not None
    assert "before shutdown" in diagnostics.cleanup_error
    assert "controlled-exit-after-hello" in diagnostics.stderr_tail


def test_closed_client_rejects_new_requests() -> None:
    client = Dnp3MasterClient(fake_config("normal"))
    client.start()
    client.close()
    with pytest.raises(ClientStateError, match="closed"):
        client.get_status()


def test_missing_executable_fails_before_process_creation(tmp_path: Path) -> None:
    client = Dnp3MasterClient(HostProcessConfig(executable=tmp_path / "missing.exe"))
    with pytest.raises(HostStartError, match="does not exist"):
        client.start()
    assert client.diagnostics.pid is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("startup_timeout", 0),
        ("request_timeout", -1),
        ("shutdown_timeout", 0),
        ("diagnostic_tail_bytes", 0),
        ("max_response_bytes", 63),
    ],
)
def test_process_config_rejects_invalid_limits(field: str, value: object) -> None:
    values: dict[str, object] = {"executable": Path(sys.executable), field: value}
    with pytest.raises(ValueError, match=field):
        HostProcessConfig(**values)


def test_hello_info_requires_started_client() -> None:
    client = Dnp3MasterClient(fake_config("normal"))
    with pytest.raises(ClientStateError, match="not completed"):
        _ = client.hello_info
