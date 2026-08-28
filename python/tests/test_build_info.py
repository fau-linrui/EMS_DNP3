from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re


OPENDNP3_COMMIT = "26b4c01e4839bbbda8866655e086471c4917ee53"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_IDENTITY_PATTERN = re.compile(r"^(?:unversioned|[0-9a-f]{40})$")
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_KEYS = {
    "schema_version",
    "host_version",
    "git_commit",
    "opendnp3_version",
    "opendnp3_commit",
    "compiler_id",
    "compiler_version",
    "build_time_utc",
    "build_configuration",
    "target_architecture",
    "protocol_schema_version",
    "capability_matrix_sha256",
    "dependency_lock_sha256",
}


def load_build_info() -> tuple[dict[str, object], Path]:
    configured = os.environ.get("DNP3_MASTER_HOST_EXE")
    assert configured, "DNP3_MASTER_HOST_EXE must identify the built native host"
    path = Path(configured).with_name("build-info.json")
    assert path.is_file(), f"build metadata does not exist beside the host: {path}"
    with path.open(encoding="utf-8") as stream:
        return json.load(stream), path


def test_build_info_contains_auditable_fixed_identity() -> None:
    build_info, _ = load_build_info()

    assert set(build_info) == EXPECTED_KEYS
    assert build_info["schema_version"] == 1
    assert build_info["host_version"] == "0.1.0"
    assert GIT_IDENTITY_PATTERN.fullmatch(build_info["git_commit"])
    assert build_info["opendnp3_version"] == "3.1.2"
    assert build_info["opendnp3_commit"] == OPENDNP3_COMMIT
    assert build_info["compiler_id"] == "MSVC"
    assert re.fullmatch(r"\d+(?:\.\d+)+", build_info["compiler_version"])
    assert build_info["target_architecture"] == "x64"
    assert build_info["protocol_schema_version"] == 1
    assert build_info["build_configuration"] in {"Debug", "Release", "RelWithDebInfo"}
    assert SHA256_PATTERN.fullmatch(build_info["capability_matrix_sha256"])
    assert SHA256_PATTERN.fullmatch(build_info["dependency_lock_sha256"])
    parsed_time = datetime.fromisoformat(
        build_info["build_time_utc"].replace("Z", "+00:00")
    )
    assert parsed_time.tzinfo is not None


def test_build_info_dependency_lock_hash_matches_repository() -> None:
    build_info, _ = load_build_info()
    lock_path = REPOSITORY_ROOT / "third_party" / "opendnp3-dependencies.lock.json"
    actual = hashlib.sha256(lock_path.read_bytes()).hexdigest()

    assert build_info["dependency_lock_sha256"] == actual
