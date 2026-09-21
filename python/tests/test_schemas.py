from __future__ import annotations

import json
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def load_schema(name: str) -> dict[str, object]:
    with (REPOSITORY_ROOT / "schemas" / name).open(encoding="utf-8") as stream:
        return json.load(stream)


def test_simulator_settings_schema_is_bounded_and_simulator_only() -> None:
    schema = load_schema("simulator-settings.schema.json")
    assert schema["additionalProperties"] is False
    assert schema["properties"]["environment"] == {"const": "SIMULATOR"}
    for key in ("points", "controls", "events"):
        assert schema["properties"][key]["maxItems"] == 128
    for key in ("connection", "point", "control", "event"):
        assert schema["$defs"][key]["additionalProperties"] is False
    assert "safety" not in schema["$defs"]["connection"]["properties"]
    assert schema["properties"]["class_counts"]["items"]["properties"]["count"]["minimum"] == 0


def test_request_schema_is_strict_and_versioned() -> None:
    schema = load_schema("request.schema.json")
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["schema_version", "id", "cmd", "params"]
    assert schema["properties"]["schema_version"]["const"] == 1
    assert {"hello", "get_status", "shutdown"}.issubset(
        schema["properties"]["cmd"]["enum"]
    )
    assert {"capture.begin", "capture.progress", "capture.end"}.issubset(
        schema["properties"]["cmd"]["enum"]
    )
    assert schema["$defs"]["captureBeginParams"]["additionalProperties"] is False
    assert schema["$defs"]["captureEndParams"]["additionalProperties"] is False


def test_capture_result_schema_is_bounded_and_truth_aware() -> None:
    schema = load_schema("capture-result.schema.json")
    capture = schema["$defs"]["capture"]
    assert capture["additionalProperties"] is False
    assert capture["properties"]["current_queue_depth"]["maximum"] == 65536
    assert capture["properties"]["mismatch_sample"]["maxItems"] == 1024
    assert {
        "expected_total",
        "missing",
        "duplicates",
        "unknown_reason",
        "completeness_scope",
        "received_sequence_sha256",
        "sequence_match",
    }.issubset(capture["required"])


def test_protocol_trace_schema_has_strict_bounded_requests_and_records() -> None:
    request = load_schema("request.schema.json")
    assert {"trace.start", "trace.read", "trace.stop"}.issubset(
        request["properties"]["cmd"]["enum"]
    )
    for name in ("traceStartParams", "traceReadParams", "traceStopParams"):
        assert request["$defs"][name]["additionalProperties"] is False
    read = request["$defs"]["traceReadParams"]
    assert read["required"] == ["trace_id"]
    assert read["properties"]["max_records"]["maximum"] == 1024
    assert read["properties"]["timeout_ms"]["maximum"] == 60000
    result = load_schema("trace-result.schema.json")
    assert result["additionalProperties"] is False
    assert result["properties"]["scope"] == {"const": "opendnp3_stack"}
    assert result["properties"]["records"]["maxItems"] == 1024
    record = result["$defs"]["record"]
    assert record["additionalProperties"] is False
    assert record["properties"]["message"]["maxLength"] == 1024
    assert {"dropped_records", "truncated_records", "complete"}.issubset(result["required"])


def test_performance_profile_schema_is_strict_and_threshold_driven() -> None:
    schema = load_schema("performance-profile.schema.json")
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == 1
    assert schema["$defs"]["scenario"]["additionalProperties"] is False
    assert "expected_objects_per_iteration" in schema["$defs"]["scenario"][
        "required"
    ]
    assert {
        "expected_by_kind",
        "expected_by_group_variation",
    }.issubset(schema["$defs"]["scenario"]["required"])
    assert schema["$defs"]["thresholds"]["additionalProperties"] is False
    assert schema["$defs"]["soak"]["properties"]["max_checkpoints"][
        "maximum"
    ] == 10000


def test_local_event_profile_schema_binds_generator_and_outstation_bounds() -> None:
    schema = load_schema("local-event-profile.schema.json")
    assert schema["additionalProperties"] is False
    assert schema["properties"]["scope"]["const"] == "LOCAL_LOOPBACK_ONLY"
    load = schema["$defs"]["load"]
    assert load["additionalProperties"] is False
    assert load["properties"]["event_count"]["maximum"] == 4096
    assert load["properties"]["queue_capacity"]["maximum"] == 65536


def test_local_event_report_schema_is_strict_and_truth_bound() -> None:
    schema = load_schema("local-event-report.schema.json")
    assert schema["additionalProperties"] is False
    assert schema["properties"]["evidence_scope"]["const"] == (
        "LOCAL_LOOPBACK_ONLY"
    )
    assert schema["properties"]["formal_dut_conclusion"]["const"] is False
    assert schema["$defs"]["truth"]["additionalProperties"] is False
    assert schema["$defs"]["unsolicitedQueue"]["properties"][
        "queue_capacity"
    ]["const"] == 4096
    assert schema["$defs"]["iterationSample"]["properties"]["capture"][
        "$ref"
    ] == "capture-result.schema.json#/$defs/capture"


def test_performance_report_schema_is_strict_and_non_conclusive() -> None:
    schema = load_schema("performance-report.schema.json")
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == 1
    assert schema["properties"]["formal_dut_conclusion"]["const"] is False
    assert schema["$defs"]["scenario"]["additionalProperties"] is False
    assert schema["$defs"]["resources"]["oneOf"][0][
        "additionalProperties"
    ] is False
    assert schema["$defs"]["limitations"]["minItems"] == 1


def test_soak_report_schema_requires_terminal_state_and_evidence_chain() -> None:
    schema = load_schema("soak-report.schema.json")
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == 1
    assert schema["properties"]["formal_dut_conclusion"]["const"] is False
    assert "INCOMPLETE_INTERRUPTED" in schema["properties"]["status"]["enum"]
    assert schema["properties"]["checkpoint_chain_tail_sha256"][
        "pattern"
    ] == "^[0-9a-f]{64}$"
    assert schema["$defs"]["scenario"]["additionalProperties"] is False
    assert schema["$defs"]["channelEvents"]["properties"]["source"][
        "const"
    ] == "native_channel_event_store"
    assert schema["$defs"]["resources"]["oneOf"][0][
        "additionalProperties"
    ] is False


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
    assert schema["properties"]["capabilities"]["propertyNames"]["maxLength"] == 256
    assert schema["properties"]["notes"]["minLength"] == 1


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
    }.issubset(
        set(control["required"])
        | set(schema["allOf"][0]["else"]["properties"]["control_scenarios"]["items"]["required"])
    )
    assert schema["$defs"]["crobCommand"]["additionalProperties"] is False
    assert "null" not in schema["$defs"]["crobCommand"]["properties"][
        "operation"
    ]["enum"]
    assert schema["$defs"]["analogCommand"]["additionalProperties"] is False


def test_local_outstation_control_schema_is_bounded_and_strict() -> None:
    schema = load_schema("local-outstation-request.schema.json")
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert len(schema["oneOf"]) == 5
    assert schema["$defs"]["hello"]["additionalProperties"] is False
    assert schema["$defs"]["update"]["additionalProperties"] is False
    assert schema["$defs"]["inputUpdateBase"]["additionalProperties"] is False
    assert schema["$defs"]["outputUpdateBase"]["additionalProperties"] is False
    assert schema["$defs"]["generateEvents"]["additionalProperties"] is False
    assert schema["$defs"]["inputUpdateBase"]["properties"]["index"] == {
        "type": "integer",
        "minimum": 0,
        "maximum": 65534,
    }
    assert schema["$defs"]["inputUpdateBase"]["properties"]["timestamp_ms"][
        "maximum"
    ] == (1 << 48) - 1


def test_preflight_report_schema_separates_valid_and_invalid_results() -> None:
    schema = load_schema("preflight-report.schema.json")
    assert len(schema["oneOf"]) == 2
    report = schema["$defs"]["report"]
    invalid = schema["$defs"]["invalidConfiguration"]
    assert report["additionalProperties"] is False
    assert invalid["additionalProperties"] is False
    assert report["properties"]["schema_version"]["const"] == 1
    assert report["properties"]["scope"]["const"] == (
        "OFFLINE_CONFIGURATION_ONLY"
    )
    assert schema["$defs"]["capability"]["additionalProperties"] is False
    assert schema["$defs"]["issue"]["additionalProperties"] is False


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


def test_compatibility_report_schema_is_strict_and_non_conclusive() -> None:
    schema = load_schema("compatibility-report.schema.json")
    assert schema["additionalProperties"] is False
    assert schema["properties"]["evidence_scope"]["const"] == (
        "LOCAL_PACKAGE_AND_LOOPBACK_ONLY"
    )
    assert schema["properties"]["formal_dut_conclusion"]["const"] is False
    assert schema["$defs"]["result"]["additionalProperties"] is False
    assert schema["$defs"]["result"]["properties"]["installation_mode"][
        "enum"
    ] == ["packaged_wheel", "source_copy_fallback"]


def test_release_closure_schema_requires_clean_reproducible_release() -> None:
    schema = load_schema("release-closure-report.schema.json")
    assert schema["additionalProperties"] is False
    assert schema["properties"]["evidence_scope"]["const"] == (
        "LOCAL_RELEASE_AND_LOOPBACK_ONLY"
    )
    assert schema["properties"]["formal_dut_conclusion"]["const"] is False
    assert schema["properties"]["git_worktree_state"]["const"] == "clean"
    assert schema["properties"]["deterministic_package"]["const"] is True
    assert schema["properties"]["migration_compatibility"]["const"] == "PASS"
