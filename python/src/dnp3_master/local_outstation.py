"""Controller for the bundled loopback-only DNP3 test outstation."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import math
from pathlib import Path
import queue
import socket
import subprocess
import threading
import time
from types import MappingProxyType
from typing import Any, BinaryIO, Mapping


_MAX_CONTROL_LINE_BYTES = 64 * 1024
_MAX_RESPONSE_LINE_BYTES = 1024 * 1024
_STDERR_TAIL_BYTES = 16 * 1024
_QUEUE_CAPACITY = 128


class LocalOutstationError(RuntimeError):
    """The bundled local outstation could not be started or controlled."""


class LocalOutstationRequestError(LocalOutstationError):
    """The local outstation rejected a strict control request."""

    def __init__(
        self,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.details = MappingProxyType(dict(details or {}))


@dataclass(frozen=True, slots=True)
class _StreamFailure:
    message: str


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _unused_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        return int(reservation.getsockname()[1])


class LocalTestOutstation:
    """Run and control the packaged stateful outstation on ``127.0.0.1`` only."""

    def __init__(
        self,
        executable: Path | str,
        *,
        port: int | None = None,
        startup_timeout: float = 5.0,
        request_timeout: float = 3.0,
        shutdown_timeout: float = 3.0,
    ) -> None:
        self.executable = Path(executable).expanduser().resolve(strict=False)
        if port is not None and (
            isinstance(port, bool) or not isinstance(port, int)
        ):
            raise TypeError("port must be an integer")
        self.port = _unused_loopback_port() if port is None else port
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        for field_name, value in (
            ("startup_timeout", startup_timeout),
            ("request_timeout", request_timeout),
            ("shutdown_timeout", shutdown_timeout),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{field_name} must be a finite number")
            if not math.isfinite(float(value)) or not 0.1 <= float(value) <= 60.0:
                raise ValueError(f"{field_name} must be between 0.1 and 60 seconds")
        self.startup_timeout = float(startup_timeout)
        self.request_timeout = float(request_timeout)
        self.shutdown_timeout = float(shutdown_timeout)
        self._process: subprocess.Popen[bytes] | None = None
        self._stdout_queue: queue.Queue[bytes | _StreamFailure] = queue.Queue(
            maxsize=_QUEUE_CAPACITY
        )
        self._stderr_chunks: deque[bytes] = deque()
        self._stderr_size = 0
        self._stderr_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._request_sequence = 0
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def stderr_tail(self) -> str:
        with self._stderr_lock:
            raw = b"".join(self._stderr_chunks)
        return raw.decode("utf-8", errors="replace")

    def __enter__(self) -> LocalTestOutstation:
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.close()

    def start(self) -> LocalTestOutstation:
        if self._process is not None:
            raise LocalOutstationError("local outstation has already been started")
        if not self.executable.is_file():
            raise FileNotFoundError(
                f"local test outstation does not exist: {self.executable}"
            )
        self._process = subprocess.Popen(
            [str(self.executable), "--port", str(self.port)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert self._process.stdout is not None
        assert self._process.stderr is not None
        self._stdout_thread = threading.Thread(
            target=self._read_stdout,
            args=(self._process.stdout,),
            name=f"dnp3-local-outstation-stdout-{self._process.pid}",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr,
            args=(self._process.stderr,),
            name=f"dnp3-local-outstation-stderr-{self._process.pid}",
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()
        try:
            ready_line = self._next_line(self.startup_timeout, "startup")
            ready = self._parse_json_line(ready_line, "readiness")
            if ready != {"ready": True, "port": self.port}:
                raise LocalOutstationError(
                    f"unexpected local outstation readiness data: {ready!r}"
                )
            hello = self.request("hello", {})
            if (
                hello.get("role") != "local_test_outstation"
                or hello.get("bind_host") != "127.0.0.1"
                or hello.get("port") != self.port
            ):
                raise LocalOutstationError(
                    f"unexpected local outstation identity: {hello!r}"
                )
            return self
        except Exception:
            self._terminate()
            raise

    def request(
        self,
        command: str,
        params: Mapping[str, Any],
        *,
        timeout: float | None = None,
    ) -> Mapping[str, Any]:
        if not self.is_running or self._process is None:
            raise LocalOutstationError("local outstation is not running")
        if not isinstance(command, str) or not command:
            raise ValueError("command must be a non-empty string")
        if not isinstance(params, Mapping):
            raise TypeError("params must be a mapping")
        if timeout is not None and (
            isinstance(timeout, bool) or not isinstance(timeout, (int, float))
        ):
            raise TypeError("timeout must be a finite number")
        request_timeout = self.request_timeout if timeout is None else float(timeout)
        if not math.isfinite(request_timeout) or not 0.1 <= request_timeout <= 60.0:
            raise ValueError("timeout must be between 0.1 and 60 seconds")

        with self._request_lock:
            self._request_sequence += 1
            request_id = f"local-{self._request_sequence}"
            request = {
                "schema_version": 1,
                "id": request_id,
                "cmd": command,
                "params": dict(params),
            }
            try:
                encoded = json.dumps(
                    request,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            except (TypeError, ValueError) as error:
                raise ValueError(f"local outstation request is not valid JSON: {error}") from error
            if len(encoded) > _MAX_CONTROL_LINE_BYTES:
                raise ValueError(
                    f"local outstation request exceeds {_MAX_CONTROL_LINE_BYTES} bytes"
                )
            assert self._process.stdin is not None
            try:
                self._process.stdin.write(encoded + b"\n")
                self._process.stdin.flush()
            except (BrokenPipeError, OSError) as error:
                failure = LocalOutstationError(
                    "failed to write to the local outstation; " + self._diagnostic_suffix()
                )
                self._terminate()
                raise failure from error
            try:
                response = self._parse_json_line(
                    self._next_line(request_timeout, command), "response"
                )
            except LocalOutstationError:
                self._terminate()
                raise
            if set(response) != {"schema_version", "id", "ok", "result"} and set(
                response
            ) != {"schema_version", "id", "ok", "error"}:
                raise self._fatal_protocol_error(
                    "local outstation returned an invalid response envelope: "
                    f"{response!r}"
                )
            if (
                type(response.get("schema_version")) is not int
                or response.get("schema_version") != 1
                or response.get("id") != request_id
            ):
                raise self._fatal_protocol_error(
                    f"local outstation response correlation failed: {response!r}"
                )
            if response.get("ok") is True:
                result = response.get("result")
                if not isinstance(result, dict):
                    raise self._fatal_protocol_error(
                        "local outstation success result must be an object"
                    )
                return MappingProxyType(dict(result))
            if response.get("ok") is not False:
                raise self._fatal_protocol_error(
                    f"local outstation response ok flag is invalid: {response!r}"
                )
            error = response.get("error")
            if not isinstance(error, dict):
                raise self._fatal_protocol_error(
                    "local outstation failure result must contain an error object"
                )
            code = error.get("code")
            message = error.get("message")
            details = error.get("details")
            if not isinstance(code, str) or not isinstance(message, str) or not isinstance(
                details, dict
            ):
                raise self._fatal_protocol_error(
                    f"local outstation returned a malformed error: {error!r}"
                )
            raise LocalOutstationRequestError(code, message, details)

    def update_binary_input(
        self,
        value: bool,
        *,
        index: int = 0,
        timestamp_ms: int | None = None,
        event_mode: str = "force",
    ) -> Mapping[str, Any]:
        if not isinstance(value, bool):
            raise TypeError("binary input value must be boolean")
        return self._update_input(
            "binary_input", value, index, timestamp_ms, event_mode
        )

    def update_analog_input(
        self,
        value: float,
        *,
        index: int = 0,
        timestamp_ms: int | None = None,
        event_mode: str = "force",
    ) -> Mapping[str, Any]:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("analog input value must be numeric")
        normalized = float(value)
        if not math.isfinite(normalized):
            raise ValueError("analog input value must be finite")
        return self._update_input(
            "analog_input", normalized, index, timestamp_ms, event_mode
        )

    def update_binary_output_status(
        self,
        value: bool,
        *,
        index: int = 0,
        event_mode: str = "suppress",
    ) -> Mapping[str, Any]:
        if not isinstance(value, bool):
            raise TypeError("binary output status value must be boolean")
        return self._update_output(
            "binary_output_status", value, index, event_mode
        )

    def update_analog_output_status(
        self,
        value: float,
        *,
        index: int = 0,
        event_mode: str = "suppress",
    ) -> Mapping[str, Any]:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("analog output status value must be numeric")
        normalized = float(value)
        if not math.isfinite(normalized):
            raise ValueError("analog output status value must be finite")
        return self._update_output(
            "analog_output_status", normalized, index, event_mode
        )

    def snapshot(self) -> Mapping[str, Any]:
        return self.request("snapshot", {})

    def close(self) -> None:
        if self._process is None:
            return
        process = self._process
        close_error: Exception | None = None
        if process.poll() is None:
            try:
                result = self.request(
                    "shutdown", {}, timeout=self.shutdown_timeout
                )
                if result.get("state") != "SHUTTING_DOWN":
                    raise LocalOutstationError(
                        f"unexpected shutdown result: {dict(result)!r}"
                    )
            except Exception as error:
                close_error = error
        try:
            process.wait(timeout=self.shutdown_timeout)
        except subprocess.TimeoutExpired:
            self._terminate()
        returncode = process.poll()
        self._close_streams()
        self._join_stream_threads()
        self._process = None
        if close_error is not None:
            raise LocalOutstationError(
                f"local outstation shutdown failed: {close_error}; "
                + self._diagnostic_suffix(returncode)
            ) from close_error
        if returncode != 0:
            raise LocalOutstationError(
                "local outstation exited unsuccessfully; "
                + self._diagnostic_suffix(returncode)
            )

    def _update_input(
        self,
        point_type: str,
        value: object,
        index: int,
        timestamp_ms: int | None,
        event_mode: str,
    ) -> Mapping[str, Any]:
        self._validate_update_common(index, event_mode)
        timestamp_value = (
            int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
        )
        if isinstance(timestamp_value, bool) or not isinstance(timestamp_value, int):
            raise TypeError("timestamp_ms must be an integer")
        if not 0 <= timestamp_value <= (1 << 48) - 1:
            raise ValueError("timestamp_ms must fit the unsigned DNP3 48-bit range")
        return self.request(
            "update",
            {
                "type": point_type,
                "index": index,
                "value": value,
                "timestamp_ms": timestamp_value,
                "event_mode": event_mode,
            },
        )

    def _update_output(
        self,
        point_type: str,
        value: object,
        index: int,
        event_mode: str,
    ) -> Mapping[str, Any]:
        self._validate_update_common(index, event_mode)
        return self.request(
            "update",
            {
                "type": point_type,
                "index": index,
                "value": value,
                "event_mode": event_mode,
            },
        )

    @staticmethod
    def _validate_update_common(index: int, event_mode: str) -> None:
        if isinstance(index, bool) or not isinstance(index, int):
            raise TypeError("index must be an integer")
        if not 0 <= index < 2:
            raise ValueError("the bundled outstation exposes indexes 0 and 1")
        if event_mode not in {"detect", "force", "suppress", "event_only"}:
            raise ValueError(
                "event_mode must be detect, force, suppress, or event_only"
            )

    def _next_line(self, timeout: float, operation: str) -> bytes:
        try:
            item = self._stdout_queue.get(timeout=timeout)
        except queue.Empty as error:
            raise LocalOutstationError(
                f"local outstation timed out during {operation}; "
                + self._diagnostic_suffix()
            ) from error
        if isinstance(item, _StreamFailure):
            raise LocalOutstationError(
                f"local outstation stdout failed: {item.message}; "
                + self._diagnostic_suffix()
            )
        return item

    @staticmethod
    def _parse_json_line(raw: bytes, context: str) -> dict[str, Any]:
        if len(raw) > _MAX_RESPONSE_LINE_BYTES:
            raise LocalOutstationError(
                f"local outstation {context} exceeds {_MAX_RESPONSE_LINE_BYTES} bytes"
            )
        try:
            text = raw.decode("utf-8")
            value = json.loads(
                text,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite,
            )
        except (UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise LocalOutstationError(
                f"local outstation returned invalid {context} JSON: {error}"
            ) from error
        if not isinstance(value, dict):
            raise LocalOutstationError(
                f"local outstation {context} root must be an object"
            )
        return value

    def _read_stdout(self, stream: BinaryIO) -> None:
        try:
            while True:
                line = stream.readline(_MAX_RESPONSE_LINE_BYTES + 2)
                if not line:
                    break
                if len(line) > _MAX_RESPONSE_LINE_BYTES + 1 or not line.endswith(b"\n"):
                    self._enqueue_stdout(
                        _StreamFailure("response line is missing a bounded newline")
                    )
                    return
                if not self._enqueue_stdout(line[:-1].removesuffix(b"\r")):
                    return
        except Exception as error:
            self._enqueue_stdout(_StreamFailure(str(error)))

    def _enqueue_stdout(self, item: bytes | _StreamFailure) -> bool:
        try:
            self._stdout_queue.put_nowait(item)
            return True
        except queue.Full:
            try:
                self._stdout_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._stdout_queue.put_nowait(
                    _StreamFailure("response queue overflowed")
                )
            except queue.Full:
                pass
            return False

    def _read_stderr(self, stream: BinaryIO) -> None:
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    return
                with self._stderr_lock:
                    self._stderr_chunks.append(chunk)
                    self._stderr_size += len(chunk)
                    while (
                        self._stderr_chunks
                        and self._stderr_size > _STDERR_TAIL_BYTES
                    ):
                        removed = self._stderr_chunks.popleft()
                        self._stderr_size -= len(removed)
        except Exception:
            return

    def _terminate(self) -> None:
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
        self._close_streams()
        self._join_stream_threads()
        self._process = None

    def _close_streams(self) -> None:
        if self._process is None:
            return
        for stream in (
            self._process.stdin,
            self._process.stdout,
            self._process.stderr,
        ):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def _join_stream_threads(self) -> None:
        for thread in (self._stdout_thread, self._stderr_thread):
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=1.0)

    def _diagnostic_suffix(self, returncode: int | None = None) -> str:
        process = self._process
        actual_returncode = (
            returncode if returncode is not None else process.poll() if process else None
        )
        stderr = self.stderr_tail.strip()
        return f"returncode={actual_returncode!r}, stderr_tail={stderr!r}"

    def _fatal_protocol_error(self, message: str) -> LocalOutstationError:
        failure = LocalOutstationError(message)
        self._terminate()
        return failure

    def __del__(self) -> None:
        try:
            self._terminate()
        except Exception:
            pass


__all__ = [
    "LocalOutstationError",
    "LocalOutstationRequestError",
    "LocalTestOutstation",
]
