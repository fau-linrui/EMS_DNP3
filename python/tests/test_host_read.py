from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

import pytest

from dnp3_master import (
    AnalogOutputCommand,
    CrobCommand,
    Dnp3MasterClient,
    HostCommandError,
    HostProcessConfig,
    LabSafetyConfig,
    ReadHeader,
    TcpConnectionConfig,
)
from dnp3_master.local_outstation import LocalTestOutstation


TC_APP_FC01_PYTHON_E2E_LOCAL_001 = "TC_APP_FC01_PYTHON_E2E_LOCAL_001"
TC_APP_TASK_RESULT_PYTHON_LOCAL_001 = "TC_APP_TASK_RESULT_PYTHON_LOCAL_001"
TC_APP_IIN_PYTHON_LOCAL_001 = "TC_APP_IIN_PYTHON_LOCAL_001"
TC_APP_SUMMARY_PYTHON_LOCAL_001 = "TC_APP_SUMMARY_PYTHON_LOCAL_001"
TC_APP_CLASS_POLL_PYTHON_LOCAL_001 = "TC_APP_CLASS_POLL_PYTHON_LOCAL_001"
TC_APP_CONTROL_PYTHON_LOCAL_001 = "TC_APP_CONTROL_PYTHON_LOCAL_001"
TC_APP_CONTROL_SAFETY_PYTHON_LOCAL_001 = "TC_APP_CONTROL_SAFETY_PYTHON_LOCAL_001"
TC_APP_UNSOLICITED_PYTHON_LOCAL_001 = "TC_APP_UNSOLICITED_PYTHON_LOCAL_001"

pytestmark = [
    pytest.mark.dnp3_capability("APP.FC.01.READ"),
    pytest.mark.dnp3_capability("APP.TASK.LIFECYCLE"),
    pytest.mark.dnp3_capability("APP.TASK.OBSERVABILITY"),
    pytest.mark.dnp3_capability("APP.CLASS.EVENTS"),
    pytest.mark.dnp3_capability("APP.UNSOLICITED"),
    pytest.mark.dnp3_capability("APP.FC.14.ENABLE_UNSOLICITED"),
    pytest.mark.dnp3_capability("APP.FC.15.DISABLE_UNSOLICITED"),
    pytest.mark.dnp3_capability("APP.FC.82.UNSOLICITED_RESPONSE"),
    pytest.mark.dnp3_capability("APP.COMMAND_STATUS.CATALOG"),
    pytest.mark.dnp3_capability("IIN.IIN2.1.OBJECT_UNKNOWN"),
    pytest.mark.dnp3_capability("APP.FC.03.SELECT"),
    pytest.mark.dnp3_capability("APP.FC.04.OPERATE"),
    pytest.mark.dnp3_capability("APP.FC.05.DIRECT_OPERATE"),
    pytest.mark.dnp3_capability("APP.FC.06.DIRECT_OPERATE_NR"),
    pytest.mark.dnp3_capability("OBJ.G1.V2"),
    pytest.mark.dnp3_capability("OBJ.G3.V2"),
    pytest.mark.dnp3_capability("OBJ.G10.V2"),
    pytest.mark.dnp3_capability("OBJ.G12.V1"),
    pytest.mark.dnp3_capability("OBJ.G20.V1"),
    pytest.mark.dnp3_capability("OBJ.G21.V1"),
    pytest.mark.dnp3_capability("OBJ.G30.V5"),
    pytest.mark.dnp3_capability("OBJ.G40.V1"),
    pytest.mark.dnp3_capability("OBJ.G41.V1"),
    pytest.mark.dnp3_capability("OBJ.G41.V2"),
    pytest.mark.dnp3_capability("OBJ.G41.V3"),
    pytest.mark.dnp3_capability("OBJ.G41.V4"),
    pytest.mark.dnp3_capability("OBJ.G50.V4"),
    pytest.mark.dnp3_capability("OBJ.G110.LENGTH_VARIANTS"),
]


@pytest.fixture
def local_outstation() -> Iterator[LocalTestOutstation]:
    configured = os.environ.get("DNP3_TEST_OUTSTATION_EXE")
    assert configured, "DNP3_TEST_OUTSTATION_EXE must identify the built test helper"
    executable = Path(configured)
    assert executable.is_file(), f"local test outstation does not exist: {executable}"
    with LocalTestOutstation(executable, startup_timeout=3.0) as controller:
        yield controller


@pytest.fixture
def real_client(tmp_path: Path) -> Iterator[Dnp3MasterClient]:
    configured = os.environ.get("DNP3_MASTER_HOST_EXE")
    assert configured, "DNP3_MASTER_HOST_EXE must identify the built native host"
    client = Dnp3MasterClient(
        HostProcessConfig(
            executable=Path(configured),
            safety_incident_directory=tmp_path / "safety-incidents",
            startup_timeout=3.0,
            request_timeout=5.0,
            shutdown_timeout=3.0,
        )
    )
    client.start()
    try:
        yield client
    finally:
        diagnostics = client.close()
        assert diagnostics.cleanup_error is None, diagnostics.cleanup_error


def test_python_read_api_against_local_opendnp3_outstation(
    real_client: Dnp3MasterClient,
    local_outstation: LocalTestOutstation,
) -> None:
    assert TC_APP_FC01_PYTHON_E2E_LOCAL_001
    assert TC_APP_TASK_RESULT_PYTHON_LOCAL_001
    assert TC_APP_IIN_PYTHON_LOCAL_001
    assert TC_APP_SUMMARY_PYTHON_LOCAL_001
    assert TC_APP_CLASS_POLL_PYTHON_LOCAL_001
    assert TC_APP_CONTROL_PYTHON_LOCAL_001
    assert TC_APP_CONTROL_SAFETY_PYTHON_LOCAL_001
    assert TC_APP_UNSOLICITED_PYTHON_LOCAL_001

    real_client.connect(
        TcpConnectionConfig(
            host="127.0.0.1",
            port=local_outstation.port,
            connect_timeout=3.0,
            retry_min=0.05,
            retry_max=0.2,
            master_address=1,
            outstation_address=1024,
            safety=LabSafetyConfig(
                operator_id="local-test-operator",
                dut_id="local-opendnp3-outstation",
                allow_state_change=True,
            ),
        )
    )
    assert real_client.state_change_authorized is True

    enabled_unsolicited = real_client.enable_unsolicited((1, 2), timeout=3.0)
    assert enabled_unsolicited.task_status == "SUCCESS"
    local_outstation.update_binary_input(
        True, timestamp_ms=1700000000101, event_mode="force"
    )
    local_outstation.update_analog_input(
        456.25, timestamp_ms=1700000000102, event_mode="force"
    )
    unsolicited = real_client.wait_unsolicited(
        wait_timeout=3.0, max_events=16
    )
    assert unsolicited.enabled is True
    assert unsolicited.classes == (1, 2)
    assert unsolicited.measurements
    assert {
        (measurement.group, measurement.variation)
        for measurement in unsolicited.measurements
    }.issuperset({(2, 2), (32, 7)})
    assert all(
        measurement.source == "unsolicited"
        and measurement.session_id == unsolicited.session_id
        for measurement in unsolicited.measurements
    )
    disabled_unsolicited = real_client.disable_unsolicited((1, 2), timeout=3.0)
    assert disabled_unsolicited.task_status == "SUCCESS"

    integrity = real_client.integrity_poll(timeout=3.0)
    assert integrity.task_status == "SUCCESS"
    assert integrity.task_started is True
    assert integrity.summary["received_total"] == len(integrity.measurements)
    sequences = [measurement.receive_seq for measurement in integrity.measurements]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))
    kinds = {measurement.kind for measurement in integrity.measurements}
    assert {
        "binary_input",
        "double_bit_binary_input",
        "analog_input",
        "counter",
        "frozen_counter",
        "binary_output_status",
        "analog_output_status",
        "octet_string",
        "time_and_interval",
    }.issubset(kinds)

    analog = real_client.read([ReadHeader.range16(30, 0, 0, 0)], timeout=3.0)
    assert len(analog.measurements) == 1
    assert analog.measurements[0].kind == "analog_input"
    assert analog.measurements[0].index == 0
    assert analog.measurements[0].value == 456.25

    summary = real_client.read(
        [ReadHeader.all_objects(1), ReadHeader.all_objects(20)],
        timeout=3.0,
        return_mode="summary",
    )
    assert summary.measurements == ()
    assert summary.summary["received_total"] >= 4

    events = real_client.class_poll((1, 2, 3), timeout=3.0, return_mode="summary")
    assert events.task_status == "SUCCESS"

    unknown = real_client.read([ReadHeader.all_objects(199, 1)], timeout=3.0)
    assert "IIN2.1.OBJECT_UNKNOWN" in unknown.iin["bits"]
    assert unknown.iin["raw_hex"] != "0000"

    with pytest.raises(HostCommandError) as overflow:
        real_client.integrity_poll(timeout=3.0, max_measurements=1)
    assert overflow.value.code == "QUEUE_OVERFLOW"
    assert overflow.value.details["overflow"] > 0

    sbo = real_client.select_and_operate(
        [
            CrobCommand(index=0, operation="latch_on"),
            AnalogOutputCommand.int16(0, -123),
            AnalogOutputCommand.int32(0, 123456),
            AnalogOutputCommand.float32(0, 12.5),
            AnalogOutputCommand.double64(0, -9876.125),
        ],
        timeout=3.0,
    )
    assert sbo.task_status == "SUCCESS"
    assert sbo.all_success is True
    assert len(sbo.point_results) == 5
    assert {point.status for point in sbo.point_results} == {"SUCCESS"}
    assert {point.requested["type"] for point in sbo.point_results} == {
        "crob",
        "analog_output_int16",
        "analog_output_int32",
        "analog_output_float32",
        "analog_output_double64",
    }
    binary_operated = real_client.read(
        [ReadHeader.range16(10, 2, 0, 0)], timeout=3.0
    )
    analog_operated = real_client.read(
        [ReadHeader.range16(40, 3, 0, 0)], timeout=3.0
    )
    assert binary_operated.measurements[0].value is True
    assert analog_operated.measurements[0].value == -9876.125

    direct = real_client.direct_operate(
        [CrobCommand(index=0, operation="latch_off")], timeout=3.0
    )
    assert direct.mode == "direct_operate"
    assert direct.all_success is True
    binary_restored = real_client.read(
        [ReadHeader.range16(10, 2, 0, 0)], timeout=3.0
    )
    assert binary_restored.measurements[0].value is False
    snapshot = local_outstation.snapshot()
    assert snapshot["operation_count"] == 6
    assert snapshot["crob_operation_count"] == 2
    assert snapshot["analog_operation_count"] == 4
    assert snapshot["select_before_operate_count"] == 5
    assert snapshot["direct_operate_count"] == 1
    assert snapshot["direct_operate_no_ack_count"] == 0

    with pytest.raises(HostCommandError) as no_response:
        real_client.direct_operate(
            [CrobCommand(index=0, operation="latch_on")],
            timeout=3.0,
            response_mode="no_response",
        )
    assert no_response.value.code == "UNSUPPORTED_BY_BACKEND"

    real_client.disconnect()
    assert real_client.state_change_authorized is False
