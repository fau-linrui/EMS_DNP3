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
