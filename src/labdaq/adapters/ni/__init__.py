"""Lazy construction boundary for the optional NI-DAQmx adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from labdaq.errors import ErrorSeverity
from labdaq.ports.daq import DaqDriverUnavailableError, DaqEvent

if TYPE_CHECKING:
    from labdaq.ports.daq import DaqPort


def create_ni_adapter(*, rate_tolerance_hz: float = 0.0) -> DaqPort:
    """Load NI-DAQmx only after the NI backend is explicitly selected."""
    try:
        from labdaq.adapters.ni.adapter import NIDaqmxAdapter
        from labdaq.adapters.ni.driver import NidaqmxDriver
    except ModuleNotFoundError as cause:
        if cause.name != "nidaqmx":
            raise
        event = DaqEvent(
            code="DAQ.HARDWARE.PACKAGE_UNAVAILABLE",
            severity=ErrorSeverity.ERROR,
            backend_id="ni",
            operation="create",
            message="The optional nidaqmx Python package is not installed.",
            timestamp=datetime.now(UTC),
            context={"dependency": "nidaqmx", "source": "ni"},
        )
        raise DaqDriverUnavailableError(
            event.message,
            event=event,
            remediation='Install LabDAQ with the "ni" extra.',
            cause=cause,
        ) from cause
    return NIDaqmxAdapter(NidaqmxDriver(), rate_tolerance_hz=rate_tolerance_hz)


__all__ = ["create_ni_adapter"]
