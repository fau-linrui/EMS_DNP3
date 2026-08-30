from __future__ import annotations

import pytest

from dnp3_master import ControlScenario, Dnp3MasterClient, PointTable

from ._scenario_helpers import (
    assert_single_command_success,
    read_exact_static_point,
    wait_for_static_value,
)


pytestmark = [
    pytest.mark.dnp3_dut,
    pytest.mark.dnp3_capability("APP.COMMAND_STATUS.CATALOG"),
]

_ATTEMPTED_CONTROL_SCENARIOS: set[str] = set()


def _execute_once(
    client: Dnp3MasterClient,
    scenario: ControlScenario,
    *,
    restore: bool,
):
    command_definition = (
        scenario.restore_command if restore else scenario.command
    )
    command = command_definition.to_command()
    if scenario.control_mode == "select_and_operate":
        return client.select_and_operate(
            [command],
            timeout=scenario.command_timeout_seconds,
            request_timeout=scenario.command_timeout_seconds + 1.0,
        )
    return client.direct_operate(
        [command],
        timeout=scenario.command_timeout_seconds,
        request_timeout=scenario.command_timeout_seconds + 1.0,
    )


def test_approved_control_with_feedback_and_restore(
    connected_master: Dnp3MasterClient,
    dnp3_point_table: PointTable | None,
    ems_control_scenario: ControlScenario | None,
) -> None:
    """Execute one explicitly selected command cycle without any control retry."""

    if ems_control_scenario is None or dnp3_point_table is None:
        pytest.skip("no approved EMS control scenario was selected")
    scenario = ems_control_scenario
    assert scenario.scenario_id not in _ATTEMPTED_CONTROL_SCENARIOS, (
        f"{scenario.scenario_id}: refusing a repeated control attempt in the same "
        "pytest process; automatic rerun/repeat plugins are unsafe for controls"
    )
    _ATTEMPTED_CONTROL_SCENARIOS.add(scenario.scenario_id)
    feedback_point = dnp3_point_table.by_id[scenario.feedback_point_id]

    baseline = read_exact_static_point(
        connected_master,
        feedback_point,
        timeout_seconds=min(5.0, scenario.feedback_timeout_seconds),
    )
    assert scenario.precondition.matches(baseline.value), (
        f"{scenario.scenario_id}: precondition is not satisfied at feedback "
        f"point {feedback_point.point_id!r}; observed={baseline.value!r}. "
        "No control was sent."
    )

    operated = _execute_once(connected_master, scenario, restore=False)
    assert_single_command_success(
        operated,
        scenario_id=scenario.scenario_id,
        expected_index=scenario.command.index,
        phase="operate",
    )
    wait_for_static_value(
        connected_master,
        feedback_point,
        scenario.postcondition,
        timeout_seconds=scenario.feedback_timeout_seconds,
        poll_interval_seconds=scenario.feedback_poll_interval_seconds,
        phase="post-control",
    )

    restored = _execute_once(connected_master, scenario, restore=True)
    assert_single_command_success(
        restored,
        scenario_id=scenario.scenario_id,
        expected_index=scenario.restore_command.index,
        phase="restore",
    )
    wait_for_static_value(
        connected_master,
        feedback_point,
        scenario.restore_expectation,
        timeout_seconds=scenario.feedback_timeout_seconds,
        poll_interval_seconds=scenario.feedback_poll_interval_seconds,
        phase="restore",
    )
