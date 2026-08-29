"""Stable Python-side exception hierarchy for process and protocol failures."""

from __future__ import annotations

from typing import Any, Mapping

from .models import HostProcessDiagnostics


class Dnp3ClientError(RuntimeError):
    """Base class for all framework client errors."""


class ClientStateError(Dnp3ClientError):
    """The requested operation is invalid for the client lifecycle state."""


class SafetyIncidentError(Dnp3ClientError):
    """Base class for persistent uncertain-control safety incidents."""

    def __init__(
        self,
        message: str,
        *,
        incident_id: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.incident_id = incident_id
        self.details = dict(details or {})


class SafetyIncidentConfigurationError(SafetyIncidentError):
    """Persistent incident storage was not configured for a control request."""


class UnresolvedSafetyIncidentError(SafetyIncidentError):
    """A state-changing request was blocked by an active incident lock."""


class SafetyIncidentAcknowledgmentError(SafetyIncidentError):
    """An incident acknowledgment was incomplete or did not match the lock."""


class SafetyIncidentPersistenceError(SafetyIncidentError):
    """An uncertain result occurred but its persistent lock could not be written."""

    def __init__(
        self,
        message: str,
        *,
        original_error: HostCommandError | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)
        self.original_error = original_error


class HostProcessError(Dnp3ClientError):
    """Base class for failures involving the native child process."""

    def __init__(
        self,
        message: str,
        diagnostics: HostProcessDiagnostics | None = None,
    ) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics
        self.incident_id: str | None = None
        self.details: dict[str, Any] = {}


class HostStartError(HostProcessError):
    """The executable could not be started or attached to its lifetime guard."""


class HostTimeoutError(HostProcessError):
    """The single in-flight RPC did not complete inside its deadline."""


class HostExitedError(HostProcessError):
    """The child exited before returning the expected response."""


class HostProtocolError(HostProcessError):
    """The child polluted stdout or returned an invalid response envelope."""


class HostCommandError(Dnp3ClientError):
    """A valid host response reported a stable machine-readable command error."""

    def __init__(
        self,
        *,
        request_id: str,
        code: str,
        message: str,
        details: Mapping[str, Any],
    ) -> None:
        super().__init__(f"host command failed with {code}: {message}")
        self.request_id = request_id
        self.code = code
        self.host_message = message
        self.details = dict(details)
