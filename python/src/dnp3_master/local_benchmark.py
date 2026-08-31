"""Deterministic loopback-only burst and paced-event performance benchmarks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any, Mapping

from .client import Dnp3MasterClient
from .local_outstation import LocalTestOutstation
from .models import CaptureConfig, CaptureResult
from .performance import nearest_rank
from .process_metrics import ProcessResourceSample, ProcessResourceSampler


_MAX_ITERATIONS = 1000
_MAX_ITERATION_SAMPLES = 32
_UNSOLICITED_QUEUE_CAPACITY = 4096
LOCAL_EVENT_PROFILE_SCHEMA_VERSION = 1
_MAX_PROFILE_BYTES = 1024 * 1024


class LocalEventProfileError(ValueError):
    """A local generator load profile is malformed or ambiguous."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise LocalEventProfileError(f"duplicate JSON key: {key!r}")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise LocalEventProfileError(f"non-finite JSON number is forbidden: {value}")


@dataclass(frozen=True, slots=True)
class LocalEventLoad:
    """One bounded load produced only by the packaged loopback outstation."""

    scenario_id: str
    point_type: str
    event_count: int
    iterations: int
    seed: int
    start_sequence: int
    timestamp_base_ms: int
    point_span: int
    interval_us: int
    capture_duration_seconds: float
    completion_timeout_seconds: float
    queue_capacity: int
    max_elapsed_seconds: float
    max_cpu_core_percent: float

    def __post_init__(self) -> None:
        if (
            not isinstance(self.scenario_id, str)
            or not self.scenario_id.isascii()
            or not 1 <= len(self.scenario_id) <= 100
            or any(
                character
                not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-"
                for character in self.scenario_id
            )
        ):
            raise ValueError("scenario_id must be a 1-100 character ASCII token")
        if self.point_type not in {"binary_input", "analog_input"}:
            raise ValueError("point_type must be binary_input or analog_input")
        for field, value, minimum, maximum in (
            ("event_count", self.event_count, 1, 65535),
            ("iterations", self.iterations, 1, _MAX_ITERATIONS),
            ("seed", self.seed, 0, (1 << 63) - 1),
            ("start_sequence", self.start_sequence, 0, (1 << 63) - 1),
            ("timestamp_base_ms", self.timestamp_base_ms, 0, (1 << 48) - 1),
            ("point_span", self.point_span, 1, 65535),
            ("interval_us", self.interval_us, 0, 55_000_000),
            ("queue_capacity", self.queue_capacity, 1, 65536),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{field} must be an integer")
            if not minimum <= value <= maximum:
                raise ValueError(f"{field} must be between {minimum} and {maximum}")
        total_events = self.event_count * self.iterations
        if self.start_sequence > (1 << 63) - total_events:
            raise ValueError("all iteration sequence ranges must fit signed 64-bit")
        if self.timestamp_base_ms > (1 << 48) - total_events:
            raise ValueError("all iteration timestamps must fit DNP3 48-bit time")
        emission_seconds = self.event_count * self.interval_us / 1_000_000.0
        if emission_seconds > 55.0:
            raise ValueError("one generator request must not exceed 55 seconds")
        if self.event_count > _UNSOLICITED_QUEUE_CAPACITY:
            raise ValueError(
                "event_count must not exceed the 4096-object master unsolicited "
                "queue; use repeated bounded chunks"
            )
        for field, minimum, maximum in (
            ("capture_duration_seconds", 0.1, 604800.0),
            ("completion_timeout_seconds", 0.1, 300.0),
            ("max_elapsed_seconds", 0.001, 604800.0),
            ("max_cpu_core_percent", 0.001, 1_000_000.0),
        ):
            value = float(getattr(self, field))
            if (
                isinstance(getattr(self, field), bool)
                or not isinstance(getattr(self, field), (int, float))
                or not math.isfinite(value)
                or not minimum <= value <= maximum
            ):
                raise ValueError(
                    f"{field} must be between {minimum:g} and {maximum:g}"
                )
        if self.capture_duration_seconds <= emission_seconds:
            raise ValueError("capture duration must exceed the generated stream duration")
        if self.completion_timeout_seconds <= emission_seconds:
            raise ValueError("completion timeout must exceed the generated stream duration")


@dataclass(frozen=True, slots=True)
class LocalEventProfile:
    """Hashed startup sizing plus deterministic burst/continuous chunks."""

    profile_id: str
    outstation_point_count: int
    event_buffer_capacity: int
    loads: tuple[LocalEventLoad, ...]
    source_sha256: str | None = None
    source_path: Path | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.profile_id, str)
            or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", self.profile_id) is None
        ):
            raise LocalEventProfileError("profile_id must be an ASCII token")
        for field in ("outstation_point_count", "event_buffer_capacity"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
                raise LocalEventProfileError(f"{field} must be between 1 and 65535")
        if not 1 <= len(self.loads) <= 64 or not all(
            isinstance(load, LocalEventLoad) for load in self.loads
        ):
            raise LocalEventProfileError("loads must contain 1-64 LocalEventLoad objects")
        if len({load.scenario_id for load in self.loads}) != len(self.loads):
            raise LocalEventProfileError("local event scenario_id values must be unique")
        for load in self.loads:
            if load.point_span > self.outstation_point_count:
                raise LocalEventProfileError(
                    f"{load.scenario_id}: point_span exceeds outstation_point_count"
                )
            if load.event_count > self.event_buffer_capacity:
                raise LocalEventProfileError(
                    f"{load.scenario_id}: event_count exceeds event buffer capacity"
                )
        if self.source_sha256 is not None and re.fullmatch(
            r"[0-9a-f]{64}", self.source_sha256
        ) is None:
            raise LocalEventProfileError("source_sha256 must be lowercase SHA-256")


def load_local_event_profile(path: Path | str) -> LocalEventProfile:
    """Load one strict local generator profile and bind its exact SHA-256."""

    resolved = Path(path).expanduser().resolve(strict=False)
    try:
        raw = resolved.read_bytes()
    except OSError as error:
        raise LocalEventProfileError(f"cannot read local event profile: {error}") from error
    if not raw or len(raw) > _MAX_PROFILE_BYTES:
        raise LocalEventProfileError(
            f"local event profile must contain 1-{_MAX_PROFILE_BYTES} bytes"
        )
    try:
        value = json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except LocalEventProfileError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise LocalEventProfileError(f"invalid local event profile JSON: {error}") from error
    required = {
        "schema_version",
        "profile_id",
        "scope",
        "outstation_point_count",
        "event_buffer_capacity",
        "loads",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise LocalEventProfileError("local event profile fields are invalid")
    if value["schema_version"] != LOCAL_EVENT_PROFILE_SCHEMA_VERSION:
        raise LocalEventProfileError(
            f"schema_version must be {LOCAL_EVENT_PROFILE_SCHEMA_VERSION}"
        )
    if value["scope"] != "LOCAL_LOOPBACK_ONLY":
        raise LocalEventProfileError("scope must be LOCAL_LOOPBACK_ONLY")
    raw_loads = value["loads"]
    if not isinstance(raw_loads, list) or not all(
        isinstance(item, Mapping) for item in raw_loads
    ):
        raise LocalEventProfileError("loads must be an array of objects")
    fields = {
        "scenario_id",
        "point_type",
        "event_count",
        "iterations",
        "seed",
        "start_sequence",
        "timestamp_base_ms",
        "point_span",
        "interval_us",
        "capture_duration_seconds",
        "completion_timeout_seconds",
        "queue_capacity",
        "max_elapsed_seconds",
        "max_cpu_core_percent",
    }
    loads = []
    for index, item in enumerate(raw_loads):
        if set(item) != fields:
            raise LocalEventProfileError(f"loads[{index}] fields are invalid")
        try:
            loads.append(LocalEventLoad(**dict(item)))
        except (TypeError, ValueError) as error:
            raise LocalEventProfileError(f"invalid loads[{index}]: {error}") from error
    return LocalEventProfile(
        profile_id=value["profile_id"],
        outstation_point_count=value["outstation_point_count"],
        event_buffer_capacity=value["event_buffer_capacity"],
        loads=tuple(loads),
        source_sha256=hashlib.sha256(raw).hexdigest(),
        source_path=resolved,
    )


def _distribution(values: list[float], unit: str, scope: str) -> dict[str, Any]:
    return {
        "sample_count": len(values),
        "p50": nearest_rank(values, 50),
        "p95": nearest_rank(values, 95),
        "p99": nearest_rank(values, 99),
        "max": max(values) if values else None,
        "unit": unit,
        "scope": scope,
        "percentile_method": "nearest_rank",
    }


def _cpu_percent(before: ProcessResourceSample, after: ProcessResourceSample) -> float | None:
    wall = (after.monotonic_ns - before.monotonic_ns) / 1e9
    cpu = after.process_cpu_seconds - before.process_cpu_seconds
    return cpu / wall * 100.0 if wall > 0 and cpu >= 0 else None


def _drain_unsolicited_queue(
    client: Dnp3MasterClient,
) -> tuple[int, int, int]:
    """Drain one complete bounded master queue and return count/drop/capacity."""

    drained = 0
    dropped_total: int | None = None
    queue_capacity: int | None = None
    maximum_batches = _UNSOLICITED_QUEUE_CAPACITY // 256 + 1
    for _ in range(maximum_batches):
        batch = client.wait_unsolicited(
            wait_timeout=0.0,
            max_events=256,
            request_timeout=2.0,
        )
        summary = batch.summary
        remaining = summary.get("remaining")
        dropped = summary.get("dropped_total")
        capacity = summary.get("queue_capacity")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (remaining, dropped, capacity)
        ):
            raise RuntimeError("unsolicited queue counters are unavailable")
        if capacity != _UNSOLICITED_QUEUE_CAPACITY:
            raise RuntimeError(
                "unexpected master unsolicited queue capacity: "
                f"{capacity!r}"
            )
        drained += len(batch.measurements)
        dropped_total = dropped
        queue_capacity = capacity
        if remaining == 0:
            return drained, dropped_total, queue_capacity
    raise RuntimeError("unsolicited queue could not be drained within its hard bound")


def run_local_event_benchmark(
    client: Dnp3MasterClient,
    outstation: LocalTestOutstation,
    load: LocalEventLoad,
    *,
    sampler: ProcessResourceSampler | None = None,
) -> dict[str, Any]:
    """Measure deterministic local events without enabling or disabling DUT services."""

    if not isinstance(client, Dnp3MasterClient):
        raise TypeError("client must be Dnp3MasterClient")
    if not isinstance(outstation, LocalTestOutstation) or not outstation.is_running:
        raise TypeError("outstation must be a running LocalTestOutstation")
    if not isinstance(load, LocalEventLoad):
        raise TypeError("load must be LocalEventLoad")
    if load.point_span > outstation.point_count:
        raise ValueError("point_span exceeds the local outstation database")
    if client.pid is None or not client.is_running:
        raise RuntimeError("client must own a running native host")
    status = client.get_status()
    required_class = 1 if load.point_type == "binary_input" else 2
    unsolicited = status.get("unsolicited")
    if (
        not isinstance(unsolicited, dict)
        or unsolicited.get("enabled") is not True
        or type(unsolicited.get("class_mask")) is not int
        or int(unsolicited["class_mask"]) & (1 << required_class) == 0
    ):
        raise RuntimeError(
            f"Class {required_class} unsolicited must be explicitly enabled first"
        )

    owned_sampler = sampler is None
    active_sampler = sampler or ProcessResourceSampler(client.pid)
    elapsed_values: list[float] = []
    throughput_values: list[float] = []
    cpu_values: list[float] = []
    resource_samples: list[ProcessResourceSample] = []
    iteration_samples: list[dict[str, Any]] = []
    failure: dict[str, Any] | None = None
    received_total = 0
    maximum_queue_depth = 0
    queue_overflow = 0
    unsolicited_events_drained = 0
    unsolicited_dropped_total = 0
    unsolicited_queue_capacity = _UNSOLICITED_QUEUE_CAPACITY
    started_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        initial_drained, initial_dropped, unsolicited_queue_capacity = (
            _drain_unsolicited_queue(client)
        )
        if initial_drained != 0 or initial_dropped != 0:
            raise RuntimeError(
                "unsolicited queue must be empty and loss-free before a local benchmark"
            )
        for iteration in range(load.iterations):
            start_sequence = load.start_sequence + iteration * load.event_count
            timestamp_base = load.timestamp_base_ms + iteration * load.event_count
            scenario_id = f"{load.scenario_id}-{iteration:04d}"
            truth = outstation.plan_events(
                load.point_type,
                load.event_count,
                scenario_id=scenario_id,
                seed=load.seed,
                start_sequence=start_sequence,
                timestamp_base_ms=timestamp_base,
                point_span=load.point_span,
                interval_us=load.interval_us,
            )
            capture: CaptureResult | None = None
            terminal: CaptureResult | None = None
            iteration_started = time.monotonic()
            before = active_sampler.sample()
            try:
                capture = client.begin_capture(
                    CaptureConfig(
                        mode="event_sequence",
                        sources=("unsolicited",),
                        duration_limit=load.capture_duration_seconds,
                        event_manifest=truth.capture_manifest(),
                        mismatch_sample_limit=16,
                        queue_capacity=load.queue_capacity,
                    )
                )
                emitted = outstation.generate_events(
                    load.point_type,
                    load.event_count,
                    scenario_id=scenario_id,
                    seed=load.seed,
                    start_sequence=start_sequence,
                    timestamp_base_ms=timestamp_base,
                    point_span=load.point_span,
                    interval_us=load.interval_us,
                )
                if emitted != truth:
                    raise RuntimeError("local generator truth changed between plan and emit")
                deadline = time.monotonic() + load.completion_timeout_seconds
                while time.monotonic() < deadline:
                    progress = client.capture_progress(capture.capture_id)
                    if progress.received_total >= truth.event_total:
                        break
                    time.sleep(0.01)
                terminal = client.end_capture(
                    capture.capture_id,
                    drain_timeout=min(300.0, load.completion_timeout_seconds),
                    request_timeout=min(
                        301.0,
                        load.completion_timeout_seconds + 1.0,
                    ),
                )
            finally:
                if capture is not None and terminal is None and client.is_running:
                    try:
                        terminal = client.end_capture(
                            capture.capture_id,
                            drain_timeout=min(300.0, load.completion_timeout_seconds),
                            request_timeout=min(
                                301.0,
                                load.completion_timeout_seconds + 1.0,
                            ),
                        )
                    except Exception:
                        pass
            after = active_sampler.sample()
            resource_samples.extend((before, after))
            elapsed = time.monotonic() - iteration_started
            cpu = _cpu_percent(before, after)
            if terminal is None:
                raise RuntimeError("event capture has no terminal result")
            drained, dropped, unsolicited_queue_capacity = _drain_unsolicited_queue(
                client
            )
            unsolicited_events_drained += drained
            unsolicited_dropped_total = dropped
            if dropped != 0 or drained != truth.event_total:
                raise RuntimeError(
                    "master unsolicited queue truth mismatch: "
                    f"expected={truth.event_total}, drained={drained}, "
                    f"dropped_total={dropped}"
                )
            received_total += terminal.received_total
            maximum_queue_depth = max(maximum_queue_depth, terminal.max_queue_depth)
            queue_overflow += terminal.queue_overflow
            if (
                terminal.state != "FINALIZED"
                or terminal.valid is not True
                or terminal.sequence_match is not True
                or terminal.received_total != truth.event_total
            ):
                raise RuntimeError("event capture did not match deterministic truth")
            elapsed_values.append(elapsed)
            throughput_values.append(terminal.throughput_per_sec)
            if cpu is not None:
                cpu_values.append(cpu)
            if len(iteration_samples) < _MAX_ITERATION_SAMPLES:
                iteration_samples.append(
                    {
                        "iteration": iteration,
                        "truth": truth.to_mapping(),
                        "capture": dict(terminal.raw),
                        "elapsed_seconds": elapsed,
                        "cpu_core_percent": cpu,
                    }
                )
    except Exception as error:
        failure = {
            "type": type(error).__name__,
            "message": str(error)[:2048],
        }
    finally:
        if client.is_running:
            try:
                drained, dropped, unsolicited_queue_capacity = (
                    _drain_unsolicited_queue(client)
                )
                unsolicited_events_drained += drained
                unsolicited_dropped_total = max(
                    unsolicited_dropped_total,
                    dropped,
                )
            except Exception:
                if failure is None:
                    failure = {
                        "type": "RuntimeError",
                        "message": "failed to drain the master unsolicited queue",
                    }
        if owned_sampler:
            active_sampler.close()

    maximum_elapsed = max(elapsed_values) if elapsed_values else None
    maximum_cpu = max(cpu_values) if cpu_values else None
    maximum_working_set = (
        max(sample.working_set_bytes for sample in resource_samples)
        if resource_samples
        else None
    )
    maximum_private = (
        max(sample.private_bytes for sample in resource_samples)
        if resource_samples
        else None
    )
    checks = [
        {
            "name": "max_elapsed_seconds",
            "observed": maximum_elapsed,
            "limit": load.max_elapsed_seconds,
            "passed": maximum_elapsed is not None
            and maximum_elapsed <= load.max_elapsed_seconds,
        },
        {
            "name": "max_cpu_core_percent",
            "observed": maximum_cpu,
            "limit": load.max_cpu_core_percent,
            "passed": maximum_cpu is not None
            and maximum_cpu <= load.max_cpu_core_percent,
        },
    ]
    passed = (
        failure is None
        and len(elapsed_values) == load.iterations
        and received_total == load.event_count * load.iterations
        and queue_overflow == 0
        and unsolicited_events_drained == load.event_count * load.iterations
        and unsolicited_dropped_total == 0
        and all(item["passed"] for item in checks)
    )
    return {
        "schema_version": 1,
        "report_type": "DNP3_LOCAL_EVENT_PERFORMANCE",
        "evidence_scope": "LOCAL_LOOPBACK_ONLY",
        "formal_dut_conclusion": False,
        "scenario_id": load.scenario_id,
        "point_type": load.point_type,
        "mode": "burst" if load.interval_us == 0 else "paced_continuous_chunk",
        "iterations_attempted": load.iterations,
        "iterations_completed": len(elapsed_values),
        "events_expected": load.event_count * load.iterations,
        "events_received": received_total,
        "queue_overflow": queue_overflow,
        "max_queue_depth": maximum_queue_depth,
        "unsolicited_queue": {
            "events_drained": unsolicited_events_drained,
            "dropped_total": unsolicited_dropped_total,
            "queue_capacity": unsolicited_queue_capacity,
            "source": "native_unsolicited_store",
            "scope": "master_session_since_enable",
        },
        "elapsed_seconds": _distribution(
            elapsed_values,
            "seconds",
            "plan_capture_emit_match_end_per_iteration",
        ),
        "capture_throughput_per_sec": _distribution(
            throughput_values,
            "objects_per_second",
            "native_received_total_per_capture_duration",
        ),
        "cpu_core_percent": _distribution(
            cpu_values,
            "percent_of_one_logical_core",
            "native_host_process_during_each_iteration",
        ),
        "resource_maximums": {
            "working_set_bytes": maximum_working_set,
            "private_bytes": maximum_private,
            "source": "windows_process_api",
            "scope": "dnp3_master_host_process",
        },
        "network_bytes": {
            "value": None,
            "reason": "NO_APPROVED_PCAP_OR_BOTTOM_LAYER_COUNTER",
            "scope": "wire_or_tcp_bytes",
        },
        "dut_resources": {
            "value": None,
            "reason": "NO_APPROVED_EXTERNAL_DUT_MONITOR",
            "scope": "dut_process_or_host",
        },
        "threshold_checks": checks,
        "failure": failure,
        "iteration_samples": iteration_samples,
        "iteration_sample_limit": _MAX_ITERATION_SAMPLES,
        "started_utc": started_utc,
        "ended_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "passed": passed,
        "limitations": [
            "packaged OpenDNP3 outstation and master share one machine and stack",
            "each paced generator request is bounded to 55 seconds; a reviewed caller "
            "must orchestrate longer streams",
        ],
    }


__all__ = [
    "LOCAL_EVENT_PROFILE_SCHEMA_VERSION",
    "LocalEventLoad",
    "LocalEventProfile",
    "LocalEventProfileError",
    "load_local_event_profile",
    "run_local_event_benchmark",
]
