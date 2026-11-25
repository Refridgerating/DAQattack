"""Immutable application-facing contracts for the DAQ-M10 operator workflow."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

import numpy as np

from labdaq._context import freeze_mapping, sanitize_context
from labdaq.ports.acquisition import AcquisitionFaultSummary, AcquisitionState

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime
    from pathlib import Path

    from labdaq.domain.enums import SignalStage
    from labdaq.domain.plotting import ExportManifest
    from labdaq.ports.processing import ChannelMetricResult
    from labdaq.ports.session import (
        RecordedSessionCompletion,
        SessionCompletionState,
        SessionSummary,
    )


@dataclass(frozen=True, slots=True)
class LiveDisplayConfiguration:
    refresh_interval_seconds: float = 0.1
    visible_window_seconds: float = 5.0
    maximum_history_seconds: float = 10.0
    maximum_points_per_channel: int = 2000
    metric_update_interval_seconds: float = 0.5
    metric_window_seconds: float = 1.0

    def __post_init__(self) -> None:
        for name in (
            "refresh_interval_seconds",
            "visible_window_seconds",
            "maximum_history_seconds",
            "metric_update_interval_seconds",
            "metric_window_seconds",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        if self.visible_window_seconds > self.maximum_history_seconds:
            raise ValueError("visible window cannot exceed maximum history")
        if self.metric_window_seconds > self.maximum_history_seconds:
            raise ValueError("metric window cannot exceed maximum history")
        if self.maximum_points_per_channel < 4:
            raise ValueError("maximum_points_per_channel must be at least four")


class OpenMonitorState(StrEnum):
    IDLE = "idle"
    STARTING = "starting"
    MONITORING = "monitoring"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAULTED = "faulted"


class OpenRecordingState(StrEnum):
    INACTIVE = "inactive"
    PREPARING = "preparing"
    RECORDING = "recording"
    FINALIZING = "finalizing"


@dataclass(frozen=True, slots=True)
class OpenAcquisitionStatus:
    monitor_state: OpenMonitorState
    recording_state: OpenRecordingState
    monitor_run_id: str | None
    selected_channel_id: str | None
    selected_channel_unit: str | None
    monitor_started_at: datetime | None
    elapsed_seconds: float
    actual_sample_rate_hz: float | None
    acquired_samples_per_channel: int
    retained_start_sample_index: int | None
    retained_stop_sample_index: int | None
    display_frames_dropped: int
    recording_session_id: str | None
    recording_session_path: Path | None
    recording_source_start_sample_index: int | None
    recording_source_stop_sample_index: int | None
    recording_persisted_samples: int
    last_recording: RecordedSessionCompletion | None = None
    monitor_fault: AcquisitionFaultSummary | None = None
    storage_fault_code: str | None = None
    ordered_channel_ids: tuple[str, ...] = ()
    ordered_channel_units: tuple[str, ...] = ()
    requested_sample_rate_hz: float | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.elapsed_seconds) or self.elapsed_seconds < 0:
            raise ValueError("open-acquisition elapsed time must be non-negative and finite")
        counts = (
            self.acquired_samples_per_channel,
            self.display_frames_dropped,
            self.recording_persisted_samples,
        )
        if any(value < 0 for value in counts):
            raise ValueError("open-acquisition counts must be non-negative")
        if (self.selected_channel_id is None) != (self.selected_channel_unit is None):
            raise ValueError("selected open-acquisition channel identity and unit must align")
        channel_ids = self.ordered_channel_ids
        channel_units = self.ordered_channel_units
        if not channel_ids and self.selected_channel_id is not None:
            channel_ids = (self.selected_channel_id,)
            channel_units = (self.selected_channel_unit or "V",)
            object.__setattr__(self, "ordered_channel_ids", channel_ids)
            object.__setattr__(self, "ordered_channel_units", channel_units)
        if len(channel_ids) != len(channel_units) or len(channel_ids) != len(set(channel_ids)):
            raise ValueError("open-acquisition channel identities and units must align")
        if channel_ids and self.selected_channel_id != channel_ids[0]:
            raise ValueError("selected open-acquisition channel must be first")

    @property
    def active(self) -> bool:
        return self.monitor_state in {
            OpenMonitorState.STARTING,
            OpenMonitorState.MONITORING,
            OpenMonitorState.STOPPING,
        }


@dataclass(frozen=True, slots=True)
class DisplayDegradation:
    code: str
    message: str
    source_start_sample_index: int | None = None
    detected_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.code.startswith("DAQ.DISPLAY.") or not self.message:
            raise ValueError("display degradation requires a DAQ.DISPLAY code and message")
        if self.detected_at is not None and (
            self.detected_at.tzinfo is None or self.detected_at.utcoffset() is None
        ):
            raise ValueError("display degradation time must be timezone-aware")


@dataclass(frozen=True, slots=True)
class LivePlotSeries:
    channel_id: str
    display_name: str
    unit: str
    source_sample_indices: np.ndarray
    relative_time_seconds: np.ndarray
    values: np.ndarray

    def __post_init__(self) -> None:
        length = len(self.values)
        if not self.channel_id or not self.display_name or not self.unit or length == 0:
            raise ValueError("live plot series require identity, units, and values")
        for name in ("source_sample_indices", "relative_time_seconds", "values"):
            values = getattr(self, name)
            if not isinstance(values, np.ndarray) or values.ndim != 1 or len(values) != length:
                raise ValueError(f"{name} must be an aligned one-dimensional NumPy array")
            if values.flags.writeable:
                raise ValueError(f"{name} must be read-only")


@dataclass(frozen=True, slots=True)
class LivePlotFrame:
    session_id: str
    frame_sequence: int
    signal_stage: SignalStage
    start_sample_index: int
    stop_sample_index: int
    sample_rate_hz: float
    source_sample_count: int
    series: tuple[LivePlotSeries, ...]
    recipe_id: str | None = None
    recipe_hash: str | None = None
    decimation_algorithm: str = "min-max-v1"
    degradation: DisplayDegradation | None = None

    def __post_init__(self) -> None:
        if (
            not self.session_id
            or self.frame_sequence < 0
            or self.start_sample_index < 0
            or self.stop_sample_index <= self.start_sample_index
            or self.source_sample_count != self.stop_sample_index - self.start_sample_index
            or not math.isfinite(self.sample_rate_hz)
            or self.sample_rate_hz <= 0
            or not self.series
        ):
            raise ValueError("live plot frame metadata is invalid")


@dataclass(frozen=True, slots=True)
class LiveMetricSnapshot:
    session_id: str
    start_sample_index: int
    stop_sample_index: int
    updated_at: datetime
    update_interval_seconds: float
    window_seconds: float
    signal_stage: SignalStage
    effective_bandwidth: str
    metrics: tuple[ChannelMetricResult, ...]
    stale: bool = False
    degradation: DisplayDegradation | None = None

    def __post_init__(self) -> None:
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise ValueError("metric update time must be timezone-aware")
        if self.start_sample_index < 0 or self.stop_sample_index <= self.start_sample_index:
            raise ValueError("metric sample range must be non-empty")


@dataclass(frozen=True, slots=True)
class OperatorRunStatus:
    state: AcquisitionState
    finalization_state: SessionCompletionState | None
    session_id: str | None
    session_path: Path | None
    experiment_name: str | None
    started_at: datetime | None
    elapsed_seconds: float
    requested_duration_seconds: float | None
    actual_sample_rate_hz: float | None
    ordered_channel_ids: tuple[str, ...]
    ordered_channel_units: tuple[str, ...]
    acquired_samples_per_channel: int
    persisted_samples_per_channel: int
    recording_queue_occupancy: int
    recording_queue_capacity: int
    display_queue_occupancy: int
    display_queue_capacity: int
    display_frames_produced: int
    display_frames_dropped: int
    acquisition_gap_count: int
    persistence_loss_samples: int | None
    warning_count: int
    fault: AcquisitionFaultSummary | None = None
    storage_fault_code: str | None = None
    metric_stale: bool = False
    display_degradation: DisplayDegradation | None = None
    requested_sample_rate_hz: float | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.elapsed_seconds) or self.elapsed_seconds < 0:
            raise ValueError("operator elapsed time must be non-negative and finite")
        if len(self.ordered_channel_ids) != len(self.ordered_channel_units):
            raise ValueError("operator channel identities and units must align")
        counts = (
            self.acquired_samples_per_channel,
            self.persisted_samples_per_channel,
            self.recording_queue_occupancy,
            self.recording_queue_capacity,
            self.display_queue_occupancy,
            self.display_queue_capacity,
            self.display_frames_produced,
            self.display_frames_dropped,
            self.acquisition_gap_count,
            self.warning_count,
        )
        if any(value < 0 for value in counts):
            raise ValueError("operator status counts must be non-negative")
        if self.persistence_loss_samples is not None and self.persistence_loss_samples < 0:
            raise ValueError("terminal persistence loss must be non-negative")

    @property
    def terminal(self) -> bool:
        return self.state in {AcquisitionState.COMPLETED, AcquisitionState.FAULTED}


@dataclass(frozen=True, slots=True)
class OperatorRunSummary:
    recorded: RecordedSessionCompletion
    session: SessionSummary
    display_frames_produced: int
    display_frames_dropped: int
    display_degradation: DisplayDegradation | None = None


@dataclass(frozen=True, slots=True)
class SessionEventRecord:
    event_sequence: int
    timestamp: datetime
    severity: str
    code: str
    acquisition_state: str
    message: str
    sample_index: int | None
    details: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("session event time must be timezone-aware")
        if self.event_sequence < 0 or not self.code.startswith("DAQ.") or not self.message:
            raise ValueError("session event identity is invalid")
        safe = sanitize_context(self.details, reject_arrays=True, strict=True)
        object.__setattr__(self, "details", freeze_mapping(safe))


@dataclass(frozen=True, slots=True)
class ExportHistoryEntry:
    manifest_path: Path
    valid: bool
    manifest: ExportManifest | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class SessionReviewSnapshot:
    summary: SessionSummary
    events: tuple[SessionEventRecord, ...]
    export_history: tuple[ExportHistoryEntry, ...]
    manifest_path: Path
    event_log_path: Path
    raw_data_path: Path
    qualification_report_path: Path | None
    loaded_at: datetime

    def __post_init__(self) -> None:
        if self.loaded_at.tzinfo is None or self.loaded_at.utcoffset() is None:
            raise ValueError("review load time must be timezone-aware")


@dataclass(frozen=True, slots=True)
class OperatorFaultPresentation:
    code: str
    severity: str
    summary: str
    technical_detail: str
    recommended_action: str
    acquisition_state: str
    session_path: Path | None
    recoverable: bool
    raw_data_available: bool
    manifest_available: bool


class SessionEventSourcePort(Protocol):
    def read_session_events(
        self,
        path: Path,
        *,
        limit: int = 500,
    ) -> tuple[SessionEventRecord, ...]: ...


class ExportHistorySourcePort(Protocol):
    def list_export_history(self, path: Path) -> tuple[ExportHistoryEntry, ...]: ...
