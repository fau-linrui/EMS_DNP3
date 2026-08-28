"""Public configuration and diagnostic models for the native host process."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Mapping


def _milliseconds(
    value: float, field_name: str, minimum: int, maximum: int
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{field_name} must be a finite number of seconds")
    milliseconds = round(float(value) * 1000)
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

        return {
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


@dataclass(frozen=True, slots=True)
class HostProcessConfig:
    """Immutable process settings suitable for a pytest fixture or context manager."""

    executable: Path
    arguments: tuple[str, ...] = ()
    working_directory: Path | None = None
    environment: Mapping[str, str] | None = None
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

        for field_name in (
            "startup_timeout",
            "request_timeout",
            "shutdown_timeout",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"{field_name} must be a positive number")
        if self.diagnostic_tail_bytes < 256:
            raise ValueError("diagnostic_tail_bytes must be at least 256")
        if self.max_response_bytes < 64:
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
