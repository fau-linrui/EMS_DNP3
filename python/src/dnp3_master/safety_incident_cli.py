"""Command-line interface for persistent uncertain-control incidents."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence

from .safety_incidents import SafetyIncidentStore


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect or acknowledge a persistent DNP3 control safety incident"
    )
    parser.add_argument("--directory", required=True, type=Path)
    subparsers = parser.add_subparsers(dest="action", required=True)
    status = subparsers.add_parser("status")
    status.add_argument("--dut-id", required=True)
    acknowledge = subparsers.add_parser("acknowledge")
    acknowledge.add_argument("--dut-id", required=True)
    acknowledge.add_argument("--incident-id", required=True)
    acknowledge.add_argument("--acknowledged-by", required=True)
    acknowledge.add_argument("--readback-summary", required=True)
    acknowledge.add_argument("--readback-json-file", required=True, type=Path)
    acknowledge.add_argument("--evidence-reference", required=True)
    arguments = parser.parse_args(argv)
    store = SafetyIncidentStore(arguments.directory)
    try:
        if arguments.action == "status":
            result = store.get_active(arguments.dut_id)
            print(json.dumps({"active": result}, ensure_ascii=False, sort_keys=True))
            return 0
        readback_path = arguments.readback_json_file.expanduser().resolve(strict=True)
        readback = json.loads(
            readback_path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
        result = store.acknowledge(
            dut_id=arguments.dut_id,
            incident_id=arguments.incident_id,
            acknowledged_by=arguments.acknowledged_by,
            readback_summary=arguments.readback_summary,
            readback=readback,
            evidence_reference=arguments.evidence_reference,
        )
        print(json.dumps({"acknowledged": result}, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as error:
        print(f"SAFETY INCIDENT ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
