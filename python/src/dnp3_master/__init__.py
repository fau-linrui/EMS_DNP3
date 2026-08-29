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
    SafetyIncidentAcknowledgmentError,
    SafetyIncidentConfigurationError,
    SafetyIncidentError,
    SafetyIncidentPersistenceError,
    UnresolvedSafetyIncidentError,
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
    UnsolicitedBatchResult,
    UnsolicitedControlResult,
)
from .point_table import (
    POINT_TABLE_COLUMNS,
    POINT_TABLE_SCHEMA_VERSION,
    PointDefinition,
    PointTable,
    PointTableError,
    load_point_table,
)
from .safety_incidents import (
    SAFETY_INCIDENT_SCHEMA_VERSION,
    IncidentRecordResult,
    SafetyIncidentStore,
    dut_identity_sha256,
)

__version__ = "0.3.0"
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
    "POINT_TABLE_COLUMNS",
    "POINT_TABLE_SCHEMA_VERSION",
    "PointDefinition",
    "PointTable",
    "PointTableError",
    "ReadHeader",
    "ReadTaskResult",
    "SAFETY_INCIDENT_SCHEMA_VERSION",
    "SafetyIncidentAcknowledgmentError",
    "SafetyIncidentConfigurationError",
    "SafetyIncidentError",
    "SafetyIncidentPersistenceError",
    "SafetyIncidentStore",
    "TARGET_STANDARD",
    "TcpConnectionConfig",
    "UnsolicitedBatchResult",
    "UnsolicitedControlResult",
    "UnresolvedSafetyIncidentError",
    "IncidentRecordResult",
    "dut_identity_sha256",
    "load_point_table",
    "__version__",
]
