from __future__ import annotations

import re

import dnp3_master


def test_package_import_exposes_stable_metadata() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", dnp3_master.__version__)
    assert dnp3_master.TARGET_STANDARD == "IEEE1815-2012"


def test_public_exports_are_explicit() -> None:
    assert set(dnp3_master.__all__) == {
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
    }
