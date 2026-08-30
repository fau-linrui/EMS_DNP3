#!/usr/bin/env python3
"""Validate the IEEE 1815-2012 capability matrix.

The validator intentionally uses only the Python standard library so it can run
in an offline build environment.  It validates claims and traceability; it does
not infer protocol facts that require the licensed standard or fixed backend
source.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence


EXPECTED_HEADER = (
    "capability_id",
    "edition",
    "layer",
    "feature",
    "direction",
    "subset_level",
    "function_code",
    "object_group",
    "variations",
    "qualifiers",
    "std_reference",
    "dut_pics_status",
    "backend_status",
    "framework_status",
    "test_case_ids",
    "evidence",
    "owner",
    "notes",
)

VALID_EDITIONS = frozenset({"IEEE1815-2012"})
VALID_LAYERS = frozenset(
    {"CHANNEL", "LINK", "TRANSPORT", "APPLICATION", "SECURITY", "ROBUSTNESS"}
)
VALID_DIRECTIONS = frozenset({"M2O", "O2M", "BIDIRECTIONAL"})
VALID_PICS_STATUSES = frozenset({"SUPPORTED", "NOT_SUPPORTED", "UNKNOWN"})
VALID_IMPLEMENTATION_STATUSES = frozenset(
    {
        "NOT_ANALYZED",
        "UNSUPPORTED_BY_BACKEND",
        "PLANNED",
        "IMPLEMENTED_UNVERIFIED",
        "VERIFIED_UNIT",
        "VERIFIED_INTEROP",
        "VERIFIED_CONFORMANCE",
        "NOT_APPLICABLE_BY_PICS",
        "BLOCKED",
    }
)
VERIFIED_STATUSES = frozenset(
    {"VERIFIED_UNIT", "VERIFIED_INTEROP", "VERIFIED_CONFORMANCE"}
)
UNRESOLVED_REFERENCES = frozenset({"REVIEW_REQUIRED", "BLOCKED"})

CAPABILITY_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:[._-][A-Z0-9]+)*$")
TEST_CASE_ID_RE = re.compile(r"^TC_[A-Z0-9]+(?:_[A-Z0-9]+)*$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
MARKER_RE = re.compile(
    r"dnp3_capability\s*\(\s*[\"'](?P<id>[A-Za-z0-9_.-]+)[\"']\s*\)"
)
TEST_LITERAL_RE = re.compile(r"[\"'](?P<id>TC_[A-Z0-9]+(?:_[A-Z0-9]+)*)[\"']")
TRACEABILITY_SOURCE_SUFFIXES = frozenset({".py", ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp"})


# Exact catalog entries that must exist even before licensed-standard review.
# These are coverage controls, not claims that the capability is implemented.
REQUIRED_EXACT_IDS = frozenset(
    {
        "CHANNEL.SERIAL",
        "CHANNEL.TCP.CLIENT",
        "CHANNEL.TCP.SERVER",
        "CHANNEL.UDP",
        "CHANNEL.TLS",
        "CHANNEL.RECONNECT",
        "LINK.FRAMING",
        "LINK.CRC",
        "LINK.CONTROL",
        "LINK.CONFIRMED_USER_DATA",
        "LINK.BROADCAST",
        "LINK.SELF_ADDRESS",
        "LINK.RECOVERY",
        "TRANSPORT.HEADER",
        "TRANSPORT.REASSEMBLY",
        "TRANSPORT.SEQUENCE",
        "TRANSPORT.RESOURCE_LIMIT",
        "APP.CONTROL",
        "APP.FRAGMENTATION",
        "APP.CONFIRMATION",
        "APP.SEQUENCING",
        "APP.UNSOLICITED",
        "APP.TASK.LIFECYCLE",
        "APP.TASK.OBSERVABILITY",
        "APP.CLASS.EVENTS",
        "APP.ASSIGN_CLASS",
        "APP.TIME",
        "APP.COMMAND_STATUS.CATALOG",
        "APP.FILE.TRANSACTION",
        "APP.DATASET.TRANSACTION",
        "APP.VIRTUAL_TERMINAL.TRANSACTION",
        "APP.CONFIG.TRANSACTION",
        "ROBUSTNESS.FAULT_PLAN",
        "SEC.SAV5.STATE_MACHINE",
        "SEC.SAV5.KEY_LIFECYCLE",
    }
    | {
        f"APP.FC.{code:02X}.{name}"
        for code, name in (
            (0x00, "CONFIRM"),
            (0x01, "READ"),
            (0x02, "WRITE"),
            (0x03, "SELECT"),
            (0x04, "OPERATE"),
            (0x05, "DIRECT_OPERATE"),
            (0x06, "DIRECT_OPERATE_NR"),
            (0x07, "IMMED_FREEZE"),
            (0x08, "IMMED_FREEZE_NR"),
            (0x09, "FREEZE_CLEAR"),
            (0x0A, "FREEZE_CLEAR_NR"),
            (0x0B, "FREEZE_AT_TIME"),
            (0x0C, "FREEZE_AT_TIME_NR"),
            (0x0D, "COLD_RESTART"),
            (0x0E, "WARM_RESTART"),
            (0x0F, "INITIALIZE_DATA"),
            (0x10, "INITIALIZE_APPL"),
            (0x11, "START_APPL"),
            (0x12, "STOP_APPL"),
            (0x13, "SAVE_CONFIG"),
            (0x14, "ENABLE_UNSOLICITED"),
            (0x15, "DISABLE_UNSOLICITED"),
            (0x16, "ASSIGN_CLASS"),
            (0x17, "DELAY_MEASURE"),
            (0x18, "RECORD_CURRENT_TIME"),
            (0x19, "OPEN_FILE"),
            (0x1A, "CLOSE_FILE"),
            (0x1B, "DELETE_FILE"),
            (0x1C, "GET_FILE_INFO"),
            (0x1D, "AUTHENTICATE_FILE"),
            (0x1E, "ABORT_FILE"),
            (0x1F, "ACTIVATE_CONFIG"),
            (0x20, "AUTHENTICATE_REQ"),
            (0x21, "AUTH_REQ_NO_ACK"),
            (0x81, "RESPONSE"),
            (0x82, "UNSOLICITED_RESPONSE"),
            (0x83, "AUTHENTICATE_RESP"),
        )
    }
    | {
        f"QUAL.Q{code}.REVIEW"
        for code in (
            "00", "01", "02", "03", "04", "05", "06", "07", "08", "09",
            "17", "18", "19", "27", "28", "29", "37", "38", "39", "4B",
            "5B", "6B",
        )
    }
    | {
        "IIN.IIN2.0.NO_FUNC_CODE_SUPPORT",
        "IIN.IIN2.2.PARAMETER_ERROR",
        "COMMAND.STATUS.SUCCESS",
        "COMMAND.STATUS.TIMEOUT",
        "COMMAND.STATUS.NO_SELECT",
        "COMMAND.STATUS.FORMAT_ERROR",
        "COMMAND.STATUS.NOT_SUPPORTED",
        "COMMAND.STATUS.ALREADY_ACTIVE",
        "COMMAND.STATUS.HARDWARE_ERROR",
        "COMMAND.STATUS.LOCAL",
        "COMMAND.STATUS.TOO_MANY_OBJS",
        "COMMAND.STATUS.NOT_AUTHORIZED",
        "COMMAND.STATUS.AUTOMATION_INHIBIT",
        "COMMAND.STATUS.PROCESSING_LIMITED",
        "COMMAND.STATUS.OUT_OF_RANGE",
        "COMMAND.STATUS.RESERVED_13_125",
        "COMMAND.STATUS.NON_PARTICIPATING",
        "COMMAND.STATUS.UNDEFINED",
        "APP.COMMAND_STATUS.RESERVED_WIRE_RAW",
        "OBJ.G20.V3",
        "OBJ.G20.V4",
        "OBJ.G20.V7",
        "OBJ.G20.V8",
        "OBJ.G21.V3",
        "OBJ.G21.V4",
        "OBJ.G21.V7",
        "OBJ.G21.V8",
        "OBJ.G21.V11",
        "OBJ.G21.V12",
        "OBJ.G22.V3",
        "OBJ.G22.V4",
        "OBJ.G22.V7",
        "OBJ.G22.V8",
        "OBJ.G23.V3",
        "OBJ.G23.V4",
        "OBJ.G23.V7",
        "OBJ.G23.V8",
        "OBJ.G70.V0",
    }
)

REQUIRED_DIRECTIONS = {
    "APP.FC.00.CONFIRM": "M2O",
    "APP.FC.20.AUTHENTICATE_REQ": "M2O",
    "APP.FC.21.AUTH_REQ_NO_ACK": "M2O",
    "APP.FC.83.AUTHENTICATE_RESP": "O2M",
}

FORBIDDEN_LEGACY_IDS = frozenset(
    {
        "IIN.IIN2.0.FUNC_NOT_SUPPORTED",
        "IIN.IIN2.2.PARAM_ERROR",
        "COMMAND.STATUS.LOCAL_CONTROL",
        "COMMAND.STATUS.TOO_MANY_OPERATIONS",
        "COMMAND.STATUS.INHIBITED",
        "COMMAND.STATUS.DOWNSTREAM",
    }
)

REQUIRED_PREFIXES = tuple(
    [f"OBJ.G{group}." for group in (0, 1, 2, 3, 4, 10, 11, 12, 13, 20, 21, 22, 23, 30, 31, 32, 33, 34, 40, 41, 42, 43, 50, 51, 52, 60, 70, 80, 81, 82, 83, 85, 86, 87, 88, 90, 91, 101, 102, 110, 111, 112, 113, 120, 121, 122)]
    + [
        "QUAL.",
        "IIN.",
        "QUALITY.BINARY.",
        "QUALITY.DOUBLE_BIT.",
        "QUALITY.BINARY_OUTPUT_STATUS.",
        "QUALITY.COUNTER.",
        "QUALITY.ANALOG.",
        "QUALITY.ANALOG_OUTPUT_STATUS.",
        "TIME.",
        "COMMAND.STATUS.",
        "SEC.G120.V1.",
        "SEC.G120.V15.",
        "SEC.G121.",
        "SEC.G122.",
    ]
)


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    row: int | None = None
    capability_id: str | None = None

    def render(self) -> str:
        location = "matrix"
        if self.row is not None:
            location += f":{self.row}"
        if self.capability_id:
            location += f" [{self.capability_id}]"
        return f"{location}: {self.code}: {self.message}"


@dataclass(frozen=True)
class ValidationResult:
    matrix: Path
    row_count: int
    issues: tuple[ValidationIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues


def _split_multi(value: str) -> list[str]:
    return [item.strip() for item in value.split("|") if item.strip()]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_matrix(path: Path) -> tuple[list[dict[str, str]], list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    try:
        handle = path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        return [], [ValidationIssue("MATRIX_READ_ERROR", str(exc))]

    with handle:
        reader = csv.DictReader(handle, strict=True)
        actual = tuple(reader.fieldnames or ())
        if actual != EXPECTED_HEADER:
            issues.append(
                ValidationIssue(
                    "INVALID_HEADER",
                    f"expected {EXPECTED_HEADER!r}, got {actual!r}",
                    row=1,
                )
            )
            return [], issues

        try:
            rows = [dict(row) for row in reader]
        except csv.Error as exc:
            issues.append(ValidationIssue("INVALID_CSV", str(exc), row=reader.line_num))
            return [], issues

    return rows, issues


def _discover_test_traceability(test_roots: Iterable[Path]) -> tuple[set[str], set[str]]:
    test_ids: set[str] = set()
    capability_markers: set[str] = set()
    for root in test_roots:
        if not root.exists():
            continue
        candidates = (
            [root]
            if root.is_file()
            else (
                path
                for path in root.rglob("*")
                if path.is_file() and path.suffix.lower() in TRACEABILITY_SOURCE_SUFFIXES
            )
        )
        for source_path in candidates:
            try:
                text = source_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            test_ids.update(match.group("id") for match in TEST_LITERAL_RE.finditer(text))
            capability_markers.update(match.group("id") for match in MARKER_RE.finditer(text))
    return test_ids, capability_markers


def _validate_evidence_reference(
    reference: str,
    *,
    project_root: Path,
    row_number: int,
    capability_id: str,
    require_digest: bool,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    path_text, separator, digest = reference.partition("#sha256=")
    if not path_text or "\\" in path_text:
        return [
            ValidationIssue(
                "UNSAFE_EVIDENCE_PATH",
                "evidence must use a non-empty project-relative POSIX path",
                row_number,
                capability_id,
            )
        ]
    pure_path = PurePosixPath(path_text)
    if pure_path.is_absolute() or ".." in pure_path.parts:
        return [
            ValidationIssue(
                "UNSAFE_EVIDENCE_PATH",
                f"evidence must be a project-relative path: {path_text!r}",
                row_number,
                capability_id,
            )
        ]

    evidence_path = project_root.joinpath(*pure_path.parts)
    evidence_exists = evidence_path.is_file()
    if not evidence_exists:
        issues.append(
            ValidationIssue(
                "MISSING_EVIDENCE_FILE",
                f"evidence file does not exist: {path_text}",
                row_number,
                capability_id,
            )
        )
    if require_digest and not separator:
        issues.append(
            ValidationIssue(
                "MISSING_EVIDENCE_SHA256",
                "VERIFIED_* evidence must append #sha256=<64 hexadecimal characters>",
                row_number,
                capability_id,
            )
        )
    if separator and not SHA256_RE.fullmatch(digest):
        issues.append(
            ValidationIssue(
                "INVALID_EVIDENCE_SHA256",
                "evidence digest must contain exactly 64 hexadecimal characters",
                row_number,
                capability_id,
            )
        )
    elif separator and evidence_exists:
        try:
            actual = _sha256_file(evidence_path)
        except OSError as error:
            issues.append(
                ValidationIssue(
                    "EVIDENCE_READ_ERROR",
                    f"cannot hash evidence file {path_text}: {error}",
                    row_number,
                    capability_id,
                )
            )
            return issues
        if actual.lower() != digest.lower():
            issues.append(
                ValidationIssue(
                    "EVIDENCE_SHA256_MISMATCH",
                    f"evidence digest does not match file content: {path_text}",
                    row_number,
                    capability_id,
                )
            )
    return issues


def validate_matrix(
    matrix_path: Path | str,
    *,
    project_root: Path | str | None = None,
    test_roots: Sequence[Path | str] = (),
    require_baseline: bool = True,
) -> ValidationResult:
    """Validate a capability matrix and return every discovered issue."""

    matrix = Path(matrix_path).resolve()
    root = Path(project_root).resolve() if project_root else matrix.parent.parent.resolve()
    roots = tuple(Path(item).resolve() for item in test_roots)
    rows, issues = _read_matrix(matrix)
    if not rows and issues:
        return ValidationResult(matrix, 0, tuple(issues))

    discovered_test_ids, marked_capabilities = _discover_test_traceability(roots)
    seen: dict[str, int] = {}
    all_ids: set[str] = set()

    for index, row in enumerate(rows, start=2):
        capability_id = (row.get("capability_id") or "").strip()
        all_ids.add(capability_id)

        if not CAPABILITY_ID_RE.fullmatch(capability_id):
            issues.append(
                ValidationIssue(
                    "INVALID_CAPABILITY_ID",
                    "ID must be uppercase and contain only alphanumerics plus '.', '_' or '-' separators",
                    index,
                    capability_id or None,
                )
            )
        if capability_id in seen:
            issues.append(
                ValidationIssue(
                    "DUPLICATE_CAPABILITY_ID",
                    f"first declared on row {seen[capability_id]}",
                    index,
                    capability_id,
                )
            )
        else:
            seen[capability_id] = index

        enum_fields = (
            ("edition", VALID_EDITIONS, "INVALID_EDITION"),
            ("layer", VALID_LAYERS, "INVALID_LAYER"),
            ("direction", VALID_DIRECTIONS, "INVALID_DIRECTION"),
            ("dut_pics_status", VALID_PICS_STATUSES, "INVALID_PICS_STATUS"),
            ("backend_status", VALID_IMPLEMENTATION_STATUSES, "INVALID_BACKEND_STATUS"),
            ("framework_status", VALID_IMPLEMENTATION_STATUSES, "INVALID_FRAMEWORK_STATUS"),
        )
        for field, allowed, issue_code in enum_fields:
            value = (row.get(field) or "").strip()
            if value not in allowed:
                issues.append(
                    ValidationIssue(
                        issue_code,
                        f"{field}={value!r}; allowed values: {', '.join(sorted(allowed))}",
                        index,
                        capability_id,
                    )
                )

        for required_field in ("feature", "subset_level", "std_reference", "owner"):
            if not (row.get(required_field) or "").strip():
                issues.append(
                    ValidationIssue(
                        "MISSING_REQUIRED_FIELD",
                        f"{required_field} must not be empty",
                        index,
                        capability_id,
                    )
                )

        reference = (row.get("std_reference") or "").strip()
        framework_status = (row.get("framework_status") or "").strip()
        backend_status = (row.get("backend_status") or "").strip()
        direction = (row.get("direction") or "").strip()
        expected_direction = REQUIRED_DIRECTIONS.get(capability_id)
        if expected_direction is not None and direction != expected_direction:
            issues.append(
                ValidationIssue(
                    "INCORRECT_STANDARD_DIRECTION",
                    f"IEEE 1815-2012 requires direction={expected_direction}",
                    index,
                    capability_id,
                )
            )
        if capability_id in FORBIDDEN_LEGACY_IDS:
            issues.append(
                ValidationIssue(
                    "NONCANONICAL_2012_IDENTIFIER",
                    "use the exact IEEE 1815-2012 identifier required by the baseline",
                    index,
                    capability_id,
                )
            )
        if (row.get("dut_pics_status") or "").strip() != "UNKNOWN":
            issues.append(
                ValidationIssue(
                    "DUT_STATE_IN_CANONICAL_MATRIX",
                    "the framework catalog must keep dut_pics_status=UNKNOWN; store per-DUT applicability in the versioned PICS overlay",
                    index,
                    capability_id,
                )
            )
        if (
            framework_status == "NOT_APPLICABLE_BY_PICS"
            or backend_status == "NOT_APPLICABLE_BY_PICS"
        ):
            issues.append(
                ValidationIssue(
                    "DUT_STATE_IN_CANONICAL_MATRIX",
                    "NOT_APPLICABLE_BY_PICS belongs in a per-DUT result overlay, not an implementation-status column",
                    index,
                    capability_id,
                )
            )
        if reference in UNRESOLVED_REFERENCES and framework_status != "BLOCKED":
            issues.append(
                ValidationIssue(
                    "UNRESOLVED_STANDARD_REFERENCE",
                    "REVIEW_REQUIRED/BLOCKED standard references require framework_status=BLOCKED",
                    index,
                    capability_id,
                )
            )

        test_ids = _split_multi(row.get("test_case_ids") or "")
        evidence_refs = _split_multi(row.get("evidence") or "")
        if framework_status in VERIFIED_STATUSES or backend_status in VERIFIED_STATUSES:
            if not test_ids:
                issues.append(
                    ValidationIssue(
                        "VERIFIED_WITHOUT_TEST",
                        "VERIFIED_* status requires at least one test_case_id",
                        index,
                        capability_id,
                    )
                )
            if not evidence_refs:
                issues.append(
                    ValidationIssue(
                        "VERIFIED_WITHOUT_EVIDENCE",
                        "VERIFIED_* status requires at least one evidence reference",
                        index,
                        capability_id,
                    )
                )

        for test_id in test_ids:
            if not TEST_CASE_ID_RE.fullmatch(test_id):
                issues.append(
                    ValidationIssue(
                        "INVALID_TEST_CASE_ID",
                        f"invalid stable test ID: {test_id!r}",
                        index,
                        capability_id,
                    )
                )
            if roots and test_id not in discovered_test_ids:
                issues.append(
                    ValidationIssue(
                        "UNKNOWN_TEST_CASE_ID",
                        f"test ID was not found under configured test roots: {test_id}",
                        index,
                        capability_id,
                    )
                )

        for evidence_ref in evidence_refs:
            issues.extend(
                _validate_evidence_reference(
                    evidence_ref,
                    project_root=root,
                    row_number=index,
                    capability_id=capability_id,
                    require_digest=(
                        framework_status in VERIFIED_STATUSES
                        or backend_status in VERIFIED_STATUSES
                    ),
                )
            )

    for marker in sorted(marked_capabilities - all_ids):
        issues.append(
            ValidationIssue(
                "UNKNOWN_MARKED_CAPABILITY",
                f"pytest marker references an ID absent from the matrix: {marker}",
            )
        )

    if require_baseline:
        for capability_id in sorted(REQUIRED_EXACT_IDS - all_ids):
            issues.append(
                ValidationIssue(
                    "MISSING_BASELINE_CAPABILITY",
                    f"required section 12 catalog entry is absent: {capability_id}",
                )
            )
        for prefix in REQUIRED_PREFIXES:
            if not any(capability_id.startswith(prefix) for capability_id in all_ids):
                issues.append(
                    ValidationIssue(
                        "MISSING_BASELINE_FAMILY",
                        f"no capability entry starts with required prefix: {prefix}",
                    )
                )

    return ValidationResult(matrix, len(rows), tuple(issues))


def _default_test_roots(project_root: Path) -> tuple[Path, ...]:
    return (
        project_root / "tests",
        project_root / "native" / "tests",
        project_root / "python" / "tests",
    )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matrix", type=Path, help="path to capability_matrix.csv")
    parser.add_argument(
        "--project-root",
        type=Path,
        help="repository root; defaults to the parent of the matrix directory",
    )
    parser.add_argument(
        "--tests-root",
        action="append",
        type=Path,
        default=[],
        help="test source root to scan; may be repeated",
    )
    parser.add_argument(
        "--skip-baseline",
        action="store_true",
        help="validate row semantics without requiring the section 12 catalog",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    matrix = args.matrix.resolve()
    project_root = (
        args.project_root.resolve()
        if args.project_root
        else matrix.parent.parent.resolve()
    )
    test_roots = tuple(path.resolve() for path in args.tests_root) or _default_test_roots(
        project_root
    )
    result = validate_matrix(
        matrix,
        project_root=project_root,
        test_roots=test_roots,
        require_baseline=not args.skip_baseline,
    )

    if args.json:
        payload = {
            "matrix": str(result.matrix),
            "ok": result.ok,
            "row_count": result.row_count,
            "issue_count": len(result.issues),
            "issues": [asdict(issue) for issue in result.issues],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif result.ok:
        print(f"PASS: {result.row_count} capability rows validated")
    else:
        for issue in result.issues:
            print(issue.render(), file=sys.stderr)
        print(
            f"FAIL: {len(result.issues)} issue(s) across {result.row_count} row(s)",
            file=sys.stderr,
        )
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
