"""Strict read-only performance profiles and bounded benchmark reports."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import time
from types import MappingProxyType
from typing import Any

from .client import Dnp3MasterClient
from .models import (
    CaptureConfig,
    CaptureEventManifest,
    CapturePointRange,
    CaptureResult,
    ReadHeader,
    ReadTaskResult,
)
from .process_metrics import ProcessResourceSample, ProcessResourceSampler


PERFORMANCE_PROFILE_SCHEMA_VERSION = 1
_MAX_PROFILE_BYTES = 1024 * 1024
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")
_SCOPES = frozenset({"LOCAL_LOOPBACK_ONLY", "TARGET_ENVIRONMENT_PENDING_REVIEW"})
_OPERATIONS = frozenset({"read", "integrity_poll", "class_poll"})
_QUALIFIER_CAPABILITIES = {
    "all_objects": "QUAL.Q06.REVIEW",
    "range8": "QUAL.Q00.REVIEW",
    "range16": "QUAL.Q01.REVIEW",
    "count8": "QUAL.Q07.REVIEW",
    "count16": "QUAL.Q08.REVIEW",
}
_EXPECTED_KINDS = frozenset(
    {
        "analog_input",
        "analog_output_status",
        "binary_input",
        "binary_output_status",
        "counter",
        "double_bit_binary_input",
        "frozen_counter",
        "octet_string",
        "time_and_interval",
    }
)


class PerformanceProfileError(ValueError):
    """A performance profile is unsafe, ambiguous, or malformed."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PerformanceProfileError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise PerformanceProfileError(f"non-finite JSON number is forbidden: {value}")


def _exact_fields(
    value: Mapping[str, Any],
    *,
    required: set[str],
    optional: frozenset[str] | set[str] = frozenset(),
    context: str,
) -> None:
    missing = required.difference(value)
    unknown = set(value).difference(required | optional)
    if missing:
        raise PerformanceProfileError(
            f"{context} is missing fields: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise PerformanceProfileError(
            f"{context} contains unknown fields: {', '.join(sorted(unknown))}"
        )


def _expected_count_mapping(
    value: Mapping[str, Any], *, context: str, group_variation: bool
) -> Mapping[str, int]:
    if not isinstance(value, Mapping) or len(value) > 256:
        raise PerformanceProfileError(f"{context} must be an object with at most 256 entries")
    result: dict[str, int] = {}
    for key, count in value.items():
        if not isinstance(key, str):
            raise PerformanceProfileError(f"{context} keys must be strings")
        if group_variation:
            match = re.fullmatch(r"([0-9]{1,3}):([0-9]{1,3})", key)
            if (
                match is None
                or any(int(item) > 255 for item in match.groups())
                or ":".join(str(int(item)) for item in match.groups()) != key
            ):
                raise PerformanceProfileError(
                    f"{context} key must be canonical group:variation within 0-255"
                )
        elif key not in _EXPECTED_KINDS:
            raise PerformanceProfileError(f"{context} contains unsupported kind {key!r}")
        _integer(count, f"{context}.{key}", 1, 1_000_000)
        result[key] = count
    return MappingProxyType(dict(sorted(result.items())))


def _number(
    value: object,
    field: str,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PerformanceProfileError(f"{field} must be a finite number")
    normalized = float(value)
    if not math.isfinite(normalized) or not minimum <= normalized <= maximum:
        raise PerformanceProfileError(
            f"{field} must be between {minimum:g} and {maximum:g}"
        )
    return normalized


def _integer(value: object, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise PerformanceProfileError(
            f"{field} must be an integer between {minimum} and {maximum}"
        )
    return value


def _token(value: object, field: str, maximum: int = 128) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value.encode("utf-8")) <= maximum
        or not value.isascii()
        or _TOKEN_PATTERN.fullmatch(value) is None
    ):
        raise PerformanceProfileError(
            f"{field} must be a 1-{maximum} byte ASCII token"
        )
    return value


@dataclass(frozen=True, slots=True)
class PerformanceThresholds:
    """Project-owned acceptance limits; no limits are hard-coded in the runner."""

    minimum_samples: int
    max_task_p95_ms: float
    max_task_p99_ms: float
    max_task_ms: float
    max_cpu_core_percent: float
    max_working_set_bytes: int
    max_private_bytes: int
    max_handle_count: int
    max_thread_count: int
    max_private_growth_bytes_per_hour: float
    max_working_set_growth_bytes_per_hour: float
    max_handle_growth: int
    max_thread_growth: int
    max_capture_overhead_percent: float

    def __post_init__(self) -> None:
        _integer(self.minimum_samples, "thresholds.minimum_samples", 1, 1_000_000)
        for field_name in (
            "max_task_p95_ms",
            "max_task_p99_ms",
            "max_task_ms",
            "max_cpu_core_percent",
            "max_private_growth_bytes_per_hour",
            "max_working_set_growth_bytes_per_hour",
            "max_capture_overhead_percent",
        ):
            _number(getattr(self, field_name), f"thresholds.{field_name}", 0, 1e18)
        for field_name in (
            "max_working_set_bytes",
            "max_private_bytes",
            "max_handle_count",
            "max_thread_count",
            "max_handle_growth",
            "max_thread_growth",
        ):
            _integer(getattr(self, field_name), f"thresholds.{field_name}", 0, 1 << 63)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> PerformanceThresholds:
        fields = {
            "minimum_samples",
            "max_task_p95_ms",
            "max_task_p99_ms",
            "max_task_ms",
            "max_cpu_core_percent",
            "max_working_set_bytes",
            "max_private_bytes",
            "max_handle_count",
            "max_thread_count",
            "max_private_growth_bytes_per_hour",
            "max_working_set_growth_bytes_per_hour",
            "max_handle_growth",
            "max_thread_growth",
            "max_capture_overhead_percent",
        }
        _exact_fields(value, required=fields, context="thresholds")
        try:
            return cls(**dict(value))
        except (TypeError, ValueError) as error:
            if isinstance(error, PerformanceProfileError):
                raise
            raise PerformanceProfileError(f"invalid thresholds: {error}") from error


@dataclass(frozen=True, slots=True)
class SoakSettings:
    """Bounded checkpoint/watchdog policy shared by short and 24-hour runs."""

    target_duration_seconds: float
    cycle_interval_seconds: float
    checkpoint_interval_seconds: float
    resource_sample_interval_seconds: float
    watchdog_timeout_seconds: float
    max_checkpoints: int
    max_evidence_bytes: int
    min_free_disk_bytes: int
    allowed_reconnects: int
    max_consecutive_failures: int

    def __post_init__(self) -> None:
        _number(
            self.target_duration_seconds,
            "soak.target_duration_seconds",
            0.1,
            604_800,
        )
        _number(
            self.cycle_interval_seconds,
            "soak.cycle_interval_seconds",
            0,
            3600,
        )
        _number(
            self.checkpoint_interval_seconds,
            "soak.checkpoint_interval_seconds",
            0.1,
            86_400,
        )
        _number(
            self.resource_sample_interval_seconds,
            "soak.resource_sample_interval_seconds",
            0.1,
            3600,
        )
        _number(
            self.watchdog_timeout_seconds,
            "soak.watchdog_timeout_seconds",
            0.1,
            3600,
        )
        _integer(self.max_checkpoints, "soak.max_checkpoints", 2, 10000)
        _integer(
            self.max_evidence_bytes,
            "soak.max_evidence_bytes",
            65536,
            1 << 50,
        )
        _integer(
            self.min_free_disk_bytes,
            "soak.min_free_disk_bytes",
            0,
            1 << 60,
        )
        _integer(self.allowed_reconnects, "soak.allowed_reconnects", 0, 10000)
        _integer(
            self.max_consecutive_failures,
            "soak.max_consecutive_failures",
            0,
            10000,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> SoakSettings:
        fields = {
            "target_duration_seconds",
            "cycle_interval_seconds",
            "checkpoint_interval_seconds",
            "resource_sample_interval_seconds",
            "watchdog_timeout_seconds",
            "max_checkpoints",
            "max_evidence_bytes",
            "min_free_disk_bytes",
            "allowed_reconnects",
            "max_consecutive_failures",
        }
        _exact_fields(value, required=fields, context="soak")
        try:
            return cls(**dict(value))
        except (TypeError, ValueError) as error:
            if isinstance(error, PerformanceProfileError):
                raise
            raise PerformanceProfileError(f"invalid soak settings: {error}") from error


@dataclass(frozen=True, slots=True)
class ReadPerformanceScenario:
    """One bounded, read-only performance population."""

    scenario_id: str
    operation: str
    headers: tuple[ReadHeader, ...]
    classes: tuple[int, ...]
    capture: CaptureConfig | None
    warmup_iterations: int
    measurement_iterations: int
    interval_seconds: float
    cooldown_seconds: float
    task_timeout_seconds: float
    max_measurements: int
    expected_objects_per_iteration: int
    expected_by_kind: Mapping[str, int]
    expected_by_group_variation: Mapping[str, int]
    baseline_scenario_id: str | None = None

    def __post_init__(self) -> None:
        _token(self.scenario_id, "scenario_id")
        if self.operation not in _OPERATIONS:
            raise PerformanceProfileError(
                "operation must be read, integrity_poll, or class_poll"
            )
        if not all(isinstance(header, ReadHeader) for header in self.headers):
            raise PerformanceProfileError("headers must contain ReadHeader objects")
        if self.operation == "read" and not 1 <= len(self.headers) <= 64:
            raise PerformanceProfileError("read scenario requires 1-64 headers")
        if self.operation != "read" and self.headers:
            raise PerformanceProfileError("only read scenarios accept headers")
        if self.operation == "class_poll":
            if (
                not 1 <= len(self.classes) <= 3
                or any(item not in {1, 2, 3} for item in self.classes)
                or len(set(self.classes)) != len(self.classes)
            ):
                raise PerformanceProfileError(
                    "class_poll scenario requires unique Class 1/2/3 values"
                )
        elif self.classes:
            raise PerformanceProfileError("only class_poll scenarios accept classes")
        if self.capture is not None and not isinstance(self.capture, CaptureConfig):
            raise PerformanceProfileError("capture must be CaptureConfig or null")
        _integer(self.warmup_iterations, "warmup_iterations", 0, 10000)
        _integer(
            self.measurement_iterations,
            "measurement_iterations",
            1,
            1_000_000,
        )
        _number(self.interval_seconds, "interval_seconds", 0, 3600)
        _number(self.cooldown_seconds, "cooldown_seconds", 0, 3600)
        _number(self.task_timeout_seconds, "task_timeout_seconds", 0.05, 300)
        _integer(self.max_measurements, "max_measurements", 1, 1_000_000)
        _integer(
            self.expected_objects_per_iteration,
            "expected_objects_per_iteration",
            0,
            self.max_measurements,
        )
        by_kind = _expected_count_mapping(
            self.expected_by_kind,
            context="expected_by_kind",
            group_variation=False,
        )
        by_group_variation = _expected_count_mapping(
            self.expected_by_group_variation,
            context="expected_by_group_variation",
            group_variation=True,
        )
        if sum(by_kind.values()) != self.expected_objects_per_iteration:
            raise PerformanceProfileError(
                "expected_by_kind must sum to expected_objects_per_iteration"
            )
        if sum(by_group_variation.values()) != self.expected_objects_per_iteration:
            raise PerformanceProfileError(
                "expected_by_group_variation must sum to "
                "expected_objects_per_iteration"
            )
        object.__setattr__(self, "expected_by_kind", by_kind)
        object.__setattr__(
            self,
            "expected_by_group_variation",
            by_group_variation,
        )
        if self.capture is not None and self.capture.mode == "static_set":
            capture_by_kind: dict[str, int] = {}
            for point_range in self.capture.point_ranges:
                capture_by_kind[point_range.kind] = (
                    capture_by_kind.get(point_range.kind, 0)
                    + point_range.point_count
                )
            if capture_by_kind != dict(by_kind):
                raise PerformanceProfileError(
                    "static capture point_ranges must exactly match expected_by_kind"
                )
        if self.baseline_scenario_id is not None:
            _token(self.baseline_scenario_id, "baseline_scenario_id")
            if self.capture is None:
                raise PerformanceProfileError(
                    "baseline_scenario_id is only valid for a capture-enabled scenario"
                )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ReadPerformanceScenario:
        required = {
            "scenario_id",
            "operation",
            "headers",
            "classes",
            "capture",
            "warmup_iterations",
            "measurement_iterations",
            "interval_seconds",
            "cooldown_seconds",
            "task_timeout_seconds",
            "max_measurements",
            "expected_objects_per_iteration",
            "expected_by_kind",
            "expected_by_group_variation",
            "baseline_scenario_id",
        }
        _exact_fields(value, required=required, context="scenario")
        try:
            raw_headers = value["headers"]
            if not isinstance(raw_headers, list):
                raise PerformanceProfileError("scenario.headers must be an array")
            if not all(isinstance(item, Mapping) for item in raw_headers):
                raise PerformanceProfileError("scenario header must be an object")
            headers = tuple(ReadHeader(**dict(item)) for item in raw_headers)
            raw_classes = value["classes"]
            if not isinstance(raw_classes, list):
                raise PerformanceProfileError("scenario.classes must be an array")
            capture = _capture_from_mapping(value["capture"])
            return cls(
                scenario_id=value["scenario_id"],
                operation=value["operation"],
                headers=headers,
                classes=tuple(raw_classes),
                capture=capture,
                warmup_iterations=value["warmup_iterations"],
                measurement_iterations=value["measurement_iterations"],
                interval_seconds=value["interval_seconds"],
                cooldown_seconds=value["cooldown_seconds"],
                task_timeout_seconds=value["task_timeout_seconds"],
                max_measurements=value["max_measurements"],
                expected_objects_per_iteration=value[
                    "expected_objects_per_iteration"
                ],
                expected_by_kind=value["expected_by_kind"],
                expected_by_group_variation=value[
                    "expected_by_group_variation"
                ],
                baseline_scenario_id=value["baseline_scenario_id"],
            )
        except PerformanceProfileError:
            raise
        except (TypeError, ValueError, KeyError) as error:
            raise PerformanceProfileError(f"invalid performance scenario: {error}") from error

    @property
    def required_capability_ids(self) -> tuple[str, ...]:
        """Return exact request/object/qualifier IDs for pytest PICS gating."""

        identifiers = {
            "APP.FC.01.READ",
            "APP.TASK.LIFECYCLE",
            "APP.TASK.OBSERVABILITY",
        }
        if self.operation == "integrity_poll":
            identifiers.add("APP.CLASS.EVENTS")
            identifiers.update(f"OBJ.G60.V{variation}" for variation in range(1, 5))
            identifiers.add("QUAL.Q06.REVIEW")
        elif self.operation == "class_poll":
            identifiers.add("APP.CLASS.EVENTS")
            identifiers.update(
                f"OBJ.G60.V{event_class + 1}" for event_class in self.classes
            )
            identifiers.add("QUAL.Q06.REVIEW")
        else:
            for header in self.headers:
                identifiers.add(f"OBJ.G{header.group}.V{header.variation}")
                identifiers.add(_QUALIFIER_CAPABILITIES[header.qualifier])
        for group_variation in self.expected_by_group_variation:
            group, variation = group_variation.split(":", 1)
            identifiers.add(f"OBJ.G{int(group)}.V{int(variation)}")
        return tuple(sorted(identifiers))


def _capture_from_mapping(value: object) -> CaptureConfig | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise PerformanceProfileError("scenario.capture must be an object or null")
    required = {"mode", "sources", "duration_limit_seconds"}
    optional = {
        "point_ranges",
        "event_manifest",
        "mismatch_sample_limit",
        "queue_capacity",
    }
    _exact_fields(value, required=required, optional=optional, context="scenario.capture")
    ranges_value = value.get("point_ranges", [])
    if not isinstance(ranges_value, list):
        raise PerformanceProfileError("capture.point_ranges must be an array")
    if not all(isinstance(item, Mapping) for item in ranges_value):
        raise PerformanceProfileError("capture point range must be an object")
    ranges = tuple(CapturePointRange(**dict(item)) for item in ranges_value)
    manifest_value = value.get("event_manifest")
    manifest = None
    if manifest_value is not None:
        if not isinstance(manifest_value, Mapping):
            raise PerformanceProfileError("capture.event_manifest must be an object")
        try:
            manifest = CaptureEventManifest(**dict(manifest_value))
        except (TypeError, ValueError) as error:
            raise PerformanceProfileError(f"invalid event manifest: {error}") from error
    try:
        return CaptureConfig(
            mode=value["mode"],
            sources=tuple(value["sources"]),
            duration_limit=value["duration_limit_seconds"],
            point_ranges=ranges,
            event_manifest=manifest,
            mismatch_sample_limit=value.get("mismatch_sample_limit", 100),
            queue_capacity=value.get("queue_capacity", 4096),
        )
    except (TypeError, ValueError) as error:
        raise PerformanceProfileError(f"invalid capture configuration: {error}") from error


@dataclass(frozen=True, slots=True)
class PerformanceProfile:
    """Validated benchmark/soak inputs plus the exact source hash."""

    profile_id: str
    scope: str
    seed: int
    scenarios: tuple[ReadPerformanceScenario, ...]
    thresholds: PerformanceThresholds
    soak: SoakSettings
    source_sha256: str | None = None
    source_path: Path | None = None

    def __post_init__(self) -> None:
        _token(self.profile_id, "profile_id")
        if self.scope not in _SCOPES:
            raise PerformanceProfileError(
                "scope must be LOCAL_LOOPBACK_ONLY or TARGET_ENVIRONMENT_PENDING_REVIEW"
            )
        _integer(self.seed, "seed", 0, (1 << 63) - 1)
        if not 1 <= len(self.scenarios) <= 64:
            raise PerformanceProfileError("profile must contain 1-64 scenarios")
        if not all(isinstance(item, ReadPerformanceScenario) for item in self.scenarios):
            raise PerformanceProfileError("scenarios must be ReadPerformanceScenario objects")
        by_id = {item.scenario_id: item for item in self.scenarios}
        if len(by_id) != len(self.scenarios):
            raise PerformanceProfileError("scenario_id values must be unique")
        for scenario in self.scenarios:
            baseline_id = scenario.baseline_scenario_id
            if baseline_id is None:
                continue
            baseline = by_id.get(baseline_id)
            if baseline is None:
                raise PerformanceProfileError(
                    f"baseline scenario {baseline_id!r} does not exist"
                )
            if baseline.capture is not None:
                raise PerformanceProfileError("capture baseline scenario must disable capture")
            comparable = (
                baseline.operation == scenario.operation
                and baseline.headers == scenario.headers
                and baseline.classes == scenario.classes
                and baseline.task_timeout_seconds == scenario.task_timeout_seconds
                and baseline.max_measurements == scenario.max_measurements
                and baseline.expected_objects_per_iteration
                == scenario.expected_objects_per_iteration
                and baseline.expected_by_kind == scenario.expected_by_kind
                and baseline.expected_by_group_variation
                == scenario.expected_by_group_variation
                and baseline.measurement_iterations == scenario.measurement_iterations
            )
            if not comparable:
                raise PerformanceProfileError(
                    "capture and baseline scenarios must use the same population"
                )
        maximum_iteration_bound = max(
            scenario.task_timeout_seconds + 1.0
            + (
                2.0
                + min(300.0, max(1.0, scenario.task_timeout_seconds))
                + 1.0
                if scenario.capture is not None
                else 0.0
            )
            for scenario in self.scenarios
        )
        if self.soak.watchdog_timeout_seconds <= maximum_iteration_bound:
            raise PerformanceProfileError(
                "soak watchdog_timeout_seconds must exceed the bounded begin/read/end "
                f"RPC budget ({maximum_iteration_bound:g} seconds)"
            )
        if self.source_sha256 is not None and re.fullmatch(
            r"[0-9a-f]{64}", self.source_sha256
        ) is None:
            raise PerformanceProfileError("source_sha256 must be lowercase SHA-256")

    @property
    def required_capability_ids(self) -> tuple[str, ...]:
        """Return the union of exact PICS dependencies for every scenario."""

        return tuple(
            sorted(
                {
                    identifier
                    for scenario in self.scenarios
                    for identifier in scenario.required_capability_ids
                }
            )
        )


def load_performance_profile(path: Path) -> PerformanceProfile:
    """Load at most 1 MiB of strict JSON and bind the source SHA-256."""

    resolved = Path(path).expanduser().resolve(strict=False)
    try:
        raw = resolved.read_bytes()
    except OSError as error:
        raise PerformanceProfileError(f"cannot read performance profile: {error}") from error
    if not raw or len(raw) > _MAX_PROFILE_BYTES:
        raise PerformanceProfileError(
            f"performance profile must contain 1-{_MAX_PROFILE_BYTES} bytes"
        )
    try:
        decoded = raw.decode("utf-8-sig")
        value = json.loads(
            decoded,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except PerformanceProfileError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise PerformanceProfileError(f"invalid performance profile JSON: {error}") from error
    if not isinstance(value, Mapping):
        raise PerformanceProfileError("performance profile root must be an object")
    required = {
        "schema_version",
        "profile_id",
        "scope",
        "seed",
        "scenarios",
        "thresholds",
        "soak",
    }
    _exact_fields(value, required=required, context="performance profile")
    if value["schema_version"] != PERFORMANCE_PROFILE_SCHEMA_VERSION:
        raise PerformanceProfileError(
            f"schema_version must be {PERFORMANCE_PROFILE_SCHEMA_VERSION}"
        )
    if not isinstance(value["scenarios"], list):
        raise PerformanceProfileError("scenarios must be an array")
    if not isinstance(value["thresholds"], Mapping) or not isinstance(value["soak"], Mapping):
        raise PerformanceProfileError("thresholds and soak must be objects")
    if not all(isinstance(item, Mapping) for item in value["scenarios"]):
        raise PerformanceProfileError("each scenario must be an object")
    scenarios = tuple(
        ReadPerformanceScenario.from_mapping(item) for item in value["scenarios"]
    )
    return PerformanceProfile(
        profile_id=value["profile_id"],
        scope=value["scope"],
        seed=value["seed"],
        scenarios=scenarios,
        thresholds=PerformanceThresholds.from_mapping(value["thresholds"]),
        soak=SoakSettings.from_mapping(value["soak"]),
        source_sha256=hashlib.sha256(raw).hexdigest(),
        source_path=resolved,
    )


def nearest_rank(values: Sequence[float], percentile: float) -> float | None:
    """Return a documented nearest-rank percentile over one explicit population."""

    if not values:
        return None
    if not 0 < percentile <= 100:
        raise ValueError("percentile must be greater than 0 and at most 100")
    normalized = [float(value) for value in values]
    if any(not math.isfinite(value) for value in normalized):
        raise ValueError("percentile population must contain only finite values")
    ordered = sorted(normalized)
    rank = max(1, math.ceil(percentile / 100.0 * len(ordered)))
    return ordered[rank - 1]


def _distribution(values: Sequence[float], *, source: str, scope: str, unit: str) -> dict[str, Any]:
    return {
        "available": bool(values),
        "value": {
            "sample_count": len(values),
            "p50": nearest_rank(values, 50),
            "p95": nearest_rank(values, 95),
            "p99": nearest_rank(values, 99),
            "max": max(values) if values else None,
        },
        "source": source,
        "scope": scope,
        "unit": unit,
        "population": "one value per completed DNP3 read task",
        "percentile_method": "nearest_rank",
        "reason": None if values else "NO_SAMPLES",
    }


def _unavailable_metric(scope: str, unit: str, reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "value": None,
        "source": None,
        "scope": scope,
        "unit": unit,
        "reason": reason,
    }


def _growth_per_hour(samples: Sequence[ProcessResourceSample], field: str) -> float:
    if len(samples) < 2:
        return 0.0
    origin = samples[0].monotonic_ns
    xs = [(sample.monotonic_ns - origin) / 3_600_000_000_000.0 for sample in samples]
    ys = [float(getattr(sample, field)) for sample in samples]
    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)
    denominator = sum((value - x_mean) ** 2 for value in xs)
    if denominator == 0:
        return 0.0
    return sum(
        (x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)
    ) / denominator


def _resource_report(
    samples: Sequence[ProcessResourceSample], cpu_percentages: Sequence[float]
) -> dict[str, Any]:
    if not samples:
        return {
            "available": False,
            "source": "windows_process_api",
            "scope": "dnp3_master_host_process",
            "reason": "NO_RESOURCE_SAMPLES",
        }
    fields = (
        "working_set_bytes",
        "private_bytes",
        "handle_count",
        "thread_count",
    )
    report: dict[str, Any] = {
        "available": True,
        "source": samples[0].source,
        "scope": samples[0].scope,
        "sample_count": len(samples),
        "first_monotonic_ns": samples[0].monotonic_ns,
        "last_monotonic_ns": samples[-1].monotonic_ns,
    }
    for field in fields:
        values = [getattr(sample, field) for sample in samples]
        report[field] = {
            "unit": "bytes" if field.endswith("_bytes") else "count",
            "minimum": min(values),
            "maximum": max(values),
            "first": values[0],
            "last": values[-1],
        }
    report["private_bytes"]["growth_per_hour"] = _growth_per_hour(
        samples, "private_bytes"
    )
    report["working_set_bytes"]["growth_per_hour"] = _growth_per_hour(
        samples, "working_set_bytes"
    )
    report["cpu_core_percent"] = {
        "unit": "percent_of_one_logical_core",
        "definition": "process CPU seconds / wall seconds * 100",
        "maximum": max(cpu_percentages) if cpu_percentages else None,
        "sample_count": len(cpu_percentages),
    }
    return report


def _capture_report(
    captures: Sequence[CaptureResult], *, enabled: bool
) -> dict[str, Any]:
    if not enabled:
        return {
            "available": False,
            "reason": "CAPTURE_DISABLED_FOR_BASELINE",
        }

    def nullable_sum(field: str) -> int | None:
        values = [getattr(capture, field) for capture in captures]
        if not values or any(value is None for value in values):
            return None
        return sum(int(value) for value in values if value is not None)

    reasons = sorted(
        {
            reason
            for capture in captures
            for reason in capture.invalid_reasons
        }
    )
    return {
        "available": True,
        "completed_runs": len(captures),
        "valid_runs": sum(capture.valid is True for capture in captures),
        "expected_total": nullable_sum("expected_total"),
        "offered_total": sum(capture.offered_total for capture in captures),
        "received_total": sum(capture.received_total for capture in captures),
        "received_unique": nullable_sum("received_unique"),
        "duplicates": nullable_sum("duplicates"),
        "missing": nullable_sum("missing"),
        "unmatched_total": sum(capture.unmatched_total for capture in captures),
        "fragments_total": sum(capture.fragments_total for capture in captures),
        "queue_overflow": sum(capture.queue_overflow for capture in captures),
        "max_queue_depth": max(
            (capture.max_queue_depth for capture in captures), default=0
        ),
        "sequence_mismatch_runs": sum(
            capture.sequence_match is False for capture in captures
        ),
        "completeness_scopes": sorted(
            {capture.completeness_scope for capture in captures}
        ),
        "invalid_reasons": reasons,
    }


def _assert_read_accepted(result: ReadTaskResult) -> None:
    bits = result.iin.get("bits")
    if not isinstance(bits, (list, tuple)):
        raise RuntimeError("read result IIN bits are unavailable")
    request_errors = [
        bit
        for bit in bits
        if isinstance(bit, str)
        and bit.startswith(("IIN2.0.", "IIN2.1.", "IIN2.2."))
    ]
    if request_errors:
        raise RuntimeError(
            "DUT rejected the performance read: " + ", ".join(request_errors)
        )


def _execute_read(
    client: Dnp3MasterClient,
    scenario: ReadPerformanceScenario,
) -> ReadTaskResult:
    common = {
        "timeout": scenario.task_timeout_seconds,
        "max_measurements": scenario.max_measurements,
        "return_mode": "summary",
        "request_timeout": scenario.task_timeout_seconds + 1.0,
    }
    if scenario.operation == "read":
        result = client.read(scenario.headers, **common)
    elif scenario.operation == "class_poll":
        result = client.class_poll(scenario.classes, **common)
    else:
        result = client.integrity_poll(**common)
    _assert_read_accepted(result)
    return result


def _execute_iteration(
    client: Dnp3MasterClient,
    scenario: ReadPerformanceScenario,
) -> tuple[ReadTaskResult, CaptureResult | None]:
    capture: CaptureResult | None = None
    read_result: ReadTaskResult | None = None
    try:
        if scenario.capture is not None:
            capture = client.begin_capture(
                scenario.capture,
                request_timeout=2.0,
            )
        read_result = _execute_read(client, scenario)
    finally:
        if capture is not None and client.is_running:
            drain_timeout = min(
                300.0,
                max(1.0, scenario.task_timeout_seconds),
            )
            terminal = client.end_capture(
                capture.capture_id,
                drain_timeout=drain_timeout,
                request_timeout=drain_timeout + 1.0,
            )
            capture = terminal
    assert read_result is not None
    if capture is not None:
        if capture.state != "FINALIZED" or capture.valid is not True:
            raise RuntimeError("native capture did not finalize as a valid bounded run")
        if capture.queue_overflow != 0:
            raise RuntimeError("native capture queue overflowed")
        if capture.mode == "static_set" and (
            capture.missing != 0
            or capture.duplicates != 0
            or capture.unmatched_total != 0
        ):
            raise RuntimeError(
                "static capture truth mismatch: "
                f"missing={capture.missing}, duplicates={capture.duplicates}, "
                f"unmatched={capture.unmatched_total}"
            )
    received = int(read_result.summary["received_total"])
    if received != scenario.expected_objects_per_iteration:
        raise RuntimeError(
            "read population mismatch: "
            f"expected={scenario.expected_objects_per_iteration}, received={received}"
        )
    for field, expected in (
        ("by_kind", scenario.expected_by_kind),
        ("by_group_variation", scenario.expected_by_group_variation),
    ):
        actual = read_result.summary.get(field)
        if not isinstance(actual, Mapping) or dict(actual) != dict(expected):
            raise RuntimeError(
                f"read population {field} mismatch: "
                f"expected={dict(expected)!r}, actual={actual!r}"
            )
    return read_result, capture


def execute_read_only_iteration(
    client: Dnp3MasterClient,
    scenario: ReadPerformanceScenario,
) -> tuple[ReadTaskResult, CaptureResult | None]:
    """Execute one validated scenario without retrying or changing DUT state."""

    if not isinstance(client, Dnp3MasterClient):
        raise TypeError("client must be Dnp3MasterClient")
    if not isinstance(scenario, ReadPerformanceScenario):
        raise TypeError("scenario must be ReadPerformanceScenario")
    return _execute_iteration(client, scenario)


def _threshold_checks(
    report: Mapping[str, Any], thresholds: PerformanceThresholds
) -> list[dict[str, Any]]:
    latency_metric = report["task_latency_ms"]
    latency = latency_metric["value"]
    resources = report["resources"]
    checks: list[tuple[str, float | None, float, str | None]] = [
        (
            "minimum_samples",
            float(latency["sample_count"]),
            float(thresholds.minimum_samples),
            None,
        ),
        (
            "max_task_p95_ms",
            float(latency["p95"]) if latency["p95"] is not None else None,
            thresholds.max_task_p95_ms,
            latency_metric.get("reason"),
        ),
        (
            "max_task_p99_ms",
            float(latency["p99"]) if latency["p99"] is not None else None,
            thresholds.max_task_p99_ms,
            latency_metric.get("reason"),
        ),
        (
            "max_task_ms",
            float(latency["max"]) if latency["max"] is not None else None,
            thresholds.max_task_ms,
            latency_metric.get("reason"),
        ),
    ]
    if resources.get("available") is True:
        checks.extend(
            [
                (
                    "max_cpu_core_percent",
                    float(resources["cpu_core_percent"]["maximum"])
                    if resources["cpu_core_percent"]["maximum"] is not None
                    else None,
                    thresholds.max_cpu_core_percent,
                    "NO_CPU_INTERVAL" if not resources["cpu_core_percent"]["sample_count"] else None,
                ),
                (
                    "max_working_set_bytes",
                    float(resources["working_set_bytes"]["maximum"]),
                    float(thresholds.max_working_set_bytes),
                    None,
                ),
                (
                    "max_private_bytes",
                    float(resources["private_bytes"]["maximum"]),
                    float(thresholds.max_private_bytes),
                    None,
                ),
                (
                    "max_handle_count",
                    float(resources["handle_count"]["maximum"]),
                    float(thresholds.max_handle_count),
                    None,
                ),
                (
                    "max_thread_count",
                    float(resources["thread_count"]["maximum"]),
                    float(thresholds.max_thread_count),
                    None,
                ),
                (
                    "max_private_growth_bytes_per_hour",
                    float(resources["private_bytes"]["growth_per_hour"]),
                    thresholds.max_private_growth_bytes_per_hour,
                    None,
                ),
                (
                    "max_working_set_growth_bytes_per_hour",
                    float(resources["working_set_bytes"]["growth_per_hour"]),
                    thresholds.max_working_set_growth_bytes_per_hour,
                    None,
                ),
                (
                    "max_handle_growth",
                    float(
                        resources["handle_count"]["last"]
                        - resources["handle_count"]["first"]
                    ),
                    float(thresholds.max_handle_growth),
                    None,
                ),
                (
                    "max_thread_growth",
                    float(
                        resources["thread_count"]["last"]
                        - resources["thread_count"]["first"]
                    ),
                    float(thresholds.max_thread_growth),
                    None,
                ),
            ]
        )
    else:
        reason = str(resources.get("reason", "RESOURCE_SOURCE_UNAVAILABLE"))
        for name, limit in (
            ("max_cpu_core_percent", thresholds.max_cpu_core_percent),
            ("max_working_set_bytes", thresholds.max_working_set_bytes),
            ("max_private_bytes", thresholds.max_private_bytes),
            ("max_handle_count", thresholds.max_handle_count),
            ("max_thread_count", thresholds.max_thread_count),
            (
                "max_private_growth_bytes_per_hour",
                thresholds.max_private_growth_bytes_per_hour,
            ),
            (
                "max_working_set_growth_bytes_per_hour",
                thresholds.max_working_set_growth_bytes_per_hour,
            ),
            ("max_handle_growth", thresholds.max_handle_growth),
            ("max_thread_growth", thresholds.max_thread_growth),
        ):
            checks.append((name, None, float(limit), reason))

    result = []
    for name, observed, limit, reason in checks:
        is_minimum = name == "minimum_samples"
        passed = (
            observed >= limit if is_minimum else observed <= limit
        ) if observed is not None else False
        result.append(
            {
                "name": name,
                "observed": observed,
                "limit": limit,
                "comparison": ">=" if is_minimum else "<=",
                "passed": passed,
                "reason": None if observed is not None else reason,
            }
        )
    return result


def run_performance_suite(
    client: Dnp3MasterClient,
    profile: PerformanceProfile,
    *,
    sampler: ProcessResourceSampler | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run bounded read-only scenarios and return a self-describing report."""

    if not isinstance(client, Dnp3MasterClient):
        raise TypeError("client must be Dnp3MasterClient")
    if not isinstance(profile, PerformanceProfile):
        raise TypeError("profile must be PerformanceProfile")
    if client.pid is None:
        raise RuntimeError("client must own a running native host process")
    owned_sampler = sampler is None
    active_sampler = sampler or ProcessResourceSampler(client.pid)
    started_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    started_ns = time.monotonic_ns()
    scenario_reports: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    try:
        for scenario in profile.scenarios:
            failures: list[str] = []
            for _ in range(scenario.warmup_iterations):
                try:
                    _execute_iteration(client, scenario)
                except Exception as error:
                    failures.append(f"warmup: {type(error).__name__}: {error}")
                    break
                if scenario.interval_seconds:
                    sleep(scenario.interval_seconds)

            latencies: list[float] = []
            capture_throughput: list[float] = []
            resource_samples: list[ProcessResourceSample] = []
            cpu_percentages: list[float] = []
            capture_results: list[CaptureResult] = []
            received_objects = 0
            fragments = 0
            completed_iterations = 0
            received_by_kind: dict[str, int] = {}
            received_by_group_variation: dict[str, int] = {}
            if not failures:
                for _ in range(scenario.measurement_iterations):
                    try:
                        before = active_sampler.sample()
                        result, capture = _execute_iteration(client, scenario)
                        after = active_sampler.sample()
                        resource_samples.extend((before, after))
                        wall_seconds = (after.monotonic_ns - before.monotonic_ns) / 1e9
                        cpu_seconds = after.process_cpu_seconds - before.process_cpu_seconds
                        if wall_seconds > 0 and cpu_seconds >= 0:
                            cpu_percentages.append(cpu_seconds / wall_seconds * 100.0)
                        duration = result.timings.get("duration_ms")
                        if isinstance(duration, (int, float)) and math.isfinite(float(duration)):
                            latencies.append(float(duration))
                        else:
                            raise RuntimeError("read task did not expose finite duration_ms")
                        received_objects += int(result.summary["received_total"])
                        fragments += int(result.summary["fragments_received"])
                        for field, target in (
                            ("by_kind", received_by_kind),
                            ("by_group_variation", received_by_group_variation),
                        ):
                            for key, count in result.summary[field].items():
                                target[key] = target.get(key, 0) + int(count)
                        if capture is not None:
                            capture_throughput.append(capture.throughput_per_sec)
                            capture_results.append(capture)
                        completed_iterations += 1
                    except Exception as error:
                        failures.append(f"measurement: {type(error).__name__}: {error}")
                        break
                    if scenario.interval_seconds:
                        sleep(scenario.interval_seconds)

            if scenario.cooldown_seconds:
                sleep(scenario.cooldown_seconds)
            resources = _resource_report(resource_samples, cpu_percentages)
            report: dict[str, Any] = {
                "scenario_id": scenario.scenario_id,
                "operation": scenario.operation,
                "capture_enabled": scenario.capture is not None,
                "baseline_scenario_id": scenario.baseline_scenario_id,
                "attempted_iterations": scenario.measurement_iterations,
                "completed_iterations": completed_iterations,
                "expected_objects_per_iteration": (
                    scenario.expected_objects_per_iteration
                ),
                "expected_by_kind": dict(scenario.expected_by_kind),
                "expected_by_group_variation": dict(
                    scenario.expected_by_group_variation
                ),
                "received_objects": received_objects,
                "received_by_kind": received_by_kind,
                "received_by_group_variation": received_by_group_variation,
                "fragments": fragments,
                "task_latency_ms": _distribution(
                    latencies,
                    source="native_read_task_timings",
                    scope="submit_to_task_completion",
                    unit="milliseconds",
                ),
                "capture_throughput_per_sec": _distribution(
                    capture_throughput,
                    source="native_capture",
                    scope="processed_objects_per_capture_duration",
                    unit="objects_per_second",
                ),
                "capture": _capture_report(
                    capture_results,
                    enabled=scenario.capture is not None,
                ),
                "resources": resources,
                "network_rx_bytes": _unavailable_metric(
                    "wire_or_tcp_bytes",
                    "bytes",
                    "NO_APPROVED_PCAP_OR_BOTTOM_LAYER_COUNTER",
                ),
                "network_tx_bytes": _unavailable_metric(
                    "wire_or_tcp_bytes",
                    "bytes",
                    "NO_APPROVED_PCAP_OR_BOTTOM_LAYER_COUNTER",
                ),
                "dut_resources": _unavailable_metric(
                    "dut_process_or_host",
                    "various",
                    "NO_APPROVED_EXTERNAL_DUT_MONITOR",
                ),
                "failures": failures,
            }
            checks = _threshold_checks(report, profile.thresholds)
            report["threshold_checks"] = checks
            report["passed"] = not failures and all(item["passed"] for item in checks)
            scenario_reports.append(report)
            by_id[scenario.scenario_id] = report

        overhead_reports = []
        for scenario in profile.scenarios:
            if scenario.baseline_scenario_id is None:
                continue
            measured = by_id[scenario.scenario_id]["task_latency_ms"]["value"]
            baseline = by_id[scenario.baseline_scenario_id]["task_latency_ms"]["value"]
            measured_p50 = measured["p50"]
            baseline_p50 = baseline["p50"]
            overhead = None
            if measured_p50 is not None and baseline_p50 is not None and baseline_p50 > 0:
                overhead = (measured_p50 - baseline_p50) / baseline_p50 * 100.0
            passed = overhead is not None and overhead <= profile.thresholds.max_capture_overhead_percent
            comparison = {
                "scenario_id": scenario.scenario_id,
                "baseline_scenario_id": scenario.baseline_scenario_id,
                "population": "paired scenario p50 submit-to-complete latency",
                "capture_overhead_percent": overhead,
                "limit": profile.thresholds.max_capture_overhead_percent,
                "passed": passed,
            }
            overhead_reports.append(comparison)
            if not passed:
                by_id[scenario.scenario_id]["passed"] = False
    finally:
        if owned_sampler:
            active_sampler.close()

    ended_ns = time.monotonic_ns()
    ended_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "schema_version": 1,
        "report_type": "DNP3_READ_ONLY_PERFORMANCE",
        "evidence_scope": profile.scope,
        "formal_dut_conclusion": False,
        "profile": {
            "profile_id": profile.profile_id,
            "sha256": profile.source_sha256,
            "seed": profile.seed,
        },
        "host": {
            "pid": client.pid,
            "hello": deepcopy(dict(client.hello_info)),
        },
        "started_utc": started_utc,
        "ended_utc": ended_utc,
        "duration_seconds": (ended_ns - started_ns) / 1e9,
        "scenarios": scenario_reports,
        "capture_overhead": overhead_reports,
        "passed": all(item["passed"] for item in scenario_reports),
        "limitations": [
            "bundled loopback is same-stack engineering evidence only",
            "wire bytes and DUT resources remain null without approved external sources",
            "a TARGET_ENVIRONMENT_PENDING_REVIEW profile still requires H09b evidence review",
        ],
    }


__all__ = [
    "PERFORMANCE_PROFILE_SCHEMA_VERSION",
    "PerformanceProfile",
    "PerformanceProfileError",
    "PerformanceThresholds",
    "ReadPerformanceScenario",
    "SoakSettings",
    "load_performance_profile",
    "nearest_rank",
    "execute_read_only_iteration",
    "run_performance_suite",
]
