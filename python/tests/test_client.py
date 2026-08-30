from __future__ import annotations

import os
from pathlib import Path
import sys
import time

import pytest

from dnp3_master import (
    AnalogOutputCommand,
    ClientStateError,
    CommandTaskResult,
    CommandPointResult,
    CrobCommand,
    Dnp3MasterClient,
    HostCommandError,
    HostExitedError,
    HostProcessConfig,
    HostProtocolError,
    HostStartError,
    HostTimeoutError,
    LabSafetyConfig,
    ReadHeader,
    ReadTaskResult,
    TcpConnectionConfig,
    UnsolicitedBatchResult,
    UnsolicitedControlResult,
    SafetyIncidentAcknowledgmentError,
    SafetyIncidentConfigurationError,
    UnresolvedSafetyIncidentError,
)
from dnp3_master.client import _TailBuffer


FAKE_HOST = Path(__file__).with_name("fake_host.py")


@pytest.mark.parametrize(
    ("raw", "status"),
    [
        (0, "SUCCESS"),
        (1, "TIMEOUT"),
        (2, "NO_SELECT"),
        (3, "FORMAT_ERROR"),
        (4, "NOT_SUPPORTED"),
        (5, "ALREADY_ACTIVE"),
        (6, "HARDWARE_ERROR"),
        (7, "LOCAL"),
        (8, "TOO_MANY_OBJS"),
        (9, "NOT_AUTHORIZED"),
        (10, "AUTOMATION_INHIBIT"),
        (11, "PROCESSING_LIMITED"),
        (12, "OUT_OF_RANGE"),
        (126, "NON_PARTICIPATING"),
        (127, "UNDEFINED"),
    ],
)
def test_command_status_exact_ieee_1815_2012_names(raw: int, status: str) -> None:
    point = CommandPointResult.from_mapping(
        {
            "header_index": 0,
            "index": 1,
            "state": "SUCCESS" if raw == 0 else "FAILURE",
            "state_raw": 5 if raw == 0 else 6,
            "status": status,
            "status_raw": raw,
            "status_edition": "IEEE1815-2012",
            "status_backend": status,
            "status_reserved_2012": False,
            "status_wire_raw_unambiguous": raw != 127,
            "requested": None,
        }
    )

    assert point.status == status


def test_command_status_uses_the_ieee_1815_2012_catalog() -> None:
    reserved = CommandPointResult.from_mapping(
        {
            "header_index": 0,
            "index": 7,
            "state": "FAILURE",
            "state_raw": 6,
            "status": "RESERVED",
            "status_raw": 13,
            "status_edition": "IEEE1815-2012",
            "status_backend": "DOWNSTREAM_LOCAL",
            "status_reserved_2012": True,
            "status_wire_raw_unambiguous": True,
            "requested": None,
        }
    )

    assert reserved.status == "RESERVED"
    assert reserved.status_backend == "DOWNSTREAM_LOCAL"
    assert reserved.status_reserved_2012 is True
    assert reserved.status_wire_raw_unambiguous is True


def test_command_status_rejects_a_later_edition_name_in_2012_mode() -> None:
    with pytest.raises(ValueError, match="does not match IEEE 1815-2012"):
        CommandPointResult.from_mapping(
            {
                "header_index": 0,
                "index": 7,
                "state": "FAILURE",
                "state_raw": 6,
                "status": "DOWNSTREAM_LOCAL",
                "status_raw": 13,
                "status_edition": "IEEE1815-2012",
                "status_backend": "DOWNSTREAM_LOCAL",
                "status_reserved_2012": True,
                "status_wire_raw_unambiguous": True,
                "requested": None,
            }
        )


def test_command_status_127_exposes_opendnp3_wire_ambiguity() -> None:
    undefined = CommandPointResult.from_mapping(
        {
            "header_index": 0,
            "index": 7,
            "state": "FAILURE",
            "state_raw": 6,
            "status": "UNDEFINED",
            "status_raw": 127,
            "status_edition": "IEEE1815-2012",
            "status_backend": "UNDEFINED",
            "status_reserved_2012": False,
            "status_wire_raw_unambiguous": False,
            "requested": None,
        }
    )

    assert undefined.status_wire_raw_unambiguous is False
    assert undefined.requires_manual_readback is True


def test_command_status_safety_policy_distinguishes_uncertain_from_rejected() -> None:
    def point(raw: int, status: str) -> CommandPointResult:
        return CommandPointResult.from_mapping(
            {
                "header_index": 0,
                "index": 7,
                "state": "FAILURE",
                "state_raw": 6,
                "status": status,
                "status_raw": raw,
                "status_edition": "IEEE1815-2012",
                "status_backend": status,
                "status_reserved_2012": 13 <= raw <= 125,
                "status_wire_raw_unambiguous": raw != 127,
                "requested": None,
            }
        )

    assert point(1, "TIMEOUT").requires_manual_readback is True
    assert point(18, "RESERVED").requires_manual_readback is True
    assert point(2, "NO_SELECT").requires_manual_readback is False


def test_diagnostic_tail_redacts_safety_tokens_across_chunks() -> None:
    token = "0123456789abcdef0123456789abcdef"
    tail = _TailBuffer(1024)
    tail.append(b'{"safety_token":"0123456789abcdef')
    tail.append(b'0123456789abcdef"}')

    assert token not in tail.text()
    assert '"safety_token":"<redacted>"' in tail.text()


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
        with pytest.raises(ClientStateError, match="raw request"):
            client.request("connect")
        assert client.get_status()["state"] == "READY"


@pytest.mark.parametrize(
    "command",
    (
        "hello",
        "shutdown",
        "connect",
        "disconnect",
        "read",
        "direct_operate",
        "select_and_operate",
    ),
)
def test_public_raw_request_cannot_bypass_typed_state_and_safety(
    command: str,
) -> None:
    with Dnp3MasterClient(fake_config("normal")) as client:
        with pytest.raises(ClientStateError, match="typed"):
            client.request(command)
        assert client.is_running


def test_public_raw_request_remains_available_for_unknown_extensions() -> None:
    with Dnp3MasterClient(fake_config("normal")) as client:
        with pytest.raises(HostCommandError) as captured:
            client.request("vendor.read_only_extension")
        assert captured.value.code == "INVALID_REQUEST"
        assert client.is_running


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
        assert client.get_stats()["scope"] == "host_channel_and_local_queues"

        waited = client.wait_event(wait_timeout=0.025, max_events=3)
        assert waited["received"] == {"timeout_ms": 25, "max_events": 3}
        assert waited["timed_out"] is True

        integrity = client.integrity_poll(timeout=0.25, max_measurements=25)
        assert isinstance(integrity, ReadTaskResult)
        assert integrity.task_status == "SUCCESS"
        assert integrity.measurements[0].kind == "analog_input"
        assert integrity.measurements[0].value == 220.5
        assert integrity.measurements_of_kind("analog_input") == integrity.measurements
        assert integrity.raw["received"] == {
            "timeout_ms": 250,
            "max_measurements": 25,
            "return_mode": "detail",
        }

        events = client.class_poll(
            (1, 3),
            timeout=0.25,
            max_measurements=5,
            return_mode="summary",
        )
        assert events.return_mode == "summary"
        assert events.measurements == ()
        assert events.raw["received"]["classes"] == [1, 3]

        header = ReadHeader.range16(30, 0, 0, 999)
        explicit = client.read([header], timeout=0.25)
        assert explicit.raw["received"]["headers"] == [header.to_params()]

        enabled = client.enable_unsolicited((1, 2), timeout=0.25)
        assert isinstance(enabled, UnsolicitedControlResult)
        assert enabled.action == "enable"
        assert enabled.classes == (1, 2)

        unsolicited = client.wait_unsolicited(
            wait_timeout=0.025, max_events=7
        )
        assert isinstance(unsolicited, UnsolicitedBatchResult)
        assert unsolicited.enabled is True
        assert unsolicited.classes == (1, 2)
        assert unsolicited.measurements[0].source == "unsolicited"
        assert unsolicited.measurements[0].session_id == 1

        disabled = client.disable_unsolicited((1, 2), timeout=0.25)
        assert disabled.action == "disable"
        after_disable = client.wait_unsolicited()
        assert after_disable.enabled is False
        assert after_disable.measurements == ()
        assert after_disable.timed_out is True

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


def test_read_header_factories_and_validation() -> None:
    assert ReadHeader.all_objects(60, 1).to_params() == {
        "group": 60,
        "variation": 1,
        "qualifier": "all_objects",
    }
    assert ReadHeader.range8(1, 2, 0, 255).to_params()["stop"] == 255
    assert ReadHeader.range16(30, 5, 0, 65535).to_params()["stop"] == 65535
    assert ReadHeader.count8(60, 2, 10).to_params()["count"] == 10
    assert ReadHeader.count16(60, 3, 1000).to_params()["count"] == 1000

    with pytest.raises(ValueError, match="start must not exceed"):
        ReadHeader.range16(30, 0, 2, 1)
    with pytest.raises(ValueError, match="class data"):
        ReadHeader.range16(60, 2, 0, 1)
    with pytest.raises(ValueError, match="requires count"):
        ReadHeader(group=1, variation=2, qualifier="count8")
    with pytest.raises(ValueError, match="qualifier"):
        ReadHeader(group=1, variation=2, qualifier="raw")


@pytest.mark.parametrize(
    ("method", "kwargs", "message"),
    [
        ("integrity_poll", {"timeout": 0.01}, "timeout"),
        ("integrity_poll", {"timeout": 10**10_000}, "timeout"),
        ("integrity_poll", {"max_measurements": 0}, "max_measurements"),
        ("integrity_poll", {"return_mode": "raw"}, "return_mode"),
        ("class_poll", {"classes": ()}, "classes"),
        ("class_poll", {"classes": (0,)}, "classes"),
        ("class_poll", {"classes": (1, 1)}, "duplicate"),
        ("read", {"headers": ()}, "headers"),
        ("read", {"headers": ({"group": 1},)}, "ReadHeader"),
        ("enable_unsolicited", {"classes": ()}, "classes"),
        ("disable_unsolicited", {"classes": (1, 1)}, "duplicate"),
        ("enable_unsolicited", {"timeout": 0.01}, "timeout"),
        ("wait_unsolicited", {"max_events": 257}, "max_events"),
        ("wait_unsolicited", {"wait_timeout": 61}, "wait_timeout"),
    ],
)
def test_read_helpers_reject_invalid_inputs(
    method: str, kwargs: dict[str, object], message: str
) -> None:
    with Dnp3MasterClient(fake_config("tcp_api")) as client:
        with pytest.raises((TypeError, ValueError), match=message):
            getattr(client, method)(**kwargs)


def test_command_models_validate_ranges_and_serialize() -> None:
    crob = CrobCommand(
        index=7,
        operation="pulse_on",
        trip_close="close",
        count=2,
        on_time_ms=250,
        off_time_ms=500,
    )
    assert crob.to_params() == {
        "type": "crob",
        "index": 7,
        "operation": "pulse_on",
        "trip_close": "close",
        "clear": False,
        "count": 2,
        "on_time_ms": 250,
        "off_time_ms": 500,
    }
    assert AnalogOutputCommand.int16(1, -32768).to_params()["value"] == -32768
    assert AnalogOutputCommand.int32(2, 2147483647).command_type.endswith("int32")
    assert AnalogOutputCommand.float32(3, 1.25).value == 1.25
    assert AnalogOutputCommand.double64(4, -2.5).value == -2.5

    with pytest.raises(ValueError, match="operation"):
        CrobCommand(index=0, operation="toggle")
    with pytest.raises(ValueError, match="int16"):
        AnalogOutputCommand.int16(0, 32768)
    with pytest.raises(ValueError, match="finite"):
        AnalogOutputCommand.double64(0, float("nan"))
    with pytest.raises(ValueError, match="finite"):
        AnalogOutputCommand.double64(0, 10**10_000)


def test_command_helpers_require_lab_session_and_preserve_batch_results(
    tmp_path: Path,
) -> None:
    locked_connection = TcpConnectionConfig(host="127.0.0.1")
    with Dnp3MasterClient(
        fake_config("tcp_api", safety_incident_directory=tmp_path / "incidents")
    ) as client:
        client.connect(locked_connection)
        assert client.state_change_authorized is False
        with pytest.raises(ClientStateError, match="locked"):
            client.direct_operate([CrobCommand(index=0, operation="latch_on")])
        client.disconnect()

    authorized_connection = TcpConnectionConfig(
        host="127.0.0.1",
        safety=LabSafetyConfig(
            operator_id="pytest-operator",
            dut_id="simulated-dut",
            allow_state_change=True,
        ),
    )
    with Dnp3MasterClient(
        fake_config("tcp_api", safety_incident_directory=tmp_path / "incidents")
    ) as client:
        connected = client.connect(authorized_connection)
        assert connected["safety"]["state_change_authorized"] is True
        assert connected["safety"]["token_exposed"] is False
        assert "safety_token" not in connected["safety"]
        assert client.state_change_authorized is True

        result = client.select_and_operate(
            [
                CrobCommand(index=0, operation="latch_on"),
                AnalogOutputCommand.int16(0, -5),
                AnalogOutputCommand.float32(0, 12.5),
            ],
            timeout=0.25,
        )
        assert isinstance(result, CommandTaskResult)
        assert result.all_success is True
        assert len(result.point_results) == 3
        assert {point.status for point in result.point_results} == {"SUCCESS"}

        direct = client.direct_operate(
            [AnalogOutputCommand.double64(1, 99.25)], timeout=0.25
        )
        assert direct.mode == "direct_operate"

        with pytest.raises(HostCommandError) as unsupported:
            client.direct_operate(
                [CrobCommand(index=0, operation="latch_off")],
                timeout=0.25,
                response_mode="no_response",
            )
        assert unsupported.value.code == "UNSUPPORTED_BY_BACKEND"

        client.disconnect()
        assert client.state_change_authorized is False

    diagnostics = client.diagnostics
    assert "0123456789abcdef0123456789abcdef" not in diagnostics.stdout_tail
    assert "pytest-operator" not in diagnostics.stdout_tail
    assert "simulated-dut" not in diagnostics.stdout_tail


def test_command_helper_rejects_duplicate_points_and_invalid_options(
    tmp_path: Path,
) -> None:
    connection = TcpConnectionConfig(
        host="127.0.0.1",
        safety=LabSafetyConfig(
            operator_id="pytest-operator",
            dut_id="simulated-dut",
            allow_state_change=True,
        ),
    )
    with Dnp3MasterClient(
        fake_config("tcp_api", safety_incident_directory=tmp_path / "incidents")
    ) as client:
        client.connect(connection)
        duplicate = CrobCommand(index=1, operation="latch_on")
        with pytest.raises(ValueError, match="repeat"):
            client.select_and_operate([duplicate, duplicate])
        with pytest.raises(ValueError, match="response_mode"):
            client.direct_operate([duplicate], response_mode="maybe")
        with pytest.raises(ValueError, match="timeout"):
            client.direct_operate([duplicate], timeout=0.01)


def test_uncertain_command_result_creates_cross_process_lock_until_readback_ack(
    tmp_path: Path,
) -> None:
    connection = TcpConnectionConfig(
        host="127.0.0.1",
        safety=LabSafetyConfig(
            operator_id="pytest-operator",
            dut_id="simulated-dut",
            allow_state_change=True,
        ),
    )
    incident_directory = tmp_path / "incidents"
    client = Dnp3MasterClient(
        fake_config("tcp_api", safety_incident_directory=incident_directory)
    )
    client.start()
    client.connect(connection)

    with pytest.raises(HostCommandError) as captured:
        client.direct_operate(
            [CrobCommand(index=65535, operation="latch_on")],
            timeout=0.25,
        )

    assert captured.value.code == "RESPONSE_TIMEOUT"
    assert captured.value.details["execution_uncertain"] is True
    assert captured.value.details["persistent_safety_lock"] is True
    incident_id = captured.value.details["incident_id"]
    assert client.state_change_authorized is False
    assert not client.is_running
    with pytest.raises(ClientStateError, match="closed"):
        client.get_status()
    client.close()

    active_files = tuple((incident_directory / "active").glob("*.json"))
    assert len(active_files) == 1
    persisted = active_files[0].read_text(encoding="utf-8")
    assert "simulated-dut" not in persisted
    assert "latch_on" not in persisted
    assert "0123456789abcdef0123456789abcdef" not in persisted

    with Dnp3MasterClient(
        fake_config("tcp_api", safety_incident_directory=incident_directory)
    ) as fresh_client:
        fresh_client.connect(connection)
        assert fresh_client.integrity_poll(timeout=0.25).task_status == "SUCCESS"
        with pytest.raises(UnresolvedSafetyIncidentError) as blocked:
            fresh_client.direct_operate(
                [CrobCommand(index=1, operation="latch_off")], timeout=0.25
            )
        assert blocked.value.incident_id == incident_id
        assert fresh_client.is_running

        with pytest.raises(SafetyIncidentAcknowledgmentError, match="does not match"):
            fresh_client.acknowledge_safety_incident(
                "INC-00000000-0000-0000-0000-000000000000",
                acknowledged_by="pytest-reviewer",
                readback_summary="Independent binary-output-status read completed",
                readback={"group": 10, "variation": 2, "index": 65535, "value": False},
                evidence_reference="pytest/readback-001",
            )

        acknowledged = fresh_client.acknowledge_safety_incident(
            incident_id,
            acknowledged_by="pytest-reviewer",
            readback_summary="Independent binary-output-status read completed",
            readback={"group": 10, "variation": 2, "index": 65535, "value": False},
            evidence_reference="pytest/readback-001",
        )
        assert acknowledged["status"] == "ACKNOWLEDGED"
        assert fresh_client.active_safety_incident() is None
        assert fresh_client.direct_operate(
            [CrobCommand(index=1, operation="latch_off")], timeout=0.25
        ).all_success

    assert not tuple((incident_directory / "active").glob("*.json"))
    assert (incident_directory / "archive" / f"{incident_id}.json").is_file()


def test_state_change_fails_closed_without_persistent_incident_directory() -> None:
    connection = TcpConnectionConfig(
        host="127.0.0.1",
        safety=LabSafetyConfig(
            operator_id="pytest-operator",
            dut_id="simulated-dut",
            allow_state_change=True,
        ),
    )
    with Dnp3MasterClient(fake_config("tcp_api")) as client:
        client.connect(connection)
        with pytest.raises(SafetyIncidentConfigurationError, match="across Python processes"):
            client.direct_operate(
                [CrobCommand(index=0, operation="latch_on")], timeout=0.25
            )
        assert client.is_running


def test_python_host_timeout_also_creates_persistent_incident(tmp_path: Path) -> None:
    connection = TcpConnectionConfig(
        host="127.0.0.1",
        safety=LabSafetyConfig(
            operator_id="pytest-operator",
            dut_id="timeout-dut",
            allow_state_change=True,
        ),
    )
    incident_directory = tmp_path / "timeout-incidents"
    client = Dnp3MasterClient(
        fake_config(
            "tcp_api",
            safety_incident_directory=incident_directory,
            request_timeout=0.1,
        )
    )
    client.start()
    client.connect(connection)

    with pytest.raises(HostTimeoutError) as captured:
        client.direct_operate(
            [CrobCommand(index=65534, operation="latch_on")],
            timeout=0.25,
            request_timeout=0.1,
        )

    assert captured.value.incident_id is not None
    assert captured.value.details["persistent_safety_lock"] is True
    assert not client.is_running
    assert len(tuple((incident_directory / "active").glob("*.json"))) == 1
    client.close()


@pytest.mark.parametrize(
    ("index", "expected_exception"),
    [
        (65533, HostCommandError),
        (65532, HostProtocolError),
    ],
)
def test_uncertain_or_invalid_success_result_creates_incident(
    tmp_path: Path,
    index: int,
    expected_exception: type[Exception],
) -> None:
    connection = TcpConnectionConfig(
        host="127.0.0.1",
        safety=LabSafetyConfig(
            operator_id="pytest-operator",
            dut_id=f"result-dut-{index}",
            allow_state_change=True,
        ),
    )
    incident_directory = tmp_path / str(index)
    client = Dnp3MasterClient(
        fake_config("tcp_api", safety_incident_directory=incident_directory)
    )
    client.start()
    client.connect(connection)

    with pytest.raises(expected_exception) as captured:
        client.direct_operate(
            [CrobCommand(index=index, operation="latch_on")], timeout=0.25
        )

    assert getattr(captured.value, "incident_id", None) is not None or getattr(
        captured.value, "details", {}
    ).get("incident_id")
    assert not client.is_running
    assert len(tuple((incident_directory / "active").glob("*.json"))) == 1
    client.close()


@pytest.mark.parametrize(
    ("index", "expected_exception"),
    [
        (65531, HostCommandError),
        (65530, HostProtocolError),
        (65529, HostCommandError),
        (65528, HostCommandError),
    ],
)
def test_unsafe_point_status_or_bad_correlation_destroys_session(
    tmp_path: Path,
    index: int,
    expected_exception: type[Exception],
) -> None:
    connection = TcpConnectionConfig(
        host="127.0.0.1",
        safety=LabSafetyConfig(
            operator_id="pytest-operator",
            dut_id=f"point-result-dut-{index}",
            allow_state_change=True,
        ),
    )
    incident_directory = tmp_path / str(index)
    client = Dnp3MasterClient(
        fake_config("tcp_api", safety_incident_directory=incident_directory)
    )
    client.start()
    client.connect(connection)

    with pytest.raises(expected_exception) as captured:
        client.direct_operate(
            [CrobCommand(index=index, operation="latch_on")], timeout=0.25
        )

    assert getattr(captured.value, "incident_id", None) is not None or getattr(
        captured.value, "details", {}
    ).get("incident_id")
    assert not client.is_running
    assert len(tuple((incident_directory / "active").glob("*.json"))) == 1
    client.close()


def test_hello_rejects_mixed_host_version() -> None:
    client = Dnp3MasterClient(fake_config("hello_version_mismatch"))
    with pytest.raises(HostProtocolError, match="version does not match"):
        client.start()
    assert not client.is_running


def test_hello_rejects_mixed_capability_matrix_hash() -> None:
    client = Dnp3MasterClient(
        fake_config(
            "hello_matrix_mismatch",
            expected_capability_matrix_sha256="0" * 64,
        )
    )
    with pytest.raises(HostProtocolError, match="matrix hash"):
        client.start()
    assert not client.is_running


def test_hello_rejects_unpinned_opendnp3_version() -> None:
    client = Dnp3MasterClient(fake_config("hello_backend_mismatch"))
    with pytest.raises(HostProtocolError, match="pinned 3.1.2"):
        client.start()
    assert not client.is_running


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
        ("startup_timeout", float("nan")),
        ("request_timeout", float("inf")),
        ("diagnostic_tail_bytes", 0),
        ("diagnostic_tail_bytes", True),
        ("max_response_bytes", 63),
        ("max_response_bytes", True),
        ("expected_host_version", ""),
        ("expected_capability_matrix_sha256", "not-a-sha256"),
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
