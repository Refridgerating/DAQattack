"""Hardware-neutral contracts published by the M04 acquisition engine."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

import numpy as np

from labdaq._context import freeze_mapping, sanitize_context, thaw_mapping

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from labdaq.errors import ErrorSeverity


class AcquisitionState(StrEnum):
    IDLE = "idle"
    CONFIGURED = "configured"
    ARMED = "armed"
    RUNNING = "running"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAULTED = "faulted"


class CompletionReason(StrEnum):
    DURATION_REACHED = "duration_reached"
    MANUAL_STOP = "manual_stop"
    ACQUISITION_FAULT = "acquisition_fault"


@dataclass(frozen=True, slots=True)
class AcquisitionChunk:
    session_id: str
    sequence_number: int
    start_sample_index: int
    sample_count: int
    sample_rate_hz: float
    ordered_channel_ids: tuple[str, ...]
    ordered_channel_units: tuple[str, ...]
    samples: np.ndarray
    read_completed_monotonic: float
    read_duration_seconds: float = 0.0

    def __post_init__(self) -> None:
        if not self.session_id:
            raise ValueError("session_id must not be empty")
        if isinstance(self.sequence_number, bool) or self.sequence_number < 0:
            raise ValueError("sequence_number must be a non-negative integer")
        if isinstance(self.start_sample_index, bool) or self.start_sample_index < 0:
            raise ValueError("start_sample_index must be a non-negative integer")
        if isinstance(self.sample_count, bool) or self.sample_count <= 0:
            raise ValueError("sample_count must be a positive integer")
        if not math.isfinite(self.sample_rate_hz) or self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive and finite")
        if not self.ordered_channel_ids:
            raise ValueError("ordered_channel_ids must not be empty")
        if len(self.ordered_channel_units) != len(self.ordered_channel_ids):
            raise ValueError("ordered_channel_units must align with ordered_channel_ids")
        expected_shape = (len(self.ordered_channel_ids), self.sample_count)
        if not isinstance(self.samples, np.ndarray):
            raise TypeError("samples must be a NumPy array")
        if self.samples.dtype != np.dtype(np.float64):
            raise ValueError("samples must have float64 dtype")
        if self.samples.ndim != 2 or self.samples.shape != expected_shape:
            raise ValueError(f"samples must have shape {expected_shape}")
        if not self.samples.flags.c_contiguous:
            raise ValueError("samples must be C-contiguous")
        if self.samples.flags.writeable:
            raise ValueError("samples must be read-only")
        if not math.isfinite(self.read_completed_monotonic) or self.read_completed_monotonic < 0:
            raise ValueError("read_completed_monotonic must be finite and non-negative")
        if not math.isfinite(self.read_duration_seconds) or self.read_duration_seconds < 0:
            raise ValueError("read_duration_seconds must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class AcquisitionEvent:
    event_sequence: int
    code: str
    severity: ErrorSeverity
    session_id: str
    state: AcquisitionState
    timestamp: datetime
    monotonic_timestamp: float
    message: str
    context: Mapping[str, object]
    sample_index: int | None = None

    def __post_init__(self) -> None:
        if isinstance(self.event_sequence, bool) or self.event_sequence < 0:
            raise ValueError("event_sequence must be a non-negative integer")
        if not self.code.startswith("DAQ."):
            raise ValueError("acquisition event codes must use the DAQ namespace")
        if not self.session_id:
            raise ValueError("session_id must not be empty")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("acquisition event timestamps must be timezone-aware")
        if not math.isfinite(self.monotonic_timestamp) or self.monotonic_timestamp < 0:
            raise ValueError("monotonic_timestamp must be finite and non-negative")
        if not self.message:
            raise ValueError("message must not be empty")
        safe = sanitize_context(self.context, reject_arrays=True, strict=True)
        object.__setattr__(self, "context", freeze_mapping(safe))

    def to_dict(self) -> dict[str, object]:
        return {
            "event_sequence": self.event_sequence,
            "code": self.code,
            "severity": self.severity.value,
            "session_id": self.session_id,
            "state": self.state.value,
            "timestamp": self.timestamp.isoformat(),
            "monotonic_timestamp": self.monotonic_timestamp,
            "message": self.message,
            "context": thaw_mapping(self.context),
            "sample_index": self.sample_index,
        }


@dataclass(frozen=True, slots=True)
class AcquisitionFaultSummary:
    code: str
    message: str
    context: Mapping[str, object]

    def __post_init__(self) -> None:
        safe = sanitize_context(self.context, reject_arrays=True, strict=True)
        object.__setattr__(self, "context", freeze_mapping(safe))

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "context": thaw_mapping(self.context),
        }


@dataclass(frozen=True, slots=True)
class CleanupSummary:
    attempted: bool
    adapter_stop_succeeded: bool
    adapter_close_forced: bool
    worker_terminated: bool
    errors: tuple[str, ...] = ()

    @property
    def succeeded(self) -> bool:
        return (
            self.attempted
            and (self.adapter_stop_succeeded or self.adapter_close_forced)
            and not self.errors
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "attempted": self.attempted,
            "adapter_stop_succeeded": self.adapter_stop_succeeded,
            "adapter_close_forced": self.adapter_close_forced,
            "worker_terminated": self.worker_terminated,
            "errors": list(self.errors),
            "succeeded": self.succeeded,
        }


@dataclass(frozen=True, slots=True)
class AcquisitionTelemetry:
    read_count: int = 0
    read_duration_total_seconds: float = 0.0
    read_duration_max_seconds: float = 0.0
    recording_queue_high_water_chunks: int = 0
    recording_queue_max_utilization: float = 0.0
    recording_backpressure_events: int = 0
    source_missing_samples: int = 0
    source_duplicated_samples: int = 0
    source_reordered_reads: int = 0
    stop_latency_seconds: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "read_count": self.read_count,
            "read_duration_total_seconds": self.read_duration_total_seconds,
            "read_duration_max_seconds": self.read_duration_max_seconds,
            "recording_queue_high_water_chunks": self.recording_queue_high_water_chunks,
            "recording_queue_max_utilization": self.recording_queue_max_utilization,
            "recording_backpressure_events": self.recording_backpressure_events,
            "source_missing_samples": self.source_missing_samples,
            "source_duplicated_samples": self.source_duplicated_samples,
            "source_reordered_reads": self.source_reordered_reads,
            "stop_latency_seconds": self.stop_latency_seconds,
        }


@dataclass(frozen=True, slots=True)
class AcquisitionStatus:
    state: AcquisitionState
    session_id: str | None
    requested_sample_rate_hz: float | None
    actual_sample_rate_hz: float | None
    requested_duration_seconds: float | None
    target_sample_count: int | None
    acquired_samples_per_channel: int
    recording_samples_enqueued: int
    display_samples_published: int
    display_frames_published: int
    display_frames_dropped: int
    recording_queue_occupancy: int
    recording_queue_capacity: int
    display_queue_occupancy: int
    display_queue_capacity: int
    stop_requested: bool
    worker_alive: bool
    last_warning_or_fault_code: str | None
    telemetry: AcquisitionTelemetry = field(default_factory=AcquisitionTelemetry)
    started_at: datetime | None = None
    elapsed_seconds: float = 0.0
    ordered_channel_ids: tuple[str, ...] = ()
    ordered_channel_units: tuple[str, ...] = ()
    warning_count: int = 0
    current_fault: AcquisitionFaultSummary | None = None

    def __post_init__(self) -> None:
        if self.started_at is not None and (
            self.started_at.tzinfo is None or self.started_at.utcoffset() is None
        ):
            raise ValueError("started_at must be timezone-aware")
        if not math.isfinite(self.elapsed_seconds) or self.elapsed_seconds < 0:
            raise ValueError("elapsed_seconds must be non-negative and finite")
        if len(self.ordered_channel_ids) != len(self.ordered_channel_units):
            raise ValueError("ordered channel identities and units must align")
        if self.warning_count < 0:
            raise ValueError("warning_count must be non-negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "session_id": self.session_id,
            "requested_sample_rate_hz": self.requested_sample_rate_hz,
            "actual_sample_rate_hz": self.actual_sample_rate_hz,
            "requested_duration_seconds": self.requested_duration_seconds,
            "target_sample_count": self.target_sample_count,
            "acquired_samples_per_channel": self.acquired_samples_per_channel,
            "recording_samples_enqueued": self.recording_samples_enqueued,
            "display_samples_published": self.display_samples_published,
            "display_frames_published": self.display_frames_published,
            "display_frames_dropped": self.display_frames_dropped,
            "recording_queue_occupancy": self.recording_queue_occupancy,
            "recording_queue_capacity": self.recording_queue_capacity,
            "display_queue_occupancy": self.display_queue_occupancy,
            "display_queue_capacity": self.display_queue_capacity,
            "stop_requested": self.stop_requested,
            "worker_alive": self.worker_alive,
            "last_warning_or_fault_code": self.last_warning_or_fault_code,
            "telemetry": self.telemetry.to_dict(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "elapsed_seconds": self.elapsed_seconds,
            "ordered_channel_ids": list(self.ordered_channel_ids),
            "ordered_channel_units": list(self.ordered_channel_units),
            "warning_count": self.warning_count,
            "current_fault": self.current_fault.to_dict() if self.current_fault else None,
        }


@dataclass(frozen=True, slots=True)
class AcquisitionCompletion:
    terminal_state: AcquisitionState
    reason: CompletionReason
    session_id: str
    started_at: datetime | None
    stopped_at: datetime
    total_acquired_samples_per_channel: int
    total_recording_samples_accepted: int
    final_sequence_number: int | None
    display_frames_published: int
    display_frames_dropped: int
    warnings: tuple[AcquisitionEvent, ...]
    fault: AcquisitionFaultSummary | None
    cleanup: CleanupSummary
    telemetry: AcquisitionTelemetry = field(default_factory=AcquisitionTelemetry)

    def __post_init__(self) -> None:
        if self.terminal_state not in {AcquisitionState.COMPLETED, AcquisitionState.FAULTED}:
            raise ValueError("completion requires a terminal state")
        if self.stopped_at.tzinfo is None or self.stopped_at.utcoffset() is None:
            raise ValueError("stopped_at must be timezone-aware")
        if self.started_at is not None and (
            self.started_at.tzinfo is None or self.started_at.utcoffset() is None
        ):
            raise ValueError("started_at must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        return {
            "terminal_state": self.terminal_state.value,
            "reason": self.reason.value,
            "session_id": self.session_id,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "stopped_at": self.stopped_at.isoformat(),
            "total_acquired_samples_per_channel": self.total_acquired_samples_per_channel,
            "total_recording_samples_accepted": self.total_recording_samples_accepted,
            "final_sequence_number": self.final_sequence_number,
            "display_frames_published": self.display_frames_published,
            "display_frames_dropped": self.display_frames_dropped,
            "warnings": [event.to_dict() for event in self.warnings],
            "fault": self.fault.to_dict() if self.fault else None,
            "cleanup": self.cleanup.to_dict(),
            "telemetry": self.telemetry.to_dict(),
        }


class AcquisitionStreamClosedError(LookupError):
    """A completed stream has no remaining items."""


class AcquisitionChunkStream(Protocol):
    @property
    def capacity(self) -> int: ...

    @property
    def closed(self) -> bool: ...

    def qsize(self) -> int: ...

    def empty(self) -> bool: ...

    def get(self, timeout: float | None = None) -> AcquisitionChunk: ...

    def get_nowait(self) -> AcquisitionChunk: ...


class AcquisitionEventStream(Protocol):
    @property
    def closed(self) -> bool: ...

    def qsize(self) -> int: ...

    def get(self, timeout: float | None = None) -> AcquisitionEvent: ...

    def get_nowait(self) -> AcquisitionEvent: ...

    def close(self) -> None: ...
