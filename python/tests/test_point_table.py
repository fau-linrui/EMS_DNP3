from __future__ import annotations

import csv
from pathlib import Path

import pytest

from dnp3_master import (
    POINT_TABLE_COLUMNS,
    PointTableError,
    load_point_table,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def base_row(**overrides: str) -> dict[str, str]:
    row = {
        "point_id": "AI_TEST_0001",
        "point_name": "测试遥测",
        "point_type": "analog_input",
        "index": "7",
        "static_group": "30",
        "static_variation": "5",
        "event_group": "32",
        "event_variation": "7",
        "event_class": "2",
        "read_qualifier": "range16",
        "engineering_unit": "kV",
        "expected_min": "0",
        "expected_max": "500",
        "enabled": "true",
        "notes": "test row",
    }
    row.update(overrides)
    return row


def write_table(
    path: Path,
    rows: list[dict[str, str]],
    columns: tuple[str, ...] = POINT_TABLE_COLUMNS,
) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def test_example_point_table_loads_and_generates_exact_headers() -> None:
    table = load_point_table(REPOSITORY_ROOT / "config" / "points.example.csv")

    assert table.schema_version == 1
    assert len(table.points) == 5
    assert len(table.enabled_points) == 4
    assert table.by_id["AI_DEMO_0001"].point_name == "示例遥测"
    analog = table.by_id["AI_DEMO_0001"]
    assert analog.capability_id == "OBJ.G30.V5"
    assert analog.event_capability_id == "OBJ.G32.V7"
    assert analog.read_qualifier_capability_id == "QUAL.Q01.REVIEW"
    assert analog.read_header().to_params() == {
        "group": 30,
        "variation": 5,
        "qualifier": "range16",
        "start": 0,
        "stop": 0,
    }
    assert table.to_mapping()["points"][0]["event_class"] == 1
    assert table.by_id["BO_DEMO_0001"].event_capability_id is None


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"point_id": "1bad"}, "point_id"),
        ({"point_type": "unknown"}, "point_type"),
        ({"static_group": "31"}, "does not match"),
        ({"static_variation": "7"}, "static_variation 7 is not defined"),
        ({"event_variation": "9"}, "event_variation 9 is not defined"),
        ({"event_variation": ""}, "must be all blank"),
        ({"read_qualifier": "range8", "index": "256"}, "range8"),
        ({"expected_min": "nan"}, "finite decimal"),
        ({"expected_min": "501", "expected_max": "500"}, "must not exceed"),
        ({"enabled": "yes"}, "exactly 'true' or 'false'"),
    ],
)
def test_invalid_point_rows_fail_closed(
    tmp_path: Path, overrides: dict[str, str], message: str
) -> None:
    path = tmp_path / "points.csv"
    write_table(path, [base_row(**overrides)])

    with pytest.raises(PointTableError, match=message):
        load_point_table(path)


def test_duplicate_ids_are_case_insensitive(tmp_path: Path) -> None:
    path = tmp_path / "points.csv"
    write_table(
        path,
        [
            base_row(point_id="AI_DUPLICATE", index="1"),
            base_row(point_id="ai_duplicate", index="2"),
        ],
    )

    with pytest.raises(PointTableError, match="duplicate point_id"):
        load_point_table(path)


def test_duplicate_type_and_index_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "points.csv"
    write_table(
        path,
        [
            base_row(point_id="AI_ONE"),
            base_row(point_id="AI_TWO"),
        ],
    )

    with pytest.raises(PointTableError, match="duplicate analog_input index 7"):
        load_point_table(path)


def test_csv_header_is_exact_and_versioned_by_the_loader(tmp_path: Path) -> None:
    path = tmp_path / "points.csv"
    write_table(path, [base_row()], POINT_TABLE_COLUMNS + ("unexpected",))

    with pytest.raises(PointTableError, match="CSV header must exactly equal"):
        load_point_table(path)


def test_empty_and_missing_tables_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "points.csv"
    write_table(path, [])
    with pytest.raises(PointTableError, match="no data rows"):
        load_point_table(path)
    with pytest.raises(PointTableError, match="does not exist"):
        load_point_table(tmp_path / "missing.csv")


def test_expected_range_rejects_non_finite_runtime_values(tmp_path: Path) -> None:
    path = tmp_path / "points.csv"
    write_table(path, [base_row()])
    point = load_point_table(path).points[0]

    assert not point.value_in_expected_range(float("nan"))
    assert not point.value_in_expected_range(float("inf"))
