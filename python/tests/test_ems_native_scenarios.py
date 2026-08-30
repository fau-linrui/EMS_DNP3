from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import sys
import threading
import time
from typing import Iterator

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT))

from dnp3_master import (
    Dnp3MasterClient,
    HostProcessConfig,
    LabSafetyConfig,
    TcpConnectionConfig,
    load_ems_test_plan,
    load_point_table,
)
from dnp3_master.local_outstation import (
    LocalOutstationRequestError,
    LocalTestOutstation,
)
from examples.pytest_ems.test_control_scenarios import (
    _ATTEMPTED_CONTROL_SCENARIOS,
    test_approved_control_with_feedback_and_restore as run_control_scenario,
)
from examples.pytest_ems.test_poll_scenarios import (
    test_configured_integrity_or_class_poll as run_poll_scenario,
)
from examples.pytest_ems.test_read_points import (
    test_configured_static_point_read as run_point_read,
)
from examples.pytest_ems.test_unsolicited_scenarios import (
    test_configured_unsolicited_change as run_unsolicited_scenario,
)


TC_EMS_NATIVE_STATIC_AND_POLL_LOCAL_001 = "TC_EMS_NATIVE_STATIC_AND_POLL_LOCAL_001"
TC_EMS_NATIVE_UNSOLICITED_LOCAL_001 = "TC_EMS_NATIVE_UNSOLICITED_LOCAL_001"
TC_EMS_NATIVE_CONTROL_CYCLE_LOCAL_001 = "TC_EMS_NATIVE_CONTROL_CYCLE_LOCAL_001"
TC_LOCAL_OUTSTATION_CONTROL_PROTOCOL_001 = "TC_LOCAL_OUTSTATION_CONTROL_PROTOCOL_001"


@pytest.fixture
def native_scenario_stack(
    tmp_path: Path,
) -> Iterator[tuple[Dnp3MasterClient, LocalTestOutstation]]:
    host_path = os.environ.get("DNP3_MASTER_HOST_EXE")
    outstation_path = os.environ.get("DNP3_TEST_OUTSTATION_EXE")
    assert host_path and outstation_path
    outstation = LocalTestOutstation(Path(outstation_path), startup_timeout=3.0)
    client = Dnp3MasterClient(
        HostProcessConfig(
            executable=Path(host_path),
            safety_incident_directory=tmp_path / "safety-incidents",
            startup_timeout=3.0,
            request_timeout=5.0,
            shutdown_timeout=3.0,
        )
    )
    outstation.start()
    client.start()
    client.connect(
        TcpConnectionConfig(
            host="127.0.0.1",
            port=outstation.port,
            connect_timeout=3.0,
            retry_min=0.05,
            retry_max=0.2,
            master_address=1,
            outstation_address=1024,
            safety=LabSafetyConfig(
                operator_id="local-native-scenario-test",
                dut_id="bundled-loopback-outstation",
                allow_state_change=True,
            ),
        )
    )
    try:
        yield client, outstation
    finally:
        if client.is_running:
            try:
                status = client.get_status(timeout=1.0)
                if status.get("state") != "READY":
                    client.disconnect(timeout=2.0)
            except Exception:
                pass
        diagnostics = client.close()
        outstation.close()
        assert diagnostics.cleanup_error is None, diagnostics.cleanup_error


@pytest.fixture
def example_scenario_inputs():
    points = load_point_table(REPOSITORY_ROOT / "config" / "points.example.csv")
    plan = load_ems_test_plan(
        REPOSITORY_ROOT / "config" / "ems_test_plan.example.json", points
    )
    return points, plan


class _PointReadConfig:
    @staticmethod
    def getoption(name: str) -> float:
        assert name == "--ems-point-read-timeout"
        return 3.0


def test_static_and_poll_templates_against_native_loopback(
    native_scenario_stack: tuple[Dnp3MasterClient, LocalTestOutstation],
    example_scenario_inputs: object,
) -> None:
    assert TC_EMS_NATIVE_STATIC_AND_POLL_LOCAL_001
    client, outstation = native_scenario_stack
    points, plan = example_scenario_inputs

    for point in points.enabled_points:
        run_point_read(client, point, _PointReadConfig())

    integrity = replace(
        plan.poll_scenarios[0],
        minimum_measurements=4,
        expected_point_ids=(
            "BI_DEMO_0001",
            "AI_DEMO_0001",
            "BO_DEMO_0001",
            "AO_DEMO_0001",
        ),
    )
    run_poll_scenario(client, points, integrity)

    outstation.update_binary_input(
        True, timestamp_ms=1700000000201, event_mode="force"
    )
    outstation.update_analog_input(
        220.0, timestamp_ms=1700000000202, event_mode="force"
    )
    class_one = replace(
        plan.poll_scenarios[1],
        minimum_measurements=1,
        expected_point_ids=("BI_DEMO_0001",),
    )
    class_two = replace(
        plan.poll_scenarios[2],
        minimum_measurements=1,
        expected_point_ids=("AI_DEMO_0001",),
    )
    run_poll_scenario(client, points, class_one)
    run_poll_scenario(client, points, class_two)


def test_unsolicited_templates_against_native_loopback(
    native_scenario_stack: tuple[Dnp3MasterClient, LocalTestOutstation],
    example_scenario_inputs: object,
) -> None:
    assert TC_EMS_NATIVE_UNSOLICITED_LOCAL_001
    client, outstation = native_scenario_stack
    points, plan = example_scenario_inputs

    scenarios = (
        (
            replace(
                plan.unsolicited_scenarios[0],
                enabled=True,
                observation_timeout_seconds=3.0,
            ),
            lambda: outstation.update_binary_input(
                True, timestamp_ms=1700000000301, event_mode="force"
            ),
        ),
        (
            replace(
                plan.unsolicited_scenarios[1],
                enabled=True,
                observation_timeout_seconds=3.0,
            ),
            lambda: outstation.update_analog_input(
                220.0, timestamp_ms=1700000000302, event_mode="force"
            ),
        ),
    )
    for scenario, trigger in scenarios:
        trigger_errors: list[BaseException] = []

        def delayed_trigger() -> None:
            try:
                time.sleep(0.2)
                trigger()
            except BaseException as error:
                trigger_errors.append(error)

        worker = threading.Thread(
            target=delayed_trigger,
            name=f"dnp3-local-trigger-{scenario.scenario_id}",
            daemon=True,
        )
        worker.start()
        run_unsolicited_scenario(client, points, scenario)
        worker.join(timeout=2.0)
        assert not worker.is_alive(), "local unsolicited trigger did not terminate"
        assert not trigger_errors, trigger_errors


def test_control_templates_against_stateful_native_loopback(
    native_scenario_stack: tuple[Dnp3MasterClient, LocalTestOutstation],
    example_scenario_inputs: object,
) -> None:
    assert TC_EMS_NATIVE_CONTROL_CYCLE_LOCAL_001
    client, outstation = native_scenario_stack
    points, plan = example_scenario_inputs
    _ATTEMPTED_CONTROL_SCENARIOS.clear()

    binary = replace(
        plan.control_scenarios[0],
        enabled=True,
        authorization_reference="LOCAL_LOOPBACK_TEST_ONLY",
        command_timeout_seconds=3.0,
        feedback_timeout_seconds=3.0,
        feedback_poll_interval_seconds=0.05,
    )
    analog = replace(
        plan.control_scenarios[1],
        enabled=True,
        authorization_reference="LOCAL_LOOPBACK_TEST_ONLY",
        command_timeout_seconds=3.0,
        feedback_timeout_seconds=3.0,
        feedback_poll_interval_seconds=0.05,
    )
    run_control_scenario(client, points, binary)
    run_control_scenario(client, points, analog)

    snapshot = outstation.snapshot()
    assert snapshot["operation_count"] == 4
    assert snapshot["crob_operation_count"] == 2
    assert snapshot["analog_operation_count"] == 2
    assert snapshot["direct_operate_count"] == 2
    assert snapshot["select_before_operate_count"] == 2
    assert snapshot["direct_operate_no_ack_count"] == 0
    assert snapshot["binary_output_status"][0] is False
    assert snapshot["analog_output_status"][0] == 0.0


def test_local_outstation_control_protocol_rejects_ambiguous_updates(
    native_scenario_stack: tuple[Dnp3MasterClient, LocalTestOutstation],
) -> None:
    assert TC_LOCAL_OUTSTATION_CONTROL_PROTOCOL_001
    _, outstation = native_scenario_stack
    with pytest.raises(LocalOutstationRequestError) as missing_timestamp:
        outstation.request(
            "update",
            {
                "type": "binary_input",
                "index": 0,
                "value": True,
                "event_mode": "force",
            },
        )
    assert missing_timestamp.value.code == "INVALID_REQUEST"
    assert missing_timestamp.value.details["reason"] == "invalid_timestamp_presence"

    with pytest.raises(LocalOutstationRequestError) as unknown_field:
        outstation.request(
            "update",
            {
                "type": "binary_output_status",
                "index": 0,
                "value": True,
                "event_mode": "suppress",
                "unexpected": "rejected",
            },
        )
    assert unknown_field.value.code == "INVALID_REQUEST"
    assert unknown_field.value.details["reason"] == "unknown_field"
