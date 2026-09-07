"""Small, bounded SIMULATOR-only acceptance helpers; no field trigger adapter.

These helpers compose public client APIs, never send time sync or Restart, and
never retry a control. They are not a replacement for the LAB acceptance plan.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import time
from typing import Iterator

from .client import Dnp3MasterClient
from .models import (
    AnalogOutputCommand, CrobCommand, HostProcessConfig, MeasurementRecord,
    ReadHeader, ReadTaskResult, TcpConnectionConfig,
)


class SimulatorCheckError(AssertionError):
    """A localized failure; callers can branch on stage instead of message text."""

    def __init__(self, stage: str, message: str) -> None:
        self.stage = stage
        super().__init__(f"[{stage}] {message}")


_OBJECTS = {
    "BI": ("binary_input", 1, 2, 2, 2, 1),
    "AI": ("analog_input", 30, 5, 32, 7, 2),
    "BO": ("binary_output_status", 10, 2, None, None, None),
    "AO": ("analog_output_status", 40, 3, None, None, None),
}


def _check(ok: bool, stage: str, message: str) -> None:
    if not ok:
        raise SimulatorCheckError(stage, message)


def _object(value: object, required: set[str], optional: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - optional:
        raise ValueError(f"expected fields {sorted(required)}, optional {sorted(optional)}")
    return value


def _int(value: object, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"expected integer in {low}..{high}")
    return value


def _number(value: object, low: float, high: float) -> float:
    if type(value) not in (int, float):
        raise ValueError("expected finite number")
    try:
        normalized = float(value)
    except OverflowError as error:
        raise ValueError("expected finite number") from error
    if not math.isfinite(normalized) or not low <= normalized <= high:
        raise ValueError(f"expected finite number in {low}..{high}")
    return normalized


def _value(value: object, kind: str) -> bool | float:
    if kind in ("BI", "BO"):
        if type(value) is not bool:
            raise ValueError("binary value must be true or false, not 0 or 1")
        return value
    return _number(value, -3.4028234663852886e38, 3.4028234663852886e38)


def _bool(value: object) -> bool:
    if type(value) is not bool:
        raise ValueError("expected boolean")
    return value


def _id(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", value):
        raise ValueError("id must be 1..64 ASCII letters/digits/underscore/hyphen, starting with a letter")
    return value


def _rows(value: object) -> list:
    if not isinstance(value, list) or len(value) > 128:
        raise ValueError("expected array of at most 128 entries")
    return value


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _json(path: Path) -> dict:
    with path.open("rb") as stream:
        data = stream.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError("JSON exceeds 1 MiB")
    def reject_constant(value: str) -> None:
        raise ValueError("non-finite JSON number")
    value = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_pairs, parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object")
    return value


@dataclass(frozen=True)
class SimulatorPoint:
    id: str
    kind: str
    index: int
    expected: bool | float | None = None
    tolerance: float = 0.01
    required_flags: int | None = None

    def __post_init__(self) -> None:
        _id(self.id)
        if not isinstance(self.kind, str) or self.kind not in _OBJECTS:
            raise ValueError("invalid point kind")
        _int(self.index, 0, 65535)
        _number(self.tolerance, 0, 1e30)
        if self.expected is not None:
            _value(self.expected, self.kind)
        if self.required_flags is not None:
            _int(self.required_flags, 0, 255)

    def header(self) -> ReadHeader:
        _, group, variation, *_ = _OBJECTS[self.kind]
        return ReadHeader.range16(group, variation, self.index, self.index)

    def matches(self, measurement: MeasurementRecord, *, event: bool = False) -> bool:
        kind, group, variation, event_group, event_variation, _ = _OBJECTS[self.kind]
        return (
            measurement.kind == kind and measurement.index == self.index
            and measurement.group == (event_group if event else group)
            and measurement.variation == (event_variation if event else variation)
            and measurement.is_event is event
            and measurement.source == ("unsolicited" if event else "solicited")
        )


@dataclass(frozen=True)
class SimulatorControl:
    id: str
    kind: str
    index: int
    feedback: SimulatorPoint
    value: bool | float
    expected: bool | float
    tolerance: float

    def __post_init__(self) -> None:
        _id(self.id)
        _int(self.index, 0, 65535)
        if self.kind not in ("BO", "AO") or not isinstance(self.feedback, SimulatorPoint) or self.feedback.kind != self.kind:
            raise ValueError("control requires matching BO/AO feedback kind")
        _value(self.value, self.kind)
        _value(self.expected, self.kind)
        _number(self.tolerance, 0, 1e30)

    def command(self) -> CrobCommand | AnalogOutputCommand:
        if self.kind == "BO":
            return CrobCommand(self.index, "latch_on" if self.value else "latch_off",
                               count=1, on_time_ms=0, off_time_ms=0)
        return AnalogOutputCommand.float32(self.index, self.value)


@dataclass(frozen=True)
class SimulatorEvent:
    id: str
    point: SimulatorPoint
    expected: bool | float
    tolerance: float
    require_timestamp: bool
    require_change: bool

    def __post_init__(self) -> None:
        _id(self.id)
        if not isinstance(self.point, SimulatorPoint) or self.point.kind not in ("BI", "AI"):
            raise ValueError("event requires BI/AI point")
        _value(self.expected, self.point.kind)
        _number(self.tolerance, 0, 1e30)
        _bool(self.require_timestamp)
        _bool(self.require_change)


@dataclass(frozen=True)
class SimulatorSettings:
    source: Path
    runtime_root: Path | None
    connection: TcpConnectionConfig
    points: tuple[SimulatorPoint, ...]
    controls: tuple[SimulatorControl, ...]
    events: tuple[SimulatorEvent, ...]
    class_counts: tuple[tuple[int, int], ...]
    task_timeout: float
    feedback_timeout: float
    event_timeout: float
    max_measurements: int

    def __post_init__(self) -> None:
        if not isinstance(self.connection, TcpConnectionConfig) or not self.connection.simulator:
            raise ValueError("SIMULATOR connection required")
        if not isinstance(self.source, Path) or (self.runtime_root is not None and not isinstance(self.runtime_root, Path)):
            raise ValueError("source and runtime_root must be Paths")
        for rows, row_type in ((self.points, SimulatorPoint), (self.controls, SimulatorControl), (self.events, SimulatorEvent)):
            if not isinstance(rows, tuple) or len(rows) > 128 or not all(isinstance(row, row_type) for row in rows):
                raise ValueError("invalid or unbounded settings rows")
        if not self.points or len({p.id for p in self.points}) != len(self.points) or len({(p.kind, p.index) for p in self.points}) != len(self.points):
            raise ValueError("points must be nonempty and unique")
        scenarios = self.controls + self.events
        if len({case.id for case in scenarios}) != len(scenarios):
            raise ValueError("duplicate scenario id")
        if any(c.feedback not in self.points for c in self.controls) or any(e.point not in self.points for e in self.events):
            raise ValueError("unknown point reference")
        _number(self.task_timeout, 0.1, 60)
        _number(self.feedback_timeout, 0.1, 300)
        _number(self.event_timeout, 0.1, 3600)
        _int(self.max_measurements, 1, 65536)
        if not isinstance(self.class_counts, tuple) or len(self.class_counts) > 3:
            raise ValueError("class_counts must be a tuple of at most three entries")
        seen = set()
        for row in self.class_counts:
            if not isinstance(row, tuple) or len(row) != 2:
                raise ValueError("invalid class expectation")
            cls, count = row
            _int(cls, 1, 3)
            _int(count, 0, self.max_measurements)
            if cls in seen:
                raise ValueError("duplicate class expectation")
            seen.add(cls)


def load_simulator_settings(path: Path) -> SimulatorSettings:
    """Load one private settings file, strictly, without launching/connecting."""
    path = Path(path).resolve()
    try:
        root = _object(_json(path), {"schema_version", "environment", "connection", "points", "controls", "events"},
                       {"runtime_root", "class_counts", "task_timeout", "feedback_timeout", "event_timeout", "max_measurements"})
        if type(root["schema_version"]) is not int or root["schema_version"] != 1 or root["environment"] != "SIMULATOR":
            raise ValueError("schema_version must be 1 and environment must be SIMULATOR")
        connection = _object(root["connection"], {"host"}, {"port", "local_adapter", "master_address", "outstation_address", "connect_timeout", "retry_min", "retry_max", "keep_alive_timeout"})
        connection = TcpConnectionConfig(**connection, simulator=True)
        runtime = root.get("runtime_root")
        if runtime is not None:
            if not isinstance(runtime, str) or not runtime.strip() or len(runtime) > 4096:
                raise ValueError("runtime_root must be a nonempty path or null")
            runtime = (path.parent / runtime).resolve()
        points = {}
        for row in _rows(root["points"]):
            row = _object(row, {"id", "kind", "index"}, {"expected", "tolerance", "required_flags"})
            point_id = _id(row["id"])
            kind = row["kind"]
            if not isinstance(kind, str) or kind not in _OBJECTS or point_id in points:
                raise ValueError("invalid kind or duplicate point id")
            points[point_id] = SimulatorPoint(point_id, kind, _int(row["index"], 0, 65535),
                _value(row["expected"], kind) if row.get("expected") is not None else None,
                _number(row.get("tolerance", 0.01), 0, 1e30),
                _int(row["required_flags"], 0, 255) if row.get("required_flags") is not None else None)
        if not points:
            raise ValueError("at least one readable point is required for the DNP3 probe")
        if len({(p.kind, p.index) for p in points.values()}) != len(points):
            raise ValueError("duplicate point kind/index")
        controls = []
        events = []
        ids = set()
        for field in ("controls", "events"):
            for row in _rows(root[field]):
                if field == "controls":
                    row = _object(row, {"id", "kind", "index", "feedback_point", "value"}, {"expected", "tolerance"})
                else:
                    row = _object(row, {"id", "point", "expected"}, {"tolerance", "require_timestamp", "require_change"})
                scenario_id = _id(row["id"])
                if scenario_id in ids:
                    raise ValueError("duplicate scenario id")
                ids.add(scenario_id)
                point_id = _id(row["feedback_point"] if field == "controls" else row["point"])
                if point_id not in points:
                    raise ValueError("unknown point reference")
                point = points[point_id]
                tolerance = _number(row.get("tolerance", 0.01), 0, 1e30)
                if field == "controls":
                    kind = row["kind"]
                    if kind not in ("BO", "AO") or point.kind != kind:
                        raise ValueError("control feedback must be the corresponding BO/AO static point")
                    value = _value(row["value"], kind)
                    controls.append(SimulatorControl(scenario_id, kind, _int(row["index"], 0, 65535), point,
                        value, _value(row.get("expected", value), kind), tolerance))
                else:
                    if point.kind not in ("BI", "AI"):
                        raise ValueError("only BI/AI have events in this EMS convention")
                    events.append(SimulatorEvent(scenario_id, point, _value(row["expected"], point.kind), tolerance,
                        _bool(row.get("require_timestamp", True)), _bool(row.get("require_change", True))))
        counts = []
        for row in _rows(root.get("class_counts", [])):
            row = _object(row, {"class", "count"})
            counts.append((_int(row["class"], 1, 3), _int(row["count"], 0, 65536)))
        if len({c for c, _ in counts}) != len(counts):
            raise ValueError("duplicate class expectation")
        maximum = _int(root.get("max_measurements", 16384), 1, 65536)
        if any(count > maximum for _, count in counts):
            raise ValueError("class count exceeds max_measurements")
        return SimulatorSettings(path, runtime, connection, tuple(points.values()), tuple(controls), tuple(events), tuple(counts),
            _number(root.get("task_timeout", 5), 0.1, 60),
            _number(root.get("feedback_timeout", 10), 0.1, 300),
            _number(root.get("event_timeout", 30), 0.1, 3600), maximum)
    except (OSError, ValueError, TypeError, RecursionError) as error:
        raise ValueError(f"invalid simulator settings ({path.name}): {error}") from error


@dataclass(frozen=True)
class SimulatorRuntime:
    root: Path
    executable: Path
    matrix: Path

    def host_config(self) -> HostProcessConfig:
        info = _json(self.executable.parent / "build-info.json")
        with self.matrix.open("rb") as stream:
            content = stream.read(8 * 1024 * 1024 + 1)
        if len(content) > 8 * 1024 * 1024:
            raise ValueError("capability matrix exceeds 8 MiB")
        digest = hashlib.sha256(content).hexdigest()
        config = HostProcessConfig(self.executable, expected_capability_matrix_sha256=digest)
        if (info.get("host_version") != config.expected_host_version
                or info.get("capability_matrix_sha256") != digest
                or info.get("opendnp3_version") != "3.1.2"
                or info.get("target_architecture") != "x64"
                or type(info.get("protocol_schema_version")) is not int
                or info.get("protocol_schema_version") != 1):
            raise ValueError("runtime metadata/version/matrix mismatch; rebuild or recopy the complete package")
        return config


def find_simulator_runtime(settings: SimulatorSettings) -> SimulatorRuntime:
    """Nearest enclosing package/repo, max eight levels; never search PATH/disk."""
    roots = (settings.runtime_root,) if settings.runtime_root else tuple(settings.source.parents)[:8]
    for root in roots:
        matrix = root / "config/capability_matrix.csv"
        for relative in ("bin", "out/build/windows-msvc-release/bin"):
            executable = root / relative / "dnp3-master-host.exe"
            if executable.is_file():
                runtime = SimulatorRuntime(root, executable, matrix)
                try:
                    runtime.host_config()
                except (OSError, ValueError, RecursionError) as error:
                    raise SimulatorCheckError("INSTALLATION", str(error)) from error
                return runtime
    raise SimulatorCheckError("INSTALLATION", "host not found: build Release or set runtime_root to the unpacked package root")


def check_read(result: ReadTaskResult) -> None:
    _check(result.task_status == "SUCCESS" and result.task_started is True, "DNP3_READ",
           "read did not succeed; check DNP3 link addresses and EMS protocol configuration")
    bits = result.iin.get("bits")
    _check(isinstance(bits, (list, tuple)) and all(isinstance(bit, str) for bit in bits), "DNP3_READ", "invalid IIN")
    _check(not any(bit.startswith(("IIN2.0.", "IIN2.1.", "IIN2.2.")) for bit in bits),
           "DNP3_READ", "EMS returned request-error IIN; check object/variation/qualifier support")
    _check(result.iin.get("observation_window_dropped", 0) == 0, "DNP3_READ", "IIN window lost observations")
    _check(result.return_mode == "detail" and result.summary.get("received_total") == len(result.measurements),
           "DNP3_READ", "incomplete measurement result")


def value_matches(value: object, expected: bool | float, tolerance: float) -> bool:
    if type(expected) is bool:
        return type(value) is bool and value is expected
    try:
        return type(value) in (int, float) and math.isfinite(value) and abs(value - expected) <= tolerance
    except OverflowError:
        return False


def _quality(point: SimulatorPoint, measurement: MeasurementRecord) -> None:
    if point.required_flags is not None:
        _check(measurement.flags_valid and measurement.flags_raw is not None
               and measurement.flags_raw & point.required_flags == point.required_flags,
               "QUALITY", f"{point.id}: required quality bits absent")


def read_simulator_point(client: Dnp3MasterClient, point: SimulatorPoint, timeout: float) -> MeasurementRecord:
    result = client.read([point.header()], timeout=timeout, max_measurements=16, request_timeout=timeout + 1)
    check_read(result)
    matches = [m for m in result.measurements if point.matches(m)]
    _check(len(matches) == 1 and len(result.measurements) == 1, "POINT_MAPPING", f"{point.id}: expected exactly one matching static object/index")
    _quality(point, matches[0])
    return matches[0]


@contextmanager
def simulator_session(settings: SimulatorSettings, host: HostProcessConfig) -> Iterator[Dnp3MasterClient]:
    """Own one host, diagnose setup layers, and never mask an original failure."""
    _check(settings.connection.simulator, "CONFIGURATION", "SIMULATOR connection required")
    client = Dnp3MasterClient(host)
    failed = False
    stage = "HOST_START"
    try:
        try:
            client.start()
            stage = "TCP_CONNECT"
            client.connect(settings.connection, timeout=settings.connection.connect_timeout + 2)
            stage = "DNP3_READ"
            read_simulator_point(client, settings.points[0], settings.task_timeout)
        except SimulatorCheckError:
            raise
        except Exception as error:
            raise SimulatorCheckError(stage, f"{type(error).__name__}; "
                + ("check EMS listener, firewall, local adapter and route" if stage == "TCP_CONNECT"
                   else "check host runtime/version" if stage == "HOST_START"
                   else "TCP connected but DNP3 read failed; check link addresses and object support")) from error
        yield client
    except BaseException:
        failed = True
        raise
    finally:
        diagnostics = client.close()
        if not failed:
            _check(diagnostics.cleanup_error is None, "CLEANUP", "host required forced cleanup")


def run_simulator_control(client: Dnp3MasterClient, settings: SimulatorSettings, case: SimulatorControl) -> None:
    _check(client.simulator_mode, "CONFIGURATION", "this helper only accepts SIMULATOR sessions")
    result = client.direct_operate([case.command()], timeout=settings.task_timeout, request_timeout=settings.task_timeout + 1)
    _check(result.all_success and result.task_status == "SUCCESS" and not result.execution_uncertain
           and len(result.point_results) == 1 and result.point_results[0].index == case.index,
           "CONTROL_RESULT", f"{case.id}: FC5 command failed; no command retried")
    deadline = time.monotonic() + settings.feedback_timeout
    while (remaining := deadline - time.monotonic()) > 0:
        measurement = read_simulator_point(client, case.feedback, min(settings.task_timeout, max(0.1, remaining)))
        if value_matches(measurement.value, case.expected, case.tolerance):
            return
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    raise SimulatorCheckError("CONTROL_FEEDBACK", f"{case.id}: acknowledged command but feedback mismatch/timeout; no command retried or restored")


def observe_simulator_event(client: Dnp3MasterClient, settings: SimulatorSettings, case: SimulatorEvent) -> MeasurementRecord:
    """Observe externally produced events. Receive time cannot prove trigger causality."""
    baseline = read_simulator_point(client, case.point, settings.task_timeout)
    classes = (_OBJECTS[case.point.kind][5],)
    enabled = client.enable_unsolicited(classes, timeout=settings.task_timeout, request_timeout=settings.task_timeout + 1)
    _check(enabled.task_status == "SUCCESS" and enabled.task_started, "EVENT_ENABLE", "EMS did not enable unsolicited reporting")
    failed = False
    try:
        print(f"DNP3 EVENT READY: {case.id}; change the external simulator signal now", flush=True)
        deadline = time.monotonic() + settings.event_timeout
        while (remaining := deadline - time.monotonic()) > 0:
            wait = min(1.0, remaining)
            batch = client.wait_unsolicited(wait_timeout=wait, max_events=256, request_timeout=wait + 1)
            _check(batch.enabled and batch.classes == classes and batch.summary.get("dropped_total") == 0,
                   "EVENT_QUEUE", "unsolicited disabled/class mismatch/event loss")
            _check(all(m.source == "unsolicited" and m.session_id == batch.session_id for m in batch.measurements),
                   "EVENT_SESSION", "event source/session mismatch")
            for measurement in batch.measurements:
                if not case.point.matches(measurement, event=True):
                    continue
                _quality(case.point, measurement)
                if not value_matches(measurement.value, case.expected, case.tolerance):
                    continue
                if case.require_change and value_matches(measurement.value, baseline.value, case.tolerance):
                    continue
                _check(not case.require_timestamp or measurement.dnp3_timestamp_ms is not None,
                       "EVENT_TIMESTAMP", "matching event lacks absolute timestamp")
                return measurement
        raise SimulatorCheckError("EVENT_TIMEOUT", f"{case.id}: no expected event/change observed; check simulator signal, mapping and unsolicited support")
    except BaseException:
        failed = True
        raise
    finally:
        try:
            disabled = client.disable_unsolicited(classes, timeout=settings.task_timeout, request_timeout=settings.task_timeout + 1)
            _check(disabled.task_status == "SUCCESS", "EVENT_DISABLE", "EMS did not disable unsolicited reporting")
        except Exception:
            if not failed:
                raise
