from __future__ import annotations

import pytest

from dnp3_master import EmsTestPlan, PointTable


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
    table: PointTable | None = getattr(
        metafunc.config, "_dnp3_point_table", None
    )
    plan: EmsTestPlan | None = getattr(
        metafunc.config, "_dnp3_ems_test_plan", None
    )

    if "ems_point" in metafunc.fixturenames:
        if table is None:
            metafunc.parametrize(
                "ems_point",
                [
                    pytest.param(
                        None,
                        marks=pytest.mark.skip(reason="no EMS point table configured"),
                    )
                ],
                ids=["no-point-table"],
            )
        elif not table.enabled_points:
            metafunc.parametrize(
                "ems_point",
                [
                    pytest.param(
                        None,
                        marks=pytest.mark.skip(
                            reason="point table has no enabled rows"
                        ),
                    )
                ],
                ids=["no-enabled-points"],
            )
        else:
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

    if "ems_poll_scenario" in metafunc.fixturenames:
        scenarios = () if plan is None else plan.enabled_poll_scenarios
        metafunc.parametrize(
            "ems_poll_scenario",
            (
                [
                    pytest.param(
                        scenario,
                        marks=[
                            pytest.mark.dnp3_capability(capability_id)
                            for capability_id in scenario.capability_ids
                        ],
                        id=scenario.scenario_id,
                    )
                    for scenario in scenarios
                ]
                or [
                    pytest.param(
                        None,
                        marks=pytest.mark.skip(
                            reason="no enabled EMS poll scenario configured"
                        ),
                        id="no-poll-scenario",
                    )
                ]
            ),
        )

    if "ems_unsolicited_scenario" in metafunc.fixturenames:
        scenarios = () if plan is None else plan.enabled_unsolicited_scenarios
        parameters = []
        if table is not None:
            for scenario in scenarios:
                point = table.by_id[scenario.expected_point_id]
                parameters.append(
                    pytest.param(
                        scenario,
                        marks=[
                            pytest.mark.dnp3_capability(capability_id)
                            for capability_id in scenario.capability_ids(point)
                        ],
                        id=scenario.scenario_id,
                    )
                )
        metafunc.parametrize(
            "ems_unsolicited_scenario",
            parameters
            or [
                pytest.param(
                    None,
                    marks=pytest.mark.skip(
                        reason="no enabled EMS unsolicited scenario configured"
                    ),
                    id="no-unsolicited-scenario",
                )
            ],
        )

    if "ems_control_scenario" in metafunc.fixturenames:
        selected = getattr(
            metafunc.config, "_dnp3_selected_control_scenario", None
        )
        if selected is None or table is None:
            parameters = [
                pytest.param(
                    None,
                    marks=pytest.mark.skip(
                        reason=(
                            "no control selected; pass the exact approved ID with "
                            "--dnp3-control-scenario"
                        )
                    ),
                    id="no-control-selected",
                )
            ]
        else:
            feedback_point = table.by_id[selected.feedback_point_id]
            parameters = [
                pytest.param(
                    selected,
                    marks=[
                        pytest.mark.dnp3_state_changing,
                        *(
                            pytest.mark.dnp3_capability(capability_id)
                            for capability_id in selected.capability_ids(
                                feedback_point
                            )
                        ),
                    ],
                    id=selected.scenario_id,
                )
            ]
        metafunc.parametrize("ems_control_scenario", parameters)
