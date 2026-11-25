"""Hardware-independent DAQ runtime contract and records."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

import numpy as np

from labdaq._context import freeze_mapping, sanitize_context, thaw_mapping
from labdaq.errors import ErrorSeverity, LabDAQError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from labdaq.domain.models import ExperimentConfig, HardwareProfile


class DaqLifecycleState(StrEnum):
    IDLE = "idle"
    CONFIGURED = "configured"
    RUNNING = "running"
    STOPPED = "stopped"
    FAULTED = "faulted"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class DaqEvent:
    code: str
    severity: ErrorSeverity
    backend_id: str
    operation: str
    message: str
    timestamp: datetime
    context: Mapping[str, object]
    sample_index: int | None = None
    read_sequence: int | None = None

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("DAQ event timestamps must be timezone-aware")
        if not self.code.startswith("DAQ."):
            raise ValueError("DAQ event codes must use the DAQ namespace")
        safe = sanitize_context(self.context, reject_arrays=True, strict=True)
        object.__setattr__(self, "context", freeze_mapping(safe))

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "backend_id": self.backend_id,
            "operation": self.operation,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "context": thaw_mapping(self.context),
            "sample_index": self.sample_index,
            "read_sequence": self.read_sequence,
        }


@dataclass(frozen=True, slots=True)
class DaqValidationIssue:
    code: str
    field_path: str
    message: str
    correction: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "field_path": self.field_path,
            "message": self.message,
            "correction": self.correction,
        }


@dataclass(frozen=True, slots=True)
class DaqModuleDescriptor:
    module_id: str
    parent_device_id: str
    vendor: str
    model: str
    slot: int
    serial_number: str | None
    available_channels: tuple[str, ...]
    simultaneous_sampling: bool
    supported_measurement_quantities: tuple[str, ...]
    supported_terminal_configurations: tuple[str, ...]
    input_range_v: tuple[float, float]
    supported_sample_rates_hz: tuple[float, ...]
    reported_model: str | None = None
    reported_slot: int | None = None
    product_category: str | None = None


@dataclass(frozen=True, slots=True)
class DaqDeviceDescriptor:
    device_id: str
    vendor: str
    model: str
    serial_number: str | None
    alias: str
    simulated: bool
    accessible: bool
    modules: tuple[DaqModuleDescriptor, ...]
    reported_model: str | None = None
    product_category: str | None = None


@dataclass(frozen=True, slots=True)
class DaqDriverDescriptor:
    provider: str
    package_name: str | None
    package_version: str | None
    driver_version: str | None


@dataclass(frozen=True, slots=True)
class DaqDiscoveryReport:
    backend_id: str
    discovered_at: datetime
    adapter_version: str
    devices: tuple[DaqDeviceDescriptor, ...]
    warnings: tuple[DaqEvent, ...]
    driver: DaqDriverDescriptor


@dataclass(frozen=True, slots=True)
class DaqValidationReport:
    accepted: bool
    hardware_profile_id: str
    profile_revision: int
    experiment_config_id: str
    config_revision: int
    backend_id: str
    scenario_id: str | None
    scenario_schema_version: int | None
    generator_version: str | None
    seed: int | None
    selected_device_id: str | None
    selected_module_id: str | None
    ordered_logical_channels: tuple[str, ...]
    ordered_physical_channels: tuple[str, ...]
    requested_sample_rate_hz: float
    supported_input_range_v: tuple[float, float] | None
    errors: tuple[DaqValidationIssue, ...]
    warnings: tuple[DaqValidationIssue, ...]


@dataclass(frozen=True, slots=True)
class DaqConfigurationResult:
    backend_id: str
    device_id: str
    module_id: str
    ordered_logical_channels: tuple[str, ...]
    ordered_physical_channels: tuple[str, ...]
    requested_sample_rate_hz: float
    effective_sample_rate_hz: float
    sampling_mode: str
    requested_input_ranges_v: tuple[tuple[float, float], ...]
    effective_input_ranges_v: tuple[tuple[float, float], ...]
    samples_per_read: int
    warnings: tuple[DaqEvent, ...]


@dataclass(frozen=True, slots=True)
class DaqStartResult:
    backend_id: str
    previous_state: DaqLifecycleState
    state: DaqLifecycleState
    started_at: datetime
    events: tuple[DaqEvent, ...]


@dataclass(frozen=True, slots=True)
class DaqSelfTestResult:
    backend_id: str
    device_id: str | None
    supported: bool
    passed: bool | None
    performed_at: datetime
    message: str
    events: tuple[DaqEvent, ...]

    def __post_init__(self) -> None:
        if self.performed_at.tzinfo is None or self.performed_at.utcoffset() is None:
            raise ValueError("self-test timestamps must be timezone-aware")
        if not self.message.strip():
            raise ValueError("self-test message must not be empty")
        if not self.supported and self.passed is not None:
            raise ValueError("unsupported self-tests cannot claim an outcome")


@dataclass(frozen=True, slots=True)
class DaqReadResult:
    samples: np.ndarray
    samples_read_per_channel: int
    ordered_logical_channel_ids: tuple[str, ...]
    actual_sample_rate_hz: float
    end_of_stream: bool
    events: tuple[DaqEvent, ...]
    read_sequence: int
    source_start_sample_index: int | None = None

    def __post_init__(self) -> None:
        samples = self.samples
        expected_shape = (
            len(self.ordered_logical_channel_ids),
            self.samples_read_per_channel,
        )
        if not isinstance(samples, np.ndarray):
            raise TypeError("DAQ samples must be a NumPy array")
        if samples.dtype != np.dtype(np.float64):
            raise ValueError("DAQ samples must have float64 dtype")
        if samples.ndim != 2 or samples.shape != expected_shape:
            raise ValueError(f"DAQ samples must have shape {expected_shape}")
        if not samples.flags.c_contiguous:
            raise ValueError("DAQ samples must be C-contiguous")
        if not samples.flags.owndata or samples.base is not None:
            raise ValueError("DAQ samples must own their data")
        if samples.flags.writeable:
            raise ValueError("DAQ samples must be read-only")
        if self.source_start_sample_index is not None and self.source_start_sample_index < 0:
            raise ValueError("source_start_sample_index must be non-negative")


@dataclass(frozen=True, slots=True)
class DaqCancellationResult:
    backend_id: str
    previous_state: DaqLifecycleState
    state: DaqLifecycleState
    requested_at: datetime
    events: tuple[DaqEvent, ...]


@dataclass(frozen=True, slots=True)
class DaqStopResult:
    backend_id: str
    previous_state: DaqLifecycleState
    state: DaqLifecycleState
    stopped_at: datetime
    samples_emitted_per_channel: int
    events: tuple[DaqEvent, ...]


@dataclass(frozen=True, slots=True)
class DaqResetResult:
    backend_id: str
    previous_state: DaqLifecycleState
    state: DaqLifecycleState
    reset_at: datetime
    events: tuple[DaqEvent, ...]


@dataclass(frozen=True, slots=True)
class DaqHealthSnapshot:
    backend_id: str
    state: DaqLifecycleState
    device_available: bool
    configured: bool
    running: bool
    last_successful_operation: str | None
    last_event_code: str | None
    last_error_summary: str | None
    effective_sample_rate_hz: float | None
    ordered_channel_count: int
    backend_details: Mapping[str, object]

    def __post_init__(self) -> None:
        safe = sanitize_context(self.backend_details, reject_arrays=True, strict=True)
        object.__setattr__(self, "backend_details", freeze_mapping(safe))

    def to_dict(self) -> dict[str, object]:
        return {
            "backend_id": self.backend_id,
            "state": self.state.value,
            "device_available": self.device_available,
            "configured": self.configured,
            "running": self.running,
            "last_successful_operation": self.last_successful_operation,
            "last_event_code": self.last_event_code,
            "last_error_summary": self.last_error_summary,
            "effective_sample_rate_hz": self.effective_sample_rate_hz,
            "ordered_channel_count": self.ordered_channel_count,
            "backend_details": thaw_mapping(self.backend_details),
        }


class DaqError(LabDAQError):
    """Base DAQ failure retaining a structured event."""

    def __init__(
        self,
        message: str,
        *,
        event: DaqEvent,
        remediation: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        self.event = event
        super().__init__(
            message,
            code=event.code,
            severity=event.severity,
            context=event.to_dict(),
            remediation=remediation,
            cause=cause,
        )


class DaqLifecycleError(DaqError):
    pass


class DaqProfileMismatchError(DaqError):
    pass


class DaqConfigurationError(DaqError):
    pass


class DaqDriverUnavailableError(DaqError):
    pass


class DaqDeviceNotFoundError(DaqError):
    pass


class DaqResourceReservedError(DaqError):
    pass


class DaqVerificationError(DaqError):
    pass


class DaqStartError(DaqError):
    pass


class DaqReadTimeoutError(DaqError):
    pass


class DaqReadError(DaqError):
    pass


class DaqReadCancelledError(DaqError):
    """A pending read ended because the owning application requested cancellation."""


class DaqSampleLossError(DaqError):
    pass


class DaqDisconnectedError(DaqError):
    pass


class DaqStopError(DaqError):
    pass


class DaqResetError(DaqError):
    pass


class DaqCleanupError(DaqError):
    pass


class DaqMalformedReadError(DaqError):
    pass


class SimulatorScenarioError(DaqError):
    pass


class SimulatorInternalError(DaqError):
    pass


class DaqPort(Protocol):
    def discover(self) -> DaqDiscoveryReport: ...

    def self_test(self) -> DaqSelfTestResult: ...

    def validate(
        self,
        hardware_profile: HardwareProfile,
        experiment: ExperimentConfig,
    ) -> DaqValidationReport: ...

    def configure(self, experiment: ExperimentConfig) -> DaqConfigurationResult: ...

    def start(self) -> DaqStartResult: ...

    def read(self, samples_per_channel: int, timeout_seconds: float) -> DaqReadResult: ...

    def cancel_pending_read(self) -> DaqCancellationResult: ...

    def stop(self) -> DaqStopResult: ...

    def health(self) -> DaqHealthSnapshot: ...

    def reset(self) -> DaqResetResult: ...

    def close(self) -> None: ...

    def __enter__(self) -> DaqPort: ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object | None,
    ) -> None: ...


def canonical_sample_bytes(samples: np.ndarray) -> bytes:
    """Return canonical little-endian channel-major sample bytes."""
    canonical = np.ascontiguousarray(samples, dtype="<f8")
    return canonical.tobytes(order="C")


def canonical_sha256(samples: np.ndarray) -> str:
    """Hash one complete canonical channel-major sample array."""
    return hashlib.sha256(canonical_sample_bytes(samples)).hexdigest()
