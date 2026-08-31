from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dnp3_master import write_json_report


def test_report_writer_is_atomic_bounded_and_hashed(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "report.json"
    persisted = write_json_report(
        {"schema_version": 1, "report_type": "TEST_REPORT", "passed": True},
        target,
    )
    encoded = target.read_bytes()
    assert persisted.path == target.resolve()
    assert persisted.size_bytes == len(encoded)
    assert persisted.sha256 == hashlib.sha256(encoded).hexdigest()
    assert json.loads(encoded) == {
        "schema_version": 1,
        "report_type": "TEST_REPORT",
        "passed": True,
    }
    assert list(target.parent.glob("*.tmp")) == []


def test_report_writer_refuses_silent_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "report.json"
    report = {"schema_version": 1, "report_type": "TEST_REPORT"}
    write_json_report(report, target)
    with pytest.raises(FileExistsError):
        write_json_report(report, target)


@pytest.mark.parametrize(
    "report",
    [
        {"schema_version": 2, "report_type": "TEST_REPORT"},
        {"schema_version": 1, "report_type": ""},
        {
            "schema_version": 1,
            "report_type": "TEST_REPORT",
            "value": float("nan"),
        },
    ],
)
def test_report_writer_rejects_invalid_identity_or_non_finite_json(
    tmp_path: Path, report: dict[str, object]
) -> None:
    with pytest.raises(ValueError):
        write_json_report(report, tmp_path / "report.json")
