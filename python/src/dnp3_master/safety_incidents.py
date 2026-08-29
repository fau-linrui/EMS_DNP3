"""Fail-closed persistence for control operations with uncertain outcomes.

The active lock is keyed by a domain-separated DUT identity hash.  It never
contains the raw DUT identity, command values, safety token, or readback data.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
import uuid

from .errors import (
    SafetyIncidentAcknowledgmentError,
    SafetyIncidentPersistenceError,
    UnresolvedSafetyIncidentError,
)


SAFETY_INCIDENT_SCHEMA_VERSION = 1
_MAX_INCIDENT_BYTES = 128 * 1024
_MAX_TEXT_BYTES = 4096
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_INCIDENT_ID_PATTERN = re.compile(
    r"^INC-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)
_COMMAND_TYPES = frozenset(
    {"crob", "analog_output_int16", "analog_output_int32", "analog_output_float32", "analog_output_double64"}
)


@dataclass(frozen=True, slots=True)
class IncidentRecordResult:
    """Result of creating a new lock or coalescing with an existing one."""

    incident: Mapping[str, Any]
    created: bool


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _canonical_bytes(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("value must be finite and JSON serializable") from error
    return encoded


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def dut_identity_sha256(dut_id: str) -> str:
    """Return the non-reversible, domain-separated identifier used on disk."""

    if not isinstance(dut_id, str):
        raise ValueError("dut_id must be a string")
    encoded = dut_id.encode("utf-8")
    if not encoded or len(encoded) > 128:
        raise ValueError("dut_id must contain between 1 and 128 UTF-8 bytes")
    if any(ord(character) < 0x21 or ord(character) > 0x7E for character in dut_id):
        raise ValueError("dut_id must contain printable non-space ASCII")
    return hashlib.sha256(b"dnp3-safety-dut-v1\0" + encoded).hexdigest()


def _bounded_text(value: object, field_name: str, *, maximum: int = _MAX_TEXT_BYTES) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    encoded = value.encode("utf-8")
    if not encoded or len(encoded) > maximum:
        raise ValueError(
            f"{field_name} must contain between 1 and {maximum} UTF-8 bytes"
        )
    if any(ord(character) < 0x20 and character not in "\t\n\r" for character in value):
        raise ValueError(f"{field_name} must not contain control characters")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _validate_incident(document: object) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ValueError("incident root must be an object")
    required = {
        "schema_version",
        "incident_id",
        "status",
        "created_at_utc",
        "updated_at_utc",
        "dut_identity_sha256",
        "command",
        "host_error",
        "required_action",
    }
    optional = {"acknowledgment"}
    if set(document).difference(required | optional) or required.difference(document):
        raise ValueError("incident contains missing or unknown root fields")
    if document["schema_version"] != SAFETY_INCIDENT_SCHEMA_VERSION:
        raise ValueError("incident schema_version must be 1")
    incident_id = document["incident_id"]
    if not isinstance(incident_id, str) or not _INCIDENT_ID_PATTERN.fullmatch(incident_id):
        raise ValueError("incident_id has an invalid format")
    if document["status"] not in {"OPEN", "ACKNOWLEDGED"}:
        raise ValueError("incident status is invalid")
    for field_name in ("created_at_utc", "updated_at_utc"):
        value = document[field_name]
        if not isinstance(value, str) or not _TIMESTAMP_PATTERN.fullmatch(value):
            raise ValueError(f"incident {field_name} is invalid")
    dut_hash = document["dut_identity_sha256"]
    if not isinstance(dut_hash, str) or not _SHA256_PATTERN.fullmatch(dut_hash):
        raise ValueError("incident dut_identity_sha256 is invalid")
    if document["required_action"] != "READBACK_THEN_EXPLICIT_ACKNOWLEDGMENT":
        raise ValueError("incident required_action is invalid")

    command = document["command"]
    if not isinstance(command, dict) or set(command) != {
        "operation",
        "points",
        "payload_sha256",
    }:
        raise ValueError("incident command metadata is invalid")
    if command["operation"] not in {"select_and_operate", "direct_operate"}:
        raise ValueError("incident command operation is invalid")
    payload_hash = command["payload_sha256"]
    if not isinstance(payload_hash, str) or not _SHA256_PATTERN.fullmatch(payload_hash):
        raise ValueError("incident command payload_sha256 is invalid")
    points = command["points"]
    if not isinstance(points, list) or not 1 <= len(points) <= 256:
        raise ValueError("incident command points are invalid")
    seen_points: set[tuple[str, int]] = set()
    for point in points:
        if not isinstance(point, dict) or set(point) != {"type", "index"}:
            raise ValueError("incident command point is invalid")
        point_type = point["type"]
        index = point["index"]
        if point_type not in _COMMAND_TYPES:
            raise ValueError("incident command point type is invalid")
        if type(index) is not int or not 0 <= index <= 65535:
            raise ValueError("incident command point index is invalid")
        identity = (point_type, index)
        if identity in seen_points:
            raise ValueError("incident command repeats a point")
        seen_points.add(identity)

    host_error = document["host_error"]
    if not isinstance(host_error, dict) or set(host_error) != {
        "code",
        "execution_uncertain",
        "may_still_execute",
        "request_id",
    }:
        raise ValueError("incident host_error metadata is invalid")
    _bounded_text(host_error["code"], "host_error.code", maximum=128)
    _bounded_text(host_error["request_id"], "host_error.request_id", maximum=256)
    for field_name in ("execution_uncertain", "may_still_execute"):
        if type(host_error[field_name]) is not bool:
            raise ValueError(f"incident host_error.{field_name} must be boolean")
    if not (
        host_error["execution_uncertain"]
        or host_error["may_still_execute"]
        or host_error["code"] == "RESPONSE_TIMEOUT"
    ):
        raise ValueError("incident host_error does not describe uncertainty")

    acknowledgment = document.get("acknowledgment")
    if document["status"] == "OPEN" and acknowledgment is not None:
        raise ValueError("an OPEN incident must not contain acknowledgment data")
    if document["status"] == "ACKNOWLEDGED":
        if not isinstance(acknowledgment, dict) or set(acknowledgment) != {
            "acknowledged_at_utc",
            "acknowledged_by",
            "evidence_reference",
            "readback_payload_sha256",
            "readback_summary",
        }:
            raise ValueError("acknowledged incident metadata is invalid")
        timestamp = acknowledgment["acknowledged_at_utc"]
        if not isinstance(timestamp, str) or not _TIMESTAMP_PATTERN.fullmatch(timestamp):
            raise ValueError("acknowledgment timestamp is invalid")
        _bounded_text(acknowledgment["acknowledged_by"], "acknowledged_by", maximum=128)
        _bounded_text(acknowledgment["readback_summary"], "readback_summary")
        _bounded_text(acknowledgment["evidence_reference"], "evidence_reference")
        readback_hash = acknowledgment["readback_payload_sha256"]
        if not isinstance(readback_hash, str) or not _SHA256_PATTERN.fullmatch(readback_hash):
            raise ValueError("acknowledgment readback_payload_sha256 is invalid")
    return deepcopy(document)


class SafetyIncidentStore:
    """Filesystem-backed unresolved-control lock store."""

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory).expanduser().resolve(strict=False)
        self.active_directory = self.directory / "active"
        self.archive_directory = self.directory / "archive"

    def _ensure_directories(self) -> None:
        try:
            self.active_directory.mkdir(parents=True, exist_ok=True)
            self.archive_directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise SafetyIncidentPersistenceError(
                f"cannot create persistent safety-incident directory: {error}"
            ) from error
        if not self.active_directory.is_dir() or not self.archive_directory.is_dir():
            raise SafetyIncidentPersistenceError(
                "persistent safety-incident paths must be directories"
            )

    def _active_path_from_hash(self, dut_hash: str) -> Path:
        return self.active_directory / f"{dut_hash}.json"

    def _archive_path(self, incident_id: str) -> Path:
        if not isinstance(incident_id, str) or not _INCIDENT_ID_PATTERN.fullmatch(
            incident_id
        ):
            raise SafetyIncidentAcknowledgmentError(
                "incident_id has an invalid format", incident_id=incident_id
            )
        return self.archive_directory / f"{incident_id}.json"

    @staticmethod
    def _read_document(path: Path, *, unresolved_on_error: bool) -> dict[str, Any]:
        error_type = (
            UnresolvedSafetyIncidentError
            if unresolved_on_error
            else SafetyIncidentPersistenceError
        )
        try:
            size = path.stat().st_size
            if size <= 0 or size > _MAX_INCIDENT_BYTES:
                raise ValueError("file size is outside the allowed range")
            raw = path.read_text(encoding="utf-8")
            document = json.loads(
                raw,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite,
            )
            return _validate_incident(document)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise error_type(
                "safety-incident record is unreadable or invalid; control remains locked",
                details={"record_name": path.name},
            ) from error

    @staticmethod
    def _write_exclusive(path: Path, document: Mapping[str, Any]) -> None:
        encoded = _canonical_bytes(document) + b"\n"
        if len(encoded) > _MAX_INCIDENT_BYTES:
            raise SafetyIncidentPersistenceError("safety-incident record is too large")
        descriptor: int | None = None
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = None
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            raise
        except OSError as error:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise SafetyIncidentPersistenceError(
                f"cannot persist safety-incident lock: {error}",
                details={"record_name": path.name},
            ) from error
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @classmethod
    def _replace_document(cls, path: Path, document: Mapping[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            cls._write_exclusive(temporary, document)
            os.replace(temporary, path)
        except SafetyIncidentPersistenceError:
            raise
        except OSError as error:
            raise SafetyIncidentPersistenceError(
                f"cannot update safety-incident record: {error}",
                details={"record_name": path.name},
            ) from error
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def get_active(self, dut_id: str) -> Mapping[str, Any] | None:
        self._ensure_directories()
        dut_hash = dut_identity_sha256(dut_id)
        path = self._active_path_from_hash(dut_hash)
        if not path.exists():
            return None
        document = self._read_document(path, unresolved_on_error=True)
        if document["dut_identity_sha256"] != dut_hash:
            raise UnresolvedSafetyIncidentError(
                "safety-incident record does not match the DUT; control remains locked",
                incident_id=document.get("incident_id"),
                details={"record_name": path.name},
            )
        return document

    def assert_clear(self, dut_id: str) -> None:
        incident = self.get_active(dut_id)
        if incident is not None:
            raise UnresolvedSafetyIncidentError(
                "control is blocked by an unresolved uncertain-result incident; "
                "perform an independent readback and explicitly acknowledge it",
                incident_id=str(incident["incident_id"]),
                details={
                    "created_at_utc": incident["created_at_utc"],
                    "operation": incident["command"]["operation"],
                    "points": deepcopy(incident["command"]["points"]),
                    "required_action": incident["required_action"],
                },
            )

    def record_uncertain(
        self,
        *,
        dut_id: str,
        operation: str,
        command_payload: object,
        points: Sequence[Mapping[str, Any]],
        request_id: str,
        error_code: str,
        execution_uncertain: bool,
        may_still_execute: bool,
    ) -> IncidentRecordResult:
        self._ensure_directories()
        dut_hash = dut_identity_sha256(dut_id)
        active_path = self._active_path_from_hash(dut_hash)
        existing = self.get_active(dut_id)
        if existing is not None:
            return IncidentRecordResult(existing, created=False)
        if operation not in {"select_and_operate", "direct_operate"}:
            raise ValueError("operation is not a supported control operation")
        normalized_points = [
            {"type": point.get("type"), "index": point.get("index")}
            for point in points
        ]
        now = _utc_now()
        incident: dict[str, Any] = {
            "schema_version": SAFETY_INCIDENT_SCHEMA_VERSION,
            "incident_id": f"INC-{uuid.uuid4()}",
            "status": "OPEN",
            "created_at_utc": now,
            "updated_at_utc": now,
            "dut_identity_sha256": dut_hash,
            "command": {
                "operation": operation,
                "points": normalized_points,
                "payload_sha256": _sha256_json(command_payload),
            },
            "host_error": {
                "code": error_code,
                "request_id": request_id,
                "execution_uncertain": execution_uncertain,
                "may_still_execute": may_still_execute,
            },
            "required_action": "READBACK_THEN_EXPLICIT_ACKNOWLEDGMENT",
        }
        incident = _validate_incident(incident)
        try:
            self._write_exclusive(active_path, incident)
        except FileExistsError:
            existing = self.get_active(dut_id)
            if existing is None:
                raise SafetyIncidentPersistenceError(
                    "a concurrent incident lock disappeared before it could be read"
                )
            return IncidentRecordResult(existing, created=False)
        return IncidentRecordResult(incident, created=True)

    def acknowledge(
        self,
        *,
        dut_id: str,
        incident_id: str,
        acknowledged_by: str,
        readback_summary: str,
        readback: object,
        evidence_reference: str,
    ) -> Mapping[str, Any]:
        self._ensure_directories()
        dut_hash = dut_identity_sha256(dut_id)
        archive_path = self._archive_path(incident_id)
        active_path = self._active_path_from_hash(dut_hash)
        if not active_path.exists():
            if archive_path.exists():
                archived = self._read_document(archive_path, unresolved_on_error=False)
                if archived["dut_identity_sha256"] == dut_hash:
                    return archived
            raise SafetyIncidentAcknowledgmentError(
                "no active safety incident exists for this DUT",
                incident_id=incident_id,
            )
        incident = self._read_document(active_path, unresolved_on_error=True)
        if incident["dut_identity_sha256"] != dut_hash:
            raise SafetyIncidentAcknowledgmentError(
                "active incident does not match this DUT",
                incident_id=incident_id,
            )
        if incident["incident_id"] != incident_id:
            raise SafetyIncidentAcknowledgmentError(
                "incident_id does not match the active DUT lock",
                incident_id=incident_id,
                details={"active_incident_id": incident["incident_id"]},
            )
        acknowledged_by = _bounded_text(
            acknowledged_by, "acknowledged_by", maximum=128
        )
        readback_summary = _bounded_text(readback_summary, "readback_summary")
        evidence_reference = _bounded_text(
            evidence_reference, "evidence_reference"
        )
        if not isinstance(readback, Mapping) or not readback:
            raise SafetyIncidentAcknowledgmentError(
                "readback must be a non-empty object containing independently "
                "observed data",
                incident_id=incident_id,
            )
        try:
            readback_hash = _sha256_json(readback)
        except ValueError as error:
            raise SafetyIncidentAcknowledgmentError(
                f"readback is invalid: {error}", incident_id=incident_id
            ) from error
        supplied_acknowledgment = {
            "acknowledged_by": acknowledged_by,
            "readback_summary": readback_summary,
            "readback_payload_sha256": readback_hash,
            "evidence_reference": evidence_reference,
        }
        if incident["status"] == "ACKNOWLEDGED":
            existing_acknowledgment = dict(incident["acknowledgment"])
            existing_acknowledgment.pop("acknowledged_at_utc")
            if existing_acknowledgment != supplied_acknowledgment:
                raise SafetyIncidentAcknowledgmentError(
                    "incident acknowledgment retry does not match the persisted data",
                    incident_id=incident_id,
                )
            acknowledged = incident
        else:
            now = _utc_now()
            acknowledged = deepcopy(incident)
            acknowledged["status"] = "ACKNOWLEDGED"
            acknowledged["updated_at_utc"] = now
            acknowledged["acknowledgment"] = {
                "acknowledged_at_utc": now,
                **supplied_acknowledgment,
            }
            acknowledged = _validate_incident(acknowledged)
            self._replace_document(active_path, acknowledged)
        try:
            self._write_exclusive(archive_path, acknowledged)
        except FileExistsError:
            archived = self._read_document(archive_path, unresolved_on_error=False)
            if archived != acknowledged:
                raise SafetyIncidentAcknowledgmentError(
                    "archive collision detected; control remains locked",
                    incident_id=incident_id,
                )
        try:
            active_path.unlink(missing_ok=True)
        except OSError as error:
            raise SafetyIncidentPersistenceError(
                f"incident was archived but its active lock remains: {error}",
                details={"incident_id": incident_id},
            ) from error
        return deepcopy(acknowledged)
