from __future__ import annotations

import pytest

from dnp3_master import Dnp3MasterClient, PointDefinition


pytestmark = [
    pytest.mark.dnp3_dut,
    pytest.mark.dnp3_capability("APP.FC.01.READ"),
]


def test_configured_static_point_read(
    connected_master: Dnp3MasterClient,
    ems_point: PointDefinition | None,
    pytestconfig: pytest.Config,
) -> None:
    """Read exactly one configured point; this test never sends a control."""

    if ems_point is None:
        pytest.skip("no enabled EMS point was configured")
    result = connected_master.read(
        [ems_point.read_header()],
        timeout=pytestconfig.getoption("--ems-point-read-timeout"),
        max_measurements=16,
    )
    matches = ems_point.matching_measurements(result.measurements)
    assert len(matches) == 1, (
        f"{ems_point.point_id} expected exactly one "
        f"G{ems_point.static_group}V{ems_point.static_variation} "
        f"{ems_point.point_type} at index {ems_point.index}; "
        f"received {len(matches)} exact matches"
    )
    measurement = matches[0]
    assert measurement.source == "solicited"
    assert ems_point.value_in_expected_range(measurement.value), (
        f"{ems_point.point_id} value {measurement.value!r} is outside "
        f"[{ems_point.expected_min}, {ems_point.expected_max}] "
        f"{ems_point.engineering_unit or ''}".rstrip()
    )
