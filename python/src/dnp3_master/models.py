"""Public configuration and diagnostic models for the native host process."""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import math
from pathlib import Path
from typing import Any, Mapping


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
        if value["return_mode"] not in {"detail", "summary"}:
            raise ValueError("read result return_mode is invalid")
        if not isinstance(value["measurements"], list):
            raise ValueError("read result measurements must be an array")
        if not isinstance(value["fragments"], list):
            raise ValueError("read result fragments must be an array")
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

        return cls(
            task_id=value["task_id"],
            task_status=value["task_status"],
            task_started=value["task_started"],
            task_destroyed=value["task_destroyed"],
            return_mode=value["return_mode"],
            measurements=measurements,
            summary=deepcopy(dict(value["summary"])),
            fragments=tuple(deepcopy(item) for item in value["fragments"]),
            iin=deepcopy(dict(value["iin"])),
            timings=deepcopy(dict(value["timings"])),
            raw=deepcopy(dict(value)),
        )

    def measurements_of_kind(self, kind: str) -> tuple[MeasurementRecord, ...]:
        """Return all detailed measurements matching one normalized kind."""

        return tuple(item for item in self.measurements if item.kind == kind)


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
    """Per-point command state and complete DNP3 Command Status."""

    header_index: int
    index: int
    state: str
    state_raw: int
    status: str
    status_raw: int
    requested: Mapping[str, Any] | None
    raw: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> CommandPointResult:
        required = {
            "header_index",
            "index",
            "state",
            "state_raw",
            "status",
            "status_raw",
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
