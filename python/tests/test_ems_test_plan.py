from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from dnp3_master import (
    AnalogOutputCommand,
    CrobCommand,
    EmsTestPlanError,
    ValueExpectation,
    load_ems_test_plan,
    load_point_table,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def example_document() -> dict[str, object]:
    return json.loads(
        (REPOSITORY_ROOT / "config" / "ems_test_plan.example.json").read_text(
            encoding="utf-8"
        )
    )


def write_plan(path: Path, document: object) -> Path:
    path.write_text(
        json.dumps(document, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def load_document(tmp_path: Path, document: object):
    return load_ems_test_plan(
        write_plan(tmp_path / "plan.json", document),
        load_point_table(REPOSITORY_ROOT / "config" / "points.example.csv"),
    )


def test_example_plan_loads_and_controls_remain_disabled() -> None:
    points = load_point_table(REPOSITORY_ROOT / "config" / "points.example.csv")
    plan = load_ems_test_plan(
        REPOSITORY_ROOT / "config" / "ems_test_plan.example.json", points
    )

    assert plan.schema_version == 1
    assert len(plan.enabled_poll_scenarios) == 4
    assert plan.enabled_unsolicited_scenarios == ()
    assert plan.enabled_control_scenarios == ()
    assert plan.poll_scenarios[0].capability_ids == (
        "APP.FC.01.READ",
        "APP.CLASS.EVENTS",
        "QUAL.Q06.REVIEW",
        "OBJ.G60.V1",
        "OBJ.G60.V2",
        "OBJ.G60.V3",
        "OBJ.G60.V4",
    )
    binary_event = plan.unsolicited_scenarios[0]
    assert binary_event.capability_ids(points.by_id["BI_DEMO_0001"])[-1] == (
        "OBJ.G2.V2"
    )
    assert "QUAL.Q06.REVIEW" in binary_event.capability_ids(
        points.by_id["BI_DEMO_0001"]
    )
    assert (
        plan.control_by_id["binary-output-latch-cycle"]
        .command.qualifier_capability_id
        == "QUAL.Q17.REVIEW"
    )
    assert plan.control_by_id["binary-output-latch-cycle"].command.to_command() == (
        CrobCommand(
            index=0,
            operation="latch_on",
            count=1,
            on_time_ms=0,
            off_time_ms=0,
        )
    )
    analog = plan.control_by_id["analog-output-float32-cycle"]
    assert analog.command.to_command() == AnalogOutputCommand.float32(0, 1.0)
    assert plan.to_mapping()["schema_version"] == 1


def test_value_expectation_is_type_strict_and_tolerant() -> None:
    assert ValueExpectation(True).matches(True)
    assert not ValueExpectation(True).matches(1)
    assert ValueExpectation(10.0, 0.1).matches(10.09)
    assert not ValueExpectation(10.0, 0.1).matches(float("nan"))
    assert ValueExpectation("INTERMEDIATE").matches("INTERMEDIATE")
    assert ValueExpectation(10.0, 0.2).overlaps(ValueExpectation(10.3, 0.1))
    assert not ValueExpectation(False).overlaps(ValueExpectation(True))
    with pytest.raises(ValueError, match="zero"):
        ValueExpectation(True, 1.0)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda value: value["poll_scenarios"][0].update(
                {"unexpected": True}
            ),
            "unknown fields",
        ),
        (
            lambda value: value["control_scenarios"][0].update(
                {"enabled": True}
            ),
            "real approved authorization",
        ),
        (
            lambda value: value["control_scenarios"][0].update(
                {
                    "restore_expectation": {
                        "value": True,
                        "absolute_tolerance": 0.0,
                    }
                }
            ),
            "must exactly equal precondition",
        ),
        (
            lambda value: value["control_scenarios"][1].update(
                {
                    "postcondition": {
                        "value": 0.005,
                        "absolute_tolerance": 0.01,
                    }
                }
            ),
            "must not overlap precondition",
        ),
        (
            lambda value: value["control_scenarios"][0]["command"].update(
                {"operation": "null"}
            ),
            "'null' is not allowed",
        ),
        (
            lambda value: value["unsolicited_scenarios"][0].update(
                {"classes": [2]}
            ),
            "must include expected point event_class 1",
        ),
        (
            lambda value: value["unsolicited_scenarios"][0].update(
                {"expected_point_id": "MISSING_POINT"}
            ),
            "unknown point_id",
        ),
        (
            lambda value: value["poll_scenarios"][1].update(
                {"scenario_id": "integrity-baseline"}
            ),
            "globally unique",
        ),
    ],
)
def test_plan_semantic_errors_fail_closed(
    tmp_path: Path, mutate: object, message: str
) -> None:
    document = deepcopy(example_document())
    mutate(document)

    with pytest.raises(EmsTestPlanError, match=message):
        load_document(tmp_path, document)


def test_duplicate_json_keys_and_nonstandard_numbers_are_rejected(
    tmp_path: Path,
) -> None:
    points = load_point_table(REPOSITORY_ROOT / "config" / "points.example.csv")
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        '{"schema_version":1,"schema_version":1,'
        '"poll_scenarios":[],"unsolicited_scenarios":[],'
        '"control_scenarios":[]}',
        encoding="utf-8",
    )
    with pytest.raises(EmsTestPlanError, match="duplicate JSON key"):
        load_ems_test_plan(duplicate, points)

    nonstandard = tmp_path / "nan.json"
    nonstandard.write_text(
        '{"schema_version":1,"poll_scenarios":[],'
        '"unsolicited_scenarios":[],"control_scenarios":[],"notes":NaN}',
        encoding="utf-8",
    )
    with pytest.raises(EmsTestPlanError, match="non-standard JSON number"):
        load_ems_test_plan(nonstandard, points)


def test_analog_command_range_is_validated_by_public_model(tmp_path: Path) -> None:
    document = deepcopy(example_document())
    document["control_scenarios"][1]["command"] = {
        "type": "analog_output_int16",
        "index": 0,
        "value": 40000,
    }
    document["control_scenarios"][1]["restore_command"] = {
        "type": "analog_output_int16",
        "index": 0,
        "value": 0,
    }

    with pytest.raises(EmsTestPlanError, match="int16"):
        load_document(tmp_path, document)
