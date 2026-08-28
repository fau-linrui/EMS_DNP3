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
from typing import Any, BinaryIO
import uuid

from ._win32_job import ProcessJob
from .errors import (
    ClientStateError,
    HostCommandError,
    HostExitedError,
    HostProtocolError,
    HostStartError,
    HostTimeoutError,
)
from .models import HostProcessConfig, HostProcessDiagnostics, TcpConnectionConfig


_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")
_STDOUT_EOF = object()


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
        return value.decode("utf-8", errors="replace")


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
        self._hello_info: dict[str, Any] | None = None
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
        with self._request_lock:
            if self._state is not _State.RUNNING:
                state_name = "closed" if self._state in {_State.CLOSED, _State.BROKEN} else "not started"
                raise ClientStateError(f"client is {state_name}")
            deadline = self.config.request_timeout if timeout is None else timeout
            if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or deadline <= 0:
                raise ValueError("timeout must be a positive number")
            request_params = {} if params is None else dict(params)
            return self._exchange(command, request_params, float(deadline))

    def get_status(self, *, timeout: float | None = None) -> Mapping[str, Any]:
        result = self.request("get_status", timeout=timeout)
        if not isinstance(result, dict):
            self._abort_process("get_status returned a non-object result")
            raise HostProtocolError(
                "get_status result must be an object",
                self.diagnostics,
            )
        return result

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
        return self._mapping_result(
            "connect",
            self.request("connect", config.to_params(), timeout=request_timeout),
        )

    def disconnect(self, *, timeout: float | None = None) -> Mapping[str, Any]:
        """Close the active master, channel, and manager in lifecycle order."""

        return self._mapping_result(
            "disconnect",
            self.request("disconnect", timeout=timeout),
        )

    def wait_event(
        self,
        *,
        wait_timeout: float = 0.0,
        max_events: int = 64,
        request_timeout: float | None = None,
    ) -> Mapping[str, Any]:
        """Consume a bounded batch of queued channel-state events."""

        if (
            isinstance(wait_timeout, bool)
            or not isinstance(wait_timeout, (int, float))
            or not math.isfinite(wait_timeout)
            or not 0 <= wait_timeout <= 60
        ):
            raise ValueError("wait_timeout must be between 0 and 60 seconds")
        if (
            isinstance(max_events, bool)
            or not isinstance(max_events, int)
            or not 1 <= max_events <= 256
        ):
            raise ValueError("max_events must be an integer between 1 and 256")
        wait_timeout_ms = round(float(wait_timeout) * 1000)
        exchange_timeout = (
            max(self.config.request_timeout, float(wait_timeout) + 1.0)
            if request_timeout is None
            else request_timeout
        )
        return self._mapping_result(
            "wait_event",
            self.request(
                "wait_event",
                {"timeout_ms": wait_timeout_ms, "max_events": max_events},
                timeout=exchange_timeout,
            ),
        )

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
                return self._last_diagnostics
            if self._state is _State.NEW:
                self._state = _State.CLOSED
                self._last_diagnostics = self._snapshot_diagnostics()
                return self._last_diagnostics
            if self._state is _State.BROKEN:
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

    def _read_stdout(self, stream: BinaryIO) -> None:
        limit = self.config.max_response_bytes
        try:
            while True:
                raw = stream.readline(limit + 2)
                if not raw:
                    break
                self._stdout_tail.append(raw)
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
                        self._stdout_tail.append(raw)
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

        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            self._raise_exited(command)
        try:
            process.stdin.write(encoded)
            process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            self._abort_process(f"failed to write request: {error}")
            raise HostExitedError(
                f"host exited while writing request '{command}'",
                self.diagnostics,
            ) from error

        try:
            item = self._stdout_queue.get(timeout=timeout)
        except queue.Empty as error:
            self._abort_process(f"request '{command}' timed out after {timeout:.3f}s")
            raise HostTimeoutError(
                f"request '{command}' timed out after {timeout:.3f}s",
                self.diagnostics,
            ) from error

        if self._stdout_overflow.is_set():
            self._abort_process("stdout response queue overflow")
            raise HostProtocolError("stdout response queue overflow", self.diagnostics)

        if item is _STDOUT_EOF:
            self._raise_exited(command)
        if isinstance(item, _StreamFailure):
            self._abort_process(item.message)
            raise HostProtocolError(item.message, self.diagnostics)
        if not isinstance(item, bytes):
            self._abort_process("stdout reader returned an unknown item")
            raise HostProtocolError("stdout reader returned an unknown item", self.diagnostics)

        try:
            return self._parse_response(item, request_id)
        except _ProtocolViolation as error:
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
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            return
        try:
            process.wait(timeout=0)
        except subprocess.TimeoutExpired:
            return
        for thread in (self._stdout_thread, self._stderr_thread):
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=2)
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
        self._stdout_thread = None
        self._stderr_thread = None

    def __del__(self) -> None:
        try:
            if getattr(self, "_state", _State.CLOSED) is not _State.CLOSED:
                self.close()
        except Exception:
            pass
