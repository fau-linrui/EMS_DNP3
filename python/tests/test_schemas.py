from __future__ import annotations

import json
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def load_schema(name: str) -> dict[str, object]:
    with (REPOSITORY_ROOT / "schemas" / name).open(encoding="utf-8") as stream:
        return json.load(stream)


def test_request_schema_is_strict_and_versioned() -> None:
    schema = load_schema("request.schema.json")
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["schema_version", "id", "cmd", "params"]
    assert schema["properties"]["schema_version"]["const"] == 1
    assert {"hello", "get_status", "shutdown"}.issubset(
        schema["properties"]["cmd"]["enum"]
    )


def test_response_schema_has_strict_success_and_error_envelopes() -> None:
    schema = load_schema("response.schema.json")
    success, failure = schema["oneOf"]
    assert success["additionalProperties"] is False
    assert failure["additionalProperties"] is False
    assert success["properties"]["ok"]["const"] is True
    assert failure["properties"]["ok"]["const"] is False
    error_codes = failure["properties"]["error"]["properties"]["code"]["enum"]
    assert {
        "INVALID_REQUEST",
        "REQUEST_TOO_LARGE",
        "SCHEMA_MISMATCH",
        "NOT_CONNECTED",
        "ALREADY_CONNECTED",
        "CONNECTION_TIMEOUT",
        "UNSUPPORTED_BY_BACKEND",
        "PROCESS_SHUTTING_DOWN",
        "INTERNAL_ERROR",
    }.issubset(error_codes)


def test_build_info_schema_requires_fixed_build_identity() -> None:
    schema = load_schema("build-info.schema.json")
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == 1
    assert {
        "host_version",
        "git_commit",
        "opendnp3_version",
        "opendnp3_commit",
        "compiler_id",
        "compiler_version",
        "build_time_utc",
        "target_architecture",
        "protocol_schema_version",
        "dependency_lock_sha256",
    }.issubset(schema["required"])


def test_ems_profile_schema_is_strict_and_uses_tri_state_capabilities() -> None:
    schema = load_schema("ems-profile.schema.json")
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == 1
    assert schema["required"] == ["schema_version", "device", "capabilities"]
    assert schema["properties"]["capabilities"]["additionalProperties"]["enum"] == [
        "SUPPORTED",
        "NOT_SUPPORTED",
        "UNKNOWN",
    ]


def test_point_table_schema_is_strict_and_versioned() -> None:
    schema = load_schema("point-table.schema.json")
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == 1
    point = schema["$defs"]["point"]
    assert point["additionalProperties"] is False
    assert {
        "point_id",
        "point_type",
        "index",
        "static_group",
        "static_variation",
        "enabled",
    }.issubset(point["required"])
    assert len(point["allOf"]) == 9


def test_ems_test_plan_schema_keeps_controls_explicit_and_strict() -> None:
    schema = load_schema("ems-test-plan.schema.json")
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == 1
    assert schema["required"] == [
        "schema_version",
        "poll_scenarios",
        "unsolicited_scenarios",
        "control_scenarios",
    ]
    control = schema["$defs"]["controlScenario"]
    assert control["additionalProperties"] is False
    assert {
        "authorization_reference",
        "command",
        "precondition",
        "postcondition",
        "restore_command",
        "restore_expectation",
    }.issubset(control["required"])
    assert schema["$defs"]["crobCommand"]["additionalProperties"] is False
    assert "null" not in schema["$defs"]["crobCommand"]["properties"][
        "operation"
    ]["enum"]
    assert schema["$defs"]["analogCommand"]["additionalProperties"] is False


def test_evidence_manifest_schema_forbids_private_top_level_fields() -> None:
    schema = load_schema("evidence-manifest.schema.json")
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == 1
    assert {
        "run_id",
        "state",
        "inputs",
        "results",
        "redaction",
    }.issubset(schema["required"])
    redaction = schema["properties"]["redaction"]
    assert redaction["additionalProperties"] is False
    assert redaction["properties"]["arbitrary_test_output_requires_review"][
        "const"
    ] is True


def test_safety_incident_schema_separates_open_and_acknowledged_records() -> None:
    schema = load_schema("safety-incident.schema.json")
    assert len(schema["oneOf"]) == 2
    assert schema["$defs"]["openIncident"]["additionalProperties"] is False
    assert schema["$defs"]["acknowledgedIncident"]["additionalProperties"] is False
    assert schema["$defs"]["openIncident"]["properties"]["status"]["const"] == "OPEN"
    assert (
        schema["$defs"]["acknowledgedIncident"]["properties"]["status"]["const"]
        == "ACKNOWLEDGED"
    )


def test_package_manifest_schema_is_strict_and_hashes_each_file() -> None:
    schema = load_schema("package-manifest.schema.json")
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == 1
    item = schema["properties"]["files"]["items"]
    assert item["additionalProperties"] is False
    assert item["required"] == ["path", "size_bytes", "sha256"]
