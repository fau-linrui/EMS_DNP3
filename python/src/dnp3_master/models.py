"""Public configuration and diagnostic models for the native host process."""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import math
from pathlib import Path
import re
from typing import Any, Mapping


_COMMAND_STATUS_2012_NAMES = {
    0: "SUCCESS",
    1: "TIMEOUT",
    2: "NO_SELECT",
    3: "FORMAT_ERROR",
    4: "NOT_SUPPORTED",
    5: "ALREADY_ACTIVE",
    6: "HARDWARE_ERROR",
    7: "LOCAL",
    8: "TOO_MANY_OBJS",
    9: "NOT_AUTHORIZED",
    10: "AUTOMATION_INHIBIT",
    11: "PROCESSING_LIMITED",
    12: "OUT_OF_RANGE",
    126: "NON_PARTICIPATING",
    127: "UNDEFINED",
}


def _command_status_name_2012(raw: int) -> str:
    return _COMMAND_STATUS_2012_NAMES.get(raw, "RESERVED")


@dataclass(frozen=True, slots=True)
class LabSafetyConfig:
    """Explicit operator/DUT authorization needed to unlock state changes."""

    operator_id: str
    dut_id: str
    allow_state_change: bool = False

    def __post_init__(self) -> None:
        _endpoint_text(self.operator_id, "operator_id", 128)
        _endpoint_text(self.dut_id, "dut_id", 128)
        if type(self.allow_state_change) is not bool:
            raise ValueError("allow_state_change must be boolean")

    def to_params(self) -> dict[str, Any]:
        return {
            "environment": "LAB",
            "allow_state_change": self.allow_state_change,
            "operator_id": self.operator_id,
            "dut_id": self.dut_id,
        }


def _milliseconds(
    value: float, field_name: str, minimum: int, maximum: int
) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number of seconds")
    try:
        normalized = float(value)
    except (OverflowError, ValueError) as error:
        raise ValueError(
            f"{field_name} must be a finite number of seconds"
        ) from error
    if not math.isfinite(normalized):
        raise ValueError(f"{field_name} must be a finite number of seconds")
    milliseconds = round(normalized * 1000)
    if milliseconds < minimum or milliseconds > maximum:
        raise ValueError(
            f"{field_name} must be between {minimum / 1000:g} and "
            f"{maximum / 1000:g} seconds"
        )
    return milliseconds


def _endpoint_text(value: str, field_name: str, maximum_bytes: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    encoded = value.encode("utf-8")
    if not encoded or len(encoded) > maximum_bytes:
        raise ValueError(
            f"{field_name} must contain between 1 and {maximum_bytes} bytes"
        )
    if not value.isascii() or any(
        ord(character) < 0x21 or ord(character) > 0x7E for character in value
    ):
        raise ValueError(f"{field_name} must contain printable non-space ASCII")
    return value


@dataclass(frozen=True, slots=True)
class TcpConnectionConfig:
    """Validated TCP initiating-endpoint settings for one DNP3 outstation."""

    host: str
    port: int = 20000
    local_adapter: str = "0.0.0.0"
    connect_timeout: float = 5.0
    retry_min: float = 1.0
    retry_max: float = 60.0
    master_address: int = 1
    outstation_address: int = 1024
    keep_alive_timeout: float = 60.0
    safety: LabSafetyConfig | None = None

    def __post_init__(self) -> None:
        _endpoint_text(self.host, "host", 253)
        _endpoint_text(self.local_adapter, "local_adapter", 64)
        if (
            isinstance(self.port, bool)
            or not isinstance(self.port, int)
            or not 1 <= self.port <= 65535
        ):
            raise ValueError("port must be an integer between 1 and 65535")
        for field_name in ("master_address", "outstation_address"):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= 65519
            ):
                raise ValueError(
                    f"{field_name} must be an individually assignable DNP3 address "
                    "between 0 and 65519"
                )
        if self.master_address == self.outstation_address:
            raise ValueError("master_address and outstation_address must differ")
        if self.safety is not None and not isinstance(self.safety, LabSafetyConfig):
            raise ValueError("safety must be a LabSafetyConfig or None")

        _milliseconds(self.connect_timeout, "connect_timeout", 50, 300000)
        retry_min_ms = _milliseconds(self.retry_min, "retry_min", 10, 300000)
        retry_max_ms = _milliseconds(self.retry_max, "retry_max", 10, 300000)
        if retry_min_ms > retry_max_ms:
            raise ValueError("retry_min must not exceed retry_max")
        _milliseconds(
            self.keep_alive_timeout,
            "keep_alive_timeout",
            1000,
            86400000,
        )

    @property
    def connect_timeout_ms(self) -> int:
        return _milliseconds(
            self.connect_timeout, "connect_timeout", 50, 300000
        )

    def to_params(self) -> dict[str, Any]:
        """Return the strict v1 host-protocol representation."""

        result = {
            "host": self.host,
            "port": self.port,
            "local_adapter": self.local_adapter,
            "connect_timeout_ms": self.connect_timeout_ms,
            "retry": {
                "min_ms": _milliseconds(
                    self.retry_min, "retry_min", 10, 300000
                ),
                "max_ms": _milliseconds(
                    self.retry_max, "retry_max", 10, 300000
                ),
            },
            "link": {
                "master_address": self.master_address,
                "outstation_address": self.outstation_address,
                "keep_alive_timeout_ms": _milliseconds(
                    self.keep_alive_timeout,
                    "keep_alive_timeout",
                    1000,
                    86400000,
                ),
            },
        }
        if self.safety is not None:
            result["safety"] = self.safety.to_params()
        return result


_READ_QUALIFIERS = frozenset(
    {"all_objects", "range8", "range16", "count8", "count16"}
)


def _unsigned_integer(
    value: object, field_name: str, minimum: int, maximum: int
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise ValueError(
            f"{field_name} must be an integer between {minimum} and {maximum}"
        )
    return value


@dataclass(frozen=True, slots=True)
class ReadHeader:
    """One strictly validated OpenDNP3 public read header."""

    group: int
    variation: int
    qualifier: str = "all_objects"
    start: int | None = None
    stop: int | None = None
    count: int | None = None

    def __post_init__(self) -> None:
        _unsigned_integer(self.group, "group", 0, 255)
        _unsigned_integer(self.variation, "variation", 0, 255)
        if self.qualifier not in _READ_QUALIFIERS:
            raise ValueError(
                "qualifier must be one of: " + ", ".join(sorted(_READ_QUALIFIERS))
            )

        if self.qualifier == "all_objects":
            if any(value is not None for value in (self.start, self.stop, self.count)):
                raise ValueError(
                    "all_objects does not accept start, stop, or count"
                )
        elif self.qualifier in {"range8", "range16"}:
            if self.count is not None:
                raise ValueError(f"{self.qualifier} does not accept count")
            maximum = 255 if self.qualifier == "range8" else 65535
            if self.start is None or self.stop is None:
                raise ValueError(f"{self.qualifier} requires start and stop")
            _unsigned_integer(self.start, "start", 0, maximum)
            _unsigned_integer(self.stop, "stop", 0, maximum)
            if self.start > self.stop:
                raise ValueError("start must not exceed stop")
        else:
            if self.start is not None or self.stop is not None:
                raise ValueError(f"{self.qualifier} does not accept start or stop")
            maximum = 255 if self.qualifier == "count8" else 65535
            if self.count is None:
                raise ValueError(f"{self.qualifier} requires count")
            _unsigned_integer(self.count, "count", 1, maximum)

        if (
            self.group == 60
            and 1 <= self.variation <= 4
            and self.qualifier in {"range8", "range16"}
        ):
            raise ValueError("DNP3 class data requests do not use range qualifiers")

    @classmethod
    def all_objects(cls, group: int, variation: int = 0) -> ReadHeader:
        return cls(group=group, variation=variation)

    @classmethod
    def range8(
        cls, group: int, variation: int, start: int, stop: int
    ) -> ReadHeader:
        return cls(
            group=group,
            variation=variation,
            qualifier="range8",
            start=start,
            stop=stop,
        )

    @classmethod
    def range16(
        cls, group: int, variation: int, start: int, stop: int
    ) -> ReadHeader:
        return cls(
            group=group,
            variation=variation,
            qualifier="range16",
            start=start,
            stop=stop,
        )

    @classmethod
    def count8(cls, group: int, variation: int, count: int) -> ReadHeader:
        return cls(
            group=group,
            variation=variation,
            qualifier="count8",
            count=count,
        )

    @classmethod
    def count16(cls, group: int, variation: int, count: int) -> ReadHeader:
        return cls(
            group=group,
            variation=variation,
            qualifier="count16",
            count=count,
        )

    def to_params(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "group": self.group,
            "variation": self.variation,
            "qualifier": self.qualifier,
        }
        if self.start is not None:
            result["start"] = self.start
        if self.stop is not None:
            result["stop"] = self.stop
        if self.count is not None:
            result["count"] = self.count
        return result


@dataclass(frozen=True, slots=True)
class MeasurementRecord:
    """Normalized measurement returned by the native OpenDNP3 host."""

    receive_seq: int
    received_monotonic_ns: int
    kind: str
    group: int
    variation: int
    qualifier: str
    qualifier_raw: int
    index: int | None
    value: Any
    flags_raw: int | None
    flags_valid: bool
    dnp3_timestamp_ms: int | None
    timestamp_quality: str
    is_event: bool
    header_index: int
    source: str
    fragment_index: int
    session_id: int | None
    raw: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> MeasurementRecord:
        required = {
            "receive_seq",
            "received_monotonic_ns",
            "kind",
            "group",
            "variation",
            "qualifier",
            "qualifier_raw",
            "index",
            "value",
            "flags_raw",
            "flags_valid",
            "dnp3_timestamp_ms",
            "timestamp_quality",
            "is_event",
            "header_index",
            "source",
            "fragment_index",
        }
        missing = required.difference(value)
        if missing:
            raise ValueError(
                "measurement result is missing fields: " + ", ".join(sorted(missing))
            )
        integer_fields = (
            "receive_seq",
            "received_monotonic_ns",
            "group",
            "variation",
            "qualifier_raw",
            "header_index",
            "fragment_index",
        )
        for field_name in integer_fields:
            if type(value[field_name]) is not int or value[field_name] < 0:
                raise ValueError(f"measurement field {field_name!r} must be non-negative")
        for field_name in ("kind", "qualifier", "timestamp_quality", "source"):
            if not isinstance(value[field_name], str) or not value[field_name]:
                raise ValueError(f"measurement field {field_name!r} must be a string")
        for field_name in ("flags_valid", "is_event"):
            if type(value[field_name]) is not bool:
                raise ValueError(f"measurement field {field_name!r} must be boolean")
        for field_name in ("index", "flags_raw", "dnp3_timestamp_ms"):
            field_value = value[field_name]
            if field_value is not None and (
                type(field_value) is not int or field_value < 0
            ):
                raise ValueError(
                    f"measurement field {field_name!r} must be null or non-negative"
                )
        session_id = value.get("session_id")
        if session_id is not None and (
            type(session_id) is not int or session_id <= 0
        ):
            raise ValueError(
                "measurement field 'session_id' must be null or a positive integer"
            )

        return cls(
            receive_seq=value["receive_seq"],
            received_monotonic_ns=value["received_monotonic_ns"],
            kind=value["kind"],
            group=value["group"],
            variation=value["variation"],
            qualifier=value["qualifier"],
            qualifier_raw=value["qualifier_raw"],
            index=value["index"],
            value=deepcopy(value["value"]),
            flags_raw=value["flags_raw"],
            flags_valid=value["flags_valid"],
            dnp3_timestamp_ms=value["dnp3_timestamp_ms"],
            timestamp_quality=value["timestamp_quality"],
            is_event=value["is_event"],
            header_index=value["header_index"],
            source=value["source"],
            fragment_index=value["fragment_index"],
            session_id=session_id,
            raw=deepcopy(dict(value)),
        )

@dataclass(frozen=True, slots=True)
class ReadTaskResult:
    """Typed result for integrity, class, and explicit read operations."""

    task_id: int
    task_status: str
    task_started: bool
    task_destroyed: bool
    return_mode: str
    measurements: tuple[MeasurementRecord, ...]
    summary: Mapping[str, Any]
    fragments: tuple[Mapping[str, Any], ...]
    iin: Mapping[str, Any]
    timings: Mapping[str, Any]
    raw: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ReadTaskResult:
        required = {
            "task_id",
            "task_status",
            "task_started",
            "task_destroyed",
            "return_mode",
            "measurements",
            "summary",
            "fragments",
            "iin",
            "timings",
        }
        missing = required.difference(value)
        if missing:
            raise ValueError(
                "read result is missing fields: " + ", ".join(sorted(missing))
            )
        if type(value["task_id"]) is not int or value["task_id"] <= 0:
            raise ValueError("read result task_id must be a positive integer")
        if not isinstance(value["task_status"], str) or not value["task_status"]:
            raise ValueError("read result task_status must be a string")
        if type(value["task_started"]) is not bool or type(value["task_destroyed"]) is not bool:
            raise ValueError("read result task flags must be boolean")
        if value["task_status"] != "SUCCESS" or value["task_started"] is not True:
            raise ValueError(
                "successful read envelope must report a started SUCCESS task"
            )
        if value["return_mode"] not in {"detail", "summary"}:
            raise ValueError("read result return_mode is invalid")
        if not isinstance(value["measurements"], list):
            raise ValueError("read result measurements must be an array")
        if not isinstance(value["fragments"], list):
            raise ValueError("read result fragments must be an array")
        if not all(isinstance(item, Mapping) for item in value["fragments"]):
            raise ValueError("read result fragments must contain only objects")
        for field_name in ("summary", "iin", "timings"):
            if not isinstance(value[field_name], Mapping):
                raise ValueError(f"read result {field_name} must be an object")
        measurements = tuple(
            MeasurementRecord.from_mapping(item)
            for item in value["measurements"]
            if isinstance(item, Mapping)
        )
        if len(measurements) != len(value["measurements"]):
            raise ValueError("read result contains a non-object measurement")
        if value["return_mode"] == "summary" and measurements:
            raise ValueError("summary read result must not contain measurement details")
        summary = value["summary"]
        for field_name in (
            "received_total",
            "stored_detail",
            "overflow",
            "fragments_received",
            "fragments_stored",
            "fragment_overflow",
            "max_fragments",
            "max_measurements",
        ):
            count = summary.get(field_name)
            if type(count) is not int or count < 0:
                raise ValueError(
                    f"read summary field {field_name!r} must be non-negative"
                )
        if summary["stored_detail"] != len(measurements):
            raise ValueError(
                "read summary stored_detail does not match measurements"
            )
        if value["return_mode"] == "detail" and summary["stored_detail"] != summary[
            "received_total"
        ]:
            raise ValueError("complete detail read did not retain every measurement")
        if summary["overflow"] or summary["fragment_overflow"]:
            raise ValueError("successful read result reports bounded-storage overflow")
        if summary["fragments_stored"] > summary["max_fragments"]:
            raise ValueError("read summary fragments_stored exceeds max_fragments")
        if summary["fragments_stored"] != len(value["fragments"]):
            raise ValueError("read summary fragments_stored does not match fragments")
        if summary["fragments_received"] < summary["fragments_stored"]:
            raise ValueError("read summary fragment counts are inconsistent")
        if summary["received_total"] > summary["max_measurements"]:
            raise ValueError("successful read received_total exceeds max_measurements")

        iin = value["iin"]
        required_iin = {
            "lsb",
            "msb",
            "raw_hex",
            "bits",
            "observations",
            "observation_store_dropped_total",
            "observation_window_dropped",
            "observation_store_capacity",
        }
        missing_iin = required_iin.difference(iin)
        if missing_iin:
            raise ValueError(
                "read result IIN is missing fields: "
                + ", ".join(sorted(missing_iin))
            )
        for field_name in ("lsb", "msb"):
            if type(iin[field_name]) is not int or not 0 <= iin[field_name] <= 255:
                raise ValueError(f"read IIN {field_name} must be an octet")
        expected_raw_hex = f"{iin['lsb']:02X}{iin['msb']:02X}"
        if iin["raw_hex"] != expected_raw_hex:
            raise ValueError("read IIN raw_hex is inconsistent with lsb/msb")
        if not isinstance(iin["bits"], list) or not all(
            isinstance(bit, str) and bit for bit in iin["bits"]
        ):
            raise ValueError("read IIN bits must be a string array")
        if not isinstance(iin["observations"], list) or not all(
            isinstance(item, Mapping) for item in iin["observations"]
        ):
            raise ValueError("read IIN observations must be an object array")
        for field_name in (
            "observation_store_dropped_total",
            "observation_window_dropped",
            "observation_store_capacity",
        ):
            count = iin[field_name]
            if type(count) is not int or count < 0:
                raise ValueError(f"read IIN {field_name} must be non-negative")
        if iin["observation_store_capacity"] < 1:
            raise ValueError("read IIN observation_store_capacity must be positive")
        if len(iin["observations"]) > iin["observation_store_capacity"]:
            raise ValueError("read IIN observations exceed their bounded capacity")
        if iin["observation_window_dropped"]:
            raise ValueError("successful read result reports lost IIN observations")

        return cls(
            task_id=value["task_id"],
            task_status=value["task_status"],
            task_started=value["task_started"],
            task_destroyed=value["task_destroyed"],
            return_mode=value["return_mode"],
            measurements=measurements,
            summary=deepcopy(dict(summary)),
            fragments=tuple(deepcopy(item) for item in value["fragments"]),
            iin=deepcopy(dict(iin)),
            timings=deepcopy(dict(value["timings"])),
            raw=deepcopy(dict(value)),
        )

    def measurements_of_kind(self, kind: str) -> tuple[MeasurementRecord, ...]:
        """Return all detailed measurements matching one normalized kind."""

        return tuple(item for item in self.measurements if item.kind == kind)


_CAPTURE_MODES = frozenset({"static_set", "event_sequence", "observation"})
_CAPTURE_SOURCES = frozenset({"solicited", "unsolicited"})
_CAPTURE_POINT_KINDS = frozenset(
    {
        "analog_command_event",
        "analog_input",
        "analog_output_status",
        "binary_command_event",
        "binary_input",
        "binary_output_status",
        "counter",
        "double_bit_binary_input",
        "frozen_counter",
        "octet_string",
        "time_and_interval",
    }
)


@dataclass(frozen=True, slots=True)
class CapturePointRange:
    """One compact indexed point range used as static capture ground truth."""

    kind: str
    start: int
    stop: int

    def __post_init__(self) -> None:
        if self.kind not in _CAPTURE_POINT_KINDS:
            raise ValueError("kind is not a supported indexed capture point type")
        _unsigned_integer(self.start, "start", 0, 65535)
        _unsigned_integer(self.stop, "stop", 0, 65535)
        if self.start > self.stop:
            raise ValueError("start must not exceed stop")

    @property
    def point_count(self) -> int:
        return self.stop - self.start + 1

    def to_params(self) -> dict[str, Any]:
        return {"kind": self.kind, "start": self.start, "stop": self.stop}


@dataclass(frozen=True, slots=True)
class CaptureEventManifest:
    """Immutable external truth identity for event-sequence reconciliation."""

    generator: str
    generator_version: str
    scenario_id: str
    seed: int
    start_sequence: int
    end_sequence: int
    event_total: int
    sha256: str
    match_rule: str = "ordered_kind_index_value"

    def __post_init__(self) -> None:
        for field_name in ("generator", "generator_version", "scenario_id"):
            _endpoint_text(getattr(self, field_name), field_name, 128)
        for field_name in ("seed", "start_sequence", "end_sequence"):
            _unsigned_integer(
                getattr(self, field_name), field_name, 0, (1 << 63) - 1
            )
        _unsigned_integer(self.event_total, "event_total", 1, 1_000_000_000)
        if (
            self.start_sequence > self.end_sequence
            or self.end_sequence - self.start_sequence + 1 != self.event_total
        ):
            raise ValueError(
                "event_total must equal end_sequence - start_sequence + 1"
            )
        if not isinstance(self.sha256, str) or re.fullmatch(
            r"[0-9a-fA-F]{64}", self.sha256
        ) is None:
            raise ValueError("sha256 must contain 64 hexadecimal characters")
        object.__setattr__(self, "sha256", self.sha256.lower())
        if self.match_rule != "ordered_kind_index_value":
            raise ValueError("match_rule must be 'ordered_kind_index_value'")

    def to_params(self) -> dict[str, Any]:
        return {
            "generator": self.generator,
            "generator_version": self.generator_version,
            "scenario_id": self.scenario_id,
            "seed": self.seed,
            "start_sequence": self.start_sequence,
            "end_sequence": self.end_sequence,
            "event_total": self.event_total,
            "sha256": self.sha256,
            "match_rule": self.match_rule,
        }


@dataclass(frozen=True, slots=True)
class CaptureConfig:
    """Strict Capture v1 configuration with explicit truth semantics."""

    mode: str
    sources: tuple[str, ...]
    duration_limit: float
    point_ranges: tuple[CapturePointRange, ...] = ()
    event_manifest: CaptureEventManifest | None = None
    mismatch_sample_limit: int = 100
    queue_capacity: int = 4096

    def __post_init__(self) -> None:
        if self.mode not in _CAPTURE_MODES:
            raise ValueError(
                "mode must be 'static_set', 'event_sequence', or 'observation'"
            )
        if isinstance(self.sources, (str, bytes)):
            raise TypeError("sources must be a tuple of capture source names")
        normalized_sources = tuple(self.sources)
        if (
            not 1 <= len(normalized_sources) <= 2
            or any(source not in _CAPTURE_SOURCES for source in normalized_sources)
            or len(set(normalized_sources)) != len(normalized_sources)
        ):
            raise ValueError(
                "sources must contain one or both unique values: solicited, unsolicited"
            )
        object.__setattr__(self, "sources", normalized_sources)
        _milliseconds(
            self.duration_limit,
            "duration_limit",
            100,
            604_800_000,
        )
        _unsigned_integer(
            self.mismatch_sample_limit,
            "mismatch_sample_limit",
            0,
            1024,
        )
        _unsigned_integer(self.queue_capacity, "queue_capacity", 1, 65536)

        if isinstance(self.point_ranges, (str, bytes)):
            raise TypeError("point_ranges must contain CapturePointRange objects")
        ranges = tuple(self.point_ranges)
        if not all(isinstance(item, CapturePointRange) for item in ranges):
            raise TypeError("point_ranges must contain CapturePointRange objects")
        if len(ranges) > 256:
            raise ValueError("point_ranges may contain at most 256 ranges")
        object.__setattr__(self, "point_ranges", ranges)

        if self.mode == "static_set":
            if not ranges:
                raise ValueError("static_set capture requires point_ranges")
            if self.event_manifest is not None:
                raise ValueError("static_set capture does not accept event_manifest")
            if sum(item.point_count for item in ranges) > 1_000_000:
                raise ValueError("static_set expected set may contain at most 1000000 points")
            ordered = sorted(ranges, key=lambda item: (item.kind, item.start))
            for previous, current in zip(ordered, ordered[1:]):
                if previous.kind == current.kind and current.start <= previous.stop:
                    raise ValueError(
                        "point_ranges must not overlap for the same point kind"
                    )
        elif self.mode == "event_sequence":
            if ranges:
                raise ValueError("event_sequence capture does not accept point_ranges")
            if not isinstance(self.event_manifest, CaptureEventManifest):
                raise ValueError(
                    "event_sequence capture requires a CaptureEventManifest"
                )
        else:
            if ranges or self.event_manifest is not None:
                raise ValueError(
                    "observation capture cannot claim point or event ground truth"
                )

    @property
    def duration_limit_ms(self) -> int:
        return _milliseconds(
            self.duration_limit,
            "duration_limit",
            100,
            604_800_000,
        )

    def to_params(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "mode": self.mode,
            "sources": list(self.sources),
            "duration_limit_ms": self.duration_limit_ms,
            "mismatch_sample_limit": self.mismatch_sample_limit,
            "queue_capacity": self.queue_capacity,
        }
        if self.mode == "static_set":
            result["expected"] = {
                "point_ranges": [item.to_params() for item in self.point_ranges]
            }
        elif self.mode == "event_sequence":
            assert self.event_manifest is not None
            result["expected"] = {"manifest": self.event_manifest.to_params()}
        return result


@dataclass(frozen=True, slots=True)
class CaptureResult:
    """Validated non-consuming Capture v1 progress or terminal snapshot."""

    capture_id: str
    session_id: int
    state: str
    valid: bool | None
    mode: str
    sources: tuple[str, ...]
    expected_total: int | None
    offered_total: int
    received_total: int
    received_unique: int | None
    duplicates: int | None
    missing: int | None
    unmatched_total: int
    fragments_total: int
    duration_ms: float
    throughput_per_sec: float
    current_queue_depth: int
    max_queue_depth: int
    queue_capacity: int
    queue_overflow: int
    invalid_reasons: tuple[str, ...]
    completeness_scope: str
    unknown_reason: str | None
    received_sequence_sha256: str | None
    sequence_match: bool | None
    canonical_record_format: str | None
    mismatch_sample: tuple[Mapping[str, Any], ...]
    timings: Mapping[str, Any]
    raw: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> CaptureResult:
        required = {
            "capture_id",
            "session_id",
            "state",
            "valid",
            "mode",
            "sources",
            "expected_total",
            "offered_total",
            "received_total",
            "received_unique",
            "duplicates",
            "missing",
            "unmatched_total",
            "fragments_total",
            "duration_ms",
            "throughput_per_sec",
            "current_queue_depth",
            "max_queue_depth",
            "queue_capacity",
            "queue_overflow",
            "discarded_on_abort",
            "invalid_reasons",
            "completeness_scope",
            "unknown_reason",
            "received_sequence_sha256",
            "sequence_match",
            "canonical_record_format",
            "mismatch_sample",
            "mismatch_sample_limit",
            "by_kind",
            "by_group_variation",
            "timings",
            "event_manifest",
        }
        missing_fields = required.difference(value)
        if missing_fields:
            raise ValueError(
                "capture result is missing fields: "
                + ", ".join(sorted(missing_fields))
            )
        capture_id = value["capture_id"]
        if not isinstance(capture_id, str) or re.fullmatch(
            r"[A-Za-z0-9._:-]{1,64}", capture_id
        ) is None:
            raise ValueError("capture result capture_id is invalid")
        if type(value["session_id"]) is not int or value["session_id"] <= 0:
            raise ValueError("capture result session_id must be positive")
        state = value["state"]
        if state not in {"ACTIVE", "FINALIZED", "TIMED_OUT", "ABORTED"}:
            raise ValueError("capture result state is invalid")
        valid = value["valid"]
        if state == "ACTIVE":
            if valid is not None:
                raise ValueError("active capture validity must be null")
        elif type(valid) is not bool:
            raise ValueError("terminal capture validity must be boolean")
        if state in {"TIMED_OUT", "ABORTED"} and valid is not False:
            raise ValueError("timed-out or aborted capture must be invalid")
        mode = value["mode"]
        if mode not in _CAPTURE_MODES:
            raise ValueError("capture result mode is invalid")
        sources = value["sources"]
        if (
            not isinstance(sources, list)
            or not 1 <= len(sources) <= 2
            or any(source not in _CAPTURE_SOURCES for source in sources)
            or len(set(sources)) != len(sources)
        ):
            raise ValueError("capture result sources are invalid")

        nullable_counts = ("expected_total", "received_unique", "duplicates", "missing")
        for field_name in nullable_counts:
            field_value = value[field_name]
            if field_value is not None and (
                type(field_value) is not int or field_value < 0
            ):
                raise ValueError(f"capture result {field_name} must be null or non-negative")
        count_fields = (
            "offered_total",
            "received_total",
            "unmatched_total",
            "fragments_total",
            "current_queue_depth",
            "max_queue_depth",
            "queue_capacity",
            "queue_overflow",
            "discarded_on_abort",
            "mismatch_sample_limit",
        )
        for field_name in count_fields:
            field_value = value[field_name]
            if type(field_value) is not int or field_value < 0:
                raise ValueError(f"capture result {field_name} must be non-negative")
        if value["queue_capacity"] < 1 or value["queue_capacity"] > 65536:
            raise ValueError("capture result queue_capacity is invalid")
        if not (
            value["current_queue_depth"]
            <= value["max_queue_depth"]
            <= value["queue_capacity"]
        ):
            raise ValueError("capture result queue depths are inconsistent")
        if value["received_total"] > value["offered_total"]:
            raise ValueError("capture received_total exceeds offered_total")
        if value["mismatch_sample_limit"] > 1024:
            raise ValueError("capture mismatch_sample_limit is invalid")
        for field_name in ("duration_ms", "throughput_per_sec"):
            field_value = value[field_name]
            if (
                isinstance(field_value, bool)
                or not isinstance(field_value, (int, float))
                or not math.isfinite(float(field_value))
                or field_value < 0
            ):
                raise ValueError(f"capture result {field_name} must be finite and non-negative")
        reasons = value["invalid_reasons"]
        if not isinstance(reasons, list) or not all(
            isinstance(reason, str) and reason for reason in reasons
        ) or len(set(reasons)) != len(reasons):
            raise ValueError("capture invalid_reasons must be a unique string array")
        samples = value["mismatch_sample"]
        if (
            not isinstance(samples, list)
            or len(samples) > value["mismatch_sample_limit"]
            or not all(isinstance(sample, Mapping) for sample in samples)
        ):
            raise ValueError("capture mismatch samples are invalid or unbounded")
        if not isinstance(value["timings"], Mapping):
            raise ValueError("capture timings must be an object")
        for field_name in ("by_kind", "by_group_variation"):
            dimension = value[field_name]
            if (
                not isinstance(dimension, Mapping)
                or len(dimension) > 256
                or any(
                    not isinstance(key, str)
                    or not key
                    or type(count) is not int
                    or count < 0
                    for key, count in dimension.items()
                )
            ):
                raise ValueError(f"capture result {field_name} counters are invalid")
        if valid is True and (reasons or value["queue_overflow"] != 0):
            raise ValueError("valid capture result reports an invalid condition")

        received_digest = value["received_sequence_sha256"]
        if received_digest is not None and (
            not isinstance(received_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", received_digest) is None
        ):
            raise ValueError(
                "capture received_sequence_sha256 must be null or lowercase SHA-256"
            )
        sequence_match = value["sequence_match"]
        if sequence_match is not None and type(sequence_match) is not bool:
            raise ValueError("capture sequence_match must be null or boolean")
        canonical_format = value["canonical_record_format"]
        if canonical_format not in {
            None,
            "compact-json-array-[kind,index,value]-plus-LF",
        }:
            raise ValueError("capture canonical_record_format is invalid")

        if mode == "static_set":
            if any(value[name] is None for name in nullable_counts):
                raise ValueError("static_set capture must report provable completeness counts")
            if value["completeness_scope"] != "NATIVE_STATIC_SET" or value["unknown_reason"] is not None:
                raise ValueError("static_set capture completeness scope is inconsistent")
            if value["received_unique"] > value["expected_total"] or value[
                "missing"
            ] != value["expected_total"] - value["received_unique"]:
                raise ValueError("static_set capture completeness counts are inconsistent")
            if value["event_manifest"] is not None:
                raise ValueError("static_set capture must not report an event manifest")
            if any(
                item is not None
                for item in (received_digest, sequence_match, canonical_format)
            ):
                raise ValueError("static_set capture must not report event sequence proof")
        else:
            if any(value[name] is not None for name in ("received_unique", "duplicates", "missing")):
                raise ValueError("capture without native point truth must use null completeness counts")
            if mode == "event_sequence":
                manifest_value = value["event_manifest"]
                if not isinstance(manifest_value, Mapping):
                    raise ValueError("event capture must report its truth manifest")
                manifest_fields = {
                    "generator",
                    "generator_version",
                    "scenario_id",
                    "seed",
                    "start_sequence",
                    "end_sequence",
                    "event_total",
                    "sha256",
                    "match_rule",
                }
                if set(manifest_value) != manifest_fields:
                    raise ValueError("event capture manifest fields are invalid")
                manifest = CaptureEventManifest(**dict(manifest_value))
                if value["expected_total"] != manifest.event_total:
                    raise ValueError("event capture expected_total does not match manifest")
                if canonical_format != "compact-json-array-[kind,index,value]-plus-LF":
                    raise ValueError("event capture must declare its canonical record format")
                if sequence_match is True:
                    if (
                        received_digest != manifest.sha256
                        or value["received_total"] != manifest.event_total
                        or value["completeness_scope"]
                        != "EXTERNAL_EVENT_MANIFEST_MATCHED"
                        or value["unknown_reason"] is not None
                    ):
                        raise ValueError("matched event capture proof is inconsistent")
                elif sequence_match is False:
                    if (
                        received_digest is None
                        or value["completeness_scope"]
                        != "EXTERNAL_EVENT_MANIFEST_REQUIRED"
                        or value["unknown_reason"]
                        != "EVENT_SEQUENCE_MISMATCH_REQUIRES_MANIFEST_DIFF"
                        or valid is not False
                    ):
                        raise ValueError("mismatched event capture proof is inconsistent")
                elif (
                    received_digest is not None
                    or value["completeness_scope"]
                    != "EXTERNAL_EVENT_MANIFEST_REQUIRED"
                    or value["unknown_reason"] != "EVENT_SEQUENCE_NOT_FINALIZED"
                    or state == "FINALIZED"
                ):
                    raise ValueError("unfinished event capture proof is inconsistent")
            else:
                if value["completeness_scope"] != "OBSERVATION_ONLY":
                    raise ValueError("observation capture completeness scope is inconsistent")
                if value["expected_total"] is not None or value["event_manifest"] is not None:
                    raise ValueError("observation capture must not report ground truth")
                if value["unknown_reason"] != "UNKNOWN_WITHOUT_GROUND_TRUTH_MATCH":
                    raise ValueError(
                        "observation capture must explain unknown completeness"
                    )
                if any(
                    item is not None
                    for item in (received_digest, sequence_match, canonical_format)
                ):
                    raise ValueError(
                        "observation capture must not report event sequence proof"
                    )

        if mode == "static_set":
            truth_complete = (
                value["missing"] == 0
                and value["duplicates"] == 0
                and value["unmatched_total"] == 0
            )
        elif mode == "event_sequence":
            truth_complete = sequence_match is True
        else:
            truth_complete = True
        expected_valid = (
            state == "FINALIZED"
            and truth_complete
            and not reasons
            and value["queue_overflow"] == 0
        )
        if valid is not expected_valid and state != "ACTIVE":
            raise ValueError("capture terminal validity is inconsistent with truth")

        return cls(
            capture_id=capture_id,
            session_id=value["session_id"],
            state=state,
            valid=valid,
            mode=mode,
            sources=tuple(sources),
            expected_total=value["expected_total"],
            offered_total=value["offered_total"],
            received_total=value["received_total"],
            received_unique=value["received_unique"],
            duplicates=value["duplicates"],
            missing=value["missing"],
            unmatched_total=value["unmatched_total"],
            fragments_total=value["fragments_total"],
            duration_ms=float(value["duration_ms"]),
            throughput_per_sec=float(value["throughput_per_sec"]),
            current_queue_depth=value["current_queue_depth"],
            max_queue_depth=value["max_queue_depth"],
            queue_capacity=value["queue_capacity"],
            queue_overflow=value["queue_overflow"],
            invalid_reasons=tuple(reasons),
            completeness_scope=value["completeness_scope"],
            unknown_reason=value["unknown_reason"],
            received_sequence_sha256=received_digest,
            sequence_match=sequence_match,
            canonical_record_format=canonical_format,
            mismatch_sample=tuple(deepcopy(sample) for sample in samples),
            timings=deepcopy(dict(value["timings"])),
            raw=deepcopy(dict(value)),
        )


@dataclass(frozen=True, slots=True)
class UnsolicitedControlResult:
    """Typed result for one explicit Enable/Disable Unsolicited task."""

    task_id: int
    task_status: str
    task_started: bool
    task_destroyed: bool
    action: str
    classes: tuple[int, ...]
    timings: Mapping[str, Any]
    raw: Mapping[str, Any]

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any]
    ) -> UnsolicitedControlResult:
        required = {
            "task_id",
            "task_status",
            "task_started",
            "task_destroyed",
            "action",
            "classes",
            "timings",
        }
        missing = required.difference(value)
        if missing:
            raise ValueError(
                "unsolicited control result is missing fields: "
                + ", ".join(sorted(missing))
            )
        if type(value["task_id"]) is not int or value["task_id"] <= 0:
            raise ValueError(
                "unsolicited control result task_id must be a positive integer"
            )
        if not isinstance(value["task_status"], str) or not value["task_status"]:
            raise ValueError(
                "unsolicited control result task_status must be a string"
            )
        if (
            type(value["task_started"]) is not bool
            or type(value["task_destroyed"]) is not bool
        ):
            raise ValueError("unsolicited control result task flags must be boolean")
        if value["action"] not in {"enable", "disable"}:
            raise ValueError("unsolicited control result action is invalid")
        classes = value["classes"]
        if (
            not isinstance(classes, list)
            or not 1 <= len(classes) <= 3
            or any(type(item) is not int or item not in {1, 2, 3} for item in classes)
            or len(set(classes)) != len(classes)
        ):
            raise ValueError("unsolicited control result classes are invalid")
        if not isinstance(value["timings"], Mapping):
            raise ValueError("unsolicited control result timings must be an object")
        return cls(
            task_id=value["task_id"],
            task_status=value["task_status"],
            task_started=value["task_started"],
            task_destroyed=value["task_destroyed"],
            action=value["action"],
            classes=tuple(classes),
            timings=deepcopy(dict(value["timings"])),
            raw=deepcopy(dict(value)),
        )


@dataclass(frozen=True, slots=True)
class UnsolicitedBatchResult:
    """A bounded batch consumed from the persistent unsolicited SOE queue."""

    session_id: int
    enabled: bool
    classes: tuple[int, ...]
    measurements: tuple[MeasurementRecord, ...]
    timed_out: bool
    summary: Mapping[str, Any]
    raw: Mapping[str, Any]

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any]
    ) -> UnsolicitedBatchResult:
        required = {
            "session_id",
            "enabled",
            "classes",
            "measurements",
            "timed_out",
            "summary",
        }
        missing = required.difference(value)
        if missing:
            raise ValueError(
                "unsolicited batch result is missing fields: "
                + ", ".join(sorted(missing))
            )
        if type(value["session_id"]) is not int or value["session_id"] <= 0:
            raise ValueError("unsolicited batch session_id must be a positive integer")
        if type(value["enabled"]) is not bool or type(value["timed_out"]) is not bool:
            raise ValueError("unsolicited batch state flags must be boolean")
        classes = value["classes"]
        if (
            not isinstance(classes, list)
            or len(classes) > 3
            or any(type(item) is not int or item not in {1, 2, 3} for item in classes)
            or len(set(classes)) != len(classes)
        ):
            raise ValueError("unsolicited batch classes are invalid")
        if value["enabled"] != bool(classes):
            raise ValueError("unsolicited batch enabled/classes state is inconsistent")
        raw_measurements = value["measurements"]
        if not isinstance(raw_measurements, list):
            raise ValueError("unsolicited batch measurements must be an array")
        measurements = tuple(
            MeasurementRecord.from_mapping(item)
            for item in raw_measurements
            if isinstance(item, Mapping)
        )
        if len(measurements) != len(raw_measurements):
            raise ValueError("unsolicited batch contains a non-object measurement")
        if any(
            item.source != "unsolicited"
            or item.session_id != value["session_id"]
            for item in measurements
        ):
            raise ValueError(
                "unsolicited batch measurement source/session is inconsistent"
            )
        if not isinstance(value["summary"], Mapping):
            raise ValueError("unsolicited batch summary must be an object")
        return cls(
            session_id=value["session_id"],
            enabled=value["enabled"],
            classes=tuple(classes),
            measurements=measurements,
            timed_out=value["timed_out"],
            summary=deepcopy(dict(value["summary"])),
            raw=deepcopy(dict(value)),
        )

    def measurements_of_kind(self, kind: str) -> tuple[MeasurementRecord, ...]:
        """Return all detailed measurements matching one normalized kind."""

        return tuple(item for item in self.measurements if item.kind == kind)


_CROB_OPERATIONS = frozenset(
    {"null", "pulse_on", "pulse_off", "latch_on", "latch_off"}
)
_TRIP_CLOSE_SELECTIONS = frozenset({"null", "close", "trip"})
_ANALOG_COMMAND_TYPES = frozenset(
    {
        "analog_output_int16",
        "analog_output_int32",
        "analog_output_float32",
        "analog_output_double64",
    }
)


@dataclass(frozen=True, slots=True)
class CrobCommand:
    """Strict Group 12 Variation 1 Control Relay Output Block request."""

    index: int
    operation: str
    trip_close: str = "null"
    clear: bool = False
    count: int = 1
    on_time_ms: int = 100
    off_time_ms: int = 100

    def __post_init__(self) -> None:
        _unsigned_integer(self.index, "index", 0, 65535)
        if self.operation not in _CROB_OPERATIONS:
            raise ValueError(
                "operation must be one of: " + ", ".join(sorted(_CROB_OPERATIONS))
            )
        if self.trip_close not in _TRIP_CLOSE_SELECTIONS:
            raise ValueError(
                "trip_close must be one of: "
                + ", ".join(sorted(_TRIP_CLOSE_SELECTIONS))
            )
        if type(self.clear) is not bool:
            raise ValueError("clear must be boolean")
        _unsigned_integer(self.count, "count", 1, 255)
        _unsigned_integer(self.on_time_ms, "on_time_ms", 0, 0xFFFFFFFF)
        _unsigned_integer(self.off_time_ms, "off_time_ms", 0, 0xFFFFFFFF)

    def to_params(self) -> dict[str, Any]:
        return {
            "type": "crob",
            "index": self.index,
            "operation": self.operation,
            "trip_close": self.trip_close,
            "clear": self.clear,
            "count": self.count,
            "on_time_ms": self.on_time_ms,
            "off_time_ms": self.off_time_ms,
        }


@dataclass(frozen=True, slots=True)
class AnalogOutputCommand:
    """One strictly typed Group 41 analog output command."""

    index: int
    value: int | float
    command_type: str

    def __post_init__(self) -> None:
        _unsigned_integer(self.index, "index", 0, 65535)
        if self.command_type not in _ANALOG_COMMAND_TYPES:
            raise ValueError(
                "command_type must be one of: "
                + ", ".join(sorted(_ANALOG_COMMAND_TYPES))
            )
        if self.command_type == "analog_output_int16":
            if type(self.value) is not int or not -(2**15) <= self.value <= 2**15 - 1:
                raise ValueError("int16 analog output value is out of range")
        elif self.command_type == "analog_output_int32":
            if type(self.value) is not int or not -(2**31) <= self.value <= 2**31 - 1:
                raise ValueError("int32 analog output value is out of range")
        else:
            if isinstance(self.value, bool) or not isinstance(
                self.value, (int, float)
            ):
                raise ValueError("floating analog output value must be finite")
            try:
                normalized = float(self.value)
            except (OverflowError, ValueError) as error:
                raise ValueError(
                    "floating analog output value must be finite"
                ) from error
            if not math.isfinite(normalized):
                raise ValueError("floating analog output value must be finite")
            if (
                self.command_type == "analog_output_float32"
                and abs(normalized) > 3.4028234663852886e38
            ):
                raise ValueError("float32 analog output value is out of range")

    @classmethod
    def int16(cls, index: int, value: int) -> AnalogOutputCommand:
        return cls(index=index, value=value, command_type="analog_output_int16")

    @classmethod
    def int32(cls, index: int, value: int) -> AnalogOutputCommand:
        return cls(index=index, value=value, command_type="analog_output_int32")

    @classmethod
    def float32(cls, index: int, value: float) -> AnalogOutputCommand:
        return cls(index=index, value=value, command_type="analog_output_float32")

    @classmethod
    def double64(cls, index: int, value: float) -> AnalogOutputCommand:
        return cls(index=index, value=value, command_type="analog_output_double64")

    def to_params(self) -> dict[str, Any]:
        return {"type": self.command_type, "index": self.index, "value": self.value}


@dataclass(frozen=True, slots=True)
class CommandPointResult:
    """Per-point command state with an IEEE 1815-2012 status view."""

    header_index: int
    index: int
    state: str
    state_raw: int
    status: str
    status_raw: int
    requested: Mapping[str, Any] | None
    raw: Mapping[str, Any]

    @property
    def status_edition(self) -> str:
        return str(self.raw.get("status_edition", "IEEE1815-2012"))

    @property
    def status_backend(self) -> str:
        return str(self.raw.get("status_backend", self.status))

    @property
    def status_reserved_2012(self) -> bool:
        return 13 <= self.status_raw <= 125

    @property
    def status_wire_raw_unambiguous(self) -> bool:
        return bool(
            self.raw.get("status_wire_raw_unambiguous", self.status_raw != 127)
        )

    @property
    def requires_manual_readback(self) -> bool:
        """Return the fail-closed 2012 safety decision for this point status."""

        return (
            self.status_raw == 1
            or self.status_reserved_2012
            or not self.status_wire_raw_unambiguous
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> CommandPointResult:
        required = {
            "header_index",
            "index",
            "state",
            "state_raw",
            "status",
            "status_raw",
            "status_edition",
            "status_backend",
            "status_reserved_2012",
            "status_wire_raw_unambiguous",
            "requested",
        }
        missing = required.difference(value)
        if missing:
            raise ValueError(
                "command point result is missing fields: " + ", ".join(sorted(missing))
            )
        for field_name in ("header_index", "index", "state_raw", "status_raw"):
            if type(value[field_name]) is not int or value[field_name] < 0:
                raise ValueError(
                    f"command point field {field_name!r} must be non-negative"
                )
        for field_name in ("state", "status"):
            if not isinstance(value[field_name], str) or not value[field_name]:
                raise ValueError(f"command point field {field_name!r} must be a string")
        if value["status_raw"] > 127:
            raise ValueError("command point status_raw must be between 0 and 127")
        expected_status = _command_status_name_2012(value["status_raw"])
        if value["status"] != expected_status:
            raise ValueError(
                "command point status does not match IEEE 1815-2012: "
                f"raw={value['status_raw']}, expected={expected_status!r}"
            )
        if value["status_edition"] != "IEEE1815-2012":
            raise ValueError("command point status_edition must be IEEE1815-2012")
        if not isinstance(value["status_backend"], str) or not value["status_backend"]:
            raise ValueError("command point status_backend must be a string")
        if type(value["status_reserved_2012"]) is not bool or value[
            "status_reserved_2012"
        ] != (13 <= value["status_raw"] <= 125):
            raise ValueError(
                "command point status_reserved_2012 is inconsistent with status_raw"
            )
        if type(value["status_wire_raw_unambiguous"]) is not bool or value[
            "status_wire_raw_unambiguous"
        ] != (value["status_raw"] != 127):
            raise ValueError(
                "command point status_wire_raw_unambiguous is inconsistent with "
                "the OpenDNP3 3.1.2 decoded status"
            )
        requested = value["requested"]
        if requested is not None and not isinstance(requested, Mapping):
            raise ValueError("command point requested field must be an object or null")
        return cls(
            header_index=value["header_index"],
            index=value["index"],
            state=value["state"],
            state_raw=value["state_raw"],
            status=value["status"],
            status_raw=value["status_raw"],
            requested=(None if requested is None else deepcopy(dict(requested))),
            raw=deepcopy(dict(value)),
        )


@dataclass(frozen=True, slots=True)
class CommandTaskResult:
    """Typed batch result that never folds per-point statuses into one boolean."""

    task_id: int
    mode: str
    task_status: str
    task_started: bool
    task_destroyed: bool
    all_success: bool
    execution_uncertain: bool
    point_results: tuple[CommandPointResult, ...]
    summary: Mapping[str, Any]
    timings: Mapping[str, Any]
    raw: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> CommandTaskResult:
        required = {
            "task_id",
            "mode",
            "response_mode",
            "task_status",
            "task_started",
            "task_destroyed",
            "all_success",
            "execution_uncertain",
            "point_results",
            "summary",
            "timings",
        }
        missing = required.difference(value)
        if missing:
            raise ValueError(
                "command result is missing fields: " + ", ".join(sorted(missing))
            )
        if type(value["task_id"]) is not int or value["task_id"] <= 0:
            raise ValueError("command task_id must be a positive integer")
        for field_name in ("mode", "task_status"):
            if not isinstance(value[field_name], str) or not value[field_name]:
                raise ValueError(f"command field {field_name!r} must be a string")
        if value["response_mode"] != "response":
            raise ValueError("command response_mode must be 'response'")
        for field_name in (
            "task_started",
            "task_destroyed",
            "all_success",
            "execution_uncertain",
        ):
            if type(value[field_name]) is not bool:
                raise ValueError(f"command field {field_name!r} must be boolean")
        if not isinstance(value["point_results"], list):
            raise ValueError("command point_results must be an array")
        for field_name in ("summary", "timings"):
            if not isinstance(value[field_name], Mapping):
                raise ValueError(f"command {field_name} must be an object")
        points = tuple(
            CommandPointResult.from_mapping(item)
            for item in value["point_results"]
            if isinstance(item, Mapping)
        )
        if len(points) != len(value["point_results"]):
            raise ValueError("command result contains a non-object point result")
        summary = value["summary"]
        summary_counts: dict[str, int] = {}
        for field_name in (
            "requested_points",
            "returned_points",
            "successful_points",
            "failed_points",
        ):
            count = summary.get(field_name)
            if type(count) is not int or count < 0:
                raise ValueError(
                    f"command summary field {field_name!r} must be non-negative"
                )
            summary_counts[field_name] = count
        if summary_counts["returned_points"] != len(points):
            raise ValueError(
                "command summary returned_points does not match point_results"
            )
        if (
            summary_counts["successful_points"]
            + summary_counts["failed_points"]
            != summary_counts["returned_points"]
        ):
            raise ValueError(
                "command summary successful/failed counts are inconsistent"
            )
        successful_points = sum(
            point.state == "SUCCESS" and point.status_raw == 0 for point in points
        )
        if successful_points != summary_counts["successful_points"]:
            raise ValueError(
                "command summary successful_points does not match point_results"
            )
        expected_all_success = (
            value["task_status"] == "SUCCESS"
            and value["task_started"] is True
            and summary_counts["requested_points"] == len(points)
            and successful_points == len(points)
        )
        if value["all_success"] != expected_all_success:
            raise ValueError("command all_success is inconsistent with task/point results")
        point_keys = {(point.header_index, point.index) for point in points}
        if len(point_keys) != len(points):
            raise ValueError("command result repeats a header/index point result")
        return cls(
            task_id=value["task_id"],
            mode=value["mode"],
            task_status=value["task_status"],
            task_started=value["task_started"],
            task_destroyed=value["task_destroyed"],
            all_success=value["all_success"],
            execution_uncertain=value["execution_uncertain"],
            point_results=points,
            summary=deepcopy(dict(value["summary"])),
            timings=deepcopy(dict(value["timings"])),
            raw=deepcopy(dict(value)),
        )


@dataclass(frozen=True, slots=True)
class HostProcessConfig:
    """Immutable process settings suitable for a pytest fixture or context manager."""

    executable: Path
    arguments: tuple[str, ...] = ()
    working_directory: Path | None = None
    environment: Mapping[str, str] | None = None
    safety_incident_directory: Path | None = None
    startup_timeout: float = 5.0
    request_timeout: float = 10.0
    shutdown_timeout: float = 2.0
    diagnostic_tail_bytes: int = 64 * 1024
    max_response_bytes: int = 16 * 1024 * 1024
    expected_host_version: str | None = "0.6.0"
    expected_capability_matrix_sha256: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "executable",
            Path(self.executable).expanduser().resolve(strict=False),
        )
        object.__setattr__(self, "arguments", tuple(str(value) for value in self.arguments))
        if self.working_directory is not None:
            object.__setattr__(
                self,
                "working_directory",
                Path(self.working_directory).expanduser().resolve(strict=False),
            )
        if self.environment is not None:
            normalized_environment = {
                str(key): str(value) for key, value in self.environment.items()
            }
            object.__setattr__(self, "environment", normalized_environment)
        if self.safety_incident_directory is not None:
            object.__setattr__(
                self,
                "safety_incident_directory",
                Path(self.safety_incident_directory)
                .expanduser()
                .resolve(strict=False),
            )

        for field_name in (
            "startup_timeout",
            "request_timeout",
            "shutdown_timeout",
        ):
            value = getattr(self, field_name)
            try:
                normalized = float(value)
            except (TypeError, ValueError, OverflowError):
                normalized = math.nan
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(normalized)
                or normalized <= 0
            ):
                raise ValueError(f"{field_name} must be a positive finite number")
        if (
            isinstance(self.diagnostic_tail_bytes, bool)
            or not isinstance(self.diagnostic_tail_bytes, int)
            or self.diagnostic_tail_bytes < 256
        ):
            raise ValueError("diagnostic_tail_bytes must be at least 256")
        if (
            isinstance(self.max_response_bytes, bool)
            or not isinstance(self.max_response_bytes, int)
            or self.max_response_bytes < 64
        ):
            raise ValueError("max_response_bytes must be at least 64")
        if self.expected_host_version is not None and (
            not isinstance(self.expected_host_version, str)
            or not self.expected_host_version.strip()
        ):
            raise ValueError("expected_host_version must be a non-empty string or None")
        if self.expected_capability_matrix_sha256 is not None:
            if (
                not isinstance(self.expected_capability_matrix_sha256, str)
                or re.fullmatch(
                    r"[0-9a-fA-F]{64}",
                    self.expected_capability_matrix_sha256,
                )
                is None
            ):
                raise ValueError(
                    "expected_capability_matrix_sha256 must be 64 hexadecimal "
                    "characters or None"
                )
            object.__setattr__(
                self,
                "expected_capability_matrix_sha256",
                self.expected_capability_matrix_sha256.lower(),
            )


@dataclass(frozen=True, slots=True)
class HostProcessDiagnostics:
    """Bounded evidence captured from the latest process lifecycle."""

    pid: int | None
    returncode: int | None
    stdout_tail: str
    stderr_tail: str
    job_object_assigned: bool
    cleanup_error: str | None = None
    dump_path: Path | None = None
