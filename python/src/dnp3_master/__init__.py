"""Pytest-facing API boundary for the DNP3 master automation framework."""

from __future__ import annotations

from .client import Dnp3MasterClient
from .errors import (
    ClientStateError,
    Dnp3ClientError,
    HostCommandError,
    HostExitedError,
    HostProcessError,
    HostProtocolError,
    HostStartError,
    HostTimeoutError,
)
from .models import (
    AnalogOutputCommand,
    CommandPointResult,
    CommandTaskResult,
    CrobCommand,
    HostProcessConfig,
    HostProcessDiagnostics,
    LabSafetyConfig,
    MeasurementRecord,
    ReadHeader,
    ReadTaskResult,
    TcpConnectionConfig,
)

__version__ = "0.2.0"
TARGET_STANDARD = "IEEE1815-2012"

__all__ = [
    "AnalogOutputCommand",
    "ClientStateError",
    "CommandPointResult",
    "CommandTaskResult",
    "CrobCommand",
    "Dnp3ClientError",
    "Dnp3MasterClient",
    "HostCommandError",
    "HostExitedError",
    "HostProcessConfig",
    "HostProcessDiagnostics",
    "HostProcessError",
    "HostProtocolError",
    "HostStartError",
    "HostTimeoutError",
    "LabSafetyConfig",
    "MeasurementRecord",
    "ReadHeader",
    "ReadTaskResult",
    "TARGET_STANDARD",
    "TcpConnectionConfig",
    "__version__",
]
