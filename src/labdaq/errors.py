"""Structured, operator-readable LabDAQ errors."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from labdaq._context import freeze_mapping, sanitize_context, thaw_mapping

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_ERROR_CODE_PATTERN: Final = re.compile(r"^DAQ\.[A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*$")


class ErrorSeverity(StrEnum):
    """Stable severity values for serialized application errors."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class LabDAQError(Exception):
    """Base exception with stable human and machine-readable fields."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        severity: ErrorSeverity | str = ErrorSeverity.ERROR,
        context: Mapping[str, object] | None = None,
        remediation: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        if _ERROR_CODE_PATTERN.fullmatch(code) is None:
            raise ValueError(f"Invalid LabDAQ error code: {code}")
        if not message:
            raise ValueError("LabDAQ error message must not be empty")
        safe_context = sanitize_context(
            context or {},
            reject_arrays=True,
            strict=True,
        )
        self.code = code
        self.message = message
        self.severity = ErrorSeverity(severity)
        self.context = freeze_mapping(safe_context)
        self.remediation = remediation
        self.cause = cause
        self.__cause__ = cause
        super().__init__(message)

    def __str__(self) -> str:
        """Return a concise operator-facing representation."""
        return f"[{self.code}] {self.message}"

    def to_dict(self) -> dict[str, object]:
        """Return the stable serialization contract without debug-only causes."""
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity.value,
            "context": thaw_mapping(self.context),
            "remediation": self.remediation,
        }


class ConfigurationError(LabDAQError):
    """Invalid or contradictory configuration."""


class ConfigurationValidationError(ConfigurationError):
    """One or more structural or semantic configuration rules failed."""

    def __init__(
        self,
        message: str,
        *,
        document_type: str,
        issues: Sequence[Mapping[str, object]],
        document: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        context: dict[str, object] = {
            "document_type": document_type,
            "issues": list(issues),
        }
        if document is not None:
            context["document"] = document
        super().__init__(
            message,
            code="DAQ.CONFIG.VALIDATION_FAILED",
            context=context,
            remediation="Correct the reported configuration fields and validate again.",
            cause=cause,
        )


class UnsupportedSchemaVersionError(ConfigurationError):
    """The document schema version is absent, malformed, or unsupported."""

    def __init__(
        self,
        *,
        document_type: str,
        received_version: object,
        document: str | None = None,
    ) -> None:
        context: dict[str, object] = {
            "document_type": document_type,
            "received_version": received_version,
            "supported_versions": [1],
        }
        if document is not None:
            context["document"] = document
        super().__init__(
            "The configuration schema version is missing, malformed, or unsupported.",
            code="DAQ.CONFIG.UNSUPPORTED_SCHEMA_VERSION",
            context=context,
            remediation="Use a document with integer schema_version 1.",
        )


class DependencyUnavailableError(LabDAQError):
    """A requested optional dependency is unavailable."""


class HardwareError(LabDAQError):
    """Hardware integration failure."""


class AcquisitionError(LabDAQError):
    """Acquisition orchestration failure."""


class AcquisitionWaitTimeoutError(AcquisitionError):
    """An explicit wait ended before acquisition became terminal."""


class AcquisitionStreamAccessError(AcquisitionError):
    """An acquisition stream was requested outside its valid lifecycle."""


class StorageError(LabDAQError):
    """Authoritative or derived storage failure."""


class InvalidSessionParentError(StorageError):
    """A session parent does not exist or cannot safely be used."""


class SessionPathCollisionError(StorageError):
    """Exclusive session creation found an existing target."""


class SessionOwnedError(StorageError):
    """A session is still owned by an active writer."""


class SessionSchemaError(StorageError):
    """A durable-session schema is absent, malformed, or unsupported."""


class SessionIntegrityError(StorageError):
    """Stored source records disagree or fail integrity checks."""


class SessionNotReplayableError(StorageError):
    """A session has no trustworthy synchronized replay extent."""


class SessionWriteError(StorageError):
    """A session artifact could not be written durably."""


class ProcessingError(LabDAQError):
    """Scientific processing failure."""


class PlotError(LabDAQError):
    """Plot request or bounded plot-data generation failure."""


class ExportError(LabDAQError):
    """Derived artifact export failure."""


class QualificationError(LabDAQError):
    """Hardware qualification could not be completed or trusted."""


class ApplicationStateError(LabDAQError):
    """Illegal application state or transition."""
