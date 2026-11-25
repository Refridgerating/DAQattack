"""Stable operator-facing messages for presentation adapters."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from labdaq.errors import ErrorSeverity, LabDAQError


@dataclass(frozen=True, slots=True)
class OperatorMessage:
    code: str
    severity: ErrorSeverity
    title: str
    message: str
    remediation: str | None = None
    field_path: str | None = None
    correlation_id: str | None = None


def operator_message_from_error(error: LabDAQError) -> OperatorMessage:
    """Translate an expected typed failure without exposing adapter internals."""
    field = error.context.get("field_path")
    return OperatorMessage(
        code=error.code,
        severity=error.severity,
        title=_title_for(error.code),
        message=error.message,
        remediation=error.remediation,
        field_path=str(field) if field is not None else None,
    )


def unexpected_operator_message() -> OperatorMessage:
    """Create a correlation handle for a traceback retained only in logs."""
    correlation_id = str(uuid4())
    return OperatorMessage(
        code="DAQ.GUI.UNEXPECTED_FAILURE",
        severity=ErrorSeverity.ERROR,
        title="Unexpected desktop failure",
        message="The requested desktop operation could not be completed.",
        remediation="Retry the operation and provide the correlation ID with diagnostics.",
        correlation_id=correlation_id,
    )


def _title_for(code: str) -> str:
    area = code.split(".")[1].replace("_", " ").title()
    return f"{area} issue"
