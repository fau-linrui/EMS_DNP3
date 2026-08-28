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
from .models import HostProcessConfig, HostProcessDiagnostics, TcpConnectionConfig

__version__ = "0.1.0"
TARGET_STANDARD = "IEEE1815-2012"

__all__ = [
    "ClientStateError",
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
    "TARGET_STANDARD",
    "TcpConnectionConfig",
    "__version__",
]
