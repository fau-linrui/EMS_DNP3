"""Strict machine-readable Device Profile/PICS model for EMS test selection."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import AbstractSet, Any, Mapping


EMS_PROFILE_SCHEMA_VERSION = 1
EMS_PICS_STATUSES = frozenset({"SUPPORTED", "NOT_SUPPORTED", "UNKNOWN"})
CAPABILITY_ID_PATTERN = re.compile(r"^[A-Z0-9]+(?:[._-][A-Z0-9]+)*$")
_MAX_PROFILE_BYTES = 1024 * 1024
_MAX_CAPABILITY_ID_CHARACTERS = 256


class EmsProfileError(ValueError):
    """A stable, user-facing EMS Profile/PICS validation failure."""


@dataclass(frozen=True, slots=True)
class EmsDeviceIdentity:
    vendor: str
    model: str
    firmware: str
    profile_revision: str

    def to_mapping(self) -> dict[str, str]:
        return {
            "vendor": self.vendor,
            "model": self.model,
            "firmware": self.firmware,
            "profile_revision": self.profile_revision,
        }


@dataclass(frozen=True, slots=True)
class EmsProfile:
    source_path: Path
    device: EmsDeviceIdentity
    capabilities: Mapping[str, str]
    notes: str | None = None
    schema_version: int = EMS_PROFILE_SCHEMA_VERSION

    def status(self, capability_id: str) -> str | None:
        return self.capabilities.get(capability_id)

    def to_mapping(self) -> dict[str, object]:
        result: dict[str, object] = {
            "schema_version": self.schema_version,
            "device": self.device.to_mapping(),
            "capabilities": dict(self.capabilities),
        }
        if self.notes is not None:
            result["notes"] = self.notes
        return result


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _exact_fields(
    value: object,
    *,
    context: str,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EmsProfileError(f"{context} must be a JSON object")
    fields = set(value)
    missing = required.difference(fields)
    unknown = fields.difference(required | optional)
    if missing:
        raise EmsProfileError(
            f"{context} is missing fields: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise EmsProfileError(
            f"{context} has unknown fields: {', '.join(sorted(unknown))}"
        )
    return value


def _bounded_text(value: object, *, context: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
    ):
        raise EmsProfileError(
            f"{context} must contain between 1 and {maximum} characters"
        )
    return value


def load_ems_profile(
    path: str | Path,
    *,
    known_capability_ids: AbstractSet[str] | None = None,
) -> EmsProfile:
    """Load and strictly validate one v1 EMS Device Profile/PICS JSON file."""

    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise EmsProfileError(f"EMS Profile/PICS does not exist: {source}")
    try:
        size = source.stat().st_size
    except OSError as error:
        raise EmsProfileError(
            f"cannot inspect EMS Profile/PICS {source}: {error}"
        ) from error
    if size > _MAX_PROFILE_BYTES:
        raise EmsProfileError(
            f"EMS Profile/PICS exceeds {_MAX_PROFILE_BYTES} bytes: {source}"
        )
    try:
        raw = source.read_text(encoding="utf-8")
        document = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise EmsProfileError(
            f"EMS Profile/PICS is not valid strict UTF-8 JSON: {error}"
        ) from error

    root = _exact_fields(
        document,
        context="EMS Profile/PICS root",
        required=frozenset({"schema_version", "device", "capabilities"}),
        optional=frozenset({"notes"}),
    )
    schema_version = root["schema_version"]
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != EMS_PROFILE_SCHEMA_VERSION
    ):
        raise EmsProfileError(
            f"EMS Profile/PICS schema_version must be {EMS_PROFILE_SCHEMA_VERSION}"
        )

    device_document = _exact_fields(
        root["device"],
        context="EMS Profile/PICS device",
        required=frozenset(
            {"vendor", "model", "firmware", "profile_revision"}
        ),
    )
    device = EmsDeviceIdentity(
        vendor=_bounded_text(
            device_document["vendor"], context="device.vendor", maximum=256
        ),
        model=_bounded_text(
            device_document["model"], context="device.model", maximum=256
        ),
        firmware=_bounded_text(
            device_document["firmware"], context="device.firmware", maximum=256
        ),
        profile_revision=_bounded_text(
            device_document["profile_revision"],
            context="device.profile_revision",
            maximum=256,
        ),
    )

    capabilities_document = root["capabilities"]
    if not isinstance(capabilities_document, dict) or not capabilities_document:
        raise EmsProfileError(
            "EMS Profile/PICS capabilities must be a non-empty JSON object"
        )
    capabilities: dict[str, str] = {}
    for capability_id, status in capabilities_document.items():
        if (
            not isinstance(capability_id, str)
            or len(capability_id) > _MAX_CAPABILITY_ID_CHARACTERS
            or not CAPABILITY_ID_PATTERN.fullmatch(capability_id)
        ):
            raise EmsProfileError(
                "EMS Profile/PICS capability IDs must use the capability-matrix syntax"
            )
        if status not in EMS_PICS_STATUSES:
            raise EmsProfileError(
                f"EMS Profile/PICS capability {capability_id!r} has invalid "
                f"status {status!r}"
            )
        if (
            known_capability_ids is not None
            and capability_id not in known_capability_ids
        ):
            raise EmsProfileError(
                f"EMS Profile/PICS capability {capability_id!r} is not present "
                "in the capability matrix"
            )
        capabilities[capability_id] = status

    notes_value = root.get("notes")
    notes = (
        None
        if notes_value is None
        else _bounded_text(notes_value, context="notes", maximum=4096)
    )
    return EmsProfile(
        source_path=source,
        device=device,
        capabilities=MappingProxyType(capabilities),
        notes=notes,
    )


__all__ = [
    "CAPABILITY_ID_PATTERN",
    "EMS_PICS_STATUSES",
    "EMS_PROFILE_SCHEMA_VERSION",
    "EmsDeviceIdentity",
    "EmsProfile",
    "EmsProfileError",
    "load_ems_profile",
]
