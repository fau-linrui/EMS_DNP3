"""Offline, fail-closed readiness checks for a private EMS test configuration."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
from typing import Mapping, Sequence, TextIO

from .ems_profile import (
    CAPABILITY_ID_PATTERN,
    EmsProfileError,
    load_ems_profile,
)
from .ems_test_plan import EmsTestPlan, EmsTestPlanError, load_ems_test_plan
from .point_table import PointTable, PointTableError, load_point_table


PREFLIGHT_REPORT_SCHEMA_VERSION = 1
_MAX_MATRIX_BYTES = 4 * 1024 * 1024
_MAX_MATRIX_ROWS = 10_000
_MAX_CAPABILITY_ID_CHARACTERS = 256
_MATRIX_HEADER = (
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
_IMPLEMENTATION_STATUSES = frozenset(
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
_FRAMEWORK_READY_STATUSES = frozenset(
    {
        "IMPLEMENTED_UNVERIFIED",
        "VERIFIED_UNIT",
        "VERIFIED_INTEROP",
        "VERIFIED_CONFORMANCE",
    }
)
_PLACEHOLDER_TOKENS = (
    "FILL_ME",
    "TODO",
    "TBD",
    "PLACEHOLDER",
    "EXAMPLE",
)


class PreflightError(ValueError):
    """A stable validation error that prevents a readiness decision."""


@dataclass(frozen=True, slots=True)
class CapabilityMatrixEntry:
    capability_id: str
    framework_status: str


def _load_capability_matrix(
    path: str | Path,
) -> Mapping[str, CapabilityMatrixEntry]:
    source = Path(path).expanduser().resolve()
    try:
        size = source.stat().st_size
    except OSError as error:
        raise PreflightError(
            f"cannot inspect capability matrix {source}: {error}"
        ) from error
    if not source.is_file():
        raise PreflightError(f"capability matrix does not exist: {source}")
    if size > _MAX_MATRIX_BYTES:
        raise PreflightError(
            f"capability matrix exceeds {_MAX_MATRIX_BYTES} bytes: {source}"
        )

    entries: dict[str, CapabilityMatrixEntry] = {}
    try:
        with source.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            if tuple(reader.fieldnames or ()) != _MATRIX_HEADER:
                raise PreflightError(
                    "capability matrix header does not match the v1 catalog format"
                )
            for line_number, row in enumerate(reader, start=2):
                if line_number > _MAX_MATRIX_ROWS + 1:
                    raise PreflightError(
                        f"capability matrix exceeds {_MAX_MATRIX_ROWS} rows"
                    )
                if None in row or any(value is None for value in row.values()):
                    raise PreflightError(
                        f"capability matrix has a malformed row at line {line_number}"
                    )
                capability_id = row["capability_id"].strip()
                if (
                    len(capability_id) > _MAX_CAPABILITY_ID_CHARACTERS
                    or not CAPABILITY_ID_PATTERN.fullmatch(capability_id)
                ):
                    raise PreflightError(
                        "capability matrix has an invalid capability_id at line "
                        f"{line_number}: {capability_id!r}"
                    )
                if capability_id in entries:
                    raise PreflightError(
                        f"capability matrix repeats capability_id {capability_id!r}"
                    )
                framework_status = row["framework_status"].strip()
                if framework_status not in _IMPLEMENTATION_STATUSES:
                    raise PreflightError(
                        "capability matrix has an invalid framework_status at line "
                        f"{line_number}: {framework_status!r}"
                    )
                entries[capability_id] = CapabilityMatrixEntry(
                    capability_id=capability_id,
                    framework_status=framework_status,
                )
    except PreflightError:
        raise
    except (OSError, UnicodeError, csv.Error) as error:
        raise PreflightError(
            f"cannot read capability matrix {source}: {error}"
        ) from error
    if not entries:
        raise PreflightError("capability matrix must not be empty")
    return entries


def _add_requirement(
    requirements: dict[str, set[str]],
    capability_id: str,
    reason: str,
) -> None:
    requirements.setdefault(capability_id, set()).add(reason)


def _required_capabilities(
    point_table: PointTable,
    plan: EmsTestPlan,
) -> dict[str, set[str]]:
    requirements: dict[str, set[str]] = {}
    _add_requirement(requirements, "CHANNEL.TCP.CLIENT", "TCP master connection")
    _add_requirement(requirements, "APP.FC.01.READ", "baseline point read")

    for point in point_table.enabled_points:
        _add_requirement(
            requirements,
            point.capability_id,
            f"enabled point {point.point_id}",
        )

    for scenario in plan.enabled_poll_scenarios:
        reason = f"enabled poll scenario {scenario.scenario_id}"
        for capability_id in scenario.capability_ids:
            _add_requirement(requirements, capability_id, reason)
        for point_id in scenario.expected_point_ids:
            point = point_table.by_id[point_id]
            if scenario.poll_type == "class":
                event_capability_id = point.event_capability_id
                if event_capability_id is None:
                    raise PreflightError(
                        f"class scenario {scenario.scenario_id} references "
                        f"static-only point {point_id}"
                    )
                _add_requirement(
                    requirements,
                    event_capability_id,
                    f"expected event point {point_id} in {scenario.scenario_id}",
                )
            else:
                _add_requirement(
                    requirements,
                    point.capability_id,
                    f"expected point {point_id} in {scenario.scenario_id}",
                )

    for scenario in plan.enabled_unsolicited_scenarios:
        point = point_table.by_id[scenario.expected_point_id]
        reason = f"enabled unsolicited scenario {scenario.scenario_id}"
        for capability_id in scenario.capability_ids(point):
            _add_requirement(requirements, capability_id, reason)

    for scenario in plan.enabled_control_scenarios:
        point = point_table.by_id[scenario.feedback_point_id]
        reason = f"enabled control scenario {scenario.scenario_id}"
        for capability_id in scenario.capability_ids(point):
            _add_requirement(requirements, capability_id, reason)

    return requirements


def _file_metadata(path: Path) -> dict[str, object]:
    try:
        content = path.read_bytes()
    except OSError as error:
        raise PreflightError(f"cannot hash input file {path}: {error}") from error
    return {
        "path": str(path),
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _contains_placeholder(value: str) -> bool:
    normalized = value.upper()
    return any(token in normalized for token in _PLACEHOLDER_TOKENS)


def build_preflight_report(
    *,
    pics_path: str | Path,
    points_path: str | Path,
    plan_path: str | Path,
    capability_matrix_path: str | Path,
) -> dict[str, object]:
    """Validate four offline inputs and return a deterministic readiness report."""

    try:
        matrix = _load_capability_matrix(capability_matrix_path)
        profile = load_ems_profile(
            pics_path,
            known_capability_ids=frozenset(matrix),
        )
        point_table = load_point_table(points_path)
        plan = load_ems_test_plan(plan_path, point_table)
    except (EmsProfileError, PointTableError, EmsTestPlanError) as error:
        raise PreflightError(str(error)) from error

    requirements = _required_capabilities(point_table, plan)
    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    for field_name, value in profile.device.to_mapping().items():
        if _contains_placeholder(value):
            blockers.append(
                {
                    "code": "DEVICE_IDENTITY_PLACEHOLDER",
                    "message": (
                        f"device.{field_name} still contains a placeholder: "
                        f"{value!r}"
                    ),
                }
            )

    if not point_table.enabled_points:
        blockers.append(
            {
                "code": "NO_ENABLED_POINTS",
                "message": "the point table has no enabled points",
            }
        )
    enabled_scenario_count = (
        len(plan.enabled_poll_scenarios)
        + len(plan.enabled_unsolicited_scenarios)
        + len(plan.enabled_control_scenarios)
    )
    if enabled_scenario_count == 0:
        blockers.append(
            {
                "code": "NO_ENABLED_SCENARIOS",
                "message": "the EMS test plan has no enabled scenarios",
            }
        )

    capability_results: list[dict[str, object]] = []
    for capability_id in sorted(requirements):
        pics_status = profile.status(capability_id) or "MISSING"
        matrix_entry = matrix.get(capability_id)
        framework_status = (
            matrix_entry.framework_status if matrix_entry is not None else "MISSING"
        )
        pics_ready = pics_status == "SUPPORTED"
        framework_ready = framework_status in _FRAMEWORK_READY_STATUSES
        capability_ready = pics_ready and framework_ready
        capability_results.append(
            {
                "capability_id": capability_id,
                "reasons": sorted(requirements[capability_id]),
                "pics_status": pics_status,
                "framework_status": framework_status,
                "ready": capability_ready,
            }
        )
        if not pics_ready:
            blockers.append(
                {
                    "code": "DUT_CAPABILITY_NOT_SUPPORTED",
                    "message": (
                        f"required capability {capability_id} has PICS status "
                        f"{pics_status}; SUPPORTED is required"
                    ),
                }
            )
        if not framework_ready:
            blockers.append(
                {
                    "code": "FRAMEWORK_CAPABILITY_NOT_READY",
                    "message": (
                        f"required capability {capability_id} has framework status "
                        f"{framework_status}"
                    ),
                }
            )

    if not plan.enabled_unsolicited_scenarios:
        warnings.append(
            {
                "code": "UNSOLICITED_NOT_ENABLED",
                "message": "no unsolicited scenario is enabled",
            }
        )
    if not plan.enabled_control_scenarios:
        warnings.append(
            {
                "code": "CONTROL_NOT_ENABLED",
                "message": "no state-changing control scenario is enabled",
            }
        )

    matrix_path = Path(capability_matrix_path).expanduser().resolve()
    inputs = {
        "pics": _file_metadata(profile.source_path),
        "points": _file_metadata(point_table.source_path),
        "plan": _file_metadata(plan.source_path),
        "capability_matrix": _file_metadata(matrix_path),
    }
    ready_count = sum(bool(item["ready"]) for item in capability_results)
    return {
        "schema_version": PREFLIGHT_REPORT_SCHEMA_VERSION,
        "ready": not blockers,
        "scope": "OFFLINE_CONFIGURATION_ONLY",
        "device": profile.device.to_mapping(),
        "inputs": inputs,
        "summary": {
            "total_points": len(point_table.points),
            "enabled_points": len(point_table.enabled_points),
            "enabled_poll_scenarios": len(plan.enabled_poll_scenarios),
            "enabled_unsolicited_scenarios": len(
                plan.enabled_unsolicited_scenarios
            ),
            "enabled_control_scenarios": len(plan.enabled_control_scenarios),
            "required_capabilities": len(capability_results),
            "ready_capabilities": ready_count,
            "blockers": len(blockers),
            "warnings": len(warnings),
        },
        "enabled_scenarios": {
            "poll": [item.scenario_id for item in plan.enabled_poll_scenarios],
            "unsolicited": [
                item.scenario_id for item in plan.enabled_unsolicited_scenarios
            ],
            "control": [
                item.scenario_id for item in plan.enabled_control_scenarios
            ],
        },
        "required_capabilities": capability_results,
        "blockers": blockers,
        "warnings": warnings,
    }


def _print_human_report(report: Mapping[str, object], stream: TextIO) -> None:
    device = report["device"]
    summary = report["summary"]
    assert isinstance(device, Mapping)
    assert isinstance(summary, Mapping)
    status = "READY" if report["ready"] else "NOT READY"
    print(f"DNP3 EMS offline preflight: {status}", file=stream)
    print(
        "Scope: configuration only; no TCP connection to the DUT was attempted.",
        file=stream,
    )
    print(
        "Device: "
        f"{device['vendor']} / {device['model']} / {device['firmware']} "
        f"(profile {device['profile_revision']})",
        file=stream,
    )
    print(
        "Enabled: "
        f"{summary['enabled_points']} points, "
        f"{summary['enabled_poll_scenarios']} polls, "
        f"{summary['enabled_unsolicited_scenarios']} unsolicited, "
        f"{summary['enabled_control_scenarios']} controls",
        file=stream,
    )
    print(
        "Capabilities: "
        f"{summary['ready_capabilities']}/{summary['required_capabilities']} ready",
        file=stream,
    )
    blockers = report["blockers"]
    warnings = report["warnings"]
    assert isinstance(blockers, list)
    assert isinstance(warnings, list)
    if blockers:
        print("Blockers:", file=stream)
        for issue in blockers:
            print(f"  - [{issue['code']}] {issue['message']}", file=stream)
    if warnings:
        print("Warnings:", file=stream)
        for issue in warnings:
            print(f"  - [{issue['code']}] {issue['message']}", file=stream)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate private EMS PICS, point table, test plan, and framework "
            "capabilities without connecting to a DUT."
        )
    )
    parser.add_argument("--pics", required=True, help="Private EMS Profile/PICS JSON")
    parser.add_argument("--points", required=True, help="Private point-table CSV")
    parser.add_argument("--plan", required=True, help="Private EMS test-plan JSON")
    parser.add_argument(
        "--capability-matrix",
        default="config/capability_matrix.csv",
        help="Framework capability matrix CSV",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable JSON report",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = build_preflight_report(
            pics_path=args.pics,
            points_path=args.points,
            plan_path=args.plan,
            capability_matrix_path=args.capability_matrix,
        )
    except PreflightError as error:
        if args.json:
            print(
                json.dumps(
                    {
                        "schema_version": PREFLIGHT_REPORT_SCHEMA_VERSION,
                        "ready": False,
                        "error": {
                            "code": "INVALID_CONFIGURATION",
                            "message": str(error),
                        },
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        else:
            print(f"DNP3 EMS offline preflight: INVALID\n{error}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_human_report(report, sys.stdout)
    return 0 if report["ready"] else 3


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "PREFLIGHT_REPORT_SCHEMA_VERSION",
    "PreflightError",
    "build_preflight_report",
    "main",
]
