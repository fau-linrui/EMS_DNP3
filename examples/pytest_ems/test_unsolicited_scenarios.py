from __future__ import annotations

import time

import pytest

from dnp3_master import Dnp3MasterClient, PointTable, UnsolicitedScenario


pytestmark = [
    pytest.mark.dnp3_dut,
    pytest.mark.dnp3_capability("APP.UNSOLICITED"),
]


def test_configured_unsolicited_change(
    connected_master: Dnp3MasterClient,
    dnp3_point_table: PointTable | None,
    ems_unsolicited_scenario: UnsolicitedScenario | None,
) -> None:
    """Observe one externally generated event; this test sends no field control."""

    if ems_unsolicited_scenario is None or dnp3_point_table is None:
        pytest.skip("no enabled EMS unsolicited scenario was configured")
    scenario = ems_unsolicited_scenario
    point = dnp3_point_table.by_id[scenario.expected_point_id]
    enable_completed = False
    match = None
    try:
        enabled = connected_master.enable_unsolicited(
            scenario.classes,
            timeout=scenario.task_timeout_seconds,
            request_timeout=scenario.task_timeout_seconds + 1.0,
        )
        enable_completed = True
        assert enabled.task_status == "SUCCESS"
        print(
            f"EMS unsolicited scenario {scenario.scenario_id}: perform the "
            "approved trigger_instructions from the private plan now"
        )
        deadline = time.monotonic() + scenario.observation_timeout_seconds
        while match is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            batch = connected_master.wait_unsolicited(
                wait_timeout=min(60.0, remaining),
                max_events=scenario.max_events_per_read,
                request_timeout=min(60.0, remaining) + 1.0,
            )
            assert batch.enabled is True
            assert batch.classes == scenario.classes
            assert batch.summary.get("dropped_total") == 0, (
                f"{scenario.scenario_id}: unsolicited queue dropped events"
            )
            assert all(
                measurement.source == "unsolicited"
                and measurement.session_id == batch.session_id
                for measurement in batch.measurements
            )
            matches = scenario.matching_measurements(point, batch.measurements)
            if matches:
                match = matches[-1]
    finally:
        if enable_completed:
            disabled = connected_master.disable_unsolicited(
                scenario.classes,
                timeout=scenario.task_timeout_seconds,
                request_timeout=scenario.task_timeout_seconds + 1.0,
            )
            assert disabled.task_status == "SUCCESS"
    assert match is not None, (
        f"{scenario.scenario_id}: no matching unsolicited event arrived for "
        f"point {point.point_id!r} within "
        f"{scenario.observation_timeout_seconds:g} seconds"
    )
