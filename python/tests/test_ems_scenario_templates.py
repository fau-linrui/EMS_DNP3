from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT))

from dnp3_master import (
    CommandPointResult,
    CommandTaskResult,
    MeasurementRecord,
    ReadTaskResult,
    UnsolicitedBatchResult,
    UnsolicitedControlResult,
    load_ems_test_plan,
    load_point_table,
)
from examples.pytest_ems.test_control_scenarios import (
    _ATTEMPTED_CONTROL_SCENARIOS,
    test_approved_control_with_feedback_and_restore as run_control_scenario,
)
from examples.pytest_ems.test_poll_scenarios import (
    test_configured_integrity_or_class_poll as run_poll_scenario,
)
from examples.pytest_ems.test_unsolicited_scenarios import (
    test_configured_unsolicited_change as run_unsolicited_scenario,
)
def measurement(
    *,
    kind: str,
    group: int,
    variation: int,
    value: object,
    is_event: bool,
    source: str,
    session_id: int | None = None,
) -> MeasurementRecord:
    return MeasurementRecord(
        receive_seq=1,
        received_monotonic_ns=1,
        kind=kind,
        group=group,
        variation=variation,
        qualifier="index16",
        qualifier_raw=0x28,
        index=0,
        value=value,
        flags_raw=1,
        flags_valid=True,
        dnp3_timestamp_ms=(1700000000000 if is_event else None),
        timestamp_quality=("synchronized" if is_event else "none"),
        is_event=is_event,
        header_index=0,
        source=source,
        fragment_index=0,
        session_id=session_id,
        raw={},
    )


def read_result(item: MeasurementRecord) -> ReadTaskResult:
    return ReadTaskResult(
        task_id=1,
        task_status="SUCCESS",
        task_started=True,
        task_destroyed=True,
        return_mode="detail",
        measurements=(item,),
        summary={"received_total": 1},
        fragments=(),
        iin={"bits": [], "raw_hex": "0000"},
        timings={},
        raw={},
    )


def command_result(index: int) -> CommandTaskResult:
    point = CommandPointResult(
        header_index=0,
        index=index,
        state="SUCCESS",
        state_raw=1,
        status="SUCCESS",
        status_raw=0,
        requested={},
        raw={},
    )
    return CommandTaskResult(
        task_id=1,
        mode="direct_operate",
        task_status="SUCCESS",
        task_started=True,
        task_destroyed=True,
        all_success=True,
        execution_uncertain=False,
        point_results=(point,),
        summary={},
        timings={},
        raw={},
    )


@pytest.fixture
def example_inputs():
    _ATTEMPTED_CONTROL_SCENARIOS.clear()
    points = load_point_table(REPOSITORY_ROOT / "config" / "points.example.csv")
    plan = load_ems_test_plan(
        REPOSITORY_ROOT / "config" / "ems_test_plan.example.json", points
    )
    return points, plan


def test_poll_template_checks_configured_point(example_inputs: object) -> None:
    points, plan = example_inputs
    scenario = replace(
        plan.poll_scenarios[0],
        minimum_measurements=1,
        expected_point_ids=("BI_DEMO_0001",),
    )

    class PollClient:
        def integrity_poll(self, **kwargs: object) -> ReadTaskResult:
            assert kwargs["max_measurements"] == scenario.max_measurements
            return read_result(
                measurement(
                    kind="binary_input",
                    group=1,
                    variation=2,
                    value=True,
                    is_event=False,
                    source="solicited",
                )
            )

    run_poll_scenario(PollClient(), points, scenario)


def test_unsolicited_template_always_disables_on_queue_loss(
    example_inputs: object,
) -> None:
    points, plan = example_inputs
    scenario = plan.unsolicited_scenarios[0]

    class UnsolicitedClient:
        disabled = False

        def enable_unsolicited(
            self, classes: tuple[int, ...], **kwargs: object
        ) -> UnsolicitedControlResult:
            return UnsolicitedControlResult(
                1, "SUCCESS", True, True, "enable", classes, {}, {}
            )

        def wait_unsolicited(self, **kwargs: object) -> UnsolicitedBatchResult:
            return UnsolicitedBatchResult(
                session_id=1,
                enabled=True,
                classes=scenario.classes,
                measurements=(),
                timed_out=False,
                summary={"dropped_total": 1},
                raw={},
            )

        def disable_unsolicited(
            self, classes: tuple[int, ...], **kwargs: object
        ) -> UnsolicitedControlResult:
            self.disabled = True
            return UnsolicitedControlResult(
                2, "SUCCESS", True, True, "disable", classes, {}, {}
            )

    client = UnsolicitedClient()
    with pytest.raises(AssertionError, match="dropped events"):
        run_unsolicited_scenario(client, points, scenario)
    assert client.disabled is True


def test_control_template_operates_once_and_restores_once(
    example_inputs: object,
) -> None:
    points, plan = example_inputs
    scenario = plan.control_scenarios[0]

    class ControlClient:
        values = iter((False, True, False))
        commands: list[str] = []

        def read(self, headers: object, **kwargs: object) -> ReadTaskResult:
            return read_result(
                measurement(
                    kind="binary_output_status",
                    group=10,
                    variation=2,
                    value=next(self.values),
                    is_event=False,
                    source="solicited",
                )
            )

        def direct_operate(
            self, commands: object, **kwargs: object
        ) -> CommandTaskResult:
            command = commands[0]
            self.commands.append(command.operation)
            return command_result(command.index)

    client = ControlClient()
    run_control_scenario(client, points, scenario)
    assert client.commands == ["latch_on", "latch_off"]


def test_control_template_does_not_restore_unknown_post_state(
    example_inputs: object,
) -> None:
    points, plan = example_inputs
    scenario = replace(
        plan.control_scenarios[0],
        feedback_timeout_seconds=0.1,
        feedback_poll_interval_seconds=0.05,
    )

    class ControlClient:
        commands: list[str] = []

        def read(self, headers: object, **kwargs: object) -> ReadTaskResult:
            return read_result(
                measurement(
                    kind="binary_output_status",
                    group=10,
                    variation=2,
                    value=False,
                    is_event=False,
                    source="solicited",
                )
            )

        def direct_operate(
            self, commands: object, **kwargs: object
        ) -> CommandTaskResult:
            command = commands[0]
            self.commands.append(command.operation)
            return command_result(command.index)

    client = ControlClient()
    with pytest.raises(AssertionError, match="manual readback"):
        run_control_scenario(client, points, scenario)
    assert client.commands == ["latch_on"]


def test_control_template_rejects_same_process_rerun(
    example_inputs: object,
) -> None:
    points, plan = example_inputs
    scenario = plan.control_scenarios[0]

    class BaselineOnlyClient:
        def read(self, headers: object, **kwargs: object) -> ReadTaskResult:
            return read_result(
                measurement(
                    kind="binary_output_status",
                    group=10,
                    variation=2,
                    value=True,
                    is_event=False,
                    source="solicited",
                )
            )

    _ATTEMPTED_CONTROL_SCENARIOS.add(scenario.scenario_id)
    with pytest.raises(AssertionError, match="refusing a repeated control attempt"):
        run_control_scenario(BaselineOnlyClient(), points, scenario)
