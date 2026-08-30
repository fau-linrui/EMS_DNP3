from __future__ import annotations

import pytest

from dnp3_master import Dnp3MasterClient, PointTable, PollScenario

from ._scenario_helpers import assert_read_task_success


pytestmark = [
    pytest.mark.dnp3_dut,
    pytest.mark.dnp3_capability("APP.FC.01.READ"),
]


def test_configured_integrity_or_class_poll(
    connected_master: Dnp3MasterClient,
    dnp3_point_table: PointTable | None,
    ems_poll_scenario: PollScenario | None,
) -> None:
    """Run one bounded, read-only integrity or Class poll from the EMS plan."""

    if ems_poll_scenario is None or dnp3_point_table is None:
        pytest.skip("no enabled EMS poll scenario was configured")
    scenario = ems_poll_scenario
    if scenario.poll_type == "integrity":
        result = connected_master.integrity_poll(
            timeout=scenario.timeout_seconds,
            max_measurements=scenario.max_measurements,
            request_timeout=scenario.timeout_seconds + 1.0,
        )
    else:
        result = connected_master.class_poll(
            scenario.classes,
            timeout=scenario.timeout_seconds,
            max_measurements=scenario.max_measurements,
            request_timeout=scenario.timeout_seconds + 1.0,
        )
    assert_read_task_success(result, scenario.scenario_id)
    assert len(result.measurements) >= scenario.minimum_measurements, (
        f"{scenario.scenario_id}: received {len(result.measurements)} measurements; "
        f"minimum is {scenario.minimum_measurements}"
    )
    assert all(
        measurement.source == "solicited"
        for measurement in result.measurements
    ), f"{scenario.scenario_id}: poll returned a non-solicited measurement"

    for point_id in scenario.expected_point_ids:
        point = dnp3_point_table.by_id[point_id]
        if scenario.poll_type == "class":
            matches = point.matching_event_measurements(result.measurements)
        else:
            matches = (
                *point.matching_measurements(result.measurements),
                *point.matching_event_measurements(result.measurements),
            )
        assert matches, (
            f"{scenario.scenario_id}: expected point {point_id!r} was absent"
        )
        assert all(point.value_in_expected_range(item.value) for item in matches), (
            f"{scenario.scenario_id}: point {point_id!r} was outside its "
            "configured engineering range"
        )
