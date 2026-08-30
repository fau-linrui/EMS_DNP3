from __future__ import annotations

from collections.abc import Mapping
import time

from dnp3_master import (
    CommandTaskResult,
    Dnp3MasterClient,
    MeasurementRecord,
    PointDefinition,
    ValueExpectation,
)


def assert_read_task_success(result: object, scenario_id: str) -> None:
    task_status = getattr(result, "task_status", None)
    task_started = getattr(result, "task_started", None)
    assert task_status == "SUCCESS", (
        f"{scenario_id}: DNP3 read task status is {task_status!r}"
    )
    assert task_started is True, f"{scenario_id}: DNP3 read task did not start"
    iin = getattr(result, "iin", None)
    assert isinstance(iin, Mapping), (
        f"{scenario_id}: read result has no validated IIN"
    )
    bits = iin.get("bits")
    assert isinstance(bits, (list, tuple)) and all(
        isinstance(bit, str) for bit in bits
    ), f"{scenario_id}: read result IIN bits are malformed"
    request_errors = tuple(
        bit
        for bit in bits
        if bit.startswith(("IIN2.0.", "IIN2.1.", "IIN2.2."))
    )
    assert not request_errors, (
        f"{scenario_id}: DUT rejected or could not interpret the READ request; "
        f"IIN request-error bits={request_errors!r}"
    )
    dropped = iin.get("observation_window_dropped", 0)
    assert type(dropped) is int and dropped == 0, (
        f"{scenario_id}: IIN observations were lost in this task window; "
        f"dropped={dropped!r}"
    )


def read_exact_static_point(
    client: Dnp3MasterClient,
    point: PointDefinition,
    *,
    timeout_seconds: float,
) -> MeasurementRecord:
    result = client.read(
        [point.read_header()],
        timeout=timeout_seconds,
        max_measurements=16,
        request_timeout=timeout_seconds + 1.0,
    )
    assert_read_task_success(result, point.point_id)
    matches = point.matching_measurements(result.measurements)
    assert len(matches) == 1, (
        f"{point.point_id}: expected exactly one solicited static "
        f"G{point.static_group}V{point.static_variation} measurement at index "
        f"{point.index}; received {len(matches)}"
    )
    measurement = matches[0]
    assert measurement.source == "solicited", (
        f"{point.point_id}: feedback source is {measurement.source!r}"
    )
    return measurement


def wait_for_static_value(
    client: Dnp3MasterClient,
    point: PointDefinition,
    expectation: ValueExpectation,
    *,
    timeout_seconds: float,
    poll_interval_seconds: float,
    phase: str,
) -> MeasurementRecord:
    deadline = time.monotonic() + timeout_seconds
    last_measurement: MeasurementRecord | None = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        last_measurement = read_exact_static_point(
            client,
            point,
            timeout_seconds=min(5.0, max(0.1, remaining)),
        )
        if expectation.matches(last_measurement.value):
            return last_measurement
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_interval_seconds, remaining))
    observed = None if last_measurement is None else last_measurement.value
    raise AssertionError(
        f"{point.point_id}: {phase} feedback did not reach the approved value; "
        f"last observed={observed!r}. Stop all further controls and perform "
        "manual readback; no control was retried."
    )


def assert_single_command_success(
    result: CommandTaskResult,
    *,
    scenario_id: str,
    expected_index: int,
    phase: str,
) -> None:
    assert result.execution_uncertain is False, (
        f"{scenario_id}: {phase} result is uncertain; stop and follow the "
        "persistent safety-incident runbook"
    )
    assert result.task_status == "SUCCESS", (
        f"{scenario_id}: {phase} task status is {result.task_status!r}"
    )
    assert result.all_success is True, (
        f"{scenario_id}: {phase} returned a non-success command status"
    )
    assert len(result.point_results) == 1, (
        f"{scenario_id}: {phase} expected one point result; received "
        f"{len(result.point_results)}"
    )
    point_result = result.point_results[0]
    assert point_result.index == expected_index
    assert point_result.status == "SUCCESS", (
        f"{scenario_id}: {phase} point status is {point_result.status!r}"
    )
