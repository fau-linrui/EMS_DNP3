from __future__ import annotations

import csv
from pathlib import Path

from scripts.validate_capabilities import EXPECTED_HEADER, validate_matrix


def _valid_row(**overrides: str) -> dict[str, str]:
    row = {
        "capability_id": "APP.FC.01.READ",
        "edition": "IEEE1815-2012",
        "layer": "APPLICATION",
        "feature": "Read",
        "direction": "M2O",
        "subset_level": "REVIEW_REQUIRED",
        "function_code": "1",
        "object_group": "",
        "variations": "",
        "qualifiers": "",
        "std_reference": "REVIEW_REQUIRED",
        "dut_pics_status": "UNKNOWN",
        "backend_status": "NOT_ANALYZED",
        "framework_status": "BLOCKED",
        "test_case_ids": "",
        "evidence": "",
        "owner": "PROTOCOL_OWNER",
        "notes": "formal standard required",
    }
    row.update(overrides)
    return row


def _write_matrix(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPECTED_HEADER, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _issue_codes(path: Path, *, tests_root: Path | None = None) -> set[str]:
    roots = (tests_root,) if tests_root else ()
    result = validate_matrix(path, test_roots=roots, require_baseline=False)
    return {issue.code for issue in result.issues}


def test_valid_minimal_matrix_passes(tmp_path: Path) -> None:
    matrix = tmp_path / "capability_matrix.csv"
    _write_matrix(matrix, [_valid_row()])

    result = validate_matrix(matrix, require_baseline=False)

    assert result.ok
    assert result.row_count == 1


def test_duplicate_capability_id_is_rejected(tmp_path: Path) -> None:
    matrix = tmp_path / "capability_matrix.csv"
    _write_matrix(matrix, [_valid_row(), _valid_row()])

    assert "DUPLICATE_CAPABILITY_ID" in _issue_codes(matrix)


def test_invalid_status_is_rejected(tmp_path: Path) -> None:
    matrix = tmp_path / "capability_matrix.csv"
    _write_matrix(matrix, [_valid_row(framework_status="DONE")])

    assert "INVALID_FRAMEWORK_STATUS" in _issue_codes(matrix)


def test_verified_without_test_or_evidence_is_rejected(tmp_path: Path) -> None:
    matrix = tmp_path / "capability_matrix.csv"
    _write_matrix(
        matrix,
        [
            _valid_row(
                std_reference="IEEE 1815-2012 Vol X clause Y",
                framework_status="VERIFIED_UNIT",
            )
        ],
    )

    codes = _issue_codes(matrix)
    assert "VERIFIED_WITHOUT_TEST" in codes
    assert "VERIFIED_WITHOUT_EVIDENCE" in codes


def test_unresolved_reference_cannot_claim_implementation(tmp_path: Path) -> None:
    matrix = tmp_path / "capability_matrix.csv"
    _write_matrix(
        matrix,
        [_valid_row(framework_status="IMPLEMENTED_UNVERIFIED")],
    )

    assert "UNRESOLVED_STANDARD_REFERENCE" in _issue_codes(matrix)


def test_verified_row_requires_real_test_and_evidence_files(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence" / "manifest.json"
    evidence.parent.mkdir()
    evidence.write_text("{}\n", encoding="utf-8")
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    (tests_root / "test_read.py").write_text(
        'TEST_ID = "TC_APP_FC01_READ_001"\n'
        '@pytest.mark.dnp3_capability("APP.FC.01.READ")\n'
        "def test_read():\n    pass\n",
        encoding="utf-8",
    )
    matrix = tmp_path / "capability_matrix.csv"
    _write_matrix(
        matrix,
        [
            _valid_row(
                std_reference="IEEE 1815-2012 Vol X clause Y",
                backend_status="VERIFIED_UNIT",
                framework_status="VERIFIED_UNIT",
                test_case_ids="TC_APP_FC01_READ_001",
                evidence="evidence/manifest.json",
            )
        ],
    )

    result = validate_matrix(
        matrix,
        project_root=tmp_path,
        test_roots=(tests_root,),
        require_baseline=False,
    )

    assert result.ok, [issue.render() for issue in result.issues]


def test_unknown_pytest_capability_marker_is_rejected(tmp_path: Path) -> None:
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    (tests_root / "test_unknown.py").write_text(
        '@pytest.mark.dnp3_capability("OBJ.G999.V1")\n'
        "def test_unknown():\n    pass\n",
        encoding="utf-8",
    )
    matrix = tmp_path / "capability_matrix.csv"
    _write_matrix(matrix, [_valid_row()])

    assert "UNKNOWN_MARKED_CAPABILITY" in _issue_codes(matrix, tests_root=tests_root)


def test_native_test_id_is_discovered(tmp_path: Path) -> None:
    tests_root = tmp_path / "native" / "tests"
    tests_root.mkdir(parents=True)
    (tests_root / "read_tests.cpp").write_text(
        'constexpr auto id = "TC_APP_FC01_READ_NATIVE_001";\n',
        encoding="utf-8",
    )
    matrix = tmp_path / "capability_matrix.csv"
    _write_matrix(
        matrix,
        [
            _valid_row(
                std_reference="IEEE 1815-2012 4.4.2",
                framework_status="IMPLEMENTED_UNVERIFIED",
                test_case_ids="TC_APP_FC01_READ_NATIVE_001",
            )
        ],
    )

    result = validate_matrix(
        matrix,
        project_root=tmp_path,
        test_roots=(tests_root,),
        require_baseline=False,
    )

    assert result.ok, [issue.render() for issue in result.issues]


def test_missing_baseline_is_rejected_when_gate_is_enabled(tmp_path: Path) -> None:
    matrix = tmp_path / "capability_matrix.csv"
    _write_matrix(matrix, [_valid_row()])

    result = validate_matrix(matrix, require_baseline=True)

    assert not result.ok
    assert any(issue.code.startswith("MISSING_BASELINE_") for issue in result.issues)
