"""Storage-neutral contracts for durable sessions, verification, and replay."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import datetime  # noqa: TC003 - runtime dataclass validation
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from labdaq._context import freeze_mapping, sanitize_context, thaw_mapping

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from labdaq.domain.models import (
        ExperimentConfig,
        HardwareProfile,
        ProcessingRecipeEnvelope,
        SessionConfig,
    )
    from labdaq.ports.acquisition import (
        AcquisitionChunk,
        AcquisitionCompletion,
        AcquisitionEvent,
        AcquisitionStatus,
    )
    from labdaq.ports.daq import DaqConfigurationResult, DaqDiscoveryReport


class SessionCompletionState(StrEnum):
    INITIALIZING = "initializing"
    RECORDING = "recording"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAULTED = "faulted"
    INTERRUPTED = "interrupted"
    CORRUPT = "corrupt"


class SessionCompletionReason(StrEnum):
    DURATION_REACHED = "duration_reached"
    MANUAL_STOP = "manual_stop"
    ACQUISITION_FAULT = "acquisition_fault"
    READER_TIMEOUT = "reader_timeout"
    HARDWARE_DISCONNECTED = "hardware_disconnected"
    STORAGE_WRITE_FAILED = "storage_write_failed"
    EVENT_WRITE_FAILED = "event_write_failed"
    MANIFEST_UPDATE_FAILED = "manifest_update_failed"
    DISK_SPACE_EXHAUSTED = "disk_space_exhausted"
    PROCESS_INTERRUPTED = "process_interrupted"
    INTEGRITY_CHECK_FAILED = "integrity_check_failed"


class VerificationLevel(StrEnum):
    FAST = "fast"
    FULL = "full"


class IntegrityStatus(StrEnum):
    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"


class Recoverability(StrEnum):
    REPLAYABLE = "replayable"
    REPLAYABLE_PREFIX = "replayable_prefix"
    RECOVERY_CANDIDATE = "recovery_candidate"
    ACTIVE_WRITER = "active_writer"
    NOT_REPLAYABLE = "not_replayable"


class ReplayState(StrEnum):
    READY = "ready"
    REPLAYING = "replaying"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    CLOSED = "closed"


class SessionType(StrEnum):
    EXPERIMENT = "experiment"
    HARDWARE_QUALIFICATION = "hardware-qualification"
    RAW_DIAGNOSTIC = "raw-diagnostic"


@dataclass(frozen=True, slots=True)
class SessionPlan:
    session_id: str
    parent_directory: Path
    experiment_name: str
    experiment_slug: str
    directory_name: str
    session_path: Path
    created_at: datetime
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.session_id:
            raise ValueError("session_id must not be empty")
        if not isinstance(self.parent_directory, Path) or not isinstance(self.session_path, Path):
            raise TypeError("session paths must be pathlib.Path values")
        if not self.experiment_name or not self.experiment_slug or not self.directory_name:
            raise ValueError("session plan names must not be empty")
        if self.session_path.parent != self.parent_directory:
            raise ValueError("session_path must be a direct child of parent_directory")
        if self.session_path.name != self.directory_name:
            raise ValueError("directory_name must match session_path")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "parent_directory": str(self.parent_directory),
            "experiment_name": self.experiment_name,
            "experiment_slug": self.experiment_slug,
            "directory_name": self.directory_name,
            "session_path": str(self.session_path),
            "created_at": self.created_at.isoformat(),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class OpenAcquisitionProvenance:
    """Map one recorded segment onto its longer-lived monitor source stream."""

    monitor_run_id: str
    monitor_started_at: datetime
    configured_logical_channels: tuple[str, ...]
    configured_physical_channels: tuple[str, ...]
    selected_channel_id: str
    source_start_sample_index: int
    record_requested_at: datetime
    maximum_history_seconds: float
    visible_window_seconds: float
    source_stop_sample_index: int | None = None
    stop_requested_at: datetime | None = None
    recorded_channel_ids: tuple[str, ...] = ()
    recorded_channel_units: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.monitor_run_id or not self.selected_channel_id:
            raise ValueError("open-acquisition identities must not be empty")
        recorded = self.recorded_channel_ids
        if not recorded:
            recorded = (self.selected_channel_id,)
            object.__setattr__(self, "recorded_channel_ids", recorded)
        recorded_units = self.recorded_channel_units
        if not recorded_units:
            recorded_units = ("V",) * len(recorded)
            object.__setattr__(self, "recorded_channel_units", recorded_units)
        for name, value in (
            ("monitor_started_at", self.monitor_started_at),
            ("record_requested_at", self.record_requested_at),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.stop_requested_at is not None and (
            self.stop_requested_at.tzinfo is None or self.stop_requested_at.utcoffset() is None
        ):
            raise ValueError("stop_requested_at must be timezone-aware")
        if self.source_start_sample_index < 0:
            raise ValueError("source_start_sample_index must be non-negative")
        if (
            self.source_stop_sample_index is not None
            and self.source_stop_sample_index < self.source_start_sample_index
        ):
            raise ValueError("source stop must not precede source start")
        if self.maximum_history_seconds <= 0 or not math.isfinite(self.maximum_history_seconds):
            raise ValueError("maximum_history_seconds must be positive and finite")
        if (
            self.visible_window_seconds <= 0
            or self.visible_window_seconds > self.maximum_history_seconds
            or not math.isfinite(self.visible_window_seconds)
        ):
            raise ValueError("visible_window_seconds must be inside retained history")
        if not self.configured_logical_channels or (
            len(self.configured_logical_channels) != len(self.configured_physical_channels)
        ):
            raise ValueError("configured logical and physical channels must align")
        if self.selected_channel_id not in self.configured_logical_channels:
            raise ValueError("selected channel must belong to the configured task")
        if (
            not recorded
            or len(recorded) != len(set(recorded))
            or tuple(
                channel_id
                for channel_id in self.configured_logical_channels
                if channel_id in recorded
            )
            != recorded
        ):
            raise ValueError("recorded channels must be an ordered configured subset")
        if self.selected_channel_id != recorded[0]:
            raise ValueError("selected channel must be the first recorded channel")
        if len(recorded_units) != len(recorded) or any(not unit for unit in recorded_units):
            raise ValueError("recorded channel identities and units must align")

    def to_dict(self) -> dict[str, object]:
        return {
            "monitor_run_id": self.monitor_run_id,
            "monitor_started_at": self.monitor_started_at.isoformat(),
            "configured_logical_channels": list(self.configured_logical_channels),
            "configured_physical_channels": list(self.configured_physical_channels),
            "selected_channel_id": self.selected_channel_id,
            "recorded_channel_ids": list(self.recorded_channel_ids),
            "recorded_channel_units": list(self.recorded_channel_units),
            "source_start_sample_index": self.source_start_sample_index,
            "source_stop_sample_index": self.source_stop_sample_index,
            "local_sample_index_origin": 0,
            "local_to_source_index_mapping": {
                "local_origin": 0,
                "source_origin": self.source_start_sample_index,
                "scale": 1,
            },
            "record_requested_at": self.record_requested_at.isoformat(),
            "stop_requested_at": (
                self.stop_requested_at.isoformat() if self.stop_requested_at else None
            ),
            "maximum_history_seconds": self.maximum_history_seconds,
            "visible_window_seconds": self.visible_window_seconds,
            "buffer_policy": "bounded-overwrite-oldest-not-recorded",
        }


@dataclass(frozen=True, slots=True)
class RecordingPolicy:
    tdms_overhead_multiplier: float = 1.10
    hdf5_overhead_multiplier: float = 1.10
    metadata_margin_bytes: int = 16 * 1024 * 1024
    metadata_reserve_bytes: int = 1024 * 1024
    warning_free_bytes: int = 1024 * 1024 * 1024
    hard_stop_free_bytes: int = 512 * 1024 * 1024
    capacity_check_interval_seconds: float = 1.0
    checkpoint_interval_seconds: float = 1.0
    path_warning_length: int = 240
    file_checksums: bool = True

    def __post_init__(self) -> None:
        if not math.isfinite(self.tdms_overhead_multiplier) or self.tdms_overhead_multiplier < 1:
            raise ValueError("tdms_overhead_multiplier must be finite and at least one")
        if not math.isfinite(self.hdf5_overhead_multiplier) or self.hdf5_overhead_multiplier < 1:
            raise ValueError("hdf5_overhead_multiplier must be finite and at least one")
        for name, value in (
            ("metadata_margin_bytes", self.metadata_margin_bytes),
            ("metadata_reserve_bytes", self.metadata_reserve_bytes),
            ("warning_free_bytes", self.warning_free_bytes),
            ("hard_stop_free_bytes", self.hard_stop_free_bytes),
            ("path_warning_length", self.path_warning_length),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.warning_free_bytes <= self.hard_stop_free_bytes:
            raise ValueError("warning_free_bytes must exceed hard_stop_free_bytes")
        for interval_name, interval_value in (
            ("capacity_check_interval_seconds", self.capacity_check_interval_seconds),
            ("checkpoint_interval_seconds", self.checkpoint_interval_seconds),
        ):
            if not math.isfinite(interval_value) or interval_value <= 0:
                raise ValueError(f"{interval_name} must be positive and finite")


@dataclass(frozen=True, slots=True)
class SessionInitialization:
    plan: SessionPlan
    profile: HardwareProfile
    experiment: ExperimentConfig
    recipe: ProcessingRecipeEnvelope
    discovery: DaqDiscoveryReport
    daq_configuration: DaqConfigurationResult
    application_version: str
    session_type: SessionType = SessionType.EXPERIMENT
    qualification_attachment: QualificationAttachment | None = None
    diagnostic_authorization: Mapping[str, object] | None = None
    open_acquisition: OpenAcquisitionProvenance | None = None

    def __post_init__(self) -> None:
        if self.session_type is SessionType.RAW_DIAGNOSTIC:
            if self.qualification_attachment is not None:
                raise ValueError("raw diagnostic sessions cannot attach qualification evidence")
            # Historical diagnostic authorizations are retained when supplied; ordinary
            # raw monitoring does not require a commissioning confirmation.
        elif self.diagnostic_authorization is not None:
            raise ValueError("diagnostic authorization is valid only for raw diagnostic sessions")
        if (
            self.open_acquisition is not None
            and self.session_type is not SessionType.RAW_DIAGNOSTIC
        ):
            raise ValueError(
                "open-acquisition provenance is valid only for raw diagnostic sessions"
            )
        if self.diagnostic_authorization is not None:
            object.__setattr__(
                self,
                "diagnostic_authorization",
                freeze_mapping(self.diagnostic_authorization),
            )


@dataclass(frozen=True, slots=True)
class QualificationAttachment:
    report_id: str
    report_sha256: str
    qualification_fingerprint: str
    overall_status: str
    evaluated_at: datetime
    age_seconds: float
    eligible: bool
    report_json: bytes
    override_reason_code: str | None = None

    def __post_init__(self) -> None:
        if not self.report_id:
            raise ValueError("qualification report_id must not be empty")
        if hashlib.sha256(self.report_json).hexdigest() != self.report_sha256:
            raise ValueError("qualification report bytes do not match report_sha256")
        if len(self.qualification_fingerprint) != 64:
            raise ValueError("qualification fingerprint must be a SHA-256 digest")
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("qualification evaluated_at must be timezone-aware")
        if not math.isfinite(self.age_seconds) or self.age_seconds < 0:
            raise ValueError("qualification age_seconds must be finite and non-negative")

    def manifest_summary(self) -> dict[str, object]:
        return {
            "report_id": self.report_id,
            "report_path": "qualification-report.json",
            "report_sha256": self.report_sha256,
            "qualification_fingerprint": self.qualification_fingerprint,
            "overall_status": self.overall_status,
            "evaluated_at": self.evaluated_at.isoformat(),
            "age_seconds": self.age_seconds,
            "eligible": self.eligible,
            "override_reason_code": self.override_reason_code,
        }


@dataclass(frozen=True, slots=True)
class PersistenceReceipt:
    session_id: str
    first_sample_index: int
    last_sample_index: int
    accepted_sample_count: int
    total_persisted_sample_count: int
    checkpoint_sequence: int
    completed_at: datetime

    def __post_init__(self) -> None:
        if not self.session_id:
            raise ValueError("session_id must not be empty")
        if self.first_sample_index < 0 or self.last_sample_index < self.first_sample_index:
            raise ValueError("receipt sample indices are invalid")
        if self.accepted_sample_count <= 0 or self.total_persisted_sample_count <= 0:
            raise ValueError("receipt sample counts must be positive")
        if self.last_sample_index - self.first_sample_index + 1 != self.accepted_sample_count:
            raise ValueError("receipt indices must match accepted_sample_count")
        if self.checkpoint_sequence < 0:
            raise ValueError("checkpoint_sequence must be non-negative")
        if self.completed_at.tzinfo is None or self.completed_at.utcoffset() is None:
            raise ValueError("completed_at must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "first_sample_index": self.first_sample_index,
            "last_sample_index": self.last_sample_index,
            "accepted_sample_count": self.accepted_sample_count,
            "total_persisted_sample_count": self.total_persisted_sample_count,
            "checkpoint_sequence": self.checkpoint_sequence,
            "completed_at": self.completed_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class SessionFaultSummary:
    code: str
    message: str
    context: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.code.startswith("DAQ.STORAGE.") and not self.code.startswith("DAQ.SESSION."):
            raise ValueError("session fault codes must use DAQ.STORAGE or DAQ.SESSION")
        if not self.message:
            raise ValueError("session fault message must not be empty")
        safe = sanitize_context(self.context, reject_arrays=True, strict=True)
        object.__setattr__(self, "context", freeze_mapping(safe))

    def to_dict(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message, "context": thaw_mapping(self.context)}


@dataclass(frozen=True, slots=True)
class SessionFinalization:
    acquisition: AcquisitionCompletion | None
    storage_fault: SessionFaultSummary | None = None
    recording_segment: RecordingSegmentCompletion | None = None

    def __post_init__(self) -> None:
        if (self.acquisition is None) == (self.recording_segment is None):
            raise ValueError(
                "session finalization requires exactly one acquisition or recording segment"
            )


@dataclass(frozen=True, slots=True)
class RecordingSegmentCompletion:
    """Terminal evidence for a recording that did not stop the DAQ task."""

    session_id: str
    started_at: datetime
    stopped_at: datetime
    source_start_sample_index: int
    source_stop_sample_index: int
    total_recording_samples_accepted: int
    stop_requested_at: datetime | None = None
    fault: SessionFaultSummary | None = None

    def __post_init__(self) -> None:
        if not self.session_id:
            raise ValueError("recording segment session_id must not be empty")
        for name, value in (("started_at", self.started_at), ("stopped_at", self.stopped_at)):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.stopped_at < self.started_at:
            raise ValueError("recording segment stop must not precede start")
        if self.source_start_sample_index < 0:
            raise ValueError("recording segment source start must be non-negative")
        if self.source_stop_sample_index < self.source_start_sample_index:
            raise ValueError("recording segment source stop must not precede start")
        expected = self.source_stop_sample_index - self.source_start_sample_index
        if self.total_recording_samples_accepted != expected:
            raise ValueError("recording segment count must match its half-open source range")
        if self.stop_requested_at is not None and (
            self.stop_requested_at.tzinfo is None or self.stop_requested_at.utcoffset() is None
        ):
            raise ValueError("recording segment stop request time must be timezone-aware")

    @property
    def total_acquired_samples_per_channel(self) -> int:
        return self.total_recording_samples_accepted


@dataclass(frozen=True, slots=True)
class RecordingSegmentStatus:
    session_id: str
    source_start_sample_index: int
    source_stop_sample_index: int | None
    acquired_samples_per_channel: int
    recording_samples_enqueued: int
    persisted_samples_per_channel: int
    display_frames_dropped: int = 0

    def __post_init__(self) -> None:
        if not self.session_id or self.source_start_sample_index < 0:
            raise ValueError("recording segment status identity is invalid")
        if (
            self.source_stop_sample_index is not None
            and self.source_stop_sample_index < self.source_start_sample_index
        ):
            raise ValueError("recording segment status source range is invalid")
        counts = (
            self.acquired_samples_per_channel,
            self.recording_samples_enqueued,
            self.persisted_samples_per_channel,
            self.display_frames_dropped,
        )
        if any(value < 0 for value in counts):
            raise ValueError("recording segment status counts must be non-negative")


@dataclass(frozen=True, slots=True)
class RecordingStatus:
    plan: SessionPlan
    session_state: SessionCompletionState
    acquisition: AcquisitionStatus
    persisted_samples_per_channel: int
    checkpoint_sequence: int
    storage_warning_code: str | None = None
    storage_fault: SessionFaultSummary | None = None
    write_count: int = 0
    write_duration_total_seconds: float = 0.0
    write_duration_max_seconds: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "plan": self.plan.to_dict(),
            "session_state": self.session_state.value,
            "acquisition": self.acquisition.to_dict(),
            "persisted_samples_per_channel": self.persisted_samples_per_channel,
            "checkpoint_sequence": self.checkpoint_sequence,
            "storage_warning_code": self.storage_warning_code,
            "storage_fault": self.storage_fault.to_dict() if self.storage_fault else None,
            "write_count": self.write_count,
            "write_duration_total_seconds": self.write_duration_total_seconds,
            "write_duration_max_seconds": self.write_duration_max_seconds,
        }


@dataclass(frozen=True, slots=True)
class IntegrityIssue:
    code: str
    status: IntegrityStatus
    message: str
    context: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.code.startswith("DAQ.") or not self.message:
            raise ValueError("integrity issues require a DAQ code and message")
        safe = sanitize_context(self.context, reject_arrays=True, strict=True)
        object.__setattr__(self, "context", freeze_mapping(safe))

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "status": self.status.value,
            "message": self.message,
            "context": thaw_mapping(self.context),
        }


@dataclass(frozen=True, slots=True)
class SessionIntegrityReport:
    session_path: Path
    verification_level: VerificationLevel
    overall: IntegrityStatus
    manifest_state: SessionCompletionState | None
    effective_state: SessionCompletionState
    recoverability: Recoverability
    session_id: str | None
    readable_common_sample_count: int
    manifest_persisted_sample_count: int | None
    per_channel_lengths: Mapping[str, int]
    checksum_results: Mapping[str, bool | None]
    event_count: int
    issues: tuple[IntegrityIssue, ...]
    verified_at: datetime

    def __post_init__(self) -> None:
        if self.readable_common_sample_count < 0 or self.event_count < 0:
            raise ValueError("integrity counts must be non-negative")
        if self.verified_at.tzinfo is None or self.verified_at.utcoffset() is None:
            raise ValueError("verified_at must be timezone-aware")
        object.__setattr__(self, "per_channel_lengths", freeze_mapping(self.per_channel_lengths))
        object.__setattr__(self, "checksum_results", freeze_mapping(self.checksum_results))

    def to_dict(self) -> dict[str, object]:
        return {
            "session_path": str(self.session_path),
            "verification_level": self.verification_level.value,
            "overall": self.overall.value,
            "manifest_state": self.manifest_state.value if self.manifest_state else None,
            "effective_state": self.effective_state.value,
            "recoverability": self.recoverability.value,
            "session_id": self.session_id,
            "readable_common_sample_count": self.readable_common_sample_count,
            "manifest_persisted_sample_count": self.manifest_persisted_sample_count,
            "per_channel_lengths": thaw_mapping(self.per_channel_lengths),
            "checksum_results": thaw_mapping(self.checksum_results),
            "event_count": self.event_count,
            "issues": [issue.to_dict() for issue in self.issues],
            "verified_at": self.verified_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class SessionHardwareSummary:
    profile_revision: int | None
    backend_id: str | None
    device_id: str | None
    device_model: str | None
    device_serial_number: str | None
    module_id: str | None
    module_model: str | None
    module_serial_number: str | None
    driver_version: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "profile_revision": self.profile_revision,
            "backend_id": self.backend_id,
            "device_id": self.device_id,
            "device_model": self.device_model,
            "device_serial_number": self.device_serial_number,
            "module_id": self.module_id,
            "module_model": self.module_model,
            "module_serial_number": self.module_serial_number,
            "driver_version": self.driver_version,
        }


@dataclass(frozen=True, slots=True)
class SessionChannelSummary:
    channel_id: str
    display_name: str
    physical_channel: str
    channel_role: str
    raw_unit: str
    engineering_unit: str
    sensor_model: str | None
    sensor_serial_number: str | None
    calibration_id: str | None
    calibration_source: str | None
    processing_disposition: str = "calibrated"
    engineering_interpretation: str | None = "calibrated"
    warning: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "channel_id": self.channel_id,
            "display_name": self.display_name,
            "physical_channel": self.physical_channel,
            "channel_role": self.channel_role,
            "raw_unit": self.raw_unit,
            "engineering_unit": self.engineering_unit,
            "sensor_model": self.sensor_model,
            "sensor_serial_number": self.sensor_serial_number,
            "calibration_id": self.calibration_id,
            "calibration_source": self.calibration_source,
            "processing_disposition": self.processing_disposition,
            "engineering_interpretation": self.engineering_interpretation,
            "warning": self.warning,
        }


@dataclass(frozen=True, slots=True)
class SessionRecipeSummary:
    recipe_id: str
    recipe_version: str
    recipe_hash: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "recipe_id": self.recipe_id,
            "recipe_version": self.recipe_version,
            "recipe_hash": self.recipe_hash,
        }


@dataclass(frozen=True, slots=True)
class SessionQualificationSummary:
    report_id: str
    qualification_fingerprint: str
    overall_status: str
    eligible: bool
    report_sha256: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "report_id": self.report_id,
            "qualification_fingerprint": self.qualification_fingerprint,
            "overall_status": self.overall_status,
            "eligible": self.eligible,
            "report_sha256": self.report_sha256,
        }


@dataclass(frozen=True, slots=True)
class RawContainerSummary:
    format: str
    filename: str
    layout_version: int
    dataset_path: str | None = None

    def __post_init__(self) -> None:
        if self.format not in {"hdf5", "tdms"} or not self.filename or self.layout_version <= 0:
            raise ValueError("raw container summary is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "format": self.format,
            "filename": self.filename,
            "layout_version": self.layout_version,
            "dataset_path": self.dataset_path,
        }


@dataclass(frozen=True, slots=True)
class SessionSummary:
    session_path: Path
    session_id: str
    experiment_name: str
    completion_state: SessionCompletionState
    completion_reason: SessionCompletionReason | None
    started_at: datetime | None
    ended_at: datetime | None
    hardware_profile_id: str
    requested_sample_rate_hz: float
    actual_sample_rate_hz: float
    ordered_channel_ids: tuple[str, ...]
    ordered_channel_units: tuple[str, ...]
    per_channel_sample_counts: Mapping[str, int]
    common_sample_count: int
    acquired_duration_seconds: float
    event_count: int
    warning_count: int
    fault_count: int
    integrity: SessionIntegrityReport
    raw_container: RawContainerSummary
    session_type: SessionType = SessionType.EXPERIMENT
    sample_id: str | None = None
    requested_duration_seconds: float | None = None
    ordered_physical_channels: tuple[str, ...] = ()
    hardware: SessionHardwareSummary | None = None
    channels: tuple[SessionChannelSummary, ...] = ()
    recipe: SessionRecipeSummary | None = None
    qualification: SessionQualificationSummary | None = None
    operator: str | None = None
    description: str | None = None
    notes: str | None = None
    acquired_samples_per_channel: int = 0
    persisted_samples_per_channel: int = 0
    display_frames_dropped: int = 0
    acquisition_gap_count: int = 0
    persistence_loss_samples: int = 0
    manifest_available: bool = False
    event_log_available: bool = False
    raw_data_available: bool = False
    qualification_report_available: bool = False
    # Persisted in the manifest so commissioning sessions remain visibly
    # partial-qualification artifacts after they are reopened.
    experiment_mode: str = "production"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "per_channel_sample_counts",
            freeze_mapping(self.per_channel_sample_counts),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "session_path": str(self.session_path),
            "session_id": self.session_id,
            "experiment_name": self.experiment_name,
            "completion_state": self.completion_state.value,
            "completion_reason": self.completion_reason.value if self.completion_reason else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "hardware_profile_id": self.hardware_profile_id,
            "requested_sample_rate_hz": self.requested_sample_rate_hz,
            "actual_sample_rate_hz": self.actual_sample_rate_hz,
            "ordered_channel_ids": list(self.ordered_channel_ids),
            "ordered_channel_units": list(self.ordered_channel_units),
            "per_channel_sample_counts": thaw_mapping(self.per_channel_sample_counts),
            "common_sample_count": self.common_sample_count,
            "acquired_duration_seconds": self.acquired_duration_seconds,
            "event_count": self.event_count,
            "warning_count": self.warning_count,
            "fault_count": self.fault_count,
            "integrity": self.integrity.to_dict(),
            "raw_container": self.raw_container.to_dict(),
            "session_type": self.session_type.value,
            "sample_id": self.sample_id,
            "requested_duration_seconds": self.requested_duration_seconds,
            "ordered_physical_channels": list(self.ordered_physical_channels),
            "hardware": self.hardware.to_dict() if self.hardware else None,
            "channels": [channel.to_dict() for channel in self.channels],
            "recipe": self.recipe.to_dict() if self.recipe else None,
            "qualification": self.qualification.to_dict() if self.qualification else None,
            "operator": self.operator,
            "description": self.description,
            "notes": self.notes,
            "acquired_samples_per_channel": self.acquired_samples_per_channel,
            "persisted_samples_per_channel": self.persisted_samples_per_channel,
            "display_frames_dropped": self.display_frames_dropped,
            "acquisition_gap_count": self.acquisition_gap_count,
            "persistence_loss_samples": self.persistence_loss_samples,
            "manifest_available": self.manifest_available,
            "event_log_available": self.event_log_available,
            "raw_data_available": self.raw_data_available,
            "qualification_report_available": self.qualification_report_available,
            "experiment_mode": self.experiment_mode,
        }


@dataclass(frozen=True, slots=True)
class RecordedSessionCompletion:
    session_path: Path
    session_id: str
    state: SessionCompletionState
    reason: SessionCompletionReason
    acquired_samples_per_channel: int
    persisted_samples_per_channel: int
    fault: SessionFaultSummary | None
    integrity: SessionIntegrityReport
    acquisition_completion: AcquisitionCompletion | None = None
    recording_segment_completion: RecordingSegmentCompletion | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "session_path": str(self.session_path),
            "session_id": self.session_id,
            "state": self.state.value,
            "reason": self.reason.value,
            "acquired_samples_per_channel": self.acquired_samples_per_channel,
            "persisted_samples_per_channel": self.persisted_samples_per_channel,
            "fault": self.fault.to_dict() if self.fault else None,
            "integrity": self.integrity.to_dict(),
            "acquisition_completion": (
                self.acquisition_completion.to_dict() if self.acquisition_completion else None
            ),
            "recording_segment_completion": (
                {
                    "session_id": self.recording_segment_completion.session_id,
                    "started_at": self.recording_segment_completion.started_at.isoformat(),
                    "stopped_at": self.recording_segment_completion.stopped_at.isoformat(),
                    "source_start_sample_index": (
                        self.recording_segment_completion.source_start_sample_index
                    ),
                    "source_stop_sample_index": (
                        self.recording_segment_completion.source_stop_sample_index
                    ),
                    "total_recording_samples_accepted": (
                        self.recording_segment_completion.total_recording_samples_accepted
                    ),
                    "stop_requested_at": (
                        self.recording_segment_completion.stop_requested_at.isoformat()
                        if self.recording_segment_completion.stop_requested_at
                        else None
                    ),
                    "fault": (
                        self.recording_segment_completion.fault.to_dict()
                        if self.recording_segment_completion.fault
                        else None
                    ),
                }
                if self.recording_segment_completion
                else None
            ),
        }


class SessionPathPlannerPort(Protocol):
    def plan_session(
        self,
        session: SessionConfig,
        *,
        session_id: str,
        created_at: datetime,
        policy: RecordingPolicy,
    ) -> SessionPlan: ...


class StorageCapacityPort(Protocol):
    def free_bytes(self, path: Path) -> int: ...

    def is_writable(self, path: Path) -> bool: ...

    def estimate_required_bytes(
        self,
        experiment: ExperimentConfig,
        policy: RecordingPolicy,
    ) -> int: ...


class SessionLocationPort(Protocol):
    def exists(self, path: Path) -> bool: ...

    def is_directory(self, path: Path) -> bool: ...


class SessionCatalogPort(Protocol):
    def discover_session_paths(self, parent: Path) -> tuple[Path, ...]: ...


class SessionWriterPort(Protocol):
    @property
    def plan(self) -> SessionPlan: ...

    @property
    def state(self) -> SessionCompletionState: ...

    @property
    def persisted_samples_per_channel(self) -> int: ...

    @property
    def checkpoint_sequence(self) -> int: ...

    def append_chunk(self, chunk: AcquisitionChunk) -> PersistenceReceipt: ...

    def append_event(self, event: AcquisitionEvent) -> None: ...

    def checkpoint(self, status: AcquisitionStatus | RecordingSegmentStatus) -> None: ...

    def finalize(self, finalization: SessionFinalization) -> RecordedSessionCompletion: ...

    def abort(self, fault: SessionFaultSummary) -> RecordedSessionCompletion: ...

    def close(self) -> None: ...


class SessionWriterFactoryPort(Protocol):
    def create_writer(
        self,
        initialization: SessionInitialization,
        policy: RecordingPolicy,
    ) -> SessionWriterPort: ...


class SessionReaderPort(Protocol):
    def inspect_session(
        self,
        path: Path,
        *,
        level: VerificationLevel = VerificationLevel.FAST,
    ) -> SessionIntegrityReport: ...

    def summarize_session(
        self,
        path: Path,
        *,
        level: VerificationLevel = VerificationLevel.FAST,
    ) -> SessionSummary: ...

    def recover_session(self, path: Path) -> SessionIntegrityReport: ...


class ReplayStream(Protocol):
    @property
    def state(self) -> ReplayState: ...

    def __iter__(self) -> Iterator[AcquisitionChunk]: ...

    def cancel(self) -> None: ...

    def close(self) -> None: ...


class ReplaySourcePort(Protocol):
    def open_replay(self, path: Path, *, chunk_size: int) -> ReplayStream: ...
