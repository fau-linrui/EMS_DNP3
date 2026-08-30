"""Strict, fail-closed plans for real-EMS pytest scenario templates."""

from __future__ import annotations

from dataclasses import dataclass
from math import isclose, isfinite
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping

from .models import AnalogOutputCommand, CrobCommand, MeasurementRecord
from .point_table import PointDefinition, PointTable


EMS_TEST_PLAN_SCHEMA_VERSION = 1

_MAX_PLAN_BYTES = 1024 * 1024
_MAX_SCENARIOS_PER_KIND = 128
_MAX_REFERENCED_POINTS = 256
_SCENARIO_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_COMMAND_TYPES = frozenset(
    {
        "crob",
        "analog_output_int16",
        "analog_output_int32",
        "analog_output_float32",
        "analog_output_double64",
    }
)
_NUMERIC_POINT_TYPES = frozenset(
    {"analog_input", "analog_output_status", "counter", "frozen_counter"}
)
_BINARY_POINT_TYPES = frozenset({"binary_input", "binary_output_status"})
_PLACEHOLDER_AUTHORIZATION_TOKENS = (
    "FILL_ME",
    "TODO",
    "TBD",
    "PLACEHOLDER",
    "EXAMPLE",
)


class EmsTestPlanError(ValueError):
    """A stable, user-facing EMS test-plan validation failure."""


@dataclass(frozen=True, slots=True)
class ValueExpectation:
    """An exact value with optional absolute numeric tolerance."""

    value: bool | int | float | str
    absolute_tolerance: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.value, (bool, int, float, str)):
            raise ValueError("expected value must be boolean, numeric, or text")
        if isinstance(self.value, (int, float)) and not isinstance(self.value, bool):
            try:
                normalized_value = float(self.value)
            except (OverflowError, TypeError, ValueError) as error:
                raise ValueError("expected numeric value must be finite") from error
            if not isfinite(normalized_value):
                raise ValueError("expected numeric value must be finite")
        if isinstance(self.value, str) and (
            not self.value or len(self.value.encode("utf-8")) > 4096
        ):
            raise ValueError("expected text value must contain 1-4096 UTF-8 bytes")
        if (
            isinstance(self.absolute_tolerance, bool)
            or not isinstance(self.absolute_tolerance, (int, float))
            or not isfinite(float(self.absolute_tolerance))
            or not 0 <= float(self.absolute_tolerance) <= 1.0e12
        ):
            raise ValueError(
                "absolute_tolerance must be a finite number between 0 and 1e12"
            )
        if (
            isinstance(self.value, (bool, str))
            and float(self.absolute_tolerance) != 0.0
        ):
            raise ValueError(
                "absolute_tolerance must be zero for boolean or text values"
            )
        object.__setattr__(self, "absolute_tolerance", float(self.absolute_tolerance))

    def matches(self, observed: object) -> bool:
        """Return whether a runtime measurement satisfies this expectation."""

        if isinstance(self.value, bool):
            return type(observed) is bool and observed is self.value
        if isinstance(self.value, str):
            return isinstance(observed, str) and observed == self.value
        if isinstance(observed, bool) or not isinstance(observed, (int, float)):
            return False
        try:
            normalized = float(observed)
            expected = float(self.value)
        except (OverflowError, TypeError, ValueError):
            return False
        return isfinite(normalized) and abs(normalized - expected) <= self.absolute_tolerance

    def overlaps(self, other: ValueExpectation) -> bool:
        """Return whether one observed value could satisfy both expectations."""

        if isinstance(self.value, bool) or isinstance(other.value, bool):
            return (
                type(self.value) is bool
                and type(other.value) is bool
                and self.value is other.value
            )
        if isinstance(self.value, str) or isinstance(other.value, str):
            return (
                isinstance(self.value, str)
                and isinstance(other.value, str)
                and self.value == other.value
            )
        distance = abs(float(self.value) - float(other.value))
        combined_tolerance = self.absolute_tolerance + other.absolute_tolerance
        return distance < combined_tolerance or isclose(
            distance,
            combined_tolerance,
            rel_tol=1.0e-12,
            abs_tol=1.0e-12,
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "value": self.value,
            "absolute_tolerance": self.absolute_tolerance,
        }


@dataclass(frozen=True, slots=True)
class PollScenario:
    scenario_id: str
    enabled: bool
    poll_type: str
    classes: tuple[int, ...]
    timeout_seconds: float
    max_measurements: int
    minimum_measurements: int
    expected_point_ids: tuple[str, ...]
    notes: str | None = None

    @property
    def capability_ids(self) -> tuple[str, ...]:
        capabilities = ["APP.FC.01.READ", "APP.CLASS.EVENTS", "QUAL.Q06.REVIEW"]
        if self.poll_type == "integrity":
            capabilities.extend(f"OBJ.G60.V{variation}" for variation in range(1, 5))
        else:
            capabilities.extend(f"OBJ.G60.V{class_number + 1}" for class_number in self.classes)
        return tuple(capabilities)

    def to_mapping(self) -> dict[str, object]:
        result: dict[str, object] = {
            "scenario_id": self.scenario_id,
            "enabled": self.enabled,
            "poll_type": self.poll_type,
            "classes": list(self.classes),
            "timeout_seconds": self.timeout_seconds,
            "max_measurements": self.max_measurements,
            "minimum_measurements": self.minimum_measurements,
            "expected_point_ids": list(self.expected_point_ids),
        }
        if self.notes is not None:
            result["notes"] = self.notes
        return result


@dataclass(frozen=True, slots=True)
class UnsolicitedScenario:
    scenario_id: str
    enabled: bool
    classes: tuple[int, ...]
    task_timeout_seconds: float
    observation_timeout_seconds: float
    max_events_per_read: int
    expected_point_id: str
    expectation: ValueExpectation
    require_timestamp: bool
    trigger_instructions: str
    notes: str | None = None

    def capability_ids(self, point: PointDefinition) -> tuple[str, ...]:
        event_capability = point.event_capability_id
        if event_capability is None:
            raise ValueError(f"point {point.point_id!r} has no event capability")
        return (
            "APP.UNSOLICITED",
            "APP.FC.14.ENABLE_UNSOLICITED",
            "APP.FC.15.DISABLE_UNSOLICITED",
            "APP.FC.82.UNSOLICITED_RESPONSE",
            "QUAL.Q06.REVIEW",
            *(f"OBJ.G60.V{class_number + 1}" for class_number in self.classes),
            event_capability,
        )

    def matching_measurements(
        self,
        point: PointDefinition,
        measurements: tuple[MeasurementRecord, ...],
    ) -> tuple[MeasurementRecord, ...]:
        return tuple(
            measurement
            for measurement in point.matching_event_measurements(measurements)
            if measurement.source == "unsolicited"
            and self.expectation.matches(measurement.value)
            and (
                not self.require_timestamp
                or measurement.dnp3_timestamp_ms is not None
            )
        )

    def to_mapping(self) -> dict[str, object]:
        result: dict[str, object] = {
            "scenario_id": self.scenario_id,
            "enabled": self.enabled,
            "classes": list(self.classes),
            "task_timeout_seconds": self.task_timeout_seconds,
            "observation_timeout_seconds": self.observation_timeout_seconds,
            "max_events_per_read": self.max_events_per_read,
            "expected_point_id": self.expected_point_id,
            "expectation": self.expectation.to_mapping(),
            "require_timestamp": self.require_timestamp,
            "trigger_instructions": self.trigger_instructions,
        }
        if self.notes is not None:
            result["notes"] = self.notes
        return result


@dataclass(frozen=True, slots=True)
class ControlCommand:
    """One bounded command supported by the fixed OpenDNP3 backend."""

    command_type: str
    index: int
    operation: str | None = None
    trip_close: str | None = None
    clear: bool | None = None
    count: int | None = None
    on_time_ms: int | None = None
    off_time_ms: int | None = None
    value: int | float | None = None

    @property
    def capability_id(self) -> str:
        if self.command_type == "crob":
            return "OBJ.G12.V1"
        variations = {
            "analog_output_int32": 1,
            "analog_output_int16": 2,
            "analog_output_float32": 3,
            "analog_output_double64": 4,
        }
        return f"OBJ.G41.V{variations[self.command_type]}"

    @property
    def qualifier_capability_id(self) -> str:
        """Return the OpenDNP3 command index-prefix qualifier for this index."""

        return "QUAL.Q17.REVIEW" if self.index <= 0xFF else "QUAL.Q28.REVIEW"

    def to_command(self) -> CrobCommand | AnalogOutputCommand:
        if self.command_type == "crob":
            assert self.operation is not None
            assert self.trip_close is not None
            assert self.clear is not None
            assert self.count is not None
            assert self.on_time_ms is not None
            assert self.off_time_ms is not None
            return CrobCommand(
                index=self.index,
                operation=self.operation,
                trip_close=self.trip_close,
                clear=self.clear,
                count=self.count,
                on_time_ms=self.on_time_ms,
                off_time_ms=self.off_time_ms,
            )
        assert self.value is not None
        return AnalogOutputCommand(
            index=self.index,
            value=self.value,
            command_type=self.command_type,
        )

    def to_mapping(self) -> dict[str, object]:
        if self.command_type == "crob":
            return {
                "type": self.command_type,
                "index": self.index,
                "operation": self.operation,
                "trip_close": self.trip_close,
                "clear": self.clear,
                "count": self.count,
                "on_time_ms": self.on_time_ms,
                "off_time_ms": self.off_time_ms,
            }
        return {
            "type": self.command_type,
            "index": self.index,
            "value": self.value,
        }


@dataclass(frozen=True, slots=True)
class ControlScenario:
    scenario_id: str
    enabled: bool
    authorization_reference: str
    control_mode: str
    command: ControlCommand
    feedback_point_id: str
    precondition: ValueExpectation
    postcondition: ValueExpectation
    restore_command: ControlCommand
    restore_expectation: ValueExpectation
    command_timeout_seconds: float
    feedback_timeout_seconds: float
    feedback_poll_interval_seconds: float
    notes: str | None = None

    def capability_ids(self, feedback_point: PointDefinition) -> tuple[str, ...]:
        operation_capabilities = (
            ("APP.FC.03.SELECT", "APP.FC.04.OPERATE")
            if self.control_mode == "select_and_operate"
            else ("APP.FC.05.DIRECT_OPERATE",)
        )
        return tuple(
            dict.fromkeys(
                (
                    "APP.COMMAND_STATUS.CATALOG",
                    *operation_capabilities,
                    self.command.capability_id,
                    self.command.qualifier_capability_id,
                    self.restore_command.qualifier_capability_id,
                    "APP.FC.01.READ",
                    feedback_point.capability_id,
                    feedback_point.read_qualifier_capability_id,
                )
            )
        )

    def to_mapping(self) -> dict[str, object]:
        result: dict[str, object] = {
            "scenario_id": self.scenario_id,
            "enabled": self.enabled,
            "authorization_reference": self.authorization_reference,
            "control_mode": self.control_mode,
            "command": self.command.to_mapping(),
            "feedback_point_id": self.feedback_point_id,
            "precondition": self.precondition.to_mapping(),
            "postcondition": self.postcondition.to_mapping(),
            "restore_command": self.restore_command.to_mapping(),
            "restore_expectation": self.restore_expectation.to_mapping(),
            "command_timeout_seconds": self.command_timeout_seconds,
            "feedback_timeout_seconds": self.feedback_timeout_seconds,
            "feedback_poll_interval_seconds": self.feedback_poll_interval_seconds,
        }
        if self.notes is not None:
            result["notes"] = self.notes
        return result


@dataclass(frozen=True, slots=True)
class EmsTestPlan:
    source_path: Path
    poll_scenarios: tuple[PollScenario, ...]
    unsolicited_scenarios: tuple[UnsolicitedScenario, ...]
    control_scenarios: tuple[ControlScenario, ...]
    notes: str | None = None
    schema_version: int = EMS_TEST_PLAN_SCHEMA_VERSION

    @property
    def enabled_poll_scenarios(self) -> tuple[PollScenario, ...]:
        return tuple(scenario for scenario in self.poll_scenarios if scenario.enabled)

    @property
    def enabled_unsolicited_scenarios(self) -> tuple[UnsolicitedScenario, ...]:
        return tuple(
            scenario for scenario in self.unsolicited_scenarios if scenario.enabled
        )

    @property
    def enabled_control_scenarios(self) -> tuple[ControlScenario, ...]:
        return tuple(
            scenario for scenario in self.control_scenarios if scenario.enabled
        )

    @property
    def control_by_id(self) -> Mapping[str, ControlScenario]:
        return MappingProxyType(
            {scenario.scenario_id: scenario for scenario in self.control_scenarios}
        )

    def to_mapping(self) -> dict[str, object]:
        result: dict[str, object] = {
            "schema_version": self.schema_version,
            "poll_scenarios": [
                scenario.to_mapping() for scenario in self.poll_scenarios
            ],
            "unsolicited_scenarios": [
                scenario.to_mapping() for scenario in self.unsolicited_scenarios
            ],
            "control_scenarios": [
                scenario.to_mapping() for scenario in self.control_scenarios
            ],
        }
        if self.notes is not None:
            result["notes"] = self.notes
        return result


def _error(path: Path, location: str, message: str) -> EmsTestPlanError:
    return EmsTestPlanError(f"{path}: {location}: {message}")


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_number(value: str) -> object:
    raise ValueError(f"non-standard JSON number is forbidden: {value}")


def _object(
    path: Path,
    location: str,
    value: object,
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise _error(path, location, "must be a JSON object")
    fields = set(value)
    missing = required.difference(fields)
    unknown = fields.difference(required | optional)
    if missing:
        raise _error(path, location, "missing fields: " + ", ".join(sorted(missing)))
    if unknown:
        raise _error(path, location, "unknown fields: " + ", ".join(sorted(unknown)))
    return value


def _array(
    path: Path,
    location: str,
    value: object,
    *,
    maximum: int,
) -> list[object]:
    if not isinstance(value, list):
        raise _error(path, location, "must be a JSON array")
    if len(value) > maximum:
        raise _error(path, location, f"must contain at most {maximum} items")
    return value


def _text(
    path: Path,
    location: str,
    value: object,
    *,
    maximum_utf8_bytes: int,
    pattern: re.Pattern[str] | None = None,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error(path, location, "must be a non-empty string")
    normalized = value.strip()
    if len(normalized.encode("utf-8")) > maximum_utf8_bytes:
        raise _error(
            path,
            location,
            f"must not exceed {maximum_utf8_bytes} UTF-8 bytes",
        )
    if pattern is not None and not pattern.fullmatch(normalized):
        raise _error(path, location, "has an invalid identifier format")
    return normalized


def _optional_notes(path: Path, location: str, value: object) -> str | None:
    if value is None:
        return None
    return _text(path, location, value, maximum_utf8_bytes=4096)


def _boolean(path: Path, location: str, value: object) -> bool:
    if type(value) is not bool:
        raise _error(path, location, "must be boolean")
    return value


def _integer(
    path: Path,
    location: str,
    value: object,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _error(path, location, f"must be an integer between {minimum} and {maximum}")
    return value


def _number(
    path: Path,
    location: str,
    value: object,
    *,
    minimum: float,
    maximum: float,
) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(float(value))
        or not minimum <= float(value) <= maximum
    ):
        raise _error(
            path,
            location,
            f"must be a finite number between {minimum:g} and {maximum:g}",
        )
    return float(value)


def _scenario_id(path: Path, location: str, value: object) -> str:
    return _text(
        path,
        location,
        value,
        maximum_utf8_bytes=128,
        pattern=_SCENARIO_ID_PATTERN,
    )


def _classes(path: Path, location: str, value: object) -> tuple[int, ...]:
    items = _array(path, location, value, maximum=3)
    if not items:
        raise _error(path, location, "must contain between one and three classes")
    classes = tuple(
        _integer(path, f"{location}[{index}]", item, minimum=1, maximum=3)
        for index, item in enumerate(items)
    )
    if len(set(classes)) != len(classes):
        raise _error(path, location, "must not contain duplicate classes")
    return classes


def _point_ids(path: Path, location: str, value: object) -> tuple[str, ...]:
    items = _array(path, location, value, maximum=_MAX_REFERENCED_POINTS)
    point_ids = tuple(
        _text(
            path,
            f"{location}[{index}]",
            item,
            maximum_utf8_bytes=128,
            pattern=_SCENARIO_ID_PATTERN,
        )
        for index, item in enumerate(items)
    )
    if len({point_id.casefold() for point_id in point_ids}) != len(point_ids):
        raise _error(path, location, "must not contain duplicate point IDs")
    return point_ids


def _expectation(path: Path, location: str, value: object) -> ValueExpectation:
    document = _object(
        path,
        location,
        value,
        required=frozenset({"value", "absolute_tolerance"}),
    )
    raw_value = document["value"]
    if not isinstance(raw_value, (bool, int, float, str)):
        raise _error(path, f"{location}.value", "must be boolean, numeric, or text")
    try:
        return ValueExpectation(
            value=raw_value,
            absolute_tolerance=_number(
                path,
                f"{location}.absolute_tolerance",
                document["absolute_tolerance"],
                minimum=0,
                maximum=1.0e12,
            ),
        )
    except ValueError as error:
        raise _error(path, location, str(error)) from error


def _poll_scenario(path: Path, index: int, value: object) -> PollScenario:
    location = f"poll_scenarios[{index}]"
    document = _object(
        path,
        location,
        value,
        required=frozenset(
            {
                "scenario_id",
                "enabled",
                "poll_type",
                "classes",
                "timeout_seconds",
                "max_measurements",
                "minimum_measurements",
                "expected_point_ids",
            }
        ),
        optional=frozenset({"notes"}),
    )
    poll_type = _text(
        path,
        f"{location}.poll_type",
        document["poll_type"],
        maximum_utf8_bytes=16,
    )
    if poll_type not in {"integrity", "class"}:
        raise _error(path, f"{location}.poll_type", "must be 'integrity' or 'class'")
    raw_classes = _array(
        path, f"{location}.classes", document["classes"], maximum=3
    )
    if poll_type == "integrity":
        if raw_classes:
            raise _error(path, f"{location}.classes", "must be empty for integrity")
        classes: tuple[int, ...] = ()
    else:
        classes = _classes(path, f"{location}.classes", raw_classes)
    max_measurements = _integer(
        path,
        f"{location}.max_measurements",
        document["max_measurements"],
        minimum=1,
        maximum=10_000,
    )
    minimum_measurements = _integer(
        path,
        f"{location}.minimum_measurements",
        document["minimum_measurements"],
        minimum=0,
        maximum=10_000,
    )
    if minimum_measurements > max_measurements:
        raise _error(
            path,
            f"{location}.minimum_measurements",
            "must not exceed max_measurements",
        )
    return PollScenario(
        scenario_id=_scenario_id(path, f"{location}.scenario_id", document["scenario_id"]),
        enabled=_boolean(path, f"{location}.enabled", document["enabled"]),
        poll_type=poll_type,
        classes=classes,
        timeout_seconds=_number(
            path,
            f"{location}.timeout_seconds",
            document["timeout_seconds"],
            minimum=0.1,
            maximum=60,
        ),
        max_measurements=max_measurements,
        minimum_measurements=minimum_measurements,
        expected_point_ids=_point_ids(
            path, f"{location}.expected_point_ids", document["expected_point_ids"]
        ),
        notes=_optional_notes(path, f"{location}.notes", document.get("notes")),
    )


def _unsolicited_scenario(
    path: Path, index: int, value: object
) -> UnsolicitedScenario:
    location = f"unsolicited_scenarios[{index}]"
    document = _object(
        path,
        location,
        value,
        required=frozenset(
            {
                "scenario_id",
                "enabled",
                "classes",
                "task_timeout_seconds",
                "observation_timeout_seconds",
                "max_events_per_read",
                "expected_point_id",
                "expectation",
                "require_timestamp",
                "trigger_instructions",
            }
        ),
        optional=frozenset({"notes"}),
    )
    return UnsolicitedScenario(
        scenario_id=_scenario_id(path, f"{location}.scenario_id", document["scenario_id"]),
        enabled=_boolean(path, f"{location}.enabled", document["enabled"]),
        classes=_classes(path, f"{location}.classes", document["classes"]),
        task_timeout_seconds=_number(
            path,
            f"{location}.task_timeout_seconds",
            document["task_timeout_seconds"],
            minimum=0.1,
            maximum=60,
        ),
        observation_timeout_seconds=_number(
            path,
            f"{location}.observation_timeout_seconds",
            document["observation_timeout_seconds"],
            minimum=0.1,
            maximum=300,
        ),
        max_events_per_read=_integer(
            path,
            f"{location}.max_events_per_read",
            document["max_events_per_read"],
            minimum=1,
            maximum=256,
        ),
        expected_point_id=_text(
            path,
            f"{location}.expected_point_id",
            document["expected_point_id"],
            maximum_utf8_bytes=128,
            pattern=_SCENARIO_ID_PATTERN,
        ),
        expectation=_expectation(
            path, f"{location}.expectation", document["expectation"]
        ),
        require_timestamp=_boolean(
            path, f"{location}.require_timestamp", document["require_timestamp"]
        ),
        trigger_instructions=_text(
            path,
            f"{location}.trigger_instructions",
            document["trigger_instructions"],
            maximum_utf8_bytes=2048,
        ),
        notes=_optional_notes(path, f"{location}.notes", document.get("notes")),
    )


def _control_command(path: Path, location: str, value: object) -> ControlCommand:
    if not isinstance(value, Mapping):
        raise _error(path, location, "must be a JSON object")
    command_type = value.get("type")
    if command_type not in _COMMAND_TYPES:
        raise _error(
            path,
            f"{location}.type",
            "must be one of: " + ", ".join(sorted(_COMMAND_TYPES)),
        )
    if command_type == "crob":
        document = _object(
            path,
            location,
            value,
            required=frozenset(
                {
                    "type",
                    "index",
                    "operation",
                    "trip_close",
                    "clear",
                    "count",
                    "on_time_ms",
                    "off_time_ms",
                }
            ),
        )
        command = ControlCommand(
            command_type="crob",
            index=_integer(
                path, f"{location}.index", document["index"], minimum=0, maximum=65535
            ),
            operation=_text(
                path,
                f"{location}.operation",
                document["operation"],
                maximum_utf8_bytes=16,
            ),
            trip_close=_text(
                path,
                f"{location}.trip_close",
                document["trip_close"],
                maximum_utf8_bytes=16,
            ),
            clear=_boolean(path, f"{location}.clear", document["clear"]),
            count=_integer(
                path, f"{location}.count", document["count"], minimum=1, maximum=255
            ),
            on_time_ms=_integer(
                path,
                f"{location}.on_time_ms",
                document["on_time_ms"],
                minimum=0,
                maximum=0xFFFFFFFF,
            ),
            off_time_ms=_integer(
                path,
                f"{location}.off_time_ms",
                document["off_time_ms"],
                minimum=0,
                maximum=0xFFFFFFFF,
            ),
        )
        if command.operation == "null":
            raise _error(
                path,
                f"{location}.operation",
                "must change an approved state; 'null' is not allowed in EMS scenarios",
            )
    else:
        document = _object(
            path,
            location,
            value,
            required=frozenset({"type", "index", "value"}),
        )
        raw_command_value = document["value"]
        if isinstance(raw_command_value, bool) or not isinstance(
            raw_command_value, (int, float)
        ):
            raise _error(path, f"{location}.value", "must be a finite number")
        command = ControlCommand(
            command_type=str(command_type),
            index=_integer(
                path, f"{location}.index", document["index"], minimum=0, maximum=65535
            ),
            value=raw_command_value,
        )
    try:
        command.to_command()
    except ValueError as error:
        raise _error(path, location, str(error)) from error
    return command


def _control_scenario(path: Path, index: int, value: object) -> ControlScenario:
    location = f"control_scenarios[{index}]"
    document = _object(
        path,
        location,
        value,
        required=frozenset(
            {
                "scenario_id",
                "enabled",
                "authorization_reference",
                "control_mode",
                "command",
                "feedback_point_id",
                "precondition",
                "postcondition",
                "restore_command",
                "restore_expectation",
                "command_timeout_seconds",
                "feedback_timeout_seconds",
                "feedback_poll_interval_seconds",
            }
        ),
        optional=frozenset({"notes"}),
    )
    scenario_id = _scenario_id(
        path, f"{location}.scenario_id", document["scenario_id"]
    )
    enabled = _boolean(path, f"{location}.enabled", document["enabled"])
    authorization_reference = _text(
        path,
        f"{location}.authorization_reference",
        document["authorization_reference"],
        maximum_utf8_bytes=512,
    )
    if enabled and any(
        token in authorization_reference.upper()
        for token in _PLACEHOLDER_AUTHORIZATION_TOKENS
    ):
        raise _error(
            path,
            f"{location}.authorization_reference",
            "enabled controls require a real approved authorization reference",
        )
    control_mode = _text(
        path,
        f"{location}.control_mode",
        document["control_mode"],
        maximum_utf8_bytes=32,
    )
    if control_mode not in {"select_and_operate", "direct_operate"}:
        raise _error(
            path,
            f"{location}.control_mode",
            "must be 'select_and_operate' or 'direct_operate'",
        )
    command = _control_command(path, f"{location}.command", document["command"])
    restore_command = _control_command(
        path, f"{location}.restore_command", document["restore_command"]
    )
    if (
        command.command_type != restore_command.command_type
        or command.index != restore_command.index
    ):
        raise _error(
            path,
            f"{location}.restore_command",
            "must use the same command type and index as command",
        )
    if command.to_mapping() == restore_command.to_mapping():
        raise _error(
            path,
            f"{location}.restore_command",
            "must differ from command",
        )
    precondition = _expectation(
        path, f"{location}.precondition", document["precondition"]
    )
    postcondition = _expectation(
        path, f"{location}.postcondition", document["postcondition"]
    )
    restore_expectation = _expectation(
        path, f"{location}.restore_expectation", document["restore_expectation"]
    )
    if precondition.to_mapping() != restore_expectation.to_mapping():
        raise _error(
            path,
            f"{location}.restore_expectation",
            "must exactly equal precondition so the scenario restores its baseline",
        )
    if precondition.overlaps(postcondition):
        raise _error(
            path,
            f"{location}.postcondition",
            "must not overlap precondition",
        )
    feedback_timeout = _number(
        path,
        f"{location}.feedback_timeout_seconds",
        document["feedback_timeout_seconds"],
        minimum=0.1,
        maximum=300,
    )
    poll_interval = _number(
        path,
        f"{location}.feedback_poll_interval_seconds",
        document["feedback_poll_interval_seconds"],
        minimum=0.05,
        maximum=10,
    )
    if poll_interval > feedback_timeout:
        raise _error(
            path,
            f"{location}.feedback_poll_interval_seconds",
            "must not exceed feedback_timeout_seconds",
        )
    return ControlScenario(
        scenario_id=scenario_id,
        enabled=enabled,
        authorization_reference=authorization_reference,
        control_mode=control_mode,
        command=command,
        feedback_point_id=_text(
            path,
            f"{location}.feedback_point_id",
            document["feedback_point_id"],
            maximum_utf8_bytes=128,
            pattern=_SCENARIO_ID_PATTERN,
        ),
        precondition=precondition,
        postcondition=postcondition,
        restore_command=restore_command,
        restore_expectation=restore_expectation,
        command_timeout_seconds=_number(
            path,
            f"{location}.command_timeout_seconds",
            document["command_timeout_seconds"],
            minimum=0.1,
            maximum=60,
        ),
        feedback_timeout_seconds=feedback_timeout,
        feedback_poll_interval_seconds=poll_interval,
        notes=_optional_notes(path, f"{location}.notes", document.get("notes")),
    )


def _point(path: Path, table: PointTable, point_id: str, location: str) -> PointDefinition:
    point = table.by_id.get(point_id)
    if point is None:
        raise _error(path, location, f"references unknown point_id {point_id!r}")
    if not point.enabled:
        raise _error(path, location, f"references disabled point_id {point_id!r}")
    return point


def _validate_expectation_for_point(
    path: Path,
    location: str,
    expectation: ValueExpectation,
    point: PointDefinition,
) -> None:
    if point.point_type in _BINARY_POINT_TYPES and not isinstance(
        expectation.value, bool
    ):
        raise _error(path, location, f"{point.point_type} requires a boolean value")
    if point.point_type in _NUMERIC_POINT_TYPES and (
        isinstance(expectation.value, bool)
        or not isinstance(expectation.value, (int, float))
    ):
        raise _error(path, location, f"{point.point_type} requires a numeric value")
    if (
        point.point_type in _NUMERIC_POINT_TYPES
        and point.expected_min is not None
        and point.expected_max is not None
    ):
        expected = float(expectation.value)
        lower = expected - expectation.absolute_tolerance
        upper = expected + expectation.absolute_tolerance
        if lower < point.expected_min or upper > point.expected_max:
            raise _error(
                path,
                location,
                "expectation including tolerance is outside the point engineering range",
            )


def _validate_point_references(
    path: Path,
    table: PointTable,
    poll_scenarios: tuple[PollScenario, ...],
    unsolicited_scenarios: tuple[UnsolicitedScenario, ...],
    control_scenarios: tuple[ControlScenario, ...],
) -> None:
    for scenario_index, scenario in enumerate(poll_scenarios):
        for point_index, point_id in enumerate(scenario.expected_point_ids):
            point = _point(
                path,
                table,
                point_id,
                f"poll_scenarios[{scenario_index}].expected_point_ids[{point_index}]",
            )
            if scenario.poll_type == "class":
                if point.event_class is None:
                    raise _error(
                        path,
                        f"poll_scenarios[{scenario_index}].expected_point_ids[{point_index}]",
                        "class polls require a point with event metadata",
                    )
                if point.event_class not in scenario.classes:
                    raise _error(
                        path,
                        f"poll_scenarios[{scenario_index}].expected_point_ids[{point_index}]",
                        f"point event_class {point.event_class} is not requested",
                    )

    for scenario_index, scenario in enumerate(unsolicited_scenarios):
        location = f"unsolicited_scenarios[{scenario_index}]"
        point = _point(path, table, scenario.expected_point_id, f"{location}.expected_point_id")
        if point.event_class is None or point.event_group is None or point.event_variation is None:
            raise _error(
                path,
                f"{location}.expected_point_id",
                "unsolicited scenarios require complete event metadata",
            )
        if point.event_class not in scenario.classes:
            raise _error(
                path,
                f"{location}.classes",
                f"must include expected point event_class {point.event_class}",
            )
        _validate_expectation_for_point(
            path, f"{location}.expectation", scenario.expectation, point
        )

    for scenario_index, scenario in enumerate(control_scenarios):
        location = f"control_scenarios[{scenario_index}]"
        point = _point(
            path,
            table,
            scenario.feedback_point_id,
            f"{location}.feedback_point_id",
        )
        for field_name, expectation in (
            ("precondition", scenario.precondition),
            ("postcondition", scenario.postcondition),
            ("restore_expectation", scenario.restore_expectation),
        ):
            _validate_expectation_for_point(
                path, f"{location}.{field_name}", expectation, point
            )


def load_ems_test_plan(path: str | Path, point_table: PointTable) -> EmsTestPlan:
    """Load a strict plan and resolve every point reference before DUT access."""

    source_path = Path(path).expanduser().resolve(strict=False)
    try:
        size = source_path.stat().st_size
    except OSError as error:
        raise EmsTestPlanError(f"{source_path}: cannot stat test plan: {error}") from error
    if not source_path.is_file():
        raise EmsTestPlanError(f"{source_path}: test plan does not exist")
    if size > _MAX_PLAN_BYTES:
        raise EmsTestPlanError(
            f"{source_path}: test plan exceeds {_MAX_PLAN_BYTES} bytes"
        )
    try:
        raw = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise EmsTestPlanError(f"{source_path}: cannot read UTF-8 test plan: {error}") from error
    try:
        document = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonstandard_number,
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise EmsTestPlanError(f"{source_path}: invalid JSON: {error}") from error
    root = _object(
        source_path,
        "$",
        document,
        required=frozenset(
            {
                "schema_version",
                "poll_scenarios",
                "unsolicited_scenarios",
                "control_scenarios",
            }
        ),
        optional=frozenset({"notes"}),
    )
    if root["schema_version"] != EMS_TEST_PLAN_SCHEMA_VERSION:
        raise _error(
            source_path,
            "$.schema_version",
            f"must equal {EMS_TEST_PLAN_SCHEMA_VERSION}",
        )
    polls_raw = _array(
        source_path,
        "$.poll_scenarios",
        root["poll_scenarios"],
        maximum=_MAX_SCENARIOS_PER_KIND,
    )
    unsolicited_raw = _array(
        source_path,
        "$.unsolicited_scenarios",
        root["unsolicited_scenarios"],
        maximum=_MAX_SCENARIOS_PER_KIND,
    )
    controls_raw = _array(
        source_path,
        "$.control_scenarios",
        root["control_scenarios"],
        maximum=_MAX_SCENARIOS_PER_KIND,
    )
    poll_scenarios = tuple(
        _poll_scenario(source_path, index, value)
        for index, value in enumerate(polls_raw)
    )
    unsolicited_scenarios = tuple(
        _unsolicited_scenario(source_path, index, value)
        for index, value in enumerate(unsolicited_raw)
    )
    control_scenarios = tuple(
        _control_scenario(source_path, index, value)
        for index, value in enumerate(controls_raw)
    )
    all_ids = [
        scenario.scenario_id
        for scenario in (*poll_scenarios, *unsolicited_scenarios, *control_scenarios)
    ]
    if len({scenario_id.casefold() for scenario_id in all_ids}) != len(all_ids):
        raise _error(source_path, "$", "scenario_id values must be globally unique")
    _validate_point_references(
        source_path,
        point_table,
        poll_scenarios,
        unsolicited_scenarios,
        control_scenarios,
    )
    return EmsTestPlan(
        source_path=source_path,
        poll_scenarios=poll_scenarios,
        unsolicited_scenarios=unsolicited_scenarios,
        control_scenarios=control_scenarios,
        notes=_optional_notes(source_path, "$.notes", root.get("notes")),
    )
