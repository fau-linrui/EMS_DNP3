from __future__ import annotations

import pytest


pytest_plugins = ("dnp3_master.pytest_plugin",)


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("ems-point-table")
    group.addoption(
        "--ems-point-read-timeout",
        action="store",
        type=float,
        default=5.0,
        help="Seconds allowed for each read-only point request",
    )


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "ems_point" not in metafunc.fixturenames:
        return
    table = getattr(metafunc.config, "_dnp3_point_table", None)
    if table is None:
        metafunc.parametrize(
            "ems_point",
            [pytest.param(None, marks=pytest.mark.skip(reason="no EMS point table configured"))],
            ids=["no-point-table"],
        )
        return
    if not table.enabled_points:
        metafunc.parametrize(
            "ems_point",
            [pytest.param(None, marks=pytest.mark.skip(reason="point table has no enabled rows"))],
            ids=["no-enabled-points"],
        )
        return
    metafunc.parametrize(
        "ems_point",
        [
            pytest.param(
                point,
                marks=pytest.mark.dnp3_capability(point.capability_id),
                id=point.point_id,
            )
            for point in table.enabled_points
        ],
    )
