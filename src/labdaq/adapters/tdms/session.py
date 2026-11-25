"""HDF5-primary durable sessions with legacy npTDMS compatibility."""

from __future__ import annotations

import errno
import json
import logging
import os
import platform
import socket
import time
import warnings
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, date, datetime
from importlib.resources import files
from pathlib import Path
from threading import Event, RLock
from typing import IO, TYPE_CHECKING, Any, cast

import numpy as np
from jsonschema import Draft202012Validator, FormatChecker

from labdaq._context import thaw_mapping
from labdaq.adapters.filesystem import FileSystemCapacityAdapter
from labdaq.domain.enums import (
    CalibrationModel,
    CalibrationSourceKind,
    ChannelRole,
    PhysicalQuantity,
    Polarity,
    ProcessingDisposition,
    SensitivityUnit,
    Unit,
)
from labdaq.domain.models import CalibrationConfig, NominalCalibrationFallbackPolicy
from labdaq.domain.serialization import canonical_sha256
from labdaq.errors import (
    InvalidSessionParentError,
    SessionIntegrityError,
    SessionNotReplayableError,
    SessionOwnedError,
    SessionPathCollisionError,
    SessionSchemaError,
    SessionWriteError,
)
from labdaq.ports.acquisition import (
    AcquisitionChunk,
    AcquisitionCompletion,
    AcquisitionEvent,
    AcquisitionState,
    CompletionReason,
)
from labdaq.ports.operator import SessionEventRecord
from labdaq.ports.processing import (
    ProcessingSourceMetadata,
    SessionChannelMetadata,
)
from labdaq.ports.session import (
    IntegrityIssue,
    IntegrityStatus,
    PersistenceReceipt,
    RawContainerSummary,
    RecordedSessionCompletion,
    RecordingPolicy,
    RecordingSegmentCompletion,
    Recoverability,
    ReplayState,
    SessionChannelSummary,
    SessionCompletionReason,
    SessionCompletionState,
    SessionFaultSummary,
    SessionFinalization,
    SessionHardwareSummary,
    SessionInitialization,
    SessionIntegrityReport,
    SessionPlan,
    SessionQualificationSummary,
    SessionRecipeSummary,
    SessionSummary,
    SessionType,
    VerificationLevel,
)
from labdaq.storage.integrity import (
    LOGICAL_DIGEST_VERSION,
    LogicalDataDigest,
    file_sha256,
)
from labdaq.storage.policy import SessionPathPolicy
from labdaq.storage.serialization import to_primitive

if TYPE_CHECKING:
    from labdaq.domain.models import ExperimentConfig, SessionConfig
    from labdaq.ports.session import StorageCapacityPort

_MANIFEST_NAME = "manifest.json"
_RAW_NAME = "raw.h5"
_TDMS_RAW_NAME = "raw.tdms"
_EVENTS_NAME = "events.jsonl"
_RECIPE_NAME = "processing-recipe.json"
_LOCK_NAME = ".recording.lock"
_RESERVE_NAME = ".metadata-reserve"
_MANIFEST_SCHEMA = "session-manifest.schema.json"
_EVENT_SCHEMA = "session-event.schema.json"
_RECIPE_SCHEMA = "processing-recipe.schema.json"
_RAW_GROUP = "raw"
_SESSION_SCHEMA_VERSION = 2
_TDMS_LAYOUT_VERSION = 1
_HDF5_LAYOUT_VERSION = 1
_EVENT_SCHEMA_VERSION = 1


def _nptdms_types() -> tuple[Any, Any, Any, Any, Any]:
    """Load legacy TDMS support only when a v1 session or TDMS export needs it."""
    from nptdms import (  # type: ignore[import-untyped]
        ChannelObject,
        GroupObject,
        RootObject,
        TdmsFile,
        TdmsWriter,
    )

    return ChannelObject, GroupObject, RootObject, TdmsFile, TdmsWriter


class _Hdf5RawWriter:
    """Append-only HDF5 raw container with an explicit committed-prefix journal."""

    def __init__(
        self,
        path: Path,
        initialization: SessionInitialization,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        import h5py  # type: ignore[import-untyped]

        configuration = initialization.daq_configuration
        channel_count = len(configuration.ordered_logical_channels)
        chunk_samples = initialization.experiment.acquisition.chunk_config.samples_per_channel
        self._path = path
        self._fault_injector = fault_injector or (lambda phase: None)
        self._file = h5py.File(path, "x", libver="latest", track_order=True)
        self._file.attrs.update(
            {
                "session_id": initialization.plan.session_id,
                "session_schema_version": _SESSION_SCHEMA_VERSION,
                "raw_container_format": "hdf5",
                "hdf5_layout_version": _HDF5_LAYOUT_VERSION,
                "application_version": initialization.application_version,
                "requested_sample_rate_hz": configuration.requested_sample_rate_hz,
                "actual_sample_rate_hz": configuration.effective_sample_rate_hz,
                "channel_count": channel_count,
                "ordered_channel_ids_json": json.dumps(configuration.ordered_logical_channels),
                "session_created_at_utc": initialization.plan.created_at.isoformat(),
                "timezone": "UTC",
                "canonical_sample_dtype": "float64-le",
                "source_classification": "unconditioned_voltage",
            }
        )
        raw = self._file.create_group("raw", track_order=True)
        self._samples = raw.create_dataset(
            "samples",
            shape=(channel_count, 0),
            maxshape=(channel_count, None),
            chunks=(channel_count, chunk_samples),
            dtype="<f8",
            compression=None,
            fletcher32=True,
        )
        self._samples.dims[0].label = "channel"
        self._samples.dims[1].label = "sample_index"
        raw.attrs["channel_order_is_authoritative"] = True
        raw.attrs["sample_index_origin"] = 0
        raw.attrs["relative_time_definition"] = "sample_index / actual_sample_rate_hz"
        ids = tuple(configuration.ordered_logical_channels)
        snapshots = _channel_snapshots(initialization)
        snapshots_by_id = {str(snapshot["logical_channel_id"]): snapshot for snapshot in snapshots}
        units = tuple(str(snapshots_by_id[channel_id]["raw_unit"]) for channel_id in ids)
        _write_fixed_utf8_dataset(raw, "channel_ids", ids)
        _write_fixed_utf8_dataset(raw, "channel_units", units)
        channels = self._file.create_group("channels", track_order=True)
        for index, snapshot in enumerate(snapshots):
            group = channels.create_group(f"{index:03d}", track_order=True)
            for name, value in _tdms_channel_properties(snapshot).items():
                group.attrs[name] = _hdf5_attribute(value)
        journal = self._file.create_group("journal", track_order=True)
        commit_dtype = np.dtype(
            [
                ("sequence_number", "<u8"),
                ("start_sample_index", "<u8"),
                ("sample_count", "<u8"),
                ("stop_sample_index", "<u8"),
            ]
        )
        self._commits = journal.create_dataset(
            "commits",
            shape=(0,),
            maxshape=(None,),
            chunks=(1024,),
            dtype=commit_dtype,
            fletcher32=True,
        )
        finalization_dtype = np.dtype(
            [
                ("terminal_state", "S16"),
                ("reason", "S64"),
                ("acquired_samples", "<u8"),
                ("persisted_samples", "<u8"),
            ]
        )
        self._finalization = journal.create_dataset(
            "finalization",
            shape=(0,),
            maxshape=(1,),
            chunks=(1,),
            dtype=finalization_dtype,
            fletcher32=True,
        )
        self._file.flush()
        self._file.swmr_mode = True

    @property
    def sample_count(self) -> int:
        return int(self._samples.shape[1])

    def append_chunk(self, chunk: AcquisitionChunk) -> None:
        self._fault_injector("before_data_write")
        old_count = self.sample_count
        new_count = old_count + chunk.sample_count
        self._samples.resize(new_count, axis=1)
        self._samples[:, old_count:new_count] = np.asarray(chunk.samples, dtype="<f8")
        self._samples.flush()
        self._fault_injector("after_data_flush_before_commit")
        commit_index = int(self._commits.shape[0])
        self._commits.resize(commit_index + 1, axis=0)
        self._commits[commit_index] = (
            chunk.sequence_number,
            chunk.start_sample_index,
            chunk.sample_count,
            chunk.start_sample_index + chunk.sample_count,
        )
        self._commits.flush()
        self._fault_injector("after_commit_before_manifest_checkpoint")

    def append_finalization(
        self,
        terminal_state: SessionCompletionState,
        completion_reason: SessionCompletionReason,
        acquired_samples: int,
        persisted_samples: int,
    ) -> None:
        self._fault_injector("during_finalization")
        self._finalization.resize(1, axis=0)
        self._finalization[0] = (
            terminal_state.value.encode("ascii"),
            completion_reason.value.encode("ascii"),
            acquired_samples,
            persisted_samples,
        )
        self._finalization.flush()

    def sync(self) -> None:
        self._file.flush()
        self._fault_injector("during_os_sync")
        handle = self._file.id.get_vfd_handle()
        if isinstance(handle, tuple):
            handle = handle[0]
        if os.name == "nt":
            import ctypes
            import msvcrt

            windows_handle = msvcrt.get_osfhandle(int(handle))
            if not ctypes.windll.kernel32.FlushFileBuffers(windows_handle):
                raise OSError(ctypes.get_last_error(), "FlushFileBuffers failed")
        else:
            os.fsync(int(handle))

    def close(self) -> None:
        if self._file:
            self._file.close()


def _write_fixed_utf8_dataset(group: Any, name: str, values: tuple[str, ...]) -> None:
    width = max(1, *(len(value.encode("utf-8")) for value in values))
    group.create_dataset(
        name, data=np.asarray([value.encode("utf-8") for value in values], dtype=f"S{width}")
    )


def _hdf5_attribute(value: object) -> object:
    if value is None:
        return "null"
    if isinstance(value, (str, bytes, bool, int, float, np.number)):
        return value
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


class TdmsSessionStore:
    """HDF5-primary session store with read-only legacy TDMS dispatch."""

    def __init__(
        self,
        *,
        capacity: StorageCapacityPort | None = None,
        now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        process_is_alive: Callable[[str, int], bool] | None = None,
        hdf5_fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self._capacity = capacity or FileSystemCapacityAdapter()
        self._now = now or (lambda: datetime.now(UTC))
        self._monotonic = monotonic or time.monotonic
        self._process_is_alive = process_is_alive or _process_is_alive
        self._path_policy = SessionPathPolicy()
        self._hdf5_fault_injector = hdf5_fault_injector

    def plan_session(
        self,
        session: SessionConfig,
        *,
        session_id: str,
        created_at: datetime,
        policy: RecordingPolicy,
    ) -> SessionPlan:
        return self._path_policy.plan_session(
            session,
            session_id=session_id,
            created_at=created_at,
            policy=policy,
        )

    def free_bytes(self, path: Path) -> int:
        return self._capacity.free_bytes(path)

    def is_writable(self, path: Path) -> bool:
        return self._capacity.is_writable(path)

    def estimate_required_bytes(
        self,
        experiment: ExperimentConfig,
        policy: RecordingPolicy,
    ) -> int:
        return self._capacity.estimate_required_bytes(experiment, policy)

    def create_writer(
        self,
        initialization: SessionInitialization,
        policy: RecordingPolicy,
    ) -> TdmsSessionWriter:
        plan = initialization.plan
        parent = plan.parent_directory
        if _is_remote_path(parent):
            raise InvalidSessionParentError(
                "Live recording to a network filesystem is not supported.",
                code="DAQ.STORAGE.REMOTE_RECORDING_UNSUPPORTED",
                context={"parent_directory": str(parent)},
                remediation="Record to a local Windows volume and archive the finalized session afterward.",
            )
        if not parent.exists() or not parent.is_dir() or not self._capacity.is_writable(parent):
            raise InvalidSessionParentError(
                "The selected session parent does not exist or is not writable.",
                code="DAQ.STORAGE.INVALID_SESSION_PARENT",
                context={"parent_directory": str(parent)},
                remediation="Choose an existing writable session parent directory.",
            )
        if plan.session_path.exists():
            raise SessionPathCollisionError(
                "The planned session directory already exists.",
                code="DAQ.STORAGE.SESSION_PATH_COLLISION",
                context={"session_path": str(plan.session_path)},
                remediation="Create a new preview with a new session UUID; existing sessions are never overwritten.",
            )
        required = self._capacity.estimate_required_bytes(initialization.experiment, policy)
        available = self._capacity.free_bytes(parent)
        if available < required:
            raise SessionWriteError(
                "The selected session parent has insufficient free space.",
                code="DAQ.STORAGE.INSUFFICIENT_SPACE",
                context={
                    "parent_directory": str(parent),
                    "available_bytes": available,
                    "required_bytes": required,
                },
                remediation="Free disk space or select a different session parent.",
            )
        return TdmsSessionWriter(
            initialization,
            policy,
            capacity=self._capacity,
            now=self._now,
            monotonic=self._monotonic,
            store=self,
            hdf5_fault_injector=self._hdf5_fault_injector,
        )

    def inspect_session(
        self,
        path: Path,
        *,
        level: VerificationLevel = VerificationLevel.FAST,
    ) -> SessionIntegrityReport:
        return _inspect_session(
            path.absolute(),
            level=level,
            now=self._now,
            process_is_alive=self._process_is_alive,
        )

    def summarize_session(
        self,
        path: Path,
        *,
        level: VerificationLevel = VerificationLevel.FAST,
    ) -> SessionSummary:
        session_path = path.absolute()
        report = self.inspect_session(session_path, level=level)
        manifest = _read_manifest(session_path)
        raw_name = _raw_filename(manifest)
        session = _mapping(manifest, "session")
        acquisition = _mapping(manifest, "acquisition")
        counts = _mapping(manifest, "counts")
        hardware = _mapping(manifest, "hardware")
        profile = _mapping(hardware, "profile")
        configuration = _mapping(hardware, "configuration")
        discovery = _mapping(hardware, "discovery")
        ordered_ids = tuple(_string_list(acquisition, "ordered_channel_ids"))
        ordered_units = tuple(_string_list(acquisition, "ordered_channel_units"))
        channel_counts = {
            channel_id: int(report.per_channel_lengths.get(channel_id, 0))
            for channel_id in ordered_ids
        }
        reason_value = session.get("completion_reason")
        if manifest.get("schema_version") == 1:
            raw_container = RawContainerSummary("tdms", _TDMS_RAW_NAME, _TDMS_LAYOUT_VERSION)
        else:
            raw = _mapping(manifest, "raw_container")
            raw_container = RawContainerSummary(
                format=_string(raw, "format"),
                filename=_string(raw, "filename"),
                layout_version=_as_int(raw["layout_version"], "layout_version"),
                dataset_path=_optional_mapping_string(raw, "dataset_path"),
            )
        return SessionSummary(
            session_path=session_path,
            session_id=_string(session, "session_id"),
            experiment_name=_string(session, "experiment_name"),
            completion_state=report.effective_state,
            completion_reason=(
                SessionCompletionReason(str(reason_value)) if reason_value is not None else None
            ),
            started_at=_optional_datetime(session.get("acquisition_started_at")),
            ended_at=_optional_datetime(session.get("acquisition_ended_at")),
            hardware_profile_id=str(
                _mapping(_mapping(manifest, "hardware"), "profile").get(
                    "hardware_profile_id", "unknown"
                )
            ),
            requested_sample_rate_hz=_as_float(
                acquisition["requested_sample_rate_hz"], "requested_sample_rate_hz"
            ),
            actual_sample_rate_hz=_as_float(
                acquisition["actual_sample_rate_hz"], "actual_sample_rate_hz"
            ),
            ordered_channel_ids=ordered_ids,
            ordered_channel_units=ordered_units,
            per_channel_sample_counts=channel_counts,
            common_sample_count=report.readable_common_sample_count,
            acquired_duration_seconds=(
                report.readable_common_sample_count
                / _as_float(acquisition["actual_sample_rate_hz"], "actual_sample_rate_hz")
            ),
            event_count=_as_int(counts.get("event_count", report.event_count), "event_count"),
            warning_count=_as_int(counts.get("warning_count", 0), "warning_count"),
            fault_count=_as_int(counts.get("fault_count", 0), "fault_count"),
            integrity=report,
            raw_container=raw_container,
            session_type=SessionType(str(session.get("session_type", "experiment"))),
            sample_id=_optional_mapping_string(session, "sample_id"),
            requested_duration_seconds=_optional_mapping_float(
                session, "requested_duration_seconds"
            ),
            ordered_physical_channels=tuple(_string_list(acquisition, "ordered_physical_channels")),
            hardware=_session_hardware_summary(profile, configuration, discovery),
            channels=_session_channel_summaries(manifest.get("channels")),
            recipe=_session_recipe_summary(manifest.get("processing_recipe")),
            qualification=_session_qualification_summary(manifest.get("qualification")),
            operator=_optional_mapping_string(session, "operator"),
            description=_optional_mapping_string(session, "description"),
            notes=_optional_mapping_string(session, "notes"),
            acquired_samples_per_channel=_as_int(
                counts.get("acquired_samples_per_channel", report.readable_common_sample_count),
                "acquired_samples_per_channel",
            ),
            persisted_samples_per_channel=_as_int(
                counts.get("persisted_samples_per_channel", report.readable_common_sample_count),
                "persisted_samples_per_channel",
            ),
            display_frames_dropped=_as_int(
                counts.get("display_frames_dropped", 0), "display_frames_dropped"
            ),
            acquisition_gap_count=sum(
                _as_int(counts.get(name, 0), name)
                for name in (
                    "detected_gap_count",
                    "detected_overlap_count",
                )
            ),
            persistence_loss_samples=max(
                0,
                _as_int(
                    counts.get("acquired_samples_per_channel", report.readable_common_sample_count),
                    "acquired_samples_per_channel",
                )
                - _as_int(
                    counts.get(
                        "persisted_samples_per_channel", report.readable_common_sample_count
                    ),
                    "persisted_samples_per_channel",
                ),
            ),
            manifest_available=(session_path / _MANIFEST_NAME).is_file(),
            event_log_available=(session_path / _EVENTS_NAME).is_file(),
            raw_data_available=(session_path / raw_name).is_file(),
            qualification_report_available=(session_path / "qualification-report.json").is_file(),
            experiment_mode=str(session.get("experiment_mode", "production")),
        )

    def read_session_events(
        self,
        path: Path,
        *,
        limit: int = 500,
    ) -> tuple[SessionEventRecord, ...]:
        """Read a bounded event suffix without exposing JSONL to application code."""
        if limit <= 0:
            raise ValueError("event limit must be positive")
        event_path = path.absolute() / _EVENTS_NAME
        retained: deque[SessionEventRecord] = deque(maxlen=limit)
        line_number = 0
        try:
            with event_path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    line_number += 1
                    if not line.strip():
                        continue
                    payload = _event_object(json.loads(line))
                    _validate_document(payload, _EVENT_SCHEMA)
                    details = _mapping(payload, "details")
                    sample_index = payload.get("sample_index")
                    retained.append(
                        SessionEventRecord(
                            event_sequence=_as_int(
                                payload.get("event_sequence"),
                                "event_sequence",
                            ),
                            timestamp=datetime.fromisoformat(_string(payload, "timestamp")),
                            severity=_string(payload, "severity"),
                            code=_string(payload, "code"),
                            acquisition_state=_string(payload, "acquisition_state"),
                            message=_string(payload, "message"),
                            sample_index=(
                                None
                                if sample_index is None
                                else _as_int(sample_index, "sample_index")
                            ),
                            details=details,
                        )
                    )
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValueError,
            SessionSchemaError,
        ) as cause:
            raise SessionIntegrityError(
                "The session event history could not be loaded.",
                code="DAQ.STORAGE.EVENT_LOG_CORRUPT",
                context={
                    "event_log_path": str(event_path),
                    "line_number": line_number,
                },
                remediation="Inspect the verified session diagnostics before using the event history.",
                cause=cause,
            ) from cause
        return tuple(retained)

    def recover_session(self, path: Path) -> SessionIntegrityReport:
        session_path = path.absolute()
        fast = self.inspect_session(session_path, level=VerificationLevel.FAST)
        if fast.recoverability is Recoverability.ACTIVE_WRITER:
            raise SessionOwnedError(
                "The session is still owned by an active writer.",
                code="DAQ.STORAGE.SESSION_ALREADY_OWNED",
                context={"session_path": str(session_path)},
                remediation="Stop the writer before attempting recovery.",
            )
        manifest = _read_manifest(session_path)
        raw_name = _raw_filename(manifest)
        session = _mapping(manifest, "session")
        original_state = SessionCompletionState(_string(session, "completion_state"))
        if original_state not in {
            SessionCompletionState.INITIALIZING,
            SessionCompletionState.RECORDING,
            SessionCompletionState.FINALIZING,
        }:
            raise SessionIntegrityError(
                "Only nonterminal abandoned sessions are recovery candidates.",
                code="DAQ.STORAGE.SESSION_NOT_RECOVERABLE",
                context={"session_path": str(session_path), "state": original_state.value},
                remediation="Inspect the session integrity report; terminal sessions are never rewritten by recovery.",
            )
        if fast.readable_common_sample_count <= 0:
            raise SessionIntegrityError(
                "No complete synchronized sample prefix is available for recovery.",
                code="DAQ.STORAGE.SESSION_CORRUPT",
                context={"session_path": str(session_path)},
                remediation="Preserve the session for diagnostics; raw samples cannot be repaired or synthesized.",
            )
        if manifest.get("schema_version") == 1:
            _, _, _, TdmsFile, _ = _nptdms_types()
            with TdmsFile.open(session_path / raw_name) as tdms:
                lengths = _tdms_channel_lengths(tdms)
                if len(set(lengths.values())) > 1 and not tdms.file_status.incomplete_final_segment:
                    raise SessionIntegrityError(
                        "Unequal channel lengths are not attributable to a recognized incomplete final segment.",
                        code="DAQ.STORAGE.CHANNEL_LENGTH_MISMATCH",
                        context={"channel_lengths": lengths},
                        remediation="Preserve the source record; replay is refused because synchronization is uncertain.",
                    )
        original_checkpoint = dict(_mapping(manifest, "checkpoint"))
        common = fast.readable_common_sample_count
        acquisition = _mapping(manifest, "acquisition")
        ordered_ids = _string_list(acquisition, "ordered_channel_ids")
        digest = _logical_digest_from_raw(
            session_path / raw_name,
            ordered_ids,
            common,
            schema_version=_as_int(manifest["schema_version"], "schema_version"),
        )
        event_count, _, _, event_issues = _read_events(
            session_path / _EVENTS_NAME,
            allow_truncated_final=True,
            validate_schema=True,
        )
        session["completion_state"] = SessionCompletionState.INTERRUPTED.value
        session["completion_reason"] = SessionCompletionReason.PROCESS_INTERRUPTED.value
        session["acquisition_ended_at"] = self._now().astimezone(UTC).isoformat()
        counts = _mapping(manifest, "counts")
        counts["persisted_samples_per_channel"] = common
        counts["common_synchronized_samples"] = common
        counts["first_sample_index"] = 0
        counts["last_sample_index"] = common - 1
        counts["event_count"] = event_count
        integrity = _mapping(manifest, "integrity")
        integrity["logical_data_sha256"] = digest
        integrity["logical_data_sample_count"] = common
        integrity["file_checksums"] = {
            raw_name: file_sha256(session_path / raw_name),
            _EVENTS_NAME: file_sha256(session_path / _EVENTS_NAME),
            _RECIPE_NAME: file_sha256(session_path / _RECIPE_NAME),
        }
        integrity["verification_timestamp"] = self._now().astimezone(UTC).isoformat()
        manifest["recovery"] = {
            "recovered_at": self._now().astimezone(UTC).isoformat(),
            "original_state": original_state.value,
            "original_checkpoint": original_checkpoint,
            "verified_common_sample_count": common,
            "event_warnings": [issue.to_dict() for issue in event_issues],
            "raw_samples_modified": False,
        }
        _atomic_write_json(session_path / _MANIFEST_NAME, manifest, schema_name=_MANIFEST_SCHEMA)
        _unlink_if_exists(session_path / _LOCK_NAME)
        _unlink_if_exists(session_path / _RESERVE_NAME)
        report = self.inspect_session(session_path, level=VerificationLevel.FULL)
        integrity["verification_timestamp"] = report.verified_at.isoformat()
        integrity["verification_status"] = report.overall.value
        integrity["verification_issues"] = [issue.to_dict() for issue in report.issues]
        _atomic_write_json(session_path / _MANIFEST_NAME, manifest, schema_name=_MANIFEST_SCHEMA)
        return report

    def open_replay(self, path: Path, *, chunk_size: int) -> TdmsReplayStream:
        return TdmsReplayStream(path.absolute(), chunk_size=chunk_size, store=self)

    def processing_metadata(self, path: Path) -> ProcessingSourceMetadata:
        """Return verified, storage-neutral metadata needed by DAQ-M06."""
        session_path = path.absolute()
        report = self.inspect_session(session_path, level=VerificationLevel.FULL)
        if report.overall is IntegrityStatus.FAIL or report.recoverability not in {
            Recoverability.REPLAYABLE,
            Recoverability.REPLAYABLE_PREFIX,
        }:
            raise SessionNotReplayableError(
                "The session does not have a verified processing extent.",
                code="DAQ.STORAGE.SESSION_NOT_REPLAYABLE",
                context={
                    "session_path": str(session_path),
                    "integrity": report.overall.value,
                    "recoverability": report.recoverability.value,
                },
                remediation="Verify or explicitly recover the session before processing.",
            )
        manifest = _read_manifest(session_path)
        session = _mapping(manifest, "session")
        acquisition = _mapping(manifest, "acquisition")
        integrity = _mapping(manifest, "integrity")
        hardware = _mapping(manifest, "hardware")
        software = _mapping(manifest, "software")
        profile_value = hardware.get("profile")
        profile = profile_value if isinstance(profile_value, Mapping) else {}
        fallback_value = profile.get("nominal_calibration_fallback")
        fallback = _processing_nominal_fallback(fallback_value)
        snapshots = manifest.get("channels")
        if not isinstance(snapshots, list):
            snapshots = []
        channels = tuple(_processing_channel_metadata(snapshot) for snapshot in snapshots)
        ordered_ids = tuple(_string_list(acquisition, "ordered_channel_ids"))
        by_id = {channel.channel_id: channel for channel in channels}
        try:
            ordered_channels = tuple(by_id[channel_id] for channel_id in ordered_ids)
        except KeyError as cause:
            raise SessionSchemaError(
                "Session channel metadata is incomplete for scientific processing.",
                code="DAQ.STORAGE.SCHEMA_INVALID",
                context={"session_path": str(session_path), "channel_id": str(cause.args[0])},
                remediation="Use a session recorded with complete channel snapshots.",
                cause=cause,
            ) from cause
        raw_digest = integrity.get("logical_data_sha256")
        if not isinstance(raw_digest, str) or len(raw_digest) != 64:
            raise SessionIntegrityError(
                "The verified session has no valid logical raw-data digest.",
                code="DAQ.STORAGE.CHECKSUM_MISSING",
                context={"session_path": str(session_path)},
                remediation="Run full session verification before processing.",
            )
        return ProcessingSourceMetadata(
            session_path=session_path,
            session_id=str(report.session_id),
            completion_state=report.effective_state.value,
            raw_data_digest=raw_digest,
            manifest_path=session_path / _MANIFEST_NAME,
            manifest_sha256=file_sha256(session_path / _MANIFEST_NAME),
            source_schema_version=_as_int(manifest["schema_version"], "schema_version"),
            source_application_version=str(software["application_version"]),
            readable_sample_count=report.readable_common_sample_count,
            sample_rate_hz=_as_float(acquisition["actual_sample_rate_hz"], "actual_sample_rate_hz"),
            ordered_channel_ids=ordered_ids,
            channels=ordered_channels,
            nominal_calibration_fallback=fallback,
            session_type=str(session.get("session_type", SessionType.EXPERIMENT.value)),
        )


class TdmsSessionWriter:
    """Single-owner append-only writer for one session."""

    def __init__(
        self,
        initialization: SessionInitialization,
        policy: RecordingPolicy,
        *,
        capacity: StorageCapacityPort,
        now: Callable[[], datetime],
        monotonic: Callable[[], float],
        store: TdmsSessionStore,
        hdf5_fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self._initialization = initialization
        self._plan = initialization.plan
        self._policy = policy
        self._capacity = capacity
        self._now = now
        self._monotonic = monotonic
        self._store = store
        self._hdf5_fault_injector = hdf5_fault_injector
        self._lock = RLock()
        self._state = SessionCompletionState.INITIALIZING
        self._persisted = 0
        self._next_sequence = 0
        self._checkpoint_sequence = 0
        self._event_sequence = 0
        self._warning_count = 0
        self._fault_count = 0
        self._storage_warning_count = 0
        self._storage_fault_count = 0
        self._last_capacity_check = self._monotonic()
        self._last_checkpoint = self._last_capacity_check
        self._capacity_warning_emitted = False
        self._digest = LogicalDataDigest()
        self._closed = False
        self._terminal = False
        self._failed = False
        self._raw_writer: _Hdf5RawWriter | None = None
        self._event_stream: IO[str] | None = None
        self._manifest = _initial_manifest(initialization)
        self._initialize()

    @property
    def plan(self) -> SessionPlan:
        return self._plan

    @property
    def state(self) -> SessionCompletionState:
        with self._lock:
            return self._state

    @property
    def persisted_samples_per_channel(self) -> int:
        with self._lock:
            return self._persisted

    @property
    def checkpoint_sequence(self) -> int:
        with self._lock:
            return self._checkpoint_sequence

    def _initialize(self) -> None:
        path = self._plan.session_path
        try:
            path.mkdir(parents=False, exist_ok=False)
        except FileExistsError as cause:
            raise SessionPathCollisionError(
                "The planned session directory already exists.",
                code="DAQ.STORAGE.SESSION_PATH_COLLISION",
                context={"session_path": str(path)},
                remediation="Create a new session preview; existing directories are never overwritten.",
                cause=cause,
            ) from cause
        except OSError as cause:
            raise SessionWriteError(
                "The session directory could not be created.",
                code="DAQ.STORAGE.SESSION_CREATE_FAILED",
                context={"session_path": str(path), "os_error": cause.errno},
                remediation="Check parent permissions and available storage.",
                cause=cause,
            ) from cause
        try:
            for directory in ("processed", "plots", "exports"):
                (path / directory).mkdir(exist_ok=False)
            _write_json_exclusive(
                path / _LOCK_NAME,
                {
                    "session_id": self._plan.session_id,
                    "host": socket.gethostname(),
                    "process_id": os.getpid(),
                    "created_at": self._now().astimezone(UTC).isoformat(),
                },
            )
            _create_reserve(path / _RESERVE_NAME, self._policy.metadata_reserve_bytes)
            recipe_payload = cast("dict[str, object]", to_primitive(self._initialization.recipe))
            _validate_document(recipe_payload, _RECIPE_SCHEMA)
            _write_json_exclusive(path / _RECIPE_NAME, recipe_payload)
            _atomic_write_json(path / _MANIFEST_NAME, self._manifest, schema_name=_MANIFEST_SCHEMA)
            attachment = self._initialization.qualification_attachment
            if attachment is not None:
                with (path / "qualification-report.json").open("xb") as stream:
                    stream.write(attachment.report_json)
                    stream.flush()
                    os.fsync(stream.fileno())
            self._event_stream = (path / _EVENTS_NAME).open("x", encoding="utf-8", newline="\n")
            self._raw_writer = _Hdf5RawWriter(
                path / _RAW_NAME,
                self._initialization,
                self._hdf5_fault_injector,
            )
            self._sync_raw()
            self._state = SessionCompletionState.RECORDING
            _mapping(self._manifest, "session")["completion_state"] = self._state.value
            self._checkpoint_manifest(force=True)
        except BaseException as cause:
            self._best_effort_initialization_fault(cause)
            if isinstance(cause, (SessionPathCollisionError, SessionWriteError)):
                raise
            raise SessionWriteError(
                "Session initialization failed after exclusive directory creation.",
                code="DAQ.STORAGE.SESSION_INITIALIZATION_FAILED",
                context={"session_path": str(path), "exception_type": type(cause).__name__},
                remediation="Inspect the retained incomplete session and storage permissions.",
                cause=cause,
            ) from cause

    def append_chunk(self, chunk: AcquisitionChunk) -> PersistenceReceipt:
        with self._lock:
            self._require_recording("append_chunk")
            self._check_capacity_if_due()
            self._validate_chunk(chunk)
            assert self._raw_writer is not None
            try:
                self._raw_writer.append_chunk(chunk)
            except OSError as cause:
                self._failed = True
                if cause.errno == errno.ENOSPC:
                    self._release_metadata_reserve()
                    self._storage_fault_count += 1
                    self._append_storage_event_best_effort(
                        "DAQ.STORAGE.DISK_FULL",
                        "error",
                        "The raw HDF5 append exhausted available disk space.",
                        {"attempted_sequence": chunk.sequence_number},
                        chunk.start_sample_index,
                    )
                    raise SessionWriteError(
                        "The raw HDF5 append exhausted available disk space.",
                        code="DAQ.STORAGE.DISK_FULL",
                        context={
                            "session_path": str(self._plan.session_path),
                            "attempted_sequence": chunk.sequence_number,
                            "persisted_samples_per_channel": self._persisted,
                        },
                        remediation="Free disk space and recover the retained partial session.",
                        cause=cause,
                    ) from cause
                self._storage_fault_count += 1
                raise SessionWriteError(
                    "The synchronized HDF5 chunk could not be appended.",
                    code="DAQ.STORAGE.HDF5_APPEND_FAILED",
                    context={
                        "attempted_sequence": chunk.sequence_number,
                        "persisted_samples_per_channel": self._persisted,
                        "os_error": cause.errno,
                    },
                    remediation="Stop acquisition and inspect the retained partial session.",
                    cause=cause,
                ) from cause
            except Exception as cause:
                self._failed = True
                self._storage_fault_count += 1
                raise SessionWriteError(
                    "The synchronized HDF5 chunk could not be appended.",
                    code="DAQ.STORAGE.HDF5_APPEND_FAILED",
                    context={
                        "attempted_sequence": chunk.sequence_number,
                        "persisted_samples_per_channel": self._persisted,
                        "exception_type": type(cause).__name__,
                    },
                    remediation="Stop acquisition and inspect the retained partial session.",
                    cause=cause,
                ) from cause
            self._digest.update(chunk.samples)
            self._persisted += chunk.sample_count
            self._next_sequence += 1
            counts = _mapping(self._manifest, "counts")
            counts["persisted_samples_per_channel"] = self._persisted
            counts["common_synchronized_samples"] = self._persisted
            counts["first_sample_index"] = 0
            counts["last_sample_index"] = self._persisted - 1
            completed_at = self._now().astimezone(UTC)
            return PersistenceReceipt(
                session_id=self._plan.session_id,
                first_sample_index=chunk.start_sample_index,
                last_sample_index=chunk.start_sample_index + chunk.sample_count - 1,
                accepted_sample_count=chunk.sample_count,
                total_persisted_sample_count=self._persisted,
                checkpoint_sequence=self._checkpoint_sequence,
                completed_at=completed_at,
            )

    def append_event(self, event: AcquisitionEvent) -> None:
        with self._lock:
            if self._terminal or self._closed:
                raise SessionWriteError(
                    "The session event log is closed.",
                    code="DAQ.STORAGE.EVENT_LOG_CLOSED",
                    context={"session_id": self._plan.session_id},
                    remediation="Do not append events after session finalization.",
                )
            if self._failed:
                raise SessionWriteError(
                    "The session writer has already encountered a storage fault.",
                    code="DAQ.STORAGE.WRITER_FAULTED",
                    context={"operation": "append_event"},
                    remediation="Finalize the session without accepting additional events.",
                )
            if event.session_id != self._plan.session_id:
                raise SessionWriteError(
                    "The event session ID does not match the active writer.",
                    code="DAQ.STORAGE.SESSION_ID_MISMATCH",
                    context={"expected": self._plan.session_id, "received": event.session_id},
                    remediation="Route each event to the writer that owns its session.",
                )
            payload = {
                "schema_version": _EVENT_SCHEMA_VERSION,
                "event_sequence": self._event_sequence,
                "source_sequence": event.event_sequence,
                "session_id": event.session_id,
                "timestamp": event.timestamp.astimezone(UTC).isoformat(),
                "monotonic_timestamp": event.monotonic_timestamp,
                "source": "acquisition_engine",
                "severity": event.severity.value,
                "code": event.code,
                "acquisition_state": event.state.value,
                "message": event.message,
                "sample_index": event.sample_index,
                "details": to_primitive(event.context),
            }
            self._append_event_payload(payload)
            if event.severity.value == "warning":
                self._warning_count += 1
            elif event.severity.value in {"error", "critical"}:
                self._fault_count += 1
            session = _mapping(self._manifest, "session")
            if event.code == "DAQ.ACQUISITION.STARTED":
                session["acquisition_started_at"] = event.timestamp.astimezone(UTC).isoformat()
            if event.code in {"DAQ.ACQUISITION.COMPLETED", "DAQ.ACQUISITION.FAULTED"}:
                self._flush_events()

    def checkpoint(self, status: Any) -> None:
        with self._lock:
            self._require_recording("checkpoint", allow_failed=True)
            now_monotonic = self._monotonic()
            if now_monotonic - self._last_checkpoint < self._policy.checkpoint_interval_seconds:
                return
            counts = _mapping(self._manifest, "counts")
            counts["acquired_samples_per_channel"] = int(status.acquired_samples_per_channel)
            counts["recording_accepted_samples_per_channel"] = int(
                status.recording_samples_enqueued
            )
            counts["display_frames_dropped"] = int(status.display_frames_dropped)
            self._checkpoint_manifest(force=True)

    def finalize(self, finalization: SessionFinalization) -> RecordedSessionCompletion:
        with self._lock:
            if self._terminal:
                raise SessionWriteError(
                    "The session has already been finalized.",
                    code="DAQ.STORAGE.SESSION_ALREADY_FINALIZED",
                    context={"session_id": self._plan.session_id},
                    remediation="Reuse the existing recorded-session completion.",
                )
            acquisition = finalization.acquisition
            segment = finalization.recording_segment
            completion = acquisition or segment
            assert completion is not None
            self._state = SessionCompletionState.FINALIZING
            session = _mapping(self._manifest, "session")
            session["completion_state"] = self._state.value
            session["acquisition_started_at"] = (
                completion.started_at.astimezone(UTC).isoformat()
                if completion.started_at is not None
                else session.get("acquisition_started_at")
            )
            session["acquisition_ended_at"] = completion.stopped_at.astimezone(UTC).isoformat()
            counts = _mapping(self._manifest, "counts")
            counts["acquired_samples_per_channel"] = completion.total_acquired_samples_per_channel
            counts["recording_accepted_samples_per_channel"] = (
                completion.total_recording_samples_accepted
            )
            counts["persisted_samples_per_channel"] = self._persisted
            counts["common_synchronized_samples"] = self._persisted
            counts["display_frames_dropped"] = (
                acquisition.display_frames_dropped if acquisition is not None else 0
            )
            if segment is not None and "open_acquisition" in self._manifest:
                provenance = _mapping(self._manifest, "open_acquisition")
                provenance["source_stop_sample_index"] = segment.source_stop_sample_index
                provenance["stop_requested_at"] = (
                    (segment.stop_requested_at or segment.stopped_at).astimezone(UTC).isoformat()
                )
            self._checkpoint_manifest(force=True)
            if finalization.storage_fault is None:
                self._write_final_raw_metadata(completion, segment=segment is not None)
            self._sync_raw()
            self._flush_events()
            self._close_streams(sync=False)
            lengths, incomplete = _closed_hdf5_lengths(
                self._plan.session_path / _RAW_NAME,
                tuple(self._initialization.daq_configuration.ordered_logical_channels),
            )
            common = min(lengths.values(), default=0)
            integrity_fault: SessionFaultSummary | None = None
            if len(set(lengths.values())) > 1 or common != self._persisted or incomplete:
                integrity_fault = SessionFaultSummary(
                    code="DAQ.STORAGE.CHANNEL_LENGTH_MISMATCH",
                    message="Final HDF5 extent does not match acknowledged persistence.",
                    context={
                        "channel_lengths": lengths,
                        "persisted_samples_per_channel": self._persisted,
                        "uncommitted_tail": incomplete,
                    },
                )
            terminal_fault = finalization.storage_fault or integrity_fault
            if segment is None:
                assert acquisition is not None
                state, reason = _map_completion(acquisition, terminal_fault)
            elif terminal_fault is not None:
                state, reason = _map_storage_fault(terminal_fault)
            elif segment.fault is not None:
                state = SessionCompletionState.FAULTED
                reason = SessionCompletionReason.ACQUISITION_FAULT
            else:
                state = SessionCompletionState.STOPPED
                reason = SessionCompletionReason.MANUAL_STOP
            fault = finalization.storage_fault or integrity_fault
            self._state = state
            session["completion_state"] = state.value
            session["completion_reason"] = reason.value
            session["acquired_duration_seconds"] = (
                completion.total_acquired_samples_per_channel
                / self._initialization.daq_configuration.effective_sample_rate_hz
            )
            session["persisted_duration_seconds"] = (
                self._persisted / self._initialization.daq_configuration.effective_sample_rate_hz
            )
            self._finish_manifest_counts()
            integrity = _mapping(self._manifest, "integrity")
            integrity["logical_data_sha256"] = self._digest.hexdigest()
            integrity["logical_data_sample_count"] = self._persisted
            if self._policy.file_checksums:
                integrity["file_checksums"] = _session_file_checksums(self._plan.session_path)
            integrity["finalization_timestamp"] = self._now().astimezone(UTC).isoformat()
            self._manifest["fault"] = (
                fault.to_dict()
                if fault is not None
                else (completion.fault.to_dict() if completion.fault is not None else None)
            )
            self._atomic_manifest_update("finalization")
            self._closed = True
            _unlink_if_exists(self._plan.session_path / _LOCK_NAME)
            _unlink_if_exists(self._plan.session_path / _RESERVE_NAME)
        report = self._store.inspect_session(
            self._plan.session_path,
            level=VerificationLevel.FULL,
        )
        if report.overall is IntegrityStatus.FAIL and self._state in {
            SessionCompletionState.COMPLETED,
            SessionCompletionState.STOPPED,
        }:
            with self._lock:
                self._state = SessionCompletionState.CORRUPT
                session = _mapping(self._manifest, "session")
                session["completion_state"] = self._state.value
                session["completion_reason"] = SessionCompletionReason.INTEGRITY_CHECK_FAILED.value
                self._manifest["fault"] = SessionFaultSummary(
                    code="DAQ.STORAGE.INTEGRITY_CHECK_FAILED",
                    message="Post-finalization verification failed.",
                    context={"issues": [issue.to_dict() for issue in report.issues]},
                ).to_dict()
                self._atomic_manifest_update("integrity_failure")
            report = self._store.inspect_session(
                self._plan.session_path,
                level=VerificationLevel.FULL,
            )
            reason = SessionCompletionReason.INTEGRITY_CHECK_FAILED
            fault = SessionFaultSummary(
                code="DAQ.STORAGE.INTEGRITY_CHECK_FAILED",
                message="Post-finalization verification failed.",
                context={"issues": [issue.code for issue in report.issues]},
            )
        with self._lock:
            integrity = _mapping(self._manifest, "integrity")
            integrity["verification_timestamp"] = report.verified_at.isoformat()
            integrity["verification_status"] = report.overall.value
            integrity["verification_issues"] = [issue.to_dict() for issue in report.issues]
            self._atomic_manifest_update("verification_result")
            self._terminal = True
        return RecordedSessionCompletion(
            session_path=self._plan.session_path,
            session_id=self._plan.session_id,
            state=self._state,
            reason=reason,
            acquired_samples_per_channel=completion.total_acquired_samples_per_channel,
            persisted_samples_per_channel=self._persisted,
            fault=fault,
            integrity=report,
            acquisition_completion=acquisition,
            recording_segment_completion=segment,
        )

    def abort(self, fault: SessionFaultSummary) -> RecordedSessionCompletion:
        with self._lock:
            if self._terminal:
                raise SessionWriteError(
                    "The session has already been finalized.",
                    code="DAQ.STORAGE.SESSION_ALREADY_FINALIZED",
                    context={"session_id": self._plan.session_id},
                )
            self._failed = True
            self._state, reason = _map_storage_fault(fault)
            session = _mapping(self._manifest, "session")
            session["completion_state"] = self._state.value
            session["completion_reason"] = reason.value
            session["acquisition_ended_at"] = self._now().astimezone(UTC).isoformat()
            self._manifest["fault"] = fault.to_dict()
            self._finish_manifest_counts()
            self._close_streams(sync=True)
            integrity = _mapping(self._manifest, "integrity")
            integrity["logical_data_sha256"] = self._digest.hexdigest()
            integrity["logical_data_sample_count"] = self._persisted
            if all(
                (self._plan.session_path / name).exists()
                for name in (_RAW_NAME, _EVENTS_NAME, _RECIPE_NAME)
            ):
                integrity["file_checksums"] = _session_file_checksums(self._plan.session_path)
            self._atomic_manifest_update("abort")
            self._terminal = True
            self._closed = True
            _unlink_if_exists(self._plan.session_path / _LOCK_NAME)
            _unlink_if_exists(self._plan.session_path / _RESERVE_NAME)
        report = self._store.inspect_session(self._plan.session_path, level=VerificationLevel.FULL)
        with self._lock:
            integrity = _mapping(self._manifest, "integrity")
            integrity["verification_timestamp"] = report.verified_at.isoformat()
            integrity["verification_status"] = report.overall.value
            integrity["verification_issues"] = [issue.to_dict() for issue in report.issues]
            self._atomic_manifest_update("abort_verification_result")
        return RecordedSessionCompletion(
            session_path=self._plan.session_path,
            session_id=self._plan.session_id,
            state=self._state,
            reason=reason,
            acquired_samples_per_channel=_as_int(
                _mapping(self._manifest, "counts").get("acquired_samples_per_channel", 0),
                "acquired_samples_per_channel",
            ),
            persisted_samples_per_channel=self._persisted,
            fault=fault,
            integrity=report,
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._close_streams(sync=True)
            self._closed = True

    def _validate_chunk(self, chunk: AcquisitionChunk) -> None:
        configuration = self._initialization.daq_configuration
        if chunk.session_id != self._plan.session_id:
            self._raise_continuity(
                "DAQ.STORAGE.SESSION_ID_MISMATCH",
                "The chunk session ID does not match the active session.",
                {"expected": self._plan.session_id, "received": chunk.session_id},
            )
        if chunk.sequence_number != self._next_sequence:
            condition = (
                "DUPLICATE_CHUNK"
                if chunk.sequence_number < self._next_sequence
                else "SAMPLE_INDEX_GAP"
            )
            self._raise_continuity(
                f"DAQ.STORAGE.{condition}",
                "The chunk sequence is not the next expected sequence.",
                {"expected": self._next_sequence, "received": chunk.sequence_number},
            )
        if chunk.start_sample_index != self._persisted:
            condition = (
                "SAMPLE_INDEX_OVERLAP"
                if chunk.start_sample_index < self._persisted
                else "SAMPLE_INDEX_GAP"
            )
            self._raise_continuity(
                f"DAQ.STORAGE.{condition}",
                "The chunk does not begin at the next persisted sample index.",
                {"expected": self._persisted, "received": chunk.start_sample_index},
            )
        if chunk.ordered_channel_ids != configuration.ordered_logical_channels:
            self._raise_continuity(
                "DAQ.STORAGE.CHANNEL_ORDER_MISMATCH",
                "The chunk channel order does not match the session configuration.",
                {
                    "expected": list(configuration.ordered_logical_channels),
                    "received": list(chunk.ordered_channel_ids),
                },
            )
        if chunk.sample_rate_hz != configuration.effective_sample_rate_hz:
            self._raise_continuity(
                "DAQ.STORAGE.SAMPLE_RATE_CHANGED",
                "The chunk sample rate changed during the session.",
                {
                    "expected": configuration.effective_sample_rate_hz,
                    "received": chunk.sample_rate_hz,
                },
            )

    def _raise_continuity(self, code: str, message: str, context: Mapping[str, object]) -> None:
        self._failed = True
        self._storage_fault_count += 1
        raise SessionWriteError(
            message,
            code=code,
            context=context,
            remediation="Stop acquisition and retain the verified synchronized prefix.",
        )

    def _check_capacity_if_due(self) -> None:
        current = self._monotonic()
        if current - self._last_capacity_check < self._policy.capacity_check_interval_seconds:
            return
        self._last_capacity_check = current
        free = self._capacity.free_bytes(self._plan.session_path)
        if free <= self._policy.hard_stop_free_bytes:
            self._failed = True
            self._storage_fault_count += 1
            self._append_storage_event_best_effort(
                "DAQ.STORAGE.RUNTIME_SPACE_THRESHOLD",
                "error",
                "The storage hard-stop threshold was reached.",
                {"free_bytes": free, "hard_stop_free_bytes": self._policy.hard_stop_free_bytes},
                self._persisted,
            )
            raise SessionWriteError(
                "The storage hard-stop threshold was reached.",
                code="DAQ.STORAGE.RUNTIME_SPACE_THRESHOLD",
                context={
                    "free_bytes": free,
                    "hard_stop_free_bytes": self._policy.hard_stop_free_bytes,
                    "persisted_samples_per_channel": self._persisted,
                },
                remediation="Stop acquisition and free disk space before starting another run.",
            )
        if free <= self._policy.warning_free_bytes and not self._capacity_warning_emitted:
            self._capacity_warning_emitted = True
            self._storage_warning_count += 1
            self._append_storage_event_best_effort(
                "DAQ.STORAGE.SPACE_LOW",
                "warning",
                "Available storage fell below the warning threshold.",
                {"free_bytes": free, "warning_free_bytes": self._policy.warning_free_bytes},
                self._persisted,
            )

    def _append_event_payload(self, payload: Mapping[str, object]) -> None:
        _validate_document(payload, _EVENT_SCHEMA)
        assert self._event_stream is not None
        try:
            line = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
            self._event_stream.write(line + "\n")
        except (OSError, TypeError, ValueError) as cause:
            self._failed = True
            self._storage_fault_count += 1
            raise SessionWriteError(
                "The structured session event could not be appended.",
                code="DAQ.STORAGE.EVENT_APPEND_FAILED",
                context={"event_sequence": self._event_sequence},
                remediation="Stop acquisition and retain the recorded synchronized prefix.",
                cause=cause,
            ) from cause
        self._event_sequence += 1

    def _append_storage_event_best_effort(
        self,
        code: str,
        severity: str,
        message: str,
        details: Mapping[str, object],
        sample_index: int | None,
    ) -> None:
        if self._event_stream is None:
            return
        payload = {
            "schema_version": _EVENT_SCHEMA_VERSION,
            "event_sequence": self._event_sequence,
            "source_sequence": self._event_sequence,
            "session_id": self._plan.session_id,
            "timestamp": self._now().astimezone(UTC).isoformat(),
            "monotonic_timestamp": max(0.0, self._monotonic()),
            "source": "session_writer",
            "severity": severity,
            "code": code,
            "acquisition_state": self._state.value,
            "message": message,
            "sample_index": sample_index,
            "details": dict(details),
        }
        try:
            self._append_event_payload(payload)
        except SessionWriteError:
            return

    def _checkpoint_manifest(self, *, force: bool) -> None:
        current = self._monotonic()
        if not force and current - self._last_checkpoint < self._policy.checkpoint_interval_seconds:
            return
        self._sync_raw()
        self._flush_events()
        self._checkpoint_sequence += 1
        checkpoint = _mapping(self._manifest, "checkpoint")
        checkpoint["sequence"] = self._checkpoint_sequence
        checkpoint["timestamp"] = self._now().astimezone(UTC).isoformat()
        checkpoint["persisted_samples_per_channel"] = self._persisted
        integrity = _mapping(self._manifest, "integrity")
        integrity["logical_data_sha256"] = self._digest.hexdigest()
        integrity["logical_data_sample_count"] = self._persisted
        self._finish_manifest_counts()
        self._atomic_manifest_update("checkpoint")
        self._last_checkpoint = current

    def _atomic_manifest_update(self, phase: str) -> None:
        try:
            _atomic_write_json(
                self._plan.session_path / _MANIFEST_NAME,
                self._manifest,
                schema_name=_MANIFEST_SCHEMA,
            )
        except (OSError, SessionSchemaError) as cause:
            self._failed = True
            self._storage_fault_count += 1
            if isinstance(cause, OSError) and cause.errno == errno.ENOSPC:
                self._release_metadata_reserve()
            raise SessionWriteError(
                "The atomic manifest update failed.",
                code="DAQ.STORAGE.MANIFEST_UPDATE_FAILED",
                context={
                    "checkpoint_sequence": self._checkpoint_sequence,
                    "phase": phase,
                },
                remediation="Stop acquisition and retain the previous durable manifest checkpoint.",
                cause=cause,
            ) from cause

    def _finish_manifest_counts(self) -> None:
        counts = _mapping(self._manifest, "counts")
        counts["event_count"] = self._event_sequence
        counts["warning_count"] = self._warning_count + self._storage_warning_count
        counts["fault_count"] = self._fault_count + self._storage_fault_count
        counts["storage_warning_count"] = self._storage_warning_count
        counts["storage_fault_count"] = self._storage_fault_count
        counts["persisted_samples_per_channel"] = self._persisted
        counts["common_synchronized_samples"] = self._persisted
        counts["first_sample_index"] = 0 if self._persisted else None
        counts["last_sample_index"] = self._persisted - 1 if self._persisted else None

    def _write_final_raw_metadata(
        self,
        completion: AcquisitionCompletion | RecordingSegmentCompletion,
        *,
        segment: bool = False,
    ) -> None:
        if self._raw_writer is None:
            return
        try:
            if segment:
                state = (
                    SessionCompletionState.FAULTED
                    if completion.fault is not None
                    else SessionCompletionState.STOPPED
                )
                reason = (
                    SessionCompletionReason.ACQUISITION_FAULT
                    if completion.fault is not None
                    else SessionCompletionReason.MANUAL_STOP
                )
            else:
                state, reason = _map_completion(cast("AcquisitionCompletion", completion), None)
            self._raw_writer.append_finalization(
                state,
                reason,
                completion.total_acquired_samples_per_channel,
                self._persisted,
            )
        except Exception as cause:
            self._failed = True
            self._storage_fault_count += 1
            raise SessionWriteError(
                "Final HDF5 journal metadata could not be written.",
                code="DAQ.STORAGE.HDF5_FINALIZATION_FAILED",
                context={"persisted_samples_per_channel": self._persisted},
                remediation="Retain and verify the partial session.",
                cause=cause,
            ) from cause

    def _sync_raw(self) -> None:
        if self._raw_writer is None:
            return
        try:
            self._raw_writer.sync()
        except Exception as cause:
            self._failed = True
            self._storage_fault_count += 1
            raise SessionWriteError(
                "The HDF5 container could not be durably synchronized.",
                code="DAQ.STORAGE.HDF5_FLUSH_FAILED",
                context={"session_path": str(self._plan.session_path)},
                remediation="Stop acquisition and retain the last durable manifest checkpoint.",
                cause=cause,
            ) from cause

    def _flush_events(self) -> None:
        if self._event_stream is None or self._event_stream.closed:
            return
        self._event_stream.flush()
        os.fsync(self._event_stream.fileno())

    def _close_streams(self, *, sync: bool) -> None:
        if sync:
            try:
                self._sync_raw()
            except (OSError, SessionWriteError):
                pass
            try:
                self._flush_events()
            except OSError:
                pass
        if self._raw_writer is not None:
            self._raw_writer.close()
            self._raw_writer = None
        if self._event_stream is not None and not self._event_stream.closed:
            self._event_stream.close()

    def _release_metadata_reserve(self) -> None:
        _unlink_if_exists(self._plan.session_path / _RESERVE_NAME)

    def _require_recording(self, operation: str, *, allow_failed: bool = False) -> None:
        if self._closed or self._terminal or self._state is not SessionCompletionState.RECORDING:
            raise SessionWriteError(
                f"Session writer operation {operation} is unavailable in state {self._state.value}.",
                code="DAQ.STORAGE.WRITER_STATE_INVALID",
                context={"operation": operation, "state": self._state.value},
                remediation="Use the session writer only during its recording lifecycle.",
            )
        if self._failed and not allow_failed:
            raise SessionWriteError(
                "The session writer has already encountered a storage fault.",
                code="DAQ.STORAGE.WRITER_FAULTED",
                context={"operation": operation},
                remediation="Finalize the session as faulted without accepting additional chunks.",
            )

    def _best_effort_initialization_fault(self, cause: BaseException) -> None:
        self._close_streams(sync=False)
        path = self._plan.session_path
        if not path.exists():
            return
        try:
            session = _mapping(self._manifest, "session")
            session["completion_state"] = SessionCompletionState.FAULTED.value
            session["completion_reason"] = SessionCompletionReason.STORAGE_WRITE_FAILED.value
            session["acquisition_ended_at"] = self._now().astimezone(UTC).isoformat()
            self._manifest["fault"] = {
                "code": "DAQ.STORAGE.SESSION_INITIALIZATION_FAILED",
                "message": "Session initialization failed.",
                "context": {"exception_type": type(cause).__name__},
            }
            _atomic_write_json(path / _MANIFEST_NAME, self._manifest, schema_name=_MANIFEST_SCHEMA)
        except Exception:
            return


class TdmsReplayStream:
    def __init__(self, path: Path, *, chunk_size: int, store: TdmsSessionStore) -> None:
        if isinstance(chunk_size, bool) or chunk_size <= 0:
            raise ValueError("chunk_size must be a positive integer")
        report = store.inspect_session(path, level=VerificationLevel.FULL)
        if report.overall is IntegrityStatus.FAIL or report.recoverability not in {
            Recoverability.REPLAYABLE,
            Recoverability.REPLAYABLE_PREFIX,
        }:
            raise SessionNotReplayableError(
                "The session does not have a verified synchronized replay extent.",
                code="DAQ.STORAGE.SESSION_NOT_REPLAYABLE",
                context={
                    "session_path": str(path),
                    "integrity": report.overall.value,
                    "recoverability": report.recoverability.value,
                },
                remediation="Run session verification or explicit recovery before replay.",
            )
        manifest = _read_manifest(path)
        acquisition = _mapping(manifest, "acquisition")
        self._path = path
        self._session_id = str(report.session_id)
        self._channel_ids = tuple(_string_list(acquisition, "ordered_channel_ids"))
        self._channel_units = tuple(_string_list(acquisition, "ordered_channel_units"))
        self._sample_rate = _as_float(acquisition["actual_sample_rate_hz"], "actual_sample_rate_hz")
        self._sample_count = report.readable_common_sample_count
        self._schema_version = _as_int(manifest["schema_version"], "schema_version")
        self._raw_name = _raw_filename(manifest)
        self._chunk_size = chunk_size
        self._state = ReplayState.READY
        self._cancelled = Event()
        self._source: Any | None = None
        self._iteration_started = False

    @property
    def state(self) -> ReplayState:
        return self._state

    def __iter__(self) -> Iterator[AcquisitionChunk]:
        if self._iteration_started:
            raise RuntimeError("Replay streams are single-use")
        self._iteration_started = True
        self._state = ReplayState.REPLAYING
        sequence = 0
        try:
            if self._schema_version == 2:
                import h5py

                self._source = h5py.File(
                    self._path / self._raw_name,
                    "r",
                    libver="latest",
                    swmr=True,
                )
            else:
                _, _, _, TdmsFile, _ = _nptdms_types()
                self._source = TdmsFile.open(self._path / self._raw_name)
            for start in range(0, self._sample_count, self._chunk_size):
                if self._cancelled.is_set():
                    self._state = ReplayState.CANCELLED
                    return
                stop = min(self._sample_count, start + self._chunk_size)
                if self._schema_version == 2:
                    samples = np.asarray(
                        self._source["/raw/samples"][:, start:stop],
                        dtype=np.float64,
                    )
                else:
                    samples = np.empty((len(self._channel_ids), stop - start), dtype=np.float64)
                    group = self._source[_RAW_GROUP]
                    for index, channel_id in enumerate(self._channel_ids):
                        samples[index] = group[channel_id][start:stop]
                samples.flags.writeable = False
                yield AcquisitionChunk(
                    session_id=self._session_id,
                    sequence_number=sequence,
                    start_sample_index=start,
                    sample_count=stop - start,
                    sample_rate_hz=self._sample_rate,
                    ordered_channel_ids=self._channel_ids,
                    ordered_channel_units=self._channel_units,
                    samples=samples,
                    read_completed_monotonic=stop / self._sample_rate,
                )
                sequence += 1
            self._state = ReplayState.COMPLETED
        finally:
            if self._source is not None:
                self._source.close()
                self._source = None

    def cancel(self) -> None:
        self._cancelled.set()
        if self._state in {ReplayState.READY, ReplayState.REPLAYING}:
            self._state = ReplayState.CANCELLED

    def close(self) -> None:
        self._cancelled.set()
        if self._source is not None:
            self._source.close()
            self._source = None
        self._state = ReplayState.CLOSED


def _initial_manifest(initialization: SessionInitialization) -> dict[str, object]:
    plan = initialization.plan
    experiment = initialization.experiment
    configuration = initialization.daq_configuration
    ordered_channels = {channel.channel_id: channel for channel in experiment.channels}
    ordered_units = [
        ordered_channels[channel_id].raw_unit.value
        for channel_id in configuration.ordered_logical_channels
    ]
    return {
        "schema_version": _SESSION_SCHEMA_VERSION,
        "raw_container": {
            "format": "hdf5",
            "filename": _RAW_NAME,
            "layout_version": _HDF5_LAYOUT_VERSION,
            "dataset_path": "/raw/samples",
            "dtype": "float64-le",
            "axis_order": ["channel", "sample_index"],
            "chunk_shape": [
                len(configuration.ordered_logical_channels),
                experiment.acquisition.chunk_config.samples_per_channel,
            ],
            "compression": "none",
            "chunk_checksum": "fletcher32",
            "swmr": True,
        },
        "event_schema_version": _EVENT_SCHEMA_VERSION,
        "logical_digest_version": LOGICAL_DIGEST_VERSION,
        "session": {
            "session_id": plan.session_id,
            "session_type": initialization.session_type.value,
            "experiment_mode": experiment.experiment_mode.value,
            "experiment_name": plan.experiment_name,
            "experiment_slug": plan.experiment_slug,
            "description": experiment.session.description,
            "operator": experiment.session.operator,
            "sample_id": experiment.session.sample_id,
            "notes": experiment.session.notes,
            "parent_path": str(plan.parent_directory),
            "created_path": str(plan.session_path),
            "created_at": plan.created_at.astimezone(UTC).isoformat(),
            "timezone": "UTC",
            "path_warnings": list(plan.warnings),
            "requested_duration_seconds": experiment.acquisition.requested_duration_seconds,
            "acquisition_started_at": None,
            "acquisition_ended_at": None,
            "acquired_duration_seconds": 0.0,
            "persisted_duration_seconds": 0.0,
            "completion_state": SessionCompletionState.INITIALIZING.value,
            "completion_reason": None,
        },
        "software": {
            "application_name": "LabDAQ",
            "application_version": initialization.application_version,
            "python_version": platform.python_version(),
            "operating_system": platform.system(),
            "operating_system_version": platform.version(),
            "host_name": socket.gethostname(),
            "python_implementation": platform.python_implementation(),
        },
        "hardware": {
            "profile": to_primitive(initialization.profile),
            "discovery": to_primitive(initialization.discovery),
            "configuration": to_primitive(configuration),
        },
        "acquisition": {
            "requested_sample_rate_hz": configuration.requested_sample_rate_hz,
            "actual_sample_rate_hz": configuration.effective_sample_rate_hz,
            "sampling_mode": configuration.sampling_mode,
            "requested_duration_seconds": experiment.acquisition.requested_duration_seconds,
            "acquisition_chunk_size": experiment.acquisition.chunk_config.samples_per_channel,
            "ordered_channel_ids": list(configuration.ordered_logical_channels),
            "ordered_channel_units": ordered_units,
            "ordered_physical_channels": list(configuration.ordered_physical_channels),
            "requested_input_ranges_v": [
                list(value) for value in configuration.requested_input_ranges_v
            ],
            "effective_input_ranges_v": [
                list(value) for value in configuration.effective_input_ranges_v
            ],
            "trigger": to_primitive(experiment.acquisition.trigger),
            "sample_dtype": "float64",
            "source_classification": "unconditioned_voltage",
        },
        "channels": _channel_snapshots(initialization),
        "processing_recipe": {
            "path": _RECIPE_NAME,
            "schema_version": initialization.recipe.schema_version,
            "recipe_id": initialization.recipe.recipe_id,
            "recipe_version": initialization.recipe.recipe_version,
            "applies_calibration": False,
            "source_units": "V",
            "output_units": "V",
            "immutable_snapshot": True,
            "derived_applies_calibration": (
                initialization.recipe.status.value == "active"
                and initialization.session_type is not SessionType.RAW_DIAGNOSTIC
            ),
            "derived_processing_allowed": (
                initialization.session_type is not SessionType.RAW_DIAGNOSTIC
            ),
            "derived_output_units": [
                channel.engineering_unit.value
                for channel in sorted(experiment.channels, key=lambda item: item.channel_order)
                if channel.enabled
            ],
            "recipe_hash": canonical_sha256(initialization.recipe),
        },
        "counts": {
            "acquired_samples_per_channel": 0,
            "recording_accepted_samples_per_channel": 0,
            "persisted_samples_per_channel": 0,
            "common_synchronized_samples": 0,
            "first_sample_index": None,
            "last_sample_index": None,
            "detected_gap_count": 0,
            "detected_overlap_count": 0,
            "display_frames_dropped": 0,
            "event_count": 0,
            "warning_count": 0,
            "fault_count": 0,
            "storage_warning_count": 0,
            "storage_fault_count": 0,
        },
        "checkpoint": {
            "sequence": 0,
            "timestamp": plan.created_at.astimezone(UTC).isoformat(),
            "persisted_samples_per_channel": 0,
        },
        "integrity": {
            "logical_data_sha256": LogicalDataDigest().hexdigest(),
            "logical_data_sample_count": 0,
            "file_checksums": {},
            "finalization_timestamp": None,
            "verification_timestamp": None,
            "verification_status": None,
            "verification_issues": [],
        },
        "fault": None,
        "recovery": None,
        "qualification": (
            initialization.qualification_attachment.manifest_summary()
            if initialization.qualification_attachment is not None
            else None
        ),
        "diagnostic_authorization": (
            thaw_mapping(initialization.diagnostic_authorization)
            if initialization.diagnostic_authorization is not None
            else None
        ),
        **(
            {"open_acquisition": initialization.open_acquisition.to_dict()}
            if initialization.open_acquisition is not None
            else {}
        ),
    }


def _channel_snapshots(initialization: SessionInitialization) -> list[dict[str, object]]:
    experiment = initialization.experiment
    configuration = initialization.daq_configuration
    channel_by_id = {channel.channel_id: channel for channel in experiment.channels}
    sensor_by_id = {sensor.sensor_id: sensor for sensor in experiment.sensors}
    calibration_by_id = {
        calibration.calibration_id: calibration for calibration in experiment.calibrations
    }
    chain_by_id = {chain.conditioning_chain_id: chain for chain in experiment.conditioning_chains}
    snapshots: list[dict[str, object]] = []
    for order, channel_id in enumerate(configuration.ordered_logical_channels):
        channel = channel_by_id[channel_id]
        sensor = sensor_by_id.get(channel.sensor_id) if channel.sensor_id is not None else None
        calibration = (
            calibration_by_id[channel.calibration_id]
            if channel.calibration_id is not None
            else None
        )
        chain = (
            chain_by_id[channel.conditioning_chain_id]
            if channel.conditioning_chain_id is not None
            else None
        )
        snapshots.append(
            {
                "logical_channel_id": channel.channel_id,
                "display_name": channel.logical_name,
                "channel_order": order,
                "physical_channel": configuration.ordered_physical_channels[order],
                "raw_quantity": channel.raw_quantity.value,
                "raw_unit": channel.raw_unit.value,
                "engineering_quantity": channel.engineering_quantity.value,
                "engineering_unit": channel.engineering_unit.value,
                "channel_role": channel.channel_role.value,
                "processing_disposition": channel.processing_disposition.value,
                "engineering_interpretation": (
                    None
                    if channel.processing_disposition is ProcessingDisposition.RAW_ONLY_DIAGNOSTIC
                    else channel.engineering_quantity.value
                ),
                "warning": (
                    (
                        "UNPOWERED / UNQUALIFIED / NOT CHARGE"
                        if sensor is not None
                        else "RAW VOLTAGE / SENSOR UNKNOWN"
                    )
                    if channel.processing_disposition is ProcessingDisposition.RAW_ONLY_DIAGNOSTIC
                    else None
                ),
                "terminal_configuration": channel.physical_channel.terminal_configuration.value,
                "requested_input_range_v": [
                    channel.requested_input_min,
                    channel.requested_input_max,
                ],
                "effective_input_range_v": list(configuration.effective_input_ranges_v[order]),
                "sensor": to_primitive(sensor) if sensor is not None else None,
                "calibration": to_primitive(calibration) if calibration is not None else None,
                "conditioning_chain": to_primitive(chain) if chain is not None else None,
            }
        )
    return snapshots


def _session_hardware_summary(
    profile: Mapping[str, object],
    configuration: Mapping[str, object],
    discovery: Mapping[str, object],
) -> SessionHardwareSummary:
    selected_device_id = _optional_mapping_string(configuration, "device_id")
    selected_module_id = _optional_mapping_string(configuration, "module_id")
    raw_devices = discovery.get("devices")
    devices = raw_devices if isinstance(raw_devices, list) else []
    device = next(
        (
            item
            for item in devices
            if isinstance(item, Mapping)
            and selected_device_id in {str(item.get("device_id")), str(item.get("alias"))}
        ),
        devices[0] if devices and isinstance(devices[0], Mapping) else None,
    )
    device_mapping = device if isinstance(device, Mapping) else {}
    raw_modules = device_mapping.get("modules")
    modules = raw_modules if isinstance(raw_modules, list) else []
    module = next(
        (
            item
            for item in modules
            if isinstance(item, Mapping)
            and selected_module_id in {str(item.get("module_id")), str(item.get("reported_model"))}
        ),
        modules[0] if modules and isinstance(modules[0], Mapping) else None,
    )
    module_mapping = module if isinstance(module, Mapping) else {}
    raw_driver = discovery.get("driver")
    driver = raw_driver if isinstance(raw_driver, Mapping) else {}
    return SessionHardwareSummary(
        profile_revision=_optional_mapping_int(profile, "profile_revision"),
        backend_id=_optional_mapping_string(discovery, "backend_id"),
        device_id=_optional_mapping_string(device_mapping, "device_id"),
        device_model=_optional_mapping_string(device_mapping, "model"),
        device_serial_number=_optional_mapping_string(device_mapping, "serial_number"),
        module_id=_optional_mapping_string(module_mapping, "module_id"),
        module_model=_optional_mapping_string(module_mapping, "model"),
        module_serial_number=_optional_mapping_string(module_mapping, "serial_number"),
        driver_version=_optional_mapping_string(driver, "driver_version"),
    )


def _session_channel_summaries(value: object) -> tuple[SessionChannelSummary, ...]:
    if not isinstance(value, list):
        return ()
    result: list[SessionChannelSummary] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            continue
        sensor_value = raw.get("sensor")
        calibration_value = raw.get("calibration")
        sensor = sensor_value if isinstance(sensor_value, Mapping) else {}
        calibration = calibration_value if isinstance(calibration_value, Mapping) else {}
        result.append(
            SessionChannelSummary(
                channel_id=str(raw.get("logical_channel_id", "unknown")),
                display_name=str(raw.get("display_name", "unknown")),
                physical_channel=str(raw.get("physical_channel", "unknown")),
                channel_role=str(raw.get("channel_role", "unknown")),
                raw_unit=str(raw.get("raw_unit", "unknown")),
                engineering_unit=str(raw.get("engineering_unit", "unknown")),
                sensor_model=_optional_mapping_string(sensor, "model"),
                sensor_serial_number=_optional_mapping_string(sensor, "serial_number"),
                calibration_id=_optional_mapping_string(calibration, "calibration_id"),
                calibration_source=_optional_mapping_string(calibration, "source_reference"),
                processing_disposition=str(raw.get("processing_disposition", "calibrated")),
                engineering_interpretation=(
                    _optional_mapping_string(raw, "engineering_interpretation")
                    if "engineering_interpretation" in raw
                    else "calibrated"
                ),
                warning=_optional_mapping_string(raw, "warning"),
            )
        )
    return tuple(result)


def _session_recipe_summary(value: object) -> SessionRecipeSummary | None:
    if not isinstance(value, Mapping):
        return None
    recipe_id = value.get("recipe_id")
    recipe_version = value.get("recipe_version")
    if not isinstance(recipe_id, str) or not isinstance(recipe_version, str):
        return None
    return SessionRecipeSummary(
        recipe_id,
        recipe_version,
        _optional_mapping_string(value, "recipe_hash"),
    )


def _session_qualification_summary(value: object) -> SessionQualificationSummary | None:
    if not isinstance(value, Mapping):
        return None
    report_id = value.get("report_id")
    fingerprint = value.get("qualification_fingerprint")
    overall = value.get("overall_status")
    eligible = value.get("eligible")
    if (
        not isinstance(report_id, str)
        or not isinstance(fingerprint, str)
        or not isinstance(overall, str)
        or not isinstance(eligible, bool)
    ):
        return None
    return SessionQualificationSummary(
        report_id,
        fingerprint,
        overall,
        eligible,
        _optional_mapping_string(value, "report_sha256"),
    )


def _processing_channel_metadata(value: object) -> SessionChannelMetadata:
    if not isinstance(value, Mapping):
        raise SessionSchemaError(
            "A session channel snapshot is invalid.",
            code="DAQ.STORAGE.SCHEMA_INVALID",
            remediation="Use an unmodified M06-compatible session manifest.",
        )
    sensor = value.get("sensor")
    calibration = value.get("calibration")
    disposition = ProcessingDisposition(
        str(value.get("processing_disposition", ProcessingDisposition.CALIBRATED.value))
    )
    if disposition is ProcessingDisposition.CALIBRATED and (
        not isinstance(sensor, Mapping) or not isinstance(calibration, Mapping)
    ):
        raise SessionSchemaError(
            "A session channel lacks processing calibration provenance.",
            code="DAQ.STORAGE.SCHEMA_INVALID",
            context={"channel_id": str(value.get("logical_channel_id", "unknown"))},
            remediation="Use a session recorded with complete M06 channel metadata.",
        )
    try:
        calibration_record = (
            CalibrationConfig(
                calibration_id=str(calibration["calibration_id"]),
                calibration_model=CalibrationModel(str(calibration["calibration_model"])),
                input_quantity=PhysicalQuantity(str(calibration["input_quantity"])),
                input_unit=Unit(str(calibration["input_unit"])),
                output_quantity=PhysicalQuantity(str(calibration["output_quantity"])),
                output_unit=Unit(str(calibration["output_unit"])),
                sensitivity_value=float(calibration["sensitivity_value"]),
                sensitivity_unit=SensitivityUnit(str(calibration["sensitivity_unit"])),
                polarity=Polarity(str(calibration["polarity"])),
                source_kind=CalibrationSourceKind(str(calibration["source_kind"])),
                source_reference=str(calibration["source_reference"]),
                effective_date=(
                    date.fromisoformat(str(calibration["effective_date"]))
                    if calibration.get("effective_date") is not None
                    else None
                ),
                expiration_date=(
                    date.fromisoformat(str(calibration["expiration_date"]))
                    if calibration.get("expiration_date") is not None
                    else None
                ),
                certificate_identifier=_optional_mapping_string(
                    calibration, "certificate_identifier"
                ),
                conditions=_optional_mapping_string(calibration, "conditions"),
                notes=_optional_mapping_string(calibration, "notes"),
                input_offset=_optional_mapping_float(calibration, "input_offset"),
                valid_input_min=_optional_mapping_float(calibration, "valid_input_min"),
                valid_input_max=_optional_mapping_float(calibration, "valid_input_max"),
                valid_output_min=_optional_mapping_float(calibration, "valid_output_min"),
                valid_output_max=_optional_mapping_float(calibration, "valid_output_max"),
                validated_frequency_min_hz=_optional_mapping_float(
                    calibration, "validated_frequency_min_hz"
                ),
                validated_frequency_max_hz=_optional_mapping_float(
                    calibration, "validated_frequency_max_hz"
                ),
                extended_frequency_min_hz=_optional_mapping_float(
                    calibration, "extended_frequency_min_hz"
                ),
                extended_frequency_max_hz=_optional_mapping_float(
                    calibration, "extended_frequency_max_hz"
                ),
                uncertainty=_optional_mapping_string(calibration, "uncertainty"),
                transcription_timestamp=(
                    datetime.fromisoformat(str(calibration["transcription_timestamp"]))
                    if calibration.get("transcription_timestamp") is not None
                    else None
                ),
                missing_provenance_fields=_mapping_string_tuple(
                    calibration, "missing_provenance_fields"
                ),
                characterization_session_ids=_mapping_string_tuple(
                    calibration, "characterization_session_ids"
                ),
                owner_approval=_optional_mapping_string(calibration, "owner_approval"),
            )
            if isinstance(calibration, Mapping)
            else None
        )
        return SessionChannelMetadata(
            channel_id=str(value["logical_channel_id"]),
            display_name=str(value["display_name"]),
            physical_channel=str(value["physical_channel"]),
            raw_unit=Unit(str(value["raw_unit"])),
            channel_role=ChannelRole(str(value["channel_role"])),
            sensor_id=_optional_mapping_string(sensor, "sensor_id")
            if isinstance(sensor, Mapping)
            else None,
            sensor_model=_optional_mapping_string(sensor, "model")
            if isinstance(sensor, Mapping)
            else None,
            sensor_serial_number=(
                _optional_mapping_string(sensor, "serial_number")
                if isinstance(sensor, Mapping)
                else None
            ),
            engineering_unit=Unit(str(value["engineering_unit"])),
            calibration=calibration_record,
            processing_disposition=disposition,
        )
    except (KeyError, TypeError, ValueError) as cause:
        raise SessionSchemaError(
            "A session channel snapshot cannot be decoded for processing.",
            code="DAQ.STORAGE.SCHEMA_INVALID",
            context={"channel_id": str(value.get("logical_channel_id", "unknown"))},
            remediation="Use an unmodified M06-compatible session manifest.",
            cause=cause,
        ) from cause


def _processing_nominal_fallback(value: object) -> NominalCalibrationFallbackPolicy | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise SessionSchemaError(
            "The stored nominal calibration fallback policy is invalid.",
            code="DAQ.STORAGE.SCHEMA_INVALID",
            remediation="Use an unmodified M06-compatible session manifest.",
        )
    try:
        roles = _processing_role_list(value.get("permitted_channel_roles"))
        return NominalCalibrationFallbackPolicy(
            sensor_model=str(value["sensor_model"]),
            sensitivity_value=float(value["sensitivity_value"]),
            sensitivity_unit=SensitivityUnit(str(value["sensitivity_unit"])),
            permitted_channel_roles=tuple(ChannelRole(role) for role in roles),
        )
    except (KeyError, TypeError, ValueError) as cause:
        raise SessionSchemaError(
            "The stored nominal calibration fallback policy cannot be decoded.",
            code="DAQ.STORAGE.SCHEMA_INVALID",
            remediation="Use an unmodified M06-compatible session manifest.",
            cause=cause,
        ) from cause


def _optional_mapping_string(value: Mapping[str, object], key: str) -> str | None:
    item = value.get(key)
    return str(item) if item is not None else None


def _mapping_string_tuple(value: Mapping[str, object], key: str) -> tuple[str, ...]:
    item = value.get(key)
    if item is None:
        return ()
    if not isinstance(item, list) or not all(isinstance(entry, str) for entry in item):
        raise TypeError(f"{key} must be a string list")
    return tuple(item)


def _optional_mapping_float(value: Mapping[str, object], key: str) -> float | None:
    item = value.get(key)
    if item is None:
        return None
    if isinstance(item, bool) or not isinstance(item, (int, float)):
        raise TypeError(f"{key} must be numeric or null")
    return float(item)


def _optional_mapping_int(value: Mapping[str, object], key: str) -> int | None:
    item = value.get(key)
    if item is None:
        return None
    if isinstance(item, bool) or not isinstance(item, int):
        raise TypeError(f"{key} must be an integer or null")
    return item


def _processing_role_list(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TypeError("permitted_channel_roles must be a string list")
    return cast("list[str]", value)


def _tdms_channel_properties(snapshot: Mapping[str, object]) -> dict[str, object]:
    sensor = snapshot.get("sensor")
    calibration = snapshot.get("calibration")
    properties: dict[str, object] = {
        "logical_channel_id": str(snapshot["logical_channel_id"]),
        "display_name": str(snapshot["display_name"]),
        "channel_order": _as_int(snapshot["channel_order"], "channel_order"),
        "physical_channel": str(snapshot["physical_channel"]),
        "raw_quantity": str(snapshot["raw_quantity"]),
        "raw_unit": str(snapshot["raw_unit"]),
        "processing_disposition": str(
            snapshot.get("processing_disposition", ProcessingDisposition.CALIBRATED.value)
        ),
        "terminal_configuration": str(snapshot["terminal_configuration"]),
        "requested_input_range_v_json": json.dumps(snapshot["requested_input_range_v"]),
        "effective_input_range_v_json": json.dumps(snapshot["effective_input_range_v"]),
    }
    if isinstance(sensor, Mapping):
        properties.update(
            {
                "sensor_type": str(sensor["sensor_type"]),
                "sensor_id": str(sensor["sensor_id"]),
                "sensor_model": str(sensor["model"]),
                "sensor_serial_number": str(sensor["serial_number"]),
            }
        )
    if isinstance(calibration, Mapping):
        properties.update(
            {
                "calibration_id": str(calibration["calibration_id"]),
                "calibration_source": str(calibration["source_reference"]),
            }
        )
        if calibration.get("effective_date") is not None:
            properties["calibration_effective_date"] = str(calibration["effective_date"])
    else:
        properties["engineering_interpretation"] = "null"
        properties["warning"] = str(snapshot.get("warning", "UNPOWERED / UNQUALIFIED / NOT CHARGE"))
    chain = snapshot.get("conditioning_chain")
    if isinstance(chain, Mapping):
        properties["conditioning_chain_id"] = str(chain["conditioning_chain_id"])
        properties["conditioning_chain_description"] = str(chain["description"])
    return properties


def _map_completion(
    completion: AcquisitionCompletion,
    storage_fault: SessionFaultSummary | None,
) -> tuple[SessionCompletionState, SessionCompletionReason]:
    if storage_fault is not None:
        return _map_storage_fault(storage_fault)
    if completion.terminal_state is AcquisitionState.FAULTED:
        fault_code = completion.fault.code if completion.fault is not None else ""
        if "READ_TIMEOUT" in fault_code:
            reason = SessionCompletionReason.READER_TIMEOUT
        elif "DISCONNECTED" in fault_code:
            reason = SessionCompletionReason.HARDWARE_DISCONNECTED
        else:
            reason = SessionCompletionReason.ACQUISITION_FAULT
        return SessionCompletionState.FAULTED, reason
    if completion.reason is CompletionReason.DURATION_REACHED:
        return SessionCompletionState.COMPLETED, SessionCompletionReason.DURATION_REACHED
    return SessionCompletionState.STOPPED, SessionCompletionReason.MANUAL_STOP


def _map_storage_fault(
    storage_fault: SessionFaultSummary,
) -> tuple[SessionCompletionState, SessionCompletionReason]:
    if storage_fault.code in {
        "DAQ.STORAGE.DISK_FULL",
        "DAQ.STORAGE.RUNTIME_SPACE_THRESHOLD",
    }:
        return SessionCompletionState.FAULTED, SessionCompletionReason.DISK_SPACE_EXHAUSTED
    if storage_fault.code == "DAQ.STORAGE.EVENT_APPEND_FAILED":
        return SessionCompletionState.FAULTED, SessionCompletionReason.EVENT_WRITE_FAILED
    if storage_fault.code == "DAQ.STORAGE.MANIFEST_UPDATE_FAILED":
        return SessionCompletionState.FAULTED, SessionCompletionReason.MANIFEST_UPDATE_FAILED
    if storage_fault.code in {
        "DAQ.STORAGE.CHANNEL_LENGTH_MISMATCH",
        "DAQ.STORAGE.INTEGRITY_CHECK_FAILED",
    }:
        return SessionCompletionState.CORRUPT, SessionCompletionReason.INTEGRITY_CHECK_FAILED
    return SessionCompletionState.FAULTED, SessionCompletionReason.STORAGE_WRITE_FAILED


def _raw_filename(manifest: Mapping[str, object]) -> str:
    if manifest.get("schema_version") == 1:
        return _TDMS_RAW_NAME
    container = _mapping(manifest, "raw_container")
    return _string(container, "filename")


def _inspect_hdf5_raw(
    raw_path: Path,
    manifest: Mapping[str, object],
    ordered_ids: list[str],
    ordered_units: list[str],
    manifest_state: SessionCompletionState,
    issues: list[IntegrityIssue],
) -> tuple[dict[str, int], bool]:
    try:
        import h5py

        with h5py.File(raw_path, "r", libver="latest", swmr=True) as hdf5:
            required_paths = (
                "/raw/samples",
                "/raw/channel_ids",
                "/raw/channel_units",
                "/journal/commits",
                "/journal/finalization",
            )
            missing_paths = [path for path in required_paths if path not in hdf5]
            if missing_paths:
                issues.append(
                    IntegrityIssue(
                        "DAQ.STORAGE.HDF5_LAYOUT_UNSUPPORTED",
                        IntegrityStatus.FAIL,
                        "A required HDF5 layout object is missing.",
                        {"missing_paths": missing_paths},
                    )
                )
                return {}, False
            samples = hdf5["/raw/samples"]
            if samples.ndim != 2 or samples.shape[0] != len(ordered_ids):
                issues.append(
                    IntegrityIssue(
                        "DAQ.STORAGE.HDF5_LAYOUT_UNSUPPORTED",
                        IntegrityStatus.FAIL,
                        "The HDF5 raw dataset shape is incompatible with the manifest.",
                        {"shape": list(samples.shape), "channel_count": len(ordered_ids)},
                    )
                )
                return {}, False
            if samples.dtype != np.dtype("<f8") or not samples.fletcher32:
                issues.append(
                    IntegrityIssue(
                        "DAQ.STORAGE.HDF5_LAYOUT_UNSUPPORTED",
                        IntegrityStatus.FAIL,
                        "The HDF5 raw dataset dtype or chunk checksum is unsupported.",
                        {"dtype": str(samples.dtype), "fletcher32": bool(samples.fletcher32)},
                    )
                )
            container = _mapping(manifest, "raw_container")
            raw_chunk_shape = container.get("chunk_shape")
            expected_chunks = (
                tuple(_as_int(item, "chunk_shape") for item in raw_chunk_shape)
                if isinstance(raw_chunk_shape, list)
                else ()
            )
            axis_labels = tuple(dimension.label for dimension in samples.dims)
            if (
                samples.chunks != expected_chunks
                or samples.compression is not None
                or samples.maxshape[1] is not None
                or axis_labels != ("channel", "sample_index")
            ):
                issues.append(
                    IntegrityIssue(
                        "DAQ.STORAGE.HDF5_LAYOUT_UNSUPPORTED",
                        IntegrityStatus.FAIL,
                        "The HDF5 raw dataset geometry or axes are unsupported.",
                        {
                            "chunks": list(samples.chunks or ()),
                            "axis_labels": list(axis_labels),
                            "compression": samples.compression,
                            "maxshape": list(samples.maxshape),
                        },
                    )
                )
            actual_ids = _decode_hdf5_strings(hdf5["/raw/channel_ids"][:])
            actual_units = _decode_hdf5_strings(hdf5["/raw/channel_units"][:])
            if actual_ids != ordered_ids:
                issues.append(
                    IntegrityIssue(
                        "DAQ.STORAGE.CHANNEL_ORDER_MISMATCH",
                        IntegrityStatus.FAIL,
                        "Manifest and HDF5 channel order disagree.",
                        {"manifest": ordered_ids, "hdf5": actual_ids},
                    )
                )
            if actual_units != ordered_units:
                issues.append(
                    IntegrityIssue(
                        "DAQ.STORAGE.METADATA_MISMATCH",
                        IntegrityStatus.FAIL,
                        "Manifest and HDF5 raw units disagree.",
                        {"manifest": ordered_units, "hdf5": actual_units},
                    )
                )
            _check_hdf5_properties(hdf5, manifest, ordered_ids, issues)
            _check_hdf5_finalization(hdf5, manifest, manifest_state, issues)
            commits = hdf5["/journal/commits"]
            committed = 0
            expected_sequence = 0
            for record in commits:
                sequence = int(record["sequence_number"])
                start = int(record["start_sample_index"])
                count = int(record["sample_count"])
                stop = int(record["stop_sample_index"])
                if (
                    sequence != expected_sequence
                    or start != committed
                    or count <= 0
                    or stop != start + count
                    or stop > int(samples.shape[1])
                ):
                    issues.append(
                        IntegrityIssue(
                            "DAQ.STORAGE.HDF5_COMMIT_INVALID",
                            IntegrityStatus.FAIL,
                            "The HDF5 commit journal is not contiguous or valid.",
                            {
                                "record": expected_sequence,
                                "sequence": sequence,
                                "start": start,
                                "count": count,
                                "stop": stop,
                            },
                        )
                    )
                    break
                committed = stop
                expected_sequence += 1
            physical = int(samples.shape[1])
            uncommitted = physical != committed
            if uncommitted:
                terminal = (
                    manifest_state
                    in {
                        SessionCompletionState.COMPLETED,
                        SessionCompletionState.STOPPED,
                        SessionCompletionState.FAULTED,
                        SessionCompletionState.INTERRUPTED,
                    }
                    and manifest.get("recovery") is None
                )
                issues.append(
                    IntegrityIssue(
                        "DAQ.STORAGE.HDF5_UNCOMMITTED_TAIL",
                        IntegrityStatus.FAIL if terminal else IntegrityStatus.WARNING,
                        "The HDF5 dataset contains samples beyond its committed prefix.",
                        {"dataset_samples": physical, "committed_samples": committed},
                    )
                )
            return {channel_id: committed for channel_id in ordered_ids}, uncommitted
    except Exception as cause:
        issues.append(
            IntegrityIssue(
                "DAQ.STORAGE.HDF5_PARSE_FAILED",
                IntegrityStatus.FAIL,
                "The raw HDF5 file could not be parsed.",
                {"exception_type": type(cause).__name__},
            )
        )
        return {}, False


def _decode_hdf5_strings(values: Any) -> list[str]:
    return [
        value.decode("utf-8") if isinstance(value, (bytes, np.bytes_)) else str(value)
        for value in values
    ]


def _check_hdf5_properties(
    hdf5: Any,
    manifest: Mapping[str, object],
    ordered_ids: list[str],
    issues: list[IntegrityIssue],
) -> None:
    acquisition = _mapping(manifest, "acquisition")
    session = _mapping(manifest, "session")
    container = _mapping(manifest, "raw_container")
    comparisons = {
        "session_id": (_string(session, "session_id"), hdf5.attrs.get("session_id")),
        "session_schema_version": (2, hdf5.attrs.get("session_schema_version")),
        "hdf5_layout_version": (
            _as_int(container["layout_version"], "layout_version"),
            hdf5.attrs.get("hdf5_layout_version"),
        ),
        "actual_sample_rate_hz": (
            _as_float(acquisition["actual_sample_rate_hz"], "actual_sample_rate_hz"),
            hdf5.attrs.get("actual_sample_rate_hz"),
        ),
        "channel_count": (len(ordered_ids), hdf5.attrs.get("channel_count")),
        "canonical_sample_dtype": ("float64-le", hdf5.attrs.get("canonical_sample_dtype")),
        "source_classification": (
            acquisition.get("source_classification"),
            hdf5.attrs.get("source_classification"),
        ),
    }
    mismatched = [name for name, (expected, actual) in comparisons.items() if expected != actual]
    if mismatched:
        issues.append(
            IntegrityIssue(
                "DAQ.STORAGE.METADATA_MISMATCH",
                IntegrityStatus.FAIL,
                "Manifest and HDF5 root metadata disagree.",
                {"fields": mismatched},
            )
        )
    channel_values = manifest.get("channels", [])
    snapshots = channel_values if isinstance(channel_values, list) else []
    channels = hdf5.get("/channels")
    if channels is None or len(channels) != len(ordered_ids):
        issues.append(
            IntegrityIssue(
                "DAQ.STORAGE.METADATA_MISMATCH",
                IntegrityStatus.FAIL,
                "The HDF5 channel metadata groups do not match the manifest.",
                {
                    "manifest_channel_count": len(ordered_ids),
                    "hdf5_channel_count": 0 if channels is None else len(channels),
                },
            )
        )
        return
    channel_mismatches: dict[str, list[str]] = {}
    for index, channel_id in enumerate(ordered_ids):
        snapshot = snapshots[index] if index < len(snapshots) else None
        group_name = f"{index:03d}"
        if not isinstance(snapshot, Mapping) or group_name not in channels:
            channel_mismatches[channel_id] = ["channel_snapshot"]
            continue
        expected_properties = _tdms_channel_properties(snapshot)
        attributes = channels[group_name].attrs
        fields = [
            name
            for name, expected in expected_properties.items()
            if attributes.get(name) != _hdf5_attribute(expected)
        ]
        if fields:
            channel_mismatches[channel_id] = fields
    if channel_mismatches:
        issues.append(
            IntegrityIssue(
                "DAQ.STORAGE.METADATA_MISMATCH",
                IntegrityStatus.FAIL,
                "Manifest and HDF5 per-channel metadata disagree.",
                {"channels": channel_mismatches},
            )
        )


def _check_hdf5_finalization(
    hdf5: Any,
    manifest: Mapping[str, object],
    manifest_state: SessionCompletionState,
    issues: list[IntegrityIssue],
) -> None:
    records = hdf5["/journal/finalization"]
    if manifest.get("recovery") is not None:
        return
    terminal = manifest_state in {
        SessionCompletionState.COMPLETED,
        SessionCompletionState.STOPPED,
        SessionCompletionState.FAULTED,
    }
    if not terminal:
        return
    if len(records) != 1:
        issues.append(
            IntegrityIssue(
                "DAQ.STORAGE.HDF5_FINALIZATION_INVALID",
                (
                    IntegrityStatus.FAIL
                    if manifest_state
                    in {SessionCompletionState.COMPLETED, SessionCompletionState.STOPPED}
                    else IntegrityStatus.WARNING
                ),
                "The terminal manifest has no matching HDF5 finalization record.",
                {"record_count": len(records), "manifest_state": manifest_state.value},
            )
        )
        return
    record = records[0]
    stored_state = bytes(record["terminal_state"]).rstrip(b"\x00").decode("ascii")
    persisted = int(record["persisted_samples"])
    manifest_persisted = _as_int(
        _mapping(manifest, "counts").get("persisted_samples_per_channel", 0),
        "persisted_samples_per_channel",
    )
    if stored_state != manifest_state.value or persisted != manifest_persisted:
        issues.append(
            IntegrityIssue(
                "DAQ.STORAGE.HDF5_FINALIZATION_INVALID",
                IntegrityStatus.FAIL,
                "The HDF5 finalization record disagrees with the terminal manifest.",
                {
                    "hdf5_state": stored_state,
                    "manifest_state": manifest_state.value,
                    "hdf5_persisted": persisted,
                    "manifest_persisted": manifest_persisted,
                },
            )
        )


def _inspect_session(
    path: Path,
    *,
    level: VerificationLevel,
    now: Callable[[], datetime],
    process_is_alive: Callable[[str, int], bool],
) -> SessionIntegrityReport:
    if not path.exists() or not path.is_dir():
        raise SessionIntegrityError(
            "The requested session directory does not exist.",
            code="DAQ.STORAGE.SESSION_NOT_FOUND",
            context={"session_path": str(path)},
            remediation="Select an existing LabDAQ session directory.",
        )
    manifest = _read_manifest(path)
    raw_name = _raw_filename(manifest)
    session = _mapping(manifest, "session")
    acquisition = _mapping(manifest, "acquisition")
    counts = _mapping(manifest, "counts")
    manifest_state = SessionCompletionState(_string(session, "completion_state"))
    session_id = _string(session, "session_id")
    ordered_ids = _string_list(acquisition, "ordered_channel_ids")
    ordered_units = _string_list(acquisition, "ordered_channel_units")
    manifest_persisted = _as_int(
        counts.get("persisted_samples_per_channel", 0),
        "persisted_samples_per_channel",
    )
    issues: list[IntegrityIssue] = []
    checksum_results: dict[str, bool | None] = {}
    lock_exists, active_writer = _lock_status(path / _LOCK_NAME, process_is_alive)
    if active_writer:
        issues.append(
            IntegrityIssue(
                "DAQ.STORAGE.ACTIVE_WRITER_DETECTED",
                IntegrityStatus.WARNING,
                "The session is currently owned by an active writer; raw data was not opened.",
                {"session_path": str(path)},
            )
        )
        return SessionIntegrityReport(
            session_path=path,
            verification_level=level,
            overall=IntegrityStatus.WARNING,
            manifest_state=manifest_state,
            effective_state=manifest_state,
            recoverability=Recoverability.ACTIVE_WRITER,
            session_id=session_id,
            readable_common_sample_count=manifest_persisted,
            manifest_persisted_sample_count=manifest_persisted,
            per_channel_lengths={channel_id: manifest_persisted for channel_id in ordered_ids},
            checksum_results={},
            event_count=_as_int(counts.get("event_count", 0), "event_count"),
            issues=tuple(issues),
            verified_at=now().astimezone(UTC),
        )
    if lock_exists:
        issues.append(
            IntegrityIssue(
                "DAQ.STORAGE.STALE_RECORDING_LOCK",
                IntegrityStatus.WARNING,
                "The session has a stale recording lock.",
                {"session_path": str(path)},
            )
        )
    required = (_MANIFEST_NAME, raw_name, _EVENTS_NAME, _RECIPE_NAME)
    for artifact in required:
        if not (path / artifact).is_file():
            issues.append(
                IntegrityIssue(
                    "DAQ.STORAGE.SESSION_ARTIFACT_MISSING",
                    IntegrityStatus.FAIL,
                    "A required session artifact is missing.",
                    {"artifact": artifact},
                )
            )
    try:
        recipe = _read_json(path / _RECIPE_NAME)
        _validate_document(recipe, _RECIPE_SCHEMA)
    except (OSError, SessionSchemaError) as cause:
        issues.append(
            IntegrityIssue(
                "DAQ.STORAGE.RECIPE_SCHEMA_INVALID",
                IntegrityStatus.FAIL,
                "The processing-recipe snapshot is missing or invalid.",
                {"error": str(cause)},
            )
        )
    lengths: dict[str, int] = {}
    incomplete = False
    if manifest.get("schema_version") == 2:
        lengths, incomplete = _inspect_hdf5_raw(
            path / raw_name,
            manifest,
            ordered_ids,
            ordered_units,
            manifest_state,
            issues,
        )
    else:
        try:
            _, _, _, TdmsFile, _ = _nptdms_types()
            metadata_log = _IncompleteTdmsLogHandler()
            tdms_logger = logging.getLogger("nptdms.reader")
            tdms_logger.addHandler(metadata_log)
            try:
                with warnings.catch_warnings(record=True) as tdms_warnings:
                    warnings.simplefilter("always")
                    tdms = TdmsFile.open(path / raw_name)
            finally:
                tdms_logger.removeHandler(metadata_log)
            metadata_incomplete = (
                any(
                    "segment metadata is incomplete" in str(warning.message).casefold()
                    for warning in tdms_warnings
                )
                or metadata_log.incomplete
            )
            with tdms:
                if _RAW_GROUP not in tdms:
                    issues.append(
                        IntegrityIssue(
                            "DAQ.STORAGE.TDMS_LAYOUT_UNSUPPORTED",
                            IntegrityStatus.FAIL,
                            "The required raw TDMS group is missing.",
                        )
                    )
                else:
                    group = tdms[_RAW_GROUP]
                    actual_order = [channel.name for channel in group.channels()]
                    if actual_order != ordered_ids:
                        issues.append(
                            IntegrityIssue(
                                "DAQ.STORAGE.CHANNEL_ORDER_MISMATCH",
                                IntegrityStatus.FAIL,
                                "Manifest and TDMS channel order disagree.",
                                {"manifest": ordered_ids, "tdms": actual_order},
                            )
                        )
                    lengths = {channel.name: len(channel) for channel in group.channels()}
                    _check_tdms_properties(
                        tdms,
                        manifest,
                        ordered_ids,
                        ordered_units,
                        issues,
                    )
                incomplete = metadata_incomplete or bool(tdms.file_status.incomplete_final_segment)
        except Exception as cause:
            issues.append(
                IntegrityIssue(
                    "DAQ.STORAGE.TDMS_PARSE_FAILED",
                    IntegrityStatus.FAIL,
                    "The raw TDMS file could not be parsed.",
                    {"exception_type": type(cause).__name__},
                )
            )
    common = min(lengths.values(), default=0)
    if lengths and len(set(lengths.values())) > 1:
        status = (
            IntegrityStatus.WARNING
            if incomplete
            and manifest_state
            in {
                SessionCompletionState.INITIALIZING,
                SessionCompletionState.RECORDING,
                SessionCompletionState.FINALIZING,
            }
            else IntegrityStatus.FAIL
        )
        issues.append(
            IntegrityIssue(
                "DAQ.STORAGE.CHANNEL_LENGTH_MISMATCH",
                status,
                "Raw container channel lengths are not equal.",
                {"channel_lengths": lengths, "incomplete_final_segment": incomplete},
            )
        )
    if incomplete and manifest.get("schema_version") == 1:
        terminal = manifest_state in {
            SessionCompletionState.COMPLETED,
            SessionCompletionState.STOPPED,
            SessionCompletionState.FAULTED,
            SessionCompletionState.INTERRUPTED,
        }
        issues.append(
            IntegrityIssue(
                (
                    "DAQ.STORAGE.HDF5_UNCOMMITTED_TAIL"
                    if manifest.get("schema_version") == 2
                    else "DAQ.STORAGE.TDMS_FINAL_SEGMENT_INCOMPLETE"
                ),
                IntegrityStatus.FAIL if terminal else IntegrityStatus.WARNING,
                "The raw container contains an incomplete or uncommitted tail.",
            )
        )
    if manifest_persisted != common:
        nonterminal = manifest_state in {
            SessionCompletionState.INITIALIZING,
            SessionCompletionState.RECORDING,
            SessionCompletionState.FINALIZING,
        }
        checkpoint_lag = nonterminal and manifest_persisted <= common
        issues.append(
            IntegrityIssue(
                "DAQ.STORAGE.MANIFEST_COUNT_MISMATCH",
                IntegrityStatus.WARNING if checkpoint_lag else IntegrityStatus.FAIL,
                "Manifest and raw-container persisted sample counts disagree.",
                {"manifest": manifest_persisted, "raw_common": common},
            )
        )
    recorded_path = Path(str(session.get("created_path", "")))
    if recorded_path and recorded_path.absolute() != path.absolute():
        issues.append(
            IntegrityIssue(
                "DAQ.STORAGE.SESSION_RELOCATED",
                IntegrityStatus.WARNING,
                "The session is being inspected from a different path than its creation path.",
                {"created_path": str(recorded_path), "current_path": str(path)},
            )
        )
    event_count = _as_int(counts.get("event_count", 0), "event_count")
    if level is VerificationLevel.FULL and (path / _EVENTS_NAME).is_file():
        allow_truncated = (
            manifest_state
            in {
                SessionCompletionState.INITIALIZING,
                SessionCompletionState.RECORDING,
                SessionCompletionState.FINALIZING,
            }
            or lock_exists
            or manifest.get("recovery") is not None
        )
        event_count, warning_count, fault_count, event_issues = _read_events(
            path / _EVENTS_NAME,
            allow_truncated_final=allow_truncated,
            validate_schema=True,
        )
        issues.extend(event_issues)
        if event_count != _as_int(counts.get("event_count", 0), "event_count"):
            issues.append(
                IntegrityIssue(
                    "DAQ.STORAGE.EVENT_COUNT_MISMATCH",
                    IntegrityStatus.WARNING if allow_truncated else IntegrityStatus.FAIL,
                    "Manifest and event-log counts disagree.",
                    {"manifest": counts.get("event_count", 0), "events": event_count},
                )
            )
        if manifest_state in {
            SessionCompletionState.COMPLETED,
            SessionCompletionState.STOPPED,
            SessionCompletionState.FAULTED,
            SessionCompletionState.INTERRUPTED,
        }:
            if warning_count != _as_int(counts.get("warning_count", 0), "warning_count"):
                issues.append(
                    IntegrityIssue(
                        "DAQ.STORAGE.WARNING_COUNT_MISMATCH",
                        IntegrityStatus.WARNING,
                        "Manifest and event-log warning counts disagree.",
                    )
                )
            if fault_count > _as_int(counts.get("fault_count", 0), "fault_count"):
                issues.append(
                    IntegrityIssue(
                        "DAQ.STORAGE.FAULT_COUNT_MISMATCH",
                        IntegrityStatus.WARNING,
                        "Event-log faults exceed the manifest fault count.",
                    )
                )
        integrity = _mapping(manifest, "integrity")
        expected_digest = integrity.get("logical_data_sha256")
        digest_count = _as_int(
            integrity.get("logical_data_sample_count", 0),
            "logical_data_sample_count",
        )
        if expected_digest is not None and digest_count <= common:
            actual_digest = _logical_digest_from_raw(
                path / raw_name,
                ordered_ids,
                digest_count,
                schema_version=_as_int(manifest["schema_version"], "schema_version"),
            )
            checksum_results["logical_data_sha256"] = actual_digest == expected_digest
            if actual_digest != expected_digest:
                issues.append(
                    IntegrityIssue(
                        "DAQ.STORAGE.CHECKSUM_MISMATCH",
                        IntegrityStatus.FAIL,
                        "The logical raw-data digest does not match the manifest.",
                    )
                )
        expected_files = integrity.get("file_checksums", {})
        if isinstance(expected_files, Mapping):
            for name, expected in expected_files.items():
                artifact_path = path / str(name)
                if artifact_path.is_file():
                    actual = file_sha256(artifact_path)
                    matches = actual == expected
                    checksum_results[str(name)] = matches
                    if not matches:
                        issues.append(
                            IntegrityIssue(
                                "DAQ.STORAGE.CHECKSUM_MISMATCH",
                                IntegrityStatus.FAIL,
                                "A session file checksum does not match the manifest.",
                                {"artifact": str(name)},
                            )
                        )
                else:
                    checksum_results[str(name)] = False
    overall = _overall_integrity(issues)
    if active_writer:
        recoverability = Recoverability.ACTIVE_WRITER
        effective = manifest_state
    elif overall is IntegrityStatus.FAIL:
        recoverability = Recoverability.NOT_REPLAYABLE
        effective = SessionCompletionState.CORRUPT
    elif manifest_state in {
        SessionCompletionState.INITIALIZING,
        SessionCompletionState.RECORDING,
        SessionCompletionState.FINALIZING,
    }:
        recoverability = (
            Recoverability.RECOVERY_CANDIDATE if common > 0 else Recoverability.NOT_REPLAYABLE
        )
        effective = SessionCompletionState.INTERRUPTED
    elif manifest_state is SessionCompletionState.INTERRUPTED:
        recoverability = (
            Recoverability.REPLAYABLE_PREFIX if common > 0 else Recoverability.NOT_REPLAYABLE
        )
        effective = manifest_state
    elif manifest_state is SessionCompletionState.CORRUPT:
        recoverability = Recoverability.NOT_REPLAYABLE
        effective = manifest_state
    else:
        recoverability = Recoverability.REPLAYABLE
        effective = manifest_state
    return SessionIntegrityReport(
        session_path=path,
        verification_level=level,
        overall=overall,
        manifest_state=manifest_state,
        effective_state=effective,
        recoverability=recoverability,
        session_id=session_id,
        readable_common_sample_count=common,
        manifest_persisted_sample_count=manifest_persisted,
        per_channel_lengths=lengths,
        checksum_results=checksum_results,
        event_count=event_count,
        issues=tuple(issues),
        verified_at=now().astimezone(UTC),
    )


class _IncompleteTdmsLogHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.incomplete = False

    def emit(self, record: logging.LogRecord) -> None:
        if "segment metadata is incomplete" in record.getMessage().casefold():
            self.incomplete = True


def _check_tdms_properties(
    tdms: Any,
    manifest: Mapping[str, object],
    ordered_ids: list[str],
    ordered_units: list[str],
    issues: list[IntegrityIssue],
) -> None:
    session = _mapping(manifest, "session")
    software = _mapping(manifest, "software")
    acquisition = _mapping(manifest, "acquisition")
    root = tdms.properties
    comparisons = (
        ("session_id", _string(session, "session_id"), root.get("session_id")),
        (
            "session_schema_version",
            manifest.get("schema_version"),
            root.get("session_schema_version"),
        ),
        (
            "tdms_layout_version",
            manifest.get("tdms_layout_version"),
            root.get("tdms_layout_version"),
        ),
        (
            "application_version",
            software.get("application_version"),
            root.get("application_version"),
        ),
        (
            "requested_sample_rate_hz",
            _as_float(acquisition["requested_sample_rate_hz"], "requested_sample_rate_hz"),
            root.get("requested_sample_rate_hz"),
        ),
        (
            "actual_sample_rate_hz",
            _as_float(acquisition["actual_sample_rate_hz"], "actual_sample_rate_hz"),
            root.get("actual_sample_rate_hz"),
        ),
        ("channel_count", len(ordered_ids), root.get("channel_count")),
        ("session_created_at_utc", session.get("created_at"), root.get("session_created_at_utc")),
        ("timezone", session.get("timezone"), root.get("timezone")),
        (
            "canonical_sample_dtype",
            acquisition.get("sample_dtype"),
            root.get("canonical_sample_dtype"),
        ),
        (
            "source_classification",
            acquisition.get("source_classification"),
            root.get("source_classification"),
        ),
    )
    for field, expected, received in comparisons:
        if expected != received:
            issues.append(
                IntegrityIssue(
                    "DAQ.STORAGE.METADATA_MISMATCH",
                    IntegrityStatus.FAIL,
                    "Manifest and TDMS root metadata disagree.",
                    {"field": field, "manifest": expected, "tdms": received},
                )
            )
    try:
        root_order = json.loads(str(root.get("ordered_channel_ids_json", "[]")))
    except json.JSONDecodeError:
        root_order = []
    if root_order != ordered_ids:
        issues.append(
            IntegrityIssue(
                "DAQ.STORAGE.CHANNEL_ORDER_MISMATCH",
                IntegrityStatus.FAIL,
                "Manifest and TDMS root channel order disagree.",
                {"manifest": ordered_ids, "tdms": root_order},
            )
        )
    if _RAW_GROUP not in tdms:
        return
    group = tdms[_RAW_GROUP]
    raw_snapshot_value = manifest.get("channels", [])
    raw_snapshots = raw_snapshot_value if isinstance(raw_snapshot_value, list) else []
    snapshots = {
        str(snapshot.get("logical_channel_id")): snapshot
        for snapshot in raw_snapshots
        if isinstance(snapshot, Mapping)
    }
    for channel_id, unit in zip(ordered_ids, ordered_units, strict=True):
        if channel_id not in group:
            continue
        properties = group[channel_id].properties
        snapshot = snapshots.get(channel_id)
        if snapshot is None:
            issues.append(
                IntegrityIssue(
                    "DAQ.STORAGE.METADATA_MISMATCH",
                    IntegrityStatus.FAIL,
                    "A TDMS channel has no matching manifest channel snapshot.",
                    {"channel_id": channel_id},
                )
            )
            continue
        sensor = snapshot.get("sensor")
        calibration = snapshot.get("calibration")
        disposition = str(snapshot.get("processing_disposition", "calibrated"))
        if disposition == ProcessingDisposition.CALIBRATED.value and (
            not isinstance(sensor, Mapping) or not isinstance(calibration, Mapping)
        ):
            issues.append(
                IntegrityIssue(
                    "DAQ.STORAGE.METADATA_MISMATCH",
                    IntegrityStatus.FAIL,
                    "A manifest calibrated channel lacks sensor or calibration provenance.",
                    {"channel_id": channel_id},
                )
            )
            continue
        channel_comparisons: dict[str, tuple[object, object]] = {
            "logical_channel_id": (channel_id, properties.get("logical_channel_id")),
            "display_name": (snapshot.get("display_name"), properties.get("display_name")),
            "physical_channel": (
                snapshot.get("physical_channel"),
                properties.get("physical_channel"),
            ),
            "channel_order": (snapshot.get("channel_order"), properties.get("channel_order")),
            "raw_quantity": (snapshot.get("raw_quantity"), properties.get("raw_quantity")),
            "raw_unit": (unit, properties.get("raw_unit")),
            "terminal_configuration": (
                snapshot.get("terminal_configuration"),
                properties.get("terminal_configuration"),
            ),
            "requested_input_range_v_json": (
                json.dumps(snapshot.get("requested_input_range_v")),
                properties.get("requested_input_range_v_json"),
            ),
            "effective_input_range_v_json": (
                json.dumps(snapshot.get("effective_input_range_v")),
                properties.get("effective_input_range_v_json"),
            ),
        }
        if isinstance(sensor, Mapping):
            channel_comparisons.update(
                {
                    "sensor_type": (sensor.get("sensor_type"), properties.get("sensor_type")),
                    "sensor_id": (sensor.get("sensor_id"), properties.get("sensor_id")),
                    "sensor_model": (sensor.get("model"), properties.get("sensor_model")),
                    "sensor_serial_number": (
                        sensor.get("serial_number"),
                        properties.get("sensor_serial_number"),
                    ),
                }
            )
        if "processing_disposition" in snapshot:
            channel_comparisons["processing_disposition"] = (
                disposition,
                properties.get("processing_disposition"),
            )
        if isinstance(calibration, Mapping):
            channel_comparisons.update(
                {
                    "calibration_id": (
                        calibration.get("calibration_id"),
                        properties.get("calibration_id"),
                    ),
                    "calibration_effective_date": (
                        calibration.get("effective_date"),
                        properties.get("calibration_effective_date"),
                    ),
                    "calibration_source": (
                        calibration.get("source_reference"),
                        properties.get("calibration_source"),
                    ),
                }
            )
        else:
            channel_comparisons.update(
                {
                    "engineering_interpretation": (
                        "null",
                        properties.get("engineering_interpretation"),
                    ),
                    "warning": (snapshot.get("warning"), properties.get("warning")),
                }
            )
        chain = snapshot.get("conditioning_chain")
        if isinstance(chain, Mapping):
            channel_comparisons["conditioning_chain_id"] = (
                chain.get("conditioning_chain_id"),
                properties.get("conditioning_chain_id"),
            )
            channel_comparisons["conditioning_chain_description"] = (
                chain.get("description"),
                properties.get("conditioning_chain_description"),
            )
        mismatched = [
            field
            for field, (expected, received) in channel_comparisons.items()
            if expected != received
        ]
        if mismatched:
            issues.append(
                IntegrityIssue(
                    "DAQ.STORAGE.METADATA_MISMATCH",
                    IntegrityStatus.FAIL,
                    "Manifest and TDMS channel metadata disagree.",
                    {"channel_id": channel_id, "fields": mismatched},
                )
            )


def _read_events(
    path: Path,
    *,
    allow_truncated_final: bool,
    validate_schema: bool,
) -> tuple[int, int, int, tuple[IntegrityIssue, ...]]:
    count = 0
    warnings = 0
    faults = 0
    expected_sequence = 0
    issues: list[IntegrityIssue] = []
    try:
        with path.open("rb") as stream:
            line = stream.readline()
            while line:
                next_line = stream.readline()
                is_final = not next_line
                complete = line.endswith(b"\n")
                try:
                    payload = _event_object(json.loads(line.decode("utf-8")))
                    if validate_schema:
                        _validate_document(payload, _EVENT_SCHEMA)
                except (
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                    ValueError,
                    SessionSchemaError,
                ) as cause:
                    if is_final and not complete and allow_truncated_final:
                        issues.append(
                            IntegrityIssue(
                                "DAQ.STORAGE.EVENT_FINAL_LINE_TRUNCATED",
                                IntegrityStatus.WARNING,
                                "A truncated final event line was ignored.",
                            )
                        )
                        break
                    issues.append(
                        IntegrityIssue(
                            "DAQ.STORAGE.EVENT_LOG_CORRUPT",
                            IntegrityStatus.FAIL,
                            "An event-log line is malformed.",
                            {"line_number": count + 1, "error": str(cause)},
                        )
                    )
                    line = next_line
                    continue
                sequence = _as_int(payload.get("event_sequence", -1), "event_sequence")
                if sequence != expected_sequence:
                    issues.append(
                        IntegrityIssue(
                            "DAQ.STORAGE.EVENT_SEQUENCE_INVALID",
                            IntegrityStatus.FAIL,
                            "Persisted event sequence is not contiguous.",
                            {"expected": expected_sequence, "received": sequence},
                        )
                    )
                expected_sequence = sequence + 1
                severity = str(payload.get("severity", ""))
                warnings += severity == "warning"
                faults += severity in {"error", "critical"}
                count += 1
                line = next_line
    except OSError as cause:
        issues.append(
            IntegrityIssue(
                "DAQ.STORAGE.EVENT_LOG_READ_FAILED",
                IntegrityStatus.FAIL,
                "The event log could not be read.",
                {"os_error": cause.errno},
            )
        )
    return count, warnings, faults, tuple(issues)


def _logical_digest_from_tdms(path: Path, ordered_ids: list[str], sample_count: int) -> str:
    digest = LogicalDataDigest()
    if sample_count == 0:
        return digest.hexdigest()
    _, _, _, TdmsFile, _ = _nptdms_types()
    with TdmsFile.open(path) as tdms:
        group = tdms[_RAW_GROUP]
        for start in range(0, sample_count, 8192):
            stop = min(sample_count, start + 8192)
            values = np.empty((len(ordered_ids), stop - start), dtype=np.float64)
            for index, channel_id in enumerate(ordered_ids):
                values[index] = group[channel_id][start:stop]
            digest.update(values)
    return digest.hexdigest()


def _logical_digest_from_raw(
    path: Path,
    ordered_ids: list[str],
    sample_count: int,
    *,
    schema_version: int,
) -> str:
    if schema_version == 1:
        return _logical_digest_from_tdms(path, ordered_ids, sample_count)
    import h5py

    digest = LogicalDataDigest()
    with h5py.File(path, "r", libver="latest", swmr=True) as hdf5:
        samples = hdf5["/raw/samples"]
        for start in range(0, sample_count, 8192):
            stop = min(sample_count, start + 8192)
            digest.update(np.asarray(samples[:, start:stop], dtype=np.float64))
    return digest.hexdigest()


def _tdms_channel_lengths(tdms: Any) -> dict[str, int]:
    if _RAW_GROUP not in tdms:
        return {}
    return {channel.name: len(channel) for channel in tdms[_RAW_GROUP].channels()}


def _closed_tdms_lengths(path: Path) -> tuple[dict[str, int], bool]:
    _, _, _, TdmsFile, _ = _nptdms_types()
    with TdmsFile.open(path) as tdms:
        return _tdms_channel_lengths(tdms), bool(tdms.file_status.incomplete_final_segment)


def _closed_hdf5_lengths(
    path: Path,
    ordered_ids: tuple[str, ...],
) -> tuple[dict[str, int], bool]:
    import h5py

    with h5py.File(path, "r", libver="latest", swmr=True) as hdf5:
        physical = int(hdf5["/raw/samples"].shape[1])
        commits = hdf5["/journal/commits"]
        committed = int(commits[-1]["stop_sample_index"]) if len(commits) else 0
    return {channel_id: committed for channel_id in ordered_ids}, physical != committed


def _session_file_checksums(path: Path) -> dict[str, str]:
    return {
        name: file_sha256(path / name)
        for name in (_RAW_NAME, _EVENTS_NAME, _RECIPE_NAME)
        if (path / name).is_file()
    }


def _read_manifest(path: Path) -> dict[str, object]:
    manifest_path = path / _MANIFEST_NAME
    try:
        payload = _read_json(manifest_path)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as cause:
        raise SessionSchemaError(
            "The session manifest could not be read as UTF-8 JSON.",
            code="DAQ.STORAGE.MANIFEST_PARSE_FAILED",
            context={"manifest_path": str(manifest_path)},
            remediation="Preserve the session and restore a valid manifest from a known checkpoint if available.",
            cause=cause,
        ) from cause
    _validate_document(payload, _MANIFEST_SCHEMA)
    return payload


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SessionSchemaError(
            "A session JSON artifact must contain an object.",
            code="DAQ.STORAGE.SCHEMA_INVALID",
            context={"path": str(path)},
            remediation="Use an unmodified LabDAQ session artifact.",
        )
    return cast("dict[str, object]", value)


def _validate_document(payload: Mapping[str, object], schema_name: str) -> None:
    received = payload.get("schema_version")
    schema_version = 1
    if schema_name == _MANIFEST_SCHEMA:
        if received not in {1, 2}:
            raise SessionSchemaError(
                "The durable session schema version is not supported.",
                code="DAQ.STORAGE.UNSUPPORTED_SESSION_SCHEMA",
                context={"schema_version": received},
                remediation="Use a LabDAQ session with schema version 1 or 2.",
            )
        schema_version = int(received)
    schema_resource = files("labdaq.resources").joinpath(
        "schemas", f"v{schema_version}", schema_name
    )
    schema = json.loads(schema_resource.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.absolute_path))
    if not errors:
        return
    first = errors[0]
    path = "$" + "".join(
        f"[{part}]" if isinstance(part, int) else f".{part}" for part in first.absolute_path
    )
    code = (
        "DAQ.STORAGE.UNSUPPORTED_SESSION_SCHEMA"
        if schema_name == _MANIFEST_SCHEMA and received not in {1, 2}
        else "DAQ.STORAGE.SCHEMA_INVALID"
    )
    raise SessionSchemaError(
        "A durable session artifact does not match its versioned schema.",
        code=code,
        context={"schema": schema_name, "field_path": path, "rule": first.message},
        remediation="Use a supported, unmodified schema-version 1 session artifact.",
    )


def _atomic_write_json(path: Path, payload: Mapping[str, object], *, schema_name: str) -> None:
    _validate_document(payload, schema_name)
    temporary = path.with_name(path.name + ".tmp")
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    with temporary.open("wb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _write_json_exclusive(path: Path, payload: Mapping[str, object]) -> None:
    encoded = (
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")
    with path.open("xb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


def _create_reserve(path: Path, byte_count: int) -> None:
    block = b"\0" * min(64 * 1024, byte_count)
    remaining = byte_count
    with path.open("xb") as stream:
        while remaining:
            chunk = block[: min(len(block), remaining)]
            stream.write(chunk)
            remaining -= len(chunk)
        stream.flush()
        os.fsync(stream.fileno())


def _lock_status(
    path: Path,
    process_is_alive: Callable[[str, int], bool],
) -> tuple[bool, bool]:
    if not path.is_file():
        return False, False
    try:
        lock = _read_json(path)
        host = str(lock["host"])
        process_id = _as_int(lock["process_id"], "process_id")
    except (OSError, KeyError, TypeError, ValueError, SessionSchemaError):
        return True, False
    return True, process_is_alive(host, process_id)


def _process_is_alive(host: str, process_id: int) -> bool:
    if process_id <= 0:
        return False
    if host.casefold() != socket.gethostname().casefold():
        return True
    if process_id == os.getpid():
        return True
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(process_query_limited_information, False, process_id)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return int(exit_code.value) == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _is_remote_path(path: Path) -> bool:
    resolved = str(path.absolute())
    if resolved.startswith(("\\\\", "//")):
        return True
    if os.name != "nt" or not path.anchor:
        return False
    import ctypes

    drive_remote = 4
    return int(ctypes.windll.kernel32.GetDriveTypeW(path.anchor)) == drive_remote


def _overall_integrity(issues: list[IntegrityIssue]) -> IntegrityStatus:
    if any(issue.status is IntegrityStatus.FAIL for issue in issues):
        return IntegrityStatus.FAIL
    if issues:
        return IntegrityStatus.WARNING
    return IntegrityStatus.PASS


def _mapping(payload: Mapping[str, object], key: str) -> dict[str, object]:
    value = payload[key]
    if not isinstance(value, dict):
        raise SessionSchemaError(
            "A session document field must contain an object.",
            code="DAQ.STORAGE.SCHEMA_INVALID",
            context={"field": key},
            remediation="Use an unmodified schema-version 1 session artifact.",
        )
    return cast("dict[str, object]", value)


def _string(payload: Mapping[str, object], key: str) -> str:
    value = payload[key]
    if not isinstance(value, str):
        raise SessionSchemaError(
            "A session document field must contain a string.",
            code="DAQ.STORAGE.SCHEMA_INVALID",
            context={"field": key},
            remediation="Use an unmodified schema-version 1 session artifact.",
        )
    return value


def _string_list(payload: Mapping[str, object], key: str) -> list[str]:
    value = payload[key]
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise SessionSchemaError(
            "A session document field must contain a string array.",
            code="DAQ.STORAGE.SCHEMA_INVALID",
            context={"field": key},
            remediation="Use an unmodified schema-version 1 session artifact.",
        )
    return cast("list[str]", value)


def _as_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SessionSchemaError(
            "A session document field must contain an integer.",
            code="DAQ.STORAGE.SCHEMA_INVALID",
            context={"field": field},
            remediation="Use an unmodified schema-version 1 session artifact.",
        )
    return value


def _as_float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SessionSchemaError(
            "A session document field must contain a number.",
            code="DAQ.STORAGE.SCHEMA_INVALID",
            context={"field": field},
            remediation="Use an unmodified schema-version 1 session artifact.",
        )
    return float(value)


def _event_object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("event line must contain an object")
    return cast("dict[str, object]", value)


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SessionSchemaError(
            "A persisted session timestamp is invalid.",
            code="DAQ.STORAGE.SCHEMA_INVALID",
            context={"timestamp_type": type(value).__name__},
            remediation="Use an unmodified schema-version 1 session manifest.",
        )
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SessionSchemaError(
            "A persisted session timestamp is not timezone-aware.",
            code="DAQ.STORAGE.SCHEMA_INVALID",
            context={"timestamp": value},
            remediation="Use UTC ISO 8601 timestamps with an explicit offset.",
        )
    return parsed


def _unlink_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
