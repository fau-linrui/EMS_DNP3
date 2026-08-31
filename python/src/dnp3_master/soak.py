"""Bounded, interruption-aware orchestration for read-only DNP3 soak runs."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import time
from typing import Any
import uuid

from .client import Dnp3MasterClient
from .performance import (
    PerformanceProfile,
    ReadPerformanceScenario,
    execute_read_only_iteration,
    nearest_rank,
)
from .process_metrics import ProcessResourceSample, ProcessResourceSampler


_MAX_FAILURE_SAMPLES = 32
_MAX_LATENCY_SAMPLES = 1_000_000
_MAX_RESOURCE_SAMPLES = 100_000
_MAX_CHANNEL_EVENT_BATCHES = 5


class SoakRunnerError(RuntimeError):
    """The runner could not create a trustworthy bounded evidence set."""


class _ConnectionEvidenceError(SoakRunnerError):
    """Channel history is incomplete or exceeds the approved reconnect limit."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _encoded_json(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _atomic_write(path: Path, encoded: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _growth_per_hour(samples: list[ProcessResourceSample], field: str) -> float:
    if len(samples) < 2:
        return 0.0
    origin = samples[0].monotonic_ns
    xs = [
        (sample.monotonic_ns - origin) / 3_600_000_000_000.0
        for sample in samples
    ]
    ys = [float(getattr(sample, field)) for sample in samples]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    denominator = sum((item - mean_x) ** 2 for item in xs)
    if denominator == 0:
        return 0.0
    return sum(
        (x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)
    ) / denominator


def _distribution(values: list[float]) -> dict[str, Any]:
    return {
        "sample_count": len(values),
        "p50": nearest_rank(values, 50),
        "p95": nearest_rank(values, 95),
        "p99": nearest_rank(values, 99),
        "max": max(values) if values else None,
        "unit": "milliseconds",
        "source": "native_read_task_timings",
        "scope": "one_submit_to_task_completion_value_per_successful_cycle",
        "percentile_method": "nearest_rank",
    }


def _resource_summary(samples: list[ProcessResourceSample]) -> dict[str, Any]:
    if not samples:
        return {
            "available": False,
            "source": "windows_process_api",
            "scope": "dnp3_master_host_process",
            "reason": "NO_RESOURCE_SAMPLES",
        }
    result: dict[str, Any] = {
        "available": True,
        "source": samples[0].source,
        "scope": samples[0].scope,
        "sample_count": len(samples),
    }
    cpu_percentages = []
    for previous, current in zip(samples, samples[1:]):
        wall_seconds = (current.monotonic_ns - previous.monotonic_ns) / 1e9
        cpu_seconds = current.process_cpu_seconds - previous.process_cpu_seconds
        if wall_seconds > 0 and cpu_seconds >= 0:
            cpu_percentages.append(cpu_seconds / wall_seconds * 100.0)
    result["cpu_core_percent"] = {
        "maximum": max(cpu_percentages) if cpu_percentages else None,
        "sample_count": len(cpu_percentages),
        "unit": "percent_of_one_logical_core",
        "definition": "process CPU seconds / wall seconds * 100",
    }
    for field in (
        "working_set_bytes",
        "private_bytes",
        "handle_count",
        "thread_count",
    ):
        values = [getattr(sample, field) for sample in samples]
        result[field] = {
            "minimum": min(values),
            "maximum": max(values),
            "first": values[0],
            "last": values[-1],
            "unit": "bytes" if field.endswith("_bytes") else "count",
        }
    result["private_bytes"]["growth_per_hour"] = _growth_per_hour(
        samples, "private_bytes"
    )
    result["working_set_bytes"]["growth_per_hour"] = _growth_per_hour(
        samples, "working_set_bytes"
    )
    return result


def _drain_channel_events(
    client: Dnp3MasterClient,
) -> tuple[list[str], int]:
    """Consume one complete 1,024-entry channel event window."""

    states: list[str] = []
    dropped_total: int | None = None
    for _ in range(_MAX_CHANNEL_EVENT_BATCHES):
        batch = client.wait_event(
            wait_timeout=0.0,
            max_events=256,
            request_timeout=2.0,
        )
        events = batch.get("events")
        remaining = batch.get("remaining")
        dropped = batch.get("dropped_total")
        if (
            not isinstance(events, list)
            or isinstance(remaining, bool)
            or not isinstance(remaining, int)
            or remaining < 0
            or isinstance(dropped, bool)
            or not isinstance(dropped, int)
            or dropped < 0
        ):
            raise _ConnectionEvidenceError(
                "channel event queue counters are unavailable"
            )
        for event in events:
            if not isinstance(event, Mapping) or event.get("type") != "channel_state":
                raise _ConnectionEvidenceError(
                    "channel event queue contains an invalid event"
                )
            state = event.get("state")
            if state not in {"CLOSED", "OPENING", "OPEN", "SHUTDOWN"}:
                raise _ConnectionEvidenceError(
                    "channel event queue contains an invalid state"
                )
            states.append(state)
        dropped_total = dropped
        if remaining == 0:
            return states, dropped_total
    raise _ConnectionEvidenceError(
        "channel event queue could not be drained within its 1,024-entry bound"
    )


def _soak_threshold_checks(
    scenario_reports: list[dict[str, Any]],
    resources: Mapping[str, Any],
    profile: PerformanceProfile,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(name: str, observed: float | None, limit: float, comparison: str) -> None:
        passed = False
        if observed is not None:
            passed = observed >= limit if comparison == ">=" else observed <= limit
        checks.append(
            {
                "name": name,
                "observed": observed,
                "limit": limit,
                "comparison": comparison,
                "passed": passed,
                "reason": None if observed is not None else "METRIC_UNAVAILABLE",
            }
        )

    for report in scenario_reports:
        latency = report["task_latency_ms"]
        prefix = report["scenario_id"]
        add(
            f"{prefix}.minimum_samples",
            float(latency["sample_count"]),
            float(profile.thresholds.minimum_samples),
            ">=",
        )
        add(
            f"{prefix}.max_task_p95_ms",
            latency["p95"],
            profile.thresholds.max_task_p95_ms,
            "<=",
        )
        add(
            f"{prefix}.max_task_p99_ms",
            latency["p99"],
            profile.thresholds.max_task_p99_ms,
            "<=",
        )
        add(
            f"{prefix}.max_task_ms",
            latency["max"],
            profile.thresholds.max_task_ms,
            "<=",
        )
    if resources.get("available") is True:
        cpu_maximum = resources["cpu_core_percent"]["maximum"]
        add(
            "max_cpu_core_percent",
            float(cpu_maximum) if cpu_maximum is not None else None,
            profile.thresholds.max_cpu_core_percent,
            "<=",
        )
        for name, field, limit in (
            (
                "max_working_set_bytes",
                "working_set_bytes",
                profile.thresholds.max_working_set_bytes,
            ),
            (
                "max_private_bytes",
                "private_bytes",
                profile.thresholds.max_private_bytes,
            ),
            (
                "max_handle_count",
                "handle_count",
                profile.thresholds.max_handle_count,
            ),
            (
                "max_thread_count",
                "thread_count",
                profile.thresholds.max_thread_count,
            ),
        ):
            add(name, float(resources[field]["maximum"]), float(limit), "<=")
        add(
            "max_private_growth_bytes_per_hour",
            float(resources["private_bytes"]["growth_per_hour"]),
            profile.thresholds.max_private_growth_bytes_per_hour,
            "<=",
        )
        add(
            "max_working_set_growth_bytes_per_hour",
            float(resources["working_set_bytes"]["growth_per_hour"]),
            profile.thresholds.max_working_set_growth_bytes_per_hour,
            "<=",
        )
        add(
            "max_handle_growth",
            float(resources["handle_count"]["last"] - resources["handle_count"]["first"]),
            float(profile.thresholds.max_handle_growth),
            "<=",
        )
        add(
            "max_thread_growth",
            float(resources["thread_count"]["last"] - resources["thread_count"]["first"]),
            float(profile.thresholds.max_thread_growth),
            "<=",
        )
    else:
        for name, limit in (
            ("max_cpu_core_percent", profile.thresholds.max_cpu_core_percent),
            ("max_working_set_bytes", profile.thresholds.max_working_set_bytes),
            ("max_private_bytes", profile.thresholds.max_private_bytes),
            ("max_handle_count", profile.thresholds.max_handle_count),
            ("max_thread_count", profile.thresholds.max_thread_count),
            (
                "max_private_growth_bytes_per_hour",
                profile.thresholds.max_private_growth_bytes_per_hour,
            ),
            (
                "max_working_set_growth_bytes_per_hour",
                profile.thresholds.max_working_set_growth_bytes_per_hour,
            ),
            ("max_handle_growth", profile.thresholds.max_handle_growth),
            ("max_thread_growth", profile.thresholds.max_thread_growth),
        ):
            add(name, None, float(limit), "<=")
    return checks


class _EvidenceWriter:
    def __init__(self, root: Path, profile: PerformanceProfile) -> None:
        root = Path(root).expanduser().resolve(strict=False)
        root.mkdir(parents=True, exist_ok=True)
        run_name = datetime.now(timezone.utc).strftime("soak-%Y%m%dT%H%M%SZ-")
        run_name += uuid.uuid4().hex[:12]
        self.directory = root / run_name
        self.directory.mkdir()
        self.profile = profile
        self.total_bytes = 0
        self.checkpoints: deque[tuple[int, Path, str, int]] = deque()
        self.previous_sha256: str | None = None

    def _write(
        self,
        name: str,
        value: Mapping[str, Any],
        *,
        pending_removal_bytes: int = 0,
    ) -> tuple[Path, str]:
        encoded = _encoded_json(value)
        if not 0 <= pending_removal_bytes <= self.total_bytes:
            raise SoakRunnerError("invalid pending evidence rotation size")
        free = shutil.disk_usage(self.directory).free
        if free < self.profile.soak.min_free_disk_bytes + len(encoded):
            raise SoakRunnerError("free disk is below the configured reserve")
        target = self.directory / name
        old_size = target.stat().st_size if target.exists() else 0
        projected = (
            self.total_bytes
            - old_size
            - pending_removal_bytes
            + len(encoded)
        )
        if projected > self.profile.soak.max_evidence_bytes:
            raise SoakRunnerError("evidence byte limit would be exceeded")
        _atomic_write(target, encoded)
        self.total_bytes = self.total_bytes - old_size + len(encoded)
        return target, hashlib.sha256(encoded).hexdigest()

    def write_preflight(self, value: Mapping[str, Any]) -> Path:
        return self._write("preflight.json", value)[0]

    def write_checkpoint(self, sequence: int, value: Mapping[str, Any]) -> Path:
        if len(self.checkpoints) >= self.profile.soak.max_checkpoints:
            old_sequence, old_path, old_digest, old_size = self.checkpoints[0]
            next_sequence = (
                self.checkpoints[1][0]
                if len(self.checkpoints) > 1
                else sequence
            )
            self._write(
                "checkpoint-anchor.json",
                {
                    "schema_version": 1,
                    "last_removed_sequence": old_sequence,
                    "last_removed_sha256": old_digest,
                    "next_sequence": next_sequence,
                    "updated_utc": _utc_now(),
                },
                pending_removal_bytes=old_size,
            )
            try:
                old_path.unlink()
            except OSError as error:
                raise SoakRunnerError(
                    f"cannot remove rotated checkpoint: {error}"
                ) from error
            self.total_bytes -= old_size
            self.checkpoints.popleft()

        checkpoint = dict(value)
        checkpoint["checkpoint_sequence"] = sequence
        checkpoint["previous_checkpoint_sha256"] = self.previous_sha256
        target, digest = self._write(f"checkpoint-{sequence:08d}.json", checkpoint)
        self.checkpoints.append((sequence, target, digest, target.stat().st_size))
        self.previous_sha256 = digest
        return target

    def write_final(self, value: Mapping[str, Any]) -> Path:
        return self._write("final-report.json", value)[0]


def run_soak(
    client: Dnp3MasterClient,
    profile: PerformanceProfile,
    evidence_root: Path | str,
    *,
    sampler: ProcessResourceSampler | None = None,
    stop_requested: Callable[[], bool] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run a bounded read-only soak; never auto-retry a state-changing action."""

    if not isinstance(client, Dnp3MasterClient):
        raise TypeError("client must be Dnp3MasterClient")
    if not isinstance(profile, PerformanceProfile):
        raise TypeError("profile must be PerformanceProfile")
    if client.pid is None or not client.is_running:
        raise SoakRunnerError("client must own a running native host process")
    stopper = stop_requested or (lambda: False)
    writer = _EvidenceWriter(Path(evidence_root), profile)
    writer.write_preflight(
        {
            "schema_version": 1,
            "report_type": "DNP3_READ_ONLY_SOAK_PREFLIGHT",
            "created_utc": _utc_now(),
            "profile": {
                "profile_id": profile.profile_id,
                "sha256": profile.source_sha256,
                "scope": profile.scope,
                "source_path": str(profile.source_path) if profile.source_path else None,
            },
            "host": {"pid": client.pid, "hello": deepcopy(dict(client.hello_info))},
            "limitations": [
                "no state-changing operation is executed or retried",
                "local loopback evidence is not a formal EMS performance conclusion",
            ],
        }
    )

    owned_sampler = sampler is None
    active_sampler = sampler or ProcessResourceSampler(client.pid)
    latencies: dict[str, list[float]] = {
        scenario.scenario_id: [] for scenario in profile.scenarios
    }
    resources: list[ProcessResourceSample] = []
    scenario_counts = {scenario.scenario_id: 0 for scenario in profile.scenarios}
    capture_totals = {
        "received_total": 0,
        "queue_overflow": 0,
        "max_queue_depth": 0,
    }
    failures: list[dict[str, Any]] = []
    failure_count = 0
    consecutive_failures = 0
    reconnect_count = 0
    channel_event_count = 0
    initial_channel_event_count = 0
    channel_event_dropped_total = 0
    observed_channel_state = "OPEN"
    cycle_count = 0
    checkpoint_sequence = 0
    last_successful_scenario: str | None = None
    status = "INCOMPLETE_INTERNAL_ERROR"
    status_reason = "runner did not reach a terminal decision"
    started_utc = _utc_now()
    start = monotonic()
    last_checkpoint = start
    last_resource = -math.inf

    def observe_channel_events() -> None:
        nonlocal channel_event_count
        nonlocal channel_event_dropped_total
        nonlocal observed_channel_state
        nonlocal reconnect_count
        states, dropped = _drain_channel_events(client)
        channel_event_count += len(states)
        channel_event_dropped_total = max(channel_event_dropped_total, dropped)
        if dropped != 0:
            raise _ConnectionEvidenceError(
                "channel event queue overflow makes reconnect history incomplete"
            )
        for state in states:
            if observed_channel_state == "OPEN" and state != "OPEN":
                reconnect_count += 1
            observed_channel_state = state
        if reconnect_count > profile.soak.allowed_reconnects:
            raise _ConnectionEvidenceError(
                "observed reconnect/disconnect episodes exceed profile"
            )

    def checkpoint(now: float) -> None:
        nonlocal checkpoint_sequence
        writer.write_checkpoint(
            checkpoint_sequence,
            {
                "schema_version": 1,
                "report_type": "DNP3_READ_ONLY_SOAK_CHECKPOINT",
                "utc": _utc_now(),
                "elapsed_seconds": max(0.0, now - start),
                "last_successful_scenario": last_successful_scenario,
                "cycle_count": cycle_count,
                "scenario_success_counts": dict(scenario_counts),
                "failure_count": failure_count,
                "consecutive_failures": consecutive_failures,
                "reconnect_count": reconnect_count,
                "channel_events": {
                    "observed_after_baseline": channel_event_count,
                    "initial_events_drained": initial_channel_event_count,
                    "dropped_total": channel_event_dropped_total,
                    "last_state": observed_channel_state,
                },
                "capture": dict(capture_totals),
                "resource_latest": resources[-1].to_mapping() if resources else None,
                "host_running": client.is_running,
            },
        )
        checkpoint_sequence += 1

    try:
        initial_states, initial_dropped = _drain_channel_events(client)
        initial_channel_event_count = len(initial_states)
        channel_event_dropped_total = initial_dropped
        if initial_dropped != 0:
            raise _ConnectionEvidenceError(
                "pre-existing channel event overflow makes reconnect history incomplete"
            )
        initial_status = client.get_status(
            timeout=min(5.0, profile.soak.watchdog_timeout_seconds)
        )
        initial_channel = initial_status.get("channel")
        if not isinstance(initial_channel, Mapping) or initial_channel.get("state") != "OPEN":
            raise _ConnectionEvidenceError(
                "soak must start with an OPEN DNP3 channel"
            )
        observed_channel_state = "OPEN"
        for scenario in profile.scenarios:
            for _ in range(scenario.warmup_iterations):
                execute_read_only_iteration(client, scenario)
                observe_channel_events()
                if scenario.interval_seconds:
                    sleep(scenario.interval_seconds)
        start = monotonic()
        last_checkpoint = start
        checkpoint(start)
        while True:
            now = monotonic()
            elapsed = now - start
            if elapsed >= profile.soak.target_duration_seconds:
                status = "COMPLETED"
                status_reason = "target duration reached"
                break
            if stopper():
                status = "INCOMPLETE_INTERRUPTED"
                status_reason = "stop was requested"
                break
            if not client.is_running:
                status = "INCOMPLETE_HOST_EXIT"
                status_reason = "native host process exited"
                break

            observe_channel_events()
            host_status = client.get_status(
                timeout=min(5.0, profile.soak.watchdog_timeout_seconds)
            )
            channel = host_status.get("channel")
            if not isinstance(channel, Mapping) or not isinstance(
                channel.get("state"), str
            ):
                raise _ConnectionEvidenceError("channel state snapshot is unavailable")
            snapshot_state = channel["state"]
            if snapshot_state not in {"CLOSED", "OPENING", "OPEN", "SHUTDOWN"}:
                raise _ConnectionEvidenceError("channel state snapshot is invalid")
            if observed_channel_state == "OPEN" and snapshot_state != "OPEN":
                reconnect_count += 1
            observed_channel_state = snapshot_state
            if reconnect_count > profile.soak.allowed_reconnects:
                raise _ConnectionEvidenceError(
                    "observed reconnect/disconnect episodes exceed profile"
                )

            if now - last_resource >= profile.soak.resource_sample_interval_seconds:
                if len(resources) >= _MAX_RESOURCE_SAMPLES:
                    status = "INCOMPLETE_RESOURCE_LIMIT"
                    status_reason = "resource sample capacity reached"
                    break
                resources.append(active_sampler.sample())
                last_resource = now

            scenario: ReadPerformanceScenario = profile.scenarios[
                cycle_count % len(profile.scenarios)
            ]
            iteration_started = monotonic()
            try:
                result, capture = execute_read_only_iteration(client, scenario)
                iteration_elapsed = monotonic() - iteration_started
                if iteration_elapsed > profile.soak.watchdog_timeout_seconds:
                    status = "INCOMPLETE_WATCHDOG"
                    status_reason = "one scenario exceeded the watchdog bound"
                    break
                duration = result.timings.get("duration_ms")
                if (
                    isinstance(duration, bool)
                    or not isinstance(duration, (int, float))
                    or not math.isfinite(float(duration))
                ):
                    raise SoakRunnerError("read task duration_ms is unavailable")
                population = latencies[scenario.scenario_id]
                if len(population) >= _MAX_LATENCY_SAMPLES:
                    status = "INCOMPLETE_RESOURCE_LIMIT"
                    status_reason = "latency sample capacity reached"
                    break
                population.append(float(duration))
                scenario_counts[scenario.scenario_id] += 1
                if capture is not None:
                    capture_totals["received_total"] += capture.received_total
                    capture_totals["queue_overflow"] += capture.queue_overflow
                    capture_totals["max_queue_depth"] = max(
                        capture_totals["max_queue_depth"], capture.max_queue_depth
                    )
                consecutive_failures = 0
                last_successful_scenario = scenario.scenario_id
            except Exception as error:
                failure_count += 1
                consecutive_failures += 1
                if len(failures) < _MAX_FAILURE_SAMPLES:
                    failures.append(
                        {
                            "utc": _utc_now(),
                            "scenario_id": scenario.scenario_id,
                            "type": type(error).__name__,
                            "message": str(error)[:2048],
                        }
                    )
                if consecutive_failures > profile.soak.max_consecutive_failures:
                    status = "INCOMPLETE_FAILURE_LIMIT"
                    status_reason = "consecutive scenario failures exceed profile"
                    break
            cycle_count += 1
            now = monotonic()
            if now - last_checkpoint >= profile.soak.checkpoint_interval_seconds:
                checkpoint(now)
                last_checkpoint = now
            if profile.soak.cycle_interval_seconds:
                sleep(profile.soak.cycle_interval_seconds)
        if client.is_running:
            observe_channel_events()
    except KeyboardInterrupt:
        status = "INCOMPLETE_INTERRUPTED"
        status_reason = "operator interrupted the run"
    except _ConnectionEvidenceError as error:
        status = "INCOMPLETE_CONNECTION_LIMIT"
        status_reason = str(error)
    except SoakRunnerError as error:
        status = "INCOMPLETE_RESOURCE_LIMIT"
        status_reason = str(error)
    except Exception as error:
        status = "INCOMPLETE_INTERNAL_ERROR"
        status_reason = f"{type(error).__name__}: {error}"
        if len(failures) < _MAX_FAILURE_SAMPLES:
            failures.append(
                {
                    "utc": _utc_now(),
                    "scenario_id": None,
                    "type": type(error).__name__,
                    "message": str(error)[:2048],
                }
            )
        failure_count += 1
    finally:
        if owned_sampler:
            try:
                active_sampler.close()
            except Exception as error:
                if status == "COMPLETED":
                    status = "INCOMPLETE_INTERNAL_ERROR"
                    status_reason = f"resource sampler cleanup failed: {error}"

    ended = monotonic()
    try:
        checkpoint(ended)
    except Exception as error:
        status = "INCOMPLETE_EVIDENCE_WRITE"
        status_reason = f"final checkpoint failed: {type(error).__name__}: {error}"
    scenario_reports = [
        {
            "scenario_id": scenario.scenario_id,
            "successful_cycles": scenario_counts[scenario.scenario_id],
            "task_latency_ms": _distribution(latencies[scenario.scenario_id]),
        }
        for scenario in profile.scenarios
    ]
    resource_report = _resource_summary(resources)
    checks = _soak_threshold_checks(scenario_reports, resource_report, profile)
    thresholds_passed = all(item["passed"] for item in checks)
    if status == "COMPLETED" and not thresholds_passed:
        status = "COMPLETED_FAILED_THRESHOLDS"
        status_reason = "target duration reached but one or more thresholds failed"
    passed = (
        status == "COMPLETED"
        and failure_count == 0
        and capture_totals["queue_overflow"] == 0
        and channel_event_dropped_total == 0
        and reconnect_count <= profile.soak.allowed_reconnects
        and thresholds_passed
    )
    report: dict[str, Any] = {
        "schema_version": 1,
        "report_type": "DNP3_READ_ONLY_SOAK",
        "status": status,
        "status_reason": status_reason,
        "passed": passed,
        "formal_dut_conclusion": False,
        "evidence_scope": profile.scope,
        "started_utc": started_utc,
        "ended_utc": _utc_now(),
        "target_duration_seconds": profile.soak.target_duration_seconds,
        "elapsed_seconds": max(0.0, ended - start),
        "cycle_count": cycle_count,
        "last_successful_scenario": last_successful_scenario,
        "scenario_results": scenario_reports,
        "failure_count": failure_count,
        "failure_samples": failures,
        "failure_sample_limit": _MAX_FAILURE_SAMPLES,
        "reconnect_count": reconnect_count,
        "channel_events": {
            "observed_after_baseline": channel_event_count,
            "initial_events_drained": initial_channel_event_count,
            "dropped_total": channel_event_dropped_total,
            "last_state": observed_channel_state,
            "source": "native_channel_event_store",
            "scope": "master_session_during_soak",
        },
        "capture": capture_totals,
        "resources": resource_report,
        "threshold_checks": checks,
        "profile": {
            "profile_id": profile.profile_id,
            "sha256": profile.source_sha256,
            "seed": profile.seed,
        },
        "checkpoint_chain_tail_sha256": writer.previous_sha256,
        "evidence_directory": str(writer.directory),
        "evidence_bytes_before_final": writer.total_bytes,
        "final_report_path": str(writer.directory / "final-report.json"),
        "limitations": [
            "wire bytes and DUT resources require separate approved collectors",
            "the bundled same-machine loopback cannot establish formal EMS performance",
            "an interrupted run is never merged with a later run",
        ],
    }
    try:
        writer.write_final(report)
    except Exception as error:
        report["passed"] = False
        report["status"] = "INCOMPLETE_EVIDENCE_WRITE"
        report["status_reason"] = f"{type(error).__name__}: {error}"
        report["final_report_path"] = None
    return report


__all__ = ["SoakRunnerError", "run_soak"]
