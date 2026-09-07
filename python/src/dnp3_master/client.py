"""Synchronous, single-in-flight NDJSON client for dnp3-master-host."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum, auto
import json
import math
import os
import queue
import re
import subprocess
import threading
import time
from typing import Any, BinaryIO, Sequence
import uuid

from ._win32_job import ProcessJob
from .errors import (
    ClientStateError,
    HostCommandError,
    HostExitedError,
    HostProtocolError,
    HostStartError,
    HostTimeoutError,
    SafetyIncidentConfigurationError,
    SafetyIncidentError,
    SafetyIncidentPersistenceError,
)
from .models import (
    AnalogOutputCommand,
    CaptureConfig,
    CaptureResult,
    CommandTaskResult,
    CrobCommand,
    HostProcessConfig,
    HostProcessDiagnostics,
    ReadHeader,
    ReadTaskResult,
    TcpConnectionConfig,
    UnsolicitedBatchResult,
    UnsolicitedControlResult,
)
from .safety_incidents import SafetyIncidentStore


_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")
_SAFETY_TOKEN_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")
_DIAGNOSTIC_SAFETY_TOKEN_PATTERN = re.compile(
    r'("safety_token"\s*:\s*")[0-9a-fA-F]{32}(")'
)
_STDOUT_EOF = object()
_STDIN_STOP = object()
_TYPED_API_COMMANDS = frozenset(
    {
        "hello",
        "shutdown",
        "connect",
        "disconnect",
        "get_status",
        "stats",
        "wait_event",
        "integrity_poll",
        "class_poll",
        "read",
        "enable_unsolicited",
        "disable_unsolicited",
        "wait_unsolicited",
        "select_and_operate",
        "direct_operate",
        "capture.begin",
        "capture.progress",
        "capture.end",
    }
)


class _State(Enum):
    NEW = auto()
    STARTING = auto()
    RUNNING = auto()
    CLOSING = auto()
    BROKEN = auto()
    CLOSED = auto()


class _ProtocolViolation(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _StreamFailure:
    message: str


@dataclass
class _PendingWrite:
    payload: bytes
    completed: threading.Event
    error: Exception | None = None


class _TailBuffer:
    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._chunks: deque[bytes] = deque()
        self._size = 0
        self._lock = threading.Lock()

    def append(self, chunk: bytes) -> None:
        if not chunk:
            return
        with self._lock:
            self._chunks.append(bytes(chunk))
            self._size += len(chunk)
            while self._size > self._capacity and self._chunks:
                overflow = self._size - self._capacity
                first = self._chunks[0]
                if len(first) <= overflow:
                    self._chunks.popleft()
                    self._size -= len(first)
                else:
                    self._chunks[0] = first[overflow:]
                    self._size -= overflow

    def text(self) -> str:
        with self._lock:
            value = b"".join(self._chunks)
        decoded = value.decode("utf-8", errors="replace")
        return _DIAGNOSTIC_SAFETY_TOKEN_PATTERN.sub(
            r"\1<redacted>\2", decoded
        )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _ProtocolViolation(f"response contains duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise _ProtocolViolation(f"response contains non-finite number: {value}")


class Dnp3MasterClient:
    """Own a native host process and expose its v1 single-request protocol."""

    def __init__(self, config: HostProcessConfig) -> None:
        self.config = config
        self._state = _State.NEW
        self._request_lock = threading.RLock()
        self._process: subprocess.Popen[bytes] | None = None
        self._job: ProcessJob | None = None
        self._job_was_assigned = False
        self._stdout_queue: queue.Queue[object] = queue.Queue(maxsize=16)
        self._stdout_overflow = threading.Event()
        self._stdout_tail = _TailBuffer(config.diagnostic_tail_bytes)
        self._stderr_tail = _TailBuffer(config.diagnostic_tail_bytes)
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stdin_thread: threading.Thread | None = None
        self._stdin_queue: queue.Queue[object] = queue.Queue(maxsize=1)
        self._io_stopping = threading.Event()
        self._request_write_started = False
        self._hello_info: dict[str, Any] | None = None
        self._safety_token: str | None = None
        self._active_dut_id: str | None = None
        self._simulator_mode = False
        self._safety_incident_store = (
            SafetyIncidentStore(config.safety_incident_directory)
            if config.safety_incident_directory is not None
            else None
        )
        self._request_prefix = f"py-{os.getpid()}-{uuid.uuid4().hex[:12]}"
        self._request_counter = 0
        self._cleanup_error: str | None = None
        self._last_diagnostics = HostProcessDiagnostics(
            pid=None,
            returncode=None,
            stdout_tail="",
            stderr_tail="",
            job_object_assigned=False,
        )

    def __enter__(self) -> Dnp3MasterClient:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def pid(self) -> int | None:
        process = self._process
        return process.pid if process is not None else self._last_diagnostics.pid

    @property
    def is_running(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None and self._state in {
            _State.STARTING,
            _State.RUNNING,
            _State.CLOSING,
        }

    @property
    def hello_info(self) -> Mapping[str, Any]:
        if self._hello_info is None:
            raise ClientStateError("hello handshake has not completed")
        return deepcopy(self._hello_info)

    @property
    def diagnostics(self) -> HostProcessDiagnostics:
        if self._process is None:
            return self._last_diagnostics
        return self._snapshot_diagnostics()

    @property
    def state_change_authorized(self) -> bool:
        """Whether the active session owns a non-exported short-lived safety token."""

        return self._safety_token is not None

    @property
    def simulator_mode(self) -> bool:
        """Whether the last successful connection explicitly selected simulation."""

        return self._simulator_mode

    def start(self) -> Mapping[str, Any]:
        with self._request_lock:
            if self._state is _State.RUNNING:
                return self.hello_info
            if self._state is not _State.NEW:
                raise ClientStateError("client cannot be started after it has been closed")

            executable = self.config.executable
            if not executable.is_file():
                self._state = _State.CLOSED
                raise HostStartError(
                    f"host executable does not exist: {executable}",
                    self.diagnostics,
                )
            if self.config.working_directory is not None and not self.config.working_directory.is_dir():
                self._state = _State.CLOSED
                raise HostStartError(
                    f"working directory does not exist: {self.config.working_directory}",
                    self.diagnostics,
                )

            self._state = _State.STARTING
            try:
                self._spawn_process()
                result = self._exchange("hello", {}, self.config.startup_timeout)
                self._validate_hello(result)
            except (HostStartError, HostTimeoutError, HostExitedError, HostProtocolError) as error:
                if self._process is not None and self._process.poll() is None:
                    self._abort_process("startup failed")
                if self._state is not _State.BROKEN:
                    self._state = _State.BROKEN
                if isinstance(error, HostProtocolError):
                    raise HostProtocolError(str(error), self.diagnostics) from error
                raise
            except Exception as error:
                self._abort_process(f"startup failed: {error}")
                raise HostStartError(
                    f"failed to start native host: {error}",
                    self.diagnostics,
                ) from error

            self._hello_info = dict(result)
            self._state = _State.RUNNING
            return deepcopy(self._hello_info)

    def request(
        self,
        command: str,
        params: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        """Send an extension command that has no typed client API.

        Commands implemented by this package must go through their typed method.
        This keeps lifecycle state, safety tokens, result validation, and the
        persistent uncertain-control lock synchronized with the native host.
        """

        if command in _TYPED_API_COMMANDS:
            raise ClientStateError(
                f"raw request for {command!r} is blocked; use the typed "
                "Dnp3MasterClient method"
            )
        return self._request(command, params, timeout=timeout)

    def _request(
        self,
        command: str,
        params: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        with self._request_lock:
            if self._state is not _State.RUNNING:
                state_name = "closed" if self._state in {_State.CLOSED, _State.BROKEN} else "not started"
                raise ClientStateError(f"client is {state_name}")
            deadline = self.config.request_timeout if timeout is None else timeout
            try:
                normalized_deadline = float(deadline)
            except (TypeError, ValueError, OverflowError):
                normalized_deadline = math.nan
            if (
                isinstance(deadline, bool)
                or not isinstance(deadline, (int, float))
                or not math.isfinite(normalized_deadline)
                or normalized_deadline <= 0
            ):
                raise ValueError("timeout must be a positive finite number")
            request_params = {} if params is None else dict(params)
            return self._exchange(command, request_params, normalized_deadline)

    def get_status(self, *, timeout: float | None = None) -> Mapping[str, Any]:
        result = self._request("get_status", timeout=timeout)
        if not isinstance(result, dict):
            self._abort_process("get_status returned a non-object result")
            raise HostProtocolError(
                "get_status result must be an object",
                self.diagnostics,
            )
        return result

    def get_stats(self, *, timeout: float | None = None) -> Mapping[str, Any]:
        """Return bounded host/channel counters and an explicit scope statement."""

        return self._mapping_result("stats", self._request("stats", timeout=timeout))

    def connect(
        self,
        config: TcpConnectionConfig,
        *,
        timeout: float | None = None,
    ) -> Mapping[str, Any]:
        """Open one OpenDNP3 TCP master session and wait for channel OPEN."""

        if not isinstance(config, TcpConnectionConfig):
            raise TypeError("config must be a TcpConnectionConfig")
        request_timeout = (
            max(self.config.request_timeout, config.connect_timeout + 1.0)
            if timeout is None
            else timeout
        )
        result = self._mapping_result(
            "connect",
            self._request("connect", config.to_params(), timeout=request_timeout),
        )
        safety = result.get("safety")
        if not isinstance(safety, Mapping):
            self._abort_process("connect returned invalid safety metadata")
            raise HostProtocolError(
                "connect result safety must be an object", self.diagnostics
            )
        authorized = safety.get("state_change_authorized")
        token = safety.get("safety_token")
        if type(authorized) is not bool or (
            authorized
            and (not isinstance(token, str) or not _SAFETY_TOKEN_PATTERN.fullmatch(token))
        ) or (not authorized and token is not None) or (
            authorized
            and not config.simulator
            and (
                config.safety is None
                or config.safety.allow_state_change is not True
            )
        ) or (config.simulator and (
            not authorized or safety.get("environment") != "SIMULATOR"
        )) or (not config.simulator and safety.get("environment") == "SIMULATOR"):
            self._abort_process("connect returned invalid safety metadata")
            raise HostProtocolError(
                "connect result contains inconsistent safety authorization",
                self.diagnostics,
            )
        self._safety_token = token if authorized else None
        self._simulator_mode = config.simulator
        self._active_dut_id = (
            config.safety.dut_id
            if authorized and config.safety is not None
            else None
        )
        public_result = deepcopy(result)
        public_safety = dict(public_result["safety"])
        public_safety.pop("safety_token", None)
        public_safety["token_exposed"] = False
        public_result["safety"] = public_safety
        return public_result

    def disconnect(self, *, timeout: float | None = None) -> Mapping[str, Any]:
        """Close the active master, channel, and manager in lifecycle order."""

        result = self._mapping_result(
            "disconnect",
            self._request("disconnect", timeout=timeout),
        )
        self._safety_token = None
        return result

    def active_safety_incident(
        self, *, dut_id: str | None = None
    ) -> Mapping[str, Any] | None:
        """Return the active uncertain-control lock without requiring a live host."""

        with self._request_lock:
            store, resolved_dut_id = self._incident_context(dut_id)
            return store.get_active(resolved_dut_id)

    def acknowledge_safety_incident(
        self,
        incident_id: str,
        *,
        acknowledged_by: str,
        readback_summary: str,
        readback: object,
        evidence_reference: str,
        dut_id: str | None = None,
    ) -> Mapping[str, Any]:
        """Archive one lock after independent readback and explicit acknowledgment."""

        with self._request_lock:
            store, resolved_dut_id = self._incident_context(dut_id)
            return store.acknowledge(
                dut_id=resolved_dut_id,
                incident_id=incident_id,
                acknowledged_by=acknowledged_by,
                readback_summary=readback_summary,
                readback=readback,
                evidence_reference=evidence_reference,
            )

    def wait_event(
        self,
        *,
        wait_timeout: float = 0.0,
        max_events: int = 64,
        request_timeout: float | None = None,
    ) -> Mapping[str, Any]:
        """Consume a bounded batch of queued channel-state events."""

        try:
            normalized_wait_timeout = float(wait_timeout)
        except (TypeError, ValueError, OverflowError):
            normalized_wait_timeout = math.nan
        if (
            isinstance(wait_timeout, bool)
            or not isinstance(wait_timeout, (int, float))
            or not math.isfinite(normalized_wait_timeout)
            or not 0 <= normalized_wait_timeout <= 60
        ):
            raise ValueError("wait_timeout must be between 0 and 60 seconds")
        if (
            isinstance(max_events, bool)
            or not isinstance(max_events, int)
            or not 1 <= max_events <= 256
        ):
            raise ValueError("max_events must be an integer between 1 and 256")
        wait_timeout_ms = round(normalized_wait_timeout * 1000)
        exchange_timeout = (
            max(self.config.request_timeout, normalized_wait_timeout + 1.0)
            if request_timeout is None
            else request_timeout
        )
        return self._mapping_result(
            "wait_event",
            self._request(
                "wait_event",
                {"timeout_ms": wait_timeout_ms, "max_events": max_events},
                timeout=exchange_timeout,
            ),
        )

    def enable_unsolicited(
        self,
        classes: Sequence[int] = (1, 2, 3),
        *,
        timeout: float = 5.0,
        request_timeout: float | None = None,
    ) -> UnsolicitedControlResult:
        """Explicitly enable Class 1/2/3 unsolicited responses."""

        return self._unsolicited_control(
            "enable_unsolicited",
            classes,
            timeout=timeout,
            request_timeout=request_timeout,
        )

    def disable_unsolicited(
        self,
        classes: Sequence[int] = (1, 2, 3),
        *,
        timeout: float = 5.0,
        request_timeout: float | None = None,
    ) -> UnsolicitedControlResult:
        """Explicitly disable Class 1/2/3 unsolicited responses."""

        return self._unsolicited_control(
            "disable_unsolicited",
            classes,
            timeout=timeout,
            request_timeout=request_timeout,
        )

    def wait_unsolicited(
        self,
        *,
        wait_timeout: float = 0.0,
        max_events: int = 256,
        request_timeout: float | None = None,
    ) -> UnsolicitedBatchResult:
        """Consume one bounded batch from the persistent unsolicited queue."""

        try:
            normalized_wait_timeout = float(wait_timeout)
        except (TypeError, ValueError, OverflowError):
            normalized_wait_timeout = math.nan
        if (
            isinstance(wait_timeout, bool)
            or not isinstance(wait_timeout, (int, float))
            or not math.isfinite(normalized_wait_timeout)
            or not 0 <= normalized_wait_timeout <= 60
        ):
            raise ValueError("wait_timeout must be between 0 and 60 seconds")
        if (
            isinstance(max_events, bool)
            or not isinstance(max_events, int)
            or not 1 <= max_events <= 256
        ):
            raise ValueError("max_events must be an integer between 1 and 256")
        exchange_timeout = (
            max(self.config.request_timeout, normalized_wait_timeout + 1.0)
            if request_timeout is None
            else request_timeout
        )
        result = self._mapping_result(
            "wait_unsolicited",
            self._request(
                "wait_unsolicited",
                {
                    "timeout_ms": round(normalized_wait_timeout * 1000),
                    "max_events": max_events,
                },
                timeout=exchange_timeout,
            ),
        )
        try:
            return UnsolicitedBatchResult.from_mapping(result)
        except (TypeError, ValueError, KeyError) as error:
            self._abort_process("wait_unsolicited returned an invalid result")
            raise HostProtocolError(
                f"wait_unsolicited result is invalid: {error}", self.diagnostics
            ) from error

    def begin_capture(
        self,
        config: CaptureConfig,
        *,
        request_timeout: float | None = None,
    ) -> CaptureResult:
        """Start the session's only bounded native measurement capture."""

        if not isinstance(config, CaptureConfig):
            raise TypeError("config must be a CaptureConfig")
        return self._capture_result(
            "capture.begin",
            config.to_params(),
            request_timeout=request_timeout,
        )

    def capture_progress(
        self,
        capture_id: str,
        *,
        request_timeout: float | None = None,
    ) -> CaptureResult:
        """Return a non-consuming bounded snapshot for the exact capture ID."""

        normalized_id = self._capture_id(capture_id)
        return self._capture_result(
            "capture.progress",
            {"capture_id": normalized_id},
            request_timeout=request_timeout,
        )

    def end_capture(
        self,
        capture_id: str,
        *,
        drain_timeout: float = 5.0,
        request_timeout: float | None = None,
    ) -> CaptureResult:
        """Stop accepting objects, drain bounded storage, and finalize capture."""

        normalized_id = self._capture_id(capture_id)
        try:
            normalized_drain_timeout = float(drain_timeout)
        except (TypeError, ValueError, OverflowError):
            normalized_drain_timeout = math.nan
        if (
            isinstance(drain_timeout, bool)
            or not isinstance(drain_timeout, (int, float))
            or not math.isfinite(normalized_drain_timeout)
            or not 0.05 <= normalized_drain_timeout <= 300
        ):
            raise ValueError("drain_timeout must be between 0.05 and 300 seconds")
        drain_timeout_ms = round(normalized_drain_timeout * 1000)
        exchange_timeout = (
            max(self.config.request_timeout, normalized_drain_timeout + 1.0)
            if request_timeout is None
            else request_timeout
        )
        return self._capture_result(
            "capture.end",
            {
                "capture_id": normalized_id,
                "drain_timeout_ms": drain_timeout_ms,
            },
            request_timeout=exchange_timeout,
        )

    @staticmethod
    def _capture_id(value: str) -> str:
        if (
            not isinstance(value, str)
            or len(value) > 64
            or _TOKEN_PATTERN.fullmatch(value) is None
        ):
            raise ValueError(
                "capture_id must be a 1-64 byte ASCII token"
            )
        return value

    def _capture_result(
        self,
        command: str,
        params: Mapping[str, Any],
        *,
        request_timeout: float | None,
    ) -> CaptureResult:
        result = self._mapping_result(
            command,
            self._request(command, params, timeout=request_timeout),
        )
        try:
            return CaptureResult.from_mapping(result)
        except (TypeError, ValueError, KeyError) as error:
            self._abort_process(f"{command} returned an invalid capture result")
            raise HostProtocolError(
                f"{command} result is invalid: {error}", self.diagnostics
            ) from error

    def integrity_poll(
        self,
        *,
        timeout: float = 5.0,
        max_measurements: int = 10_000,
        return_mode: str = "detail",
        request_timeout: float | None = None,
    ) -> ReadTaskResult:
        """Read Class 0 static data and Class 1/2/3 events once."""

        params, task_timeout = self._read_options(
            timeout, max_measurements, return_mode
        )
        return self._read_task_result(
            "integrity_poll",
            params,
            task_timeout=task_timeout,
            request_timeout=request_timeout,
        )

    def class_poll(
        self,
        classes: Sequence[int] = (1, 2, 3),
        *,
        timeout: float = 5.0,
        max_measurements: int = 10_000,
        return_mode: str = "detail",
        request_timeout: float | None = None,
    ) -> ReadTaskResult:
        """Read one or more event classes; Class 0 belongs to integrity_poll."""

        if isinstance(classes, (str, bytes)):
            raise TypeError("classes must be a sequence containing 1, 2, and/or 3")
        normalized = tuple(classes)
        if not 1 <= len(normalized) <= 3:
            raise ValueError("classes must contain between one and three items")
        if any(type(value) is not int or value not in {1, 2, 3} for value in normalized):
            raise ValueError("classes may contain only the integers 1, 2, and 3")
        if len(set(normalized)) != len(normalized):
            raise ValueError("classes must not contain duplicate values")

        params, task_timeout = self._read_options(
            timeout, max_measurements, return_mode
        )
        params["classes"] = list(normalized)
        return self._read_task_result(
            "class_poll",
            params,
            task_timeout=task_timeout,
            request_timeout=request_timeout,
        )

    def read(
        self,
        headers: Sequence[ReadHeader],
        *,
        timeout: float = 5.0,
        max_measurements: int = 10_000,
        return_mode: str = "detail",
        request_timeout: float | None = None,
    ) -> ReadTaskResult:
        """Perform one bounded multi-header READ through OpenDNP3."""

        if isinstance(headers, (str, bytes)):
            raise TypeError("headers must be a sequence of ReadHeader objects")
        normalized = tuple(headers)
        if not 1 <= len(normalized) <= 64:
            raise ValueError("headers must contain between one and 64 items")
        if not all(isinstance(header, ReadHeader) for header in normalized):
            raise TypeError("every header must be a ReadHeader")

        params, task_timeout = self._read_options(
            timeout, max_measurements, return_mode
        )
        params["headers"] = [header.to_params() for header in normalized]
        return self._read_task_result(
            "read",
            params,
            task_timeout=task_timeout,
            request_timeout=request_timeout,
        )

    def select_and_operate(
        self,
        commands: Sequence[CrobCommand | AnalogOutputCommand],
        *,
        timeout: float = 10.0,
        request_timeout: float | None = None,
    ) -> CommandTaskResult:
        """Execute one bounded Select-Before-Operate batch without automatic retry."""

        return self._command_task_result(
            "select_and_operate",
            commands,
            timeout=timeout,
            response_mode="response",
            request_timeout=request_timeout,
        )

    def direct_operate(
        self,
        commands: Sequence[CrobCommand | AnalogOutputCommand],
        *,
        timeout: float = 10.0,
        response_mode: str = "response",
        request_timeout: float | None = None,
    ) -> CommandTaskResult:
        """Execute Direct Operate; no-response is reported unsupported by this backend."""

        return self._command_task_result(
            "direct_operate",
            commands,
            timeout=timeout,
            response_mode=response_mode,
            request_timeout=request_timeout,
        )

    def _command_task_result(
        self,
        command: str,
        commands: Sequence[CrobCommand | AnalogOutputCommand],
        *,
        timeout: float,
        response_mode: str,
        request_timeout: float | None,
    ) -> CommandTaskResult:
        # Keep validation, dispatch, and incident handling in one critical
        # section, so another caller cannot race an uncertain result.
        with self._request_lock:
            return self._command_task_result_locked(
                command, commands, timeout=timeout,
                response_mode=response_mode, request_timeout=request_timeout,
            )

    def _command_task_result_locked(
        self,
        command: str,
        commands: Sequence[CrobCommand | AnalogOutputCommand],
        *,
        timeout: float,
        response_mode: str,
        request_timeout: float | None,
    ) -> CommandTaskResult:
        if self._safety_token is None:
            raise ClientStateError(
                "state-changing command is locked; connect with a LabSafetyConfig "
                "whose allow_state_change is explicitly true, or simulator=True "
                "for a simulated device"
            )
        if isinstance(commands, (str, bytes)):
            raise TypeError("commands must be a sequence of command model objects")
        normalized = tuple(commands)
        if not 1 <= len(normalized) <= 256:
            raise ValueError("commands must contain between one and 256 items")
        if not all(isinstance(item, (CrobCommand, AnalogOutputCommand)) for item in normalized):
            raise TypeError(
                "every command must be a CrobCommand or AnalogOutputCommand"
            )
        identities = {
            (
                "crob" if isinstance(item, CrobCommand) else item.command_type,
                item.index,
            )
            for item in normalized
        }
        if len(identities) != len(normalized):
            raise ValueError("commands must not repeat the same type and point index")
        try:
            normalized_timeout = float(timeout)
        except (TypeError, ValueError, OverflowError):
            normalized_timeout = math.nan
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(normalized_timeout)
            or not 0.05 <= normalized_timeout <= 300
        ):
            raise ValueError("timeout must be between 0.05 and 300 seconds")
        if response_mode not in {"response", "no_response"}:
            raise ValueError("response_mode must be 'response' or 'no_response'")

        task_timeout = normalized_timeout
        exchange_timeout = (
            max(self.config.request_timeout, task_timeout + 1.0)
            if request_timeout is None
            else request_timeout
        )
        command_payload = {
            "operation": command,
            "timeout_ms": round(task_timeout * 1000),
            "response_mode": response_mode,
            "commands": [item.to_params() for item in normalized],
        }
        if not self._simulator_mode:
            store, dut_id = self._incident_context(self._active_dut_id)
            store.assert_clear(dut_id)
        params = {
            "safety_token": self._safety_token,
            "timeout_ms": command_payload["timeout_ms"],
            "response_mode": command_payload["response_mode"],
            "commands": command_payload["commands"],
        }
        self._request_write_started = False
        try:
            return self._execute_command_task(
                command, normalized, command_payload, params, exchange_timeout,
            )
        except (KeyboardInterrupt, SystemExit) as error:
            if self._request_write_started:
                self._handle_uncertain_command(
                    operation=command,
                    command_payload=command_payload,
                    commands=normalized,
                    request_id="interrupted-command-exchange",
                    error_code=type(error).__name__.upper(),
                    execution_uncertain=True,
                    may_still_execute=True,
                    original_error=error,
                )
            raise
        finally:
            self._request_write_started = False

    def _execute_command_task(
        self,
        command: str,
        normalized: tuple[CrobCommand | AnalogOutputCommand, ...],
        command_payload: Mapping[str, Any],
        params: Mapping[str, Any],
        exchange_timeout: float,
    ) -> CommandTaskResult:
        try:
            raw_result = self._request(command, params, timeout=exchange_timeout)
            result = self._mapping_result(command, raw_result)
        except HostCommandError as error:
            if (
                error.code == "RESPONSE_TIMEOUT"
                or error.details.get("execution_uncertain") is True
                or error.details.get("may_still_execute") is True
            ):
                self._handle_uncertain_command(
                    operation=command,
                    command_payload=command_payload,
                    commands=normalized,
                    request_id=error.request_id,
                    error_code=error.code,
                    execution_uncertain=bool(
                        error.details.get("execution_uncertain", True)
                    ),
                    may_still_execute=bool(
                        error.details.get("may_still_execute", True)
                    ),
                    original_error=error,
                )
            raise
        except (HostTimeoutError, HostExitedError, HostProtocolError) as error:
            self._handle_uncertain_command(
                operation=command,
                command_payload=command_payload,
                commands=normalized,
                request_id="python-host-exchange",
                error_code=type(error).__name__.upper(),
                execution_uncertain=True,
                may_still_execute=True,
                original_error=error,
            )
            raise
        try:
            parsed = CommandTaskResult.from_mapping(result)
            self._validate_command_correlation(command, normalized, parsed)
        except (TypeError, ValueError, KeyError) as error:
            protocol_error = HostProtocolError(
                f"{command} result is invalid: {error}", self.diagnostics
            )
            self._handle_uncertain_command(
                operation=command,
                command_payload=command_payload,
                commands=normalized,
                request_id="invalid-command-result",
                error_code="INVALID_COMMAND_RESULT",
                execution_uncertain=True,
                may_still_execute=True,
                original_error=protocol_error,
            )
            raise protocol_error from error
        unsafe_points = tuple(
            point
            for point in parsed.point_results
            if point.requires_manual_readback
        )
        if parsed.execution_uncertain or unsafe_points:
            uncertain_error = HostCommandError(
                request_id=f"command-task-{parsed.task_id}",
                code="UNCERTAIN_COMMAND_RESULT",
                message=(
                    "simulator command outcome is uncertain; start a new client session"
                    if self._simulator_mode else
                    "command result requires independent readback before the "
                    "session can be reused"
                ),
                details={
                    "execution_uncertain": True,
                    "may_still_execute": True,
                    "automatic_retry_safe": False,
                    "manual_readback_points": len(unsafe_points),
                    "unsafe_status_raw": sorted(
                        {point.status_raw for point in unsafe_points}
                    ),
                },
            )
            self._handle_uncertain_command(
                operation=command,
                command_payload=command_payload,
                commands=normalized,
                request_id=uncertain_error.request_id,
                error_code=uncertain_error.code,
                execution_uncertain=True,
                may_still_execute=True,
                original_error=uncertain_error,
            )
            raise uncertain_error
        return parsed

    def _validate_command_correlation(
        self,
        operation: str,
        commands: Sequence[CrobCommand | AnalogOutputCommand],
        result: CommandTaskResult,
    ) -> None:
        if result.mode != operation:
            raise ValueError(
                f"command result mode {result.mode!r} does not match {operation!r}"
            )
        if len(result.point_results) != len(commands):
            raise ValueError(
                "command point_results count does not match the submitted batch"
            )
        seen_ordinals: set[int] = set()
        for point in result.point_results:
            requested = point.requested
            if requested is None:
                raise ValueError("command point result is missing request correlation")
            ordinal = requested.get("request_ordinal")
            if type(ordinal) is not int or not 0 <= ordinal < len(commands):
                raise ValueError("command request_ordinal is invalid")
            if ordinal in seen_ordinals:
                raise ValueError("command result repeats a request_ordinal")
            seen_ordinals.add(ordinal)
            expected = commands[ordinal]
            expected_type = (
                "crob" if isinstance(expected, CrobCommand) else expected.command_type
            )
            if (
                point.index != expected.index
                or requested.get("type") != expected_type
                or requested.get("index") != expected.index
            ):
                raise ValueError(
                    "command result request correlation does not match the "
                    "submitted type/index"
                )
        if seen_ordinals != set(range(len(commands))):
            raise ValueError("command result does not correlate every submitted point")

    def _incident_context(
        self, dut_id: str | None
    ) -> tuple[SafetyIncidentStore, str]:
        store = self._safety_incident_store
        if store is None:
            raise SafetyIncidentConfigurationError(
                "state-changing operations require HostProcessConfig."
                "safety_incident_directory so uncertain results remain locked "
                "across Python processes"
            )
        resolved_dut_id = dut_id or self._active_dut_id
        if resolved_dut_id is None:
            raise SafetyIncidentConfigurationError(
                "a DUT identity is required to inspect or update a safety incident"
            )
        return store, resolved_dut_id

    def _handle_uncertain_command(
        self,
        *,
        operation: str,
        command_payload: object,
        commands: Sequence[CrobCommand | AnalogOutputCommand],
        request_id: str,
        error_code: str,
        execution_uncertain: bool,
        may_still_execute: bool,
        original_error: BaseException,
    ) -> None:
        if self._simulator_mode:
            # The exchange is no longer trustworthy, but a simulator needs no
            # persistent incident or operator readback before a fresh session.
            self._abort_process(
                f"simulator command '{operation}' has an uncertain outcome"
            )
            details = getattr(original_error, "details", None)
            if not isinstance(details, dict):
                details = {}
                setattr(original_error, "details", details)
            details.update({
                "environment": "SIMULATOR",
                "execution_uncertain": execution_uncertain,
                "may_still_execute": may_still_execute,
                "persistent_safety_lock": False,
                "required_action": "START_NEW_SESSION",
            })
            return
        incident: Mapping[str, Any] | None = None
        persistence_error: Exception | None = None
        try:
            store, dut_id = self._incident_context(self._active_dut_id)
            recorded = store.record_uncertain(
                dut_id=dut_id,
                operation=operation,
                command_payload=command_payload,
                points=[
                    {
                        "type": (
                            "crob"
                            if isinstance(item, CrobCommand)
                            else item.command_type
                        ),
                        "index": item.index,
                    }
                    for item in commands
                ],
                request_id=request_id,
                error_code=error_code,
                execution_uncertain=execution_uncertain,
                may_still_execute=may_still_execute,
            )
            incident = recorded.incident
            lock_created = recorded.created
        except (SafetyIncidentError, OSError, TypeError, ValueError) as error:
            persistence_error = error
        finally:
            self._safety_token = None
            self._abort_process(
                f"state-changing command '{operation}' has an uncertain outcome"
            )

        if persistence_error is not None:
            raise SafetyIncidentPersistenceError(
                "an uncertain command outcome invalidated the host, but the "
                "persistent safety lock could not be verified; do not issue "
                "another control until manual readback and incident-store repair",
                original_error=(
                    original_error
                    if isinstance(original_error, HostCommandError)
                    else None
                ),
                details={
                    "operation": operation,
                    "persistence_error": str(persistence_error),
                    "required_action": "MANUAL_READBACK_AND_STORE_REPAIR",
                },
            ) from original_error

        assert incident is not None
        incident_id = str(incident["incident_id"])
        incident_details = {
            "incident_id": incident_id,
            "persistent_safety_lock": True,
            "incident_lock_created": lock_created,
            "required_action": incident["required_action"],
        }
        if isinstance(original_error, HostCommandError):
            original_error.details.update(incident_details)
        else:
            setattr(original_error, "incident_id", incident_id)
            details = getattr(original_error, "details", None)
            if isinstance(details, dict):
                details.update(incident_details)
            else:
                setattr(original_error, "details", incident_details)

    @staticmethod
    def _event_classes(classes: Sequence[int]) -> tuple[int, ...]:
        if isinstance(classes, (str, bytes)):
            raise TypeError("classes must be a sequence containing 1, 2, and/or 3")
        normalized = tuple(classes)
        if not 1 <= len(normalized) <= 3:
            raise ValueError("classes must contain between one and three items")
        if any(
            type(value) is not int or value not in {1, 2, 3}
            for value in normalized
        ):
            raise ValueError("classes may contain only the integers 1, 2, and 3")
        if len(set(normalized)) != len(normalized):
            raise ValueError("classes must not contain duplicate values")
        return normalized

    def _unsolicited_control(
        self,
        command: str,
        classes: Sequence[int],
        *,
        timeout: float,
        request_timeout: float | None,
    ) -> UnsolicitedControlResult:
        normalized_classes = self._event_classes(classes)
        try:
            normalized_timeout = float(timeout)
        except (TypeError, ValueError, OverflowError):
            normalized_timeout = math.nan
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(normalized_timeout)
            or not 0.05 <= normalized_timeout <= 300
        ):
            raise ValueError("timeout must be between 0.05 and 300 seconds")
        exchange_timeout = (
            max(self.config.request_timeout, normalized_timeout + 1.0)
            if request_timeout is None
            else request_timeout
        )
        result = self._mapping_result(
            command,
            self._request(
                command,
                {
                    "timeout_ms": round(normalized_timeout * 1000),
                    "classes": list(normalized_classes),
                },
                timeout=exchange_timeout,
            ),
        )
        try:
            return UnsolicitedControlResult.from_mapping(result)
        except (TypeError, ValueError, KeyError) as error:
            self._abort_process(f"{command} returned an invalid result")
            raise HostProtocolError(
                f"{command} result is invalid: {error}", self.diagnostics
            ) from error

    @staticmethod
    def _read_options(
        timeout: float,
        max_measurements: int,
        return_mode: str,
    ) -> tuple[dict[str, Any], float]:
        try:
            normalized_timeout = float(timeout)
        except (TypeError, ValueError, OverflowError):
            normalized_timeout = math.nan
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(normalized_timeout)
            or not 0.05 <= normalized_timeout <= 300
        ):
            raise ValueError("timeout must be between 0.05 and 300 seconds")
        if (
            isinstance(max_measurements, bool)
            or not isinstance(max_measurements, int)
            or not 1 <= max_measurements <= 1_000_000
        ):
            raise ValueError(
                "max_measurements must be an integer between 1 and 1000000"
            )
        if return_mode not in {"detail", "summary"}:
            raise ValueError("return_mode must be 'detail' or 'summary'")
        return (
            {
                "timeout_ms": round(normalized_timeout * 1000),
                "max_measurements": max_measurements,
                "return_mode": return_mode,
            },
            normalized_timeout,
        )

    def _read_task_result(
        self,
        command: str,
        params: Mapping[str, Any],
        *,
        task_timeout: float,
        request_timeout: float | None,
    ) -> ReadTaskResult:
        exchange_timeout = (
            max(self.config.request_timeout, task_timeout + 1.0)
            if request_timeout is None
            else request_timeout
        )
        result = self._mapping_result(
            command,
            self._request(command, params, timeout=exchange_timeout),
        )
        try:
            return ReadTaskResult.from_mapping(result)
        except (TypeError, ValueError, KeyError) as error:
            self._abort_process(f"{command} returned an invalid read result")
            raise HostProtocolError(
                f"{command} result is invalid: {error}", self.diagnostics
            ) from error

    def _mapping_result(self, command: str, result: Any) -> Mapping[str, Any]:
        if not isinstance(result, dict):
            self._abort_process(f"{command} returned a non-object result")
            raise HostProtocolError(
                f"{command} result must be an object",
                self.diagnostics,
            )
        return result

    def close(self) -> HostProcessDiagnostics:
        with self._request_lock:
            if self._state is _State.CLOSED:
                self._safety_token = None
                return self._last_diagnostics
            if self._state is _State.NEW:
                self._safety_token = None
                self._state = _State.CLOSED
                self._last_diagnostics = self._snapshot_diagnostics()
                return self._last_diagnostics
            if self._state is _State.BROKEN:
                self._safety_token = None
                self._state = _State.CLOSED
                self._last_diagnostics = self._snapshot_diagnostics()
                return self._last_diagnostics

            self._state = _State.CLOSING
            process = self._process
            if process is not None and process.poll() is None:
                try:
                    self._exchange("shutdown", {}, self.config.shutdown_timeout)
                    process.wait(timeout=self.config.shutdown_timeout)
                except Exception as error:
                    self._cleanup_error = f"graceful shutdown failed: {error}"
                    if process.poll() is None:
                        self._abort_process(self._cleanup_error)
            elif process is not None:
                self._cleanup_error = (
                    "host exited with code "
                    f"{process.returncode} before shutdown was requested"
                )

            self._finish_process_io()
            if self._job is not None:
                self._job.close()
                self._job = None
            self._state = _State.CLOSED
            self._safety_token = None
            self._store_diagnostics_and_release_process()
            return self._last_diagnostics

    def _spawn_process(self) -> None:
        environment = os.environ.copy()
        if self.config.environment is not None:
            environment.update(self.config.environment)
        command = [str(self.config.executable), *self.config.arguments]
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

        try:
            job = ProcessJob()
            process = subprocess.Popen(
                command,
                cwd=(
                    str(self.config.working_directory)
                    if self.config.working_directory is not None
                    else None
                ),
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                creationflags=creation_flags,
                close_fds=True,
            )
        except (OSError, ValueError) as error:
            if "job" in locals():
                job.close()
            self._state = _State.BROKEN
            raise HostStartError(f"failed to create host process: {error}", self.diagnostics) from error

        self._job = job
        self._process = process
        try:
            job.assign(process.pid)
            self._job_was_assigned = job.assigned
        except Exception as error:
            process.kill()
            process.wait(timeout=2)
            job.close()
            self._job = None
            self._finish_process_io()
            self._state = _State.BROKEN
            self._store_diagnostics_and_release_process()
            raise HostStartError(
                f"failed to assign host to Windows Job Object: {error}",
                self._last_diagnostics,
            ) from error

        assert process.stdout is not None
        assert process.stderr is not None
        assert process.stdin is not None
        self._stdin_thread = threading.Thread(
            target=self._write_stdin,
            args=(process.stdin,),
            name=f"dnp3-host-stdin-{process.pid}",
            daemon=True,
        )
        self._stdout_thread = threading.Thread(
            target=self._read_stdout,
            args=(process.stdout,),
            name=f"dnp3-host-stdout-{process.pid}",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr,
            args=(process.stderr,),
            name=f"dnp3-host-stderr-{process.pid}",
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()
        self._stdin_thread.start()

    def _write_stdin(self, stream: BinaryIO) -> None:
        while not self._io_stopping.is_set():
            pending = self._stdin_queue.get()
            if not isinstance(pending, _PendingWrite) or self._io_stopping.is_set():
                return
            try:
                remaining = memoryview(pending.payload)
                while remaining:
                    written = stream.write(remaining)
                    if written is None or written <= 0:
                        raise BrokenPipeError("stdin write made no progress")
                    remaining = remaining[written:]
                stream.flush()
            except Exception as error:
                pending.error = error
            finally:
                pending.completed.set()
            # Do not retain a previous command payload while idle.
            del pending
            if 'remaining' in locals():
                del remaining

    def _read_stdout(self, stream: BinaryIO) -> None:
        limit = self.config.max_response_bytes
        try:
            while True:
                raw = stream.readline(limit + 2)
                if not raw:
                    break
                if raw.endswith(b"\n"):
                    payload = raw[:-1]
                    if payload.endswith(b"\r"):
                        payload = payload[:-1]
                    if len(payload) > limit:
                        self._enqueue_stdout(
                            _StreamFailure("response line exceeds max_response_bytes")
                        )
                    else:
                        self._enqueue_stdout(raw)
                    continue

                if len(raw) > limit:
                    while raw and not raw.endswith(b"\n"):
                        raw = stream.readline(4096)
                    self._enqueue_stdout(
                        _StreamFailure("response line exceeds max_response_bytes")
                    )
                else:
                    self._enqueue_stdout(raw)
        except Exception as error:
            self._enqueue_stdout(_StreamFailure(f"stdout reader failed: {error}"))
        finally:
            self._enqueue_stdout(_STDOUT_EOF)

    def _enqueue_stdout(self, item: object) -> None:
        try:
            self._stdout_queue.put_nowait(item)
        except queue.Full:
            self._stdout_overflow.set()
            self._stdout_tail.append(b"[stdout response queue overflow]\n")

    def _read_stderr(self, stream: BinaryIO) -> None:
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                self._stderr_tail.append(chunk)
        except Exception as error:
            self._stderr_tail.append(f"\n[stderr reader failed: {error}]".encode("utf-8"))

    def _exchange(self, command: str, params: Mapping[str, Any], timeout: float) -> Any:
        self._request_write_started = False
        if not isinstance(command, str) or not command or len(command.encode("ascii", errors="ignore")) > 64:
            raise ValueError("command must be a non-empty ASCII token of at most 64 bytes")
        if not _TOKEN_PATTERN.fullmatch(command):
            raise ValueError("command contains characters outside the v1 token alphabet")

        request_id = self._next_request_id()
        envelope = {
            "schema_version": 1,
            "id": request_id,
            "cmd": command,
            "params": dict(params),
        }
        try:
            encoded = (
                json.dumps(
                    envelope,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n"
            )
        except (TypeError, ValueError) as error:
            raise ValueError(f"request is not JSON serializable: {error}") from error
        if len(encoded) - 1 > self.config.max_request_bytes:
            raise ValueError("request exceeds max_request_bytes")

        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            self._raise_exited(command)
        deadline = time.monotonic() + timeout
        pending = _PendingWrite(encoded, threading.Event())
        try:
            # Conservative dispatch boundary: a write may start immediately
            # after enqueue. A cancellation beyond here cannot prove no send.
            self._request_write_started = True
            self._stdin_queue.put_nowait(pending)
            if not pending.completed.wait(max(0.0, deadline - time.monotonic())):
                raise queue.Empty
            if pending.error is not None:
                self._abort_process("failed to write request")
                raise HostExitedError(
                    f"host failed while writing request '{command}'",
                    self.diagnostics,
                ) from pending.error
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise queue.Empty
            item = self._stdout_queue.get(timeout=remaining)
        except queue.Empty as error:
            self._abort_process(f"request '{command}' timed out after {timeout:.3f}s")
            raise HostTimeoutError(
                f"request '{command}' timed out after {timeout:.3f}s",
                self.diagnostics,
            ) from error
        except queue.Full as error:
            self._abort_process("stdin request queue overflow")
            raise HostProtocolError("stdin request queue overflow", self.diagnostics) from error
        except (KeyboardInterrupt, SystemExit):
            # Typed commands must persist the incident before destroying the
            # session; other interrupted exchanges still invalidate the pipe.
            if command not in {"direct_operate", "select_and_operate"}:
                self._abort_process("host exchange interrupted")
            raise

        if self._stdout_overflow.is_set():
            self._abort_process("stdout response queue overflow")
            raise HostProtocolError("stdout response queue overflow", self.diagnostics)

        if item is _STDOUT_EOF:
            self._raise_exited(command)
        if isinstance(item, _StreamFailure):
            self._stdout_tail.append(f"[{item.message}]\n".encode("utf-8"))
            self._abort_process(item.message)
            raise HostProtocolError(item.message, self.diagnostics)
        if not isinstance(item, bytes):
            self._abort_process("stdout reader returned an unknown item")
            raise HostProtocolError("stdout reader returned an unknown item", self.diagnostics)

        try:
            return self._parse_response(item, request_id)
        except _ProtocolViolation as error:
            # Normal responses can contain EMS values and the private safety
            # token. Retain stdout only when the protocol itself is malformed;
            # exposed diagnostics also redact token-shaped safety fields.
            self._stdout_tail.append(item)
            self._abort_process(str(error))
            raise HostProtocolError(str(error), self.diagnostics) from error

    def _parse_response(self, raw: bytes, expected_id: str) -> Any:
        if not raw.endswith(b"\n"):
            raise _ProtocolViolation("response is not terminated by LF")
        payload = raw[:-1]
        if payload.endswith(b"\r"):
            payload = payload[:-1]
        if payload.startswith(b"\xef\xbb\xbf"):
            raise _ProtocolViolation("response contains a UTF-8 BOM")
        try:
            text = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise _ProtocolViolation("response is not valid UTF-8") from error
        try:
            response = json.loads(
                text,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite,
            )
        except _ProtocolViolation:
            raise
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            raise _ProtocolViolation("response is not valid JSON") from error

        if not isinstance(response, dict):
            raise _ProtocolViolation("response root must be an object")
        if type(response.get("schema_version")) is not int or response["schema_version"] != 1:
            raise _ProtocolViolation("response schema_version must be 1")
        if response.get("id") != expected_id:
            raise _ProtocolViolation("response id does not match the in-flight request")
        if type(response.get("ok")) is not bool:
            raise _ProtocolViolation("response ok field must be a boolean")

        if response["ok"]:
            if set(response) != {"schema_version", "id", "ok", "result"}:
                raise _ProtocolViolation("success response envelope has unexpected fields")
            return response["result"]

        if set(response) != {"schema_version", "id", "ok", "error"}:
            raise _ProtocolViolation("error response envelope has unexpected fields")
        host_error = response["error"]
        if not isinstance(host_error, dict) or set(host_error) != {"code", "message", "details"}:
            raise _ProtocolViolation("error response body is invalid")
        code = host_error["code"]
        message = host_error["message"]
        details = host_error["details"]
        if not isinstance(code, str) or not code:
            raise _ProtocolViolation("error code must be a non-empty string")
        if not isinstance(message, str) or not isinstance(details, dict):
            raise _ProtocolViolation("error message/details types are invalid")
        raise HostCommandError(
            request_id=expected_id,
            code=code,
            message=message,
            details=details,
        )

    def _validate_hello(self, result: Any) -> None:
        if not isinstance(result, dict):
            raise HostProtocolError("hello result must be an object", self.diagnostics)
        required_strings = (
            "host_version",
            "backend",
            "git_commit",
            "platform",
            "capability_matrix_version",
            "capability_matrix_sha256",
        )
        for field_name in required_strings:
            if not isinstance(result.get(field_name), str) or not result[field_name]:
                raise HostProtocolError(
                    f"hello field '{field_name}' must be a non-empty string",
                    self.diagnostics,
                )
        expected_host_version = self.config.expected_host_version
        if (
            expected_host_version is not None
            and result["host_version"] != expected_host_version
        ):
            raise HostProtocolError(
                "native host version does not match the Python package: "
                f"expected {expected_host_version!r}, received "
                f"{result['host_version']!r}",
                self.diagnostics,
            )
        expected_matrix_sha256 = self.config.expected_capability_matrix_sha256
        if (
            expected_matrix_sha256 is not None
            and result["capability_matrix_sha256"].lower()
            != expected_matrix_sha256
        ):
            raise HostProtocolError(
                "native host capability matrix hash does not match the configured "
                "pytest matrix",
                self.diagnostics,
            )
        if not re.fullmatch(r"[0-9a-fA-F]{64}", result["capability_matrix_sha256"]):
            raise HostProtocolError(
                "hello capability_matrix_sha256 must contain 64 hexadecimal characters",
                self.diagnostics,
            )
        if result["backend"] == "opendnp3" and result.get("backend_version") != "3.1.2":
            raise HostProtocolError(
                "native host OpenDNP3 version must be the pinned 3.1.2",
                self.diagnostics,
            )
        commands = result.get("supported_commands")
        if not isinstance(commands, list) or not all(isinstance(value, str) for value in commands):
            raise HostProtocolError("hello supported_commands must be a string array", self.diagnostics)
        if not {"hello", "shutdown"}.issubset(commands):
            raise HostProtocolError("hello does not advertise required lifecycle commands", self.diagnostics)
        if not isinstance(result.get("capabilities"), dict):
            raise HostProtocolError("hello capabilities must be an object", self.diagnostics)

    def _next_request_id(self) -> str:
        self._request_counter += 1
        return f"{self._request_prefix}-{self._request_counter}"

    def _raise_exited(self, command: str) -> None:
        process = self._process
        if process is not None:
            try:
                process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                pass
        self._abort_process(f"host exited before responding to '{command}'")
        raise HostExitedError(
            f"host exited before responding to '{command}'",
            self.diagnostics,
        )

    def _abort_process(self, reason: str) -> None:
        self._cleanup_error = self._cleanup_error or reason
        self._safety_token = None
        process = self._process
        if self._job is not None:
            self._job.close()
            self._job = None
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=0.5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                    process.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        self._finish_process_io()
        self._state = _State.BROKEN
        self._store_diagnostics_and_release_process()

    def _finish_process_io(self) -> None:
        self._io_stopping.set()
        try:
            self._stdin_queue.put_nowait(_STDIN_STOP)
        except queue.Full:
            pass
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            return
        try:
            process.wait(timeout=0)
        except subprocess.TimeoutExpired:
            return
        for thread in (self._stdin_thread, self._stdout_thread, self._stderr_thread):
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=2)
        # A cancellation can stop the writer before it takes the queued item.
        # Release any remaining payload along with the terminated process.
        while True:
            try:
                self._stdin_queue.get_nowait()
            except queue.Empty:
                break
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def _snapshot_diagnostics(self) -> HostProcessDiagnostics:
        process = self._process
        return HostProcessDiagnostics(
            pid=process.pid if process is not None else self._last_diagnostics.pid,
            returncode=process.poll() if process is not None else self._last_diagnostics.returncode,
            stdout_tail=self._stdout_tail.text(),
            stderr_tail=self._stderr_tail.text(),
            job_object_assigned=self._job_was_assigned,
            cleanup_error=self._cleanup_error,
            dump_path=None,
        )

    def _store_diagnostics_and_release_process(self) -> None:
        self._last_diagnostics = self._snapshot_diagnostics()
        self._process = None
        self._stdin_thread = None
        self._stdout_thread = None
        self._stderr_thread = None

    def __del__(self) -> None:
        try:
            if getattr(self, "_state", _State.CLOSED) is not _State.CLOSED:
                self.close()
        except Exception:
            pass
