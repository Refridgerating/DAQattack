from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import numpy as np
import pytest

from labdaq.application.open_acquisition_service import (
    OpenAcquisitionCoordinator,
    _project_chunk,
)
from labdaq.application.readiness_service import (
    OpenAcquisitionIntent,
    RawDiagnosticAuthorization,
    RawDiagnosticConfirmation,
)
from labdaq.ports.acquisition import AcquisitionChunk
from labdaq.ports.operator import (
    OpenAcquisitionStatus,
    OpenMonitorState,
    OpenRecordingState,
)
from labdaq.ports.session import (
    OpenAcquisitionProvenance,
    RecordingSegmentCompletion,
    RecordingSegmentStatus,
)

if TYPE_CHECKING:
    from labdaq.domain.models import ExperimentConfig, HardwareProfile


def test_projected_recording_chunk_rebases_identity_index_and_sequence() -> None:
    values = np.arange(24, dtype=np.float64).reshape(4, 6)
    values.flags.writeable = False
    source = AcquisitionChunk(
        session_id="monitor",
        sequence_number=9,
        start_sample_index=150,
        sample_count=6,
        sample_rate_hz=50_000.0,
        ordered_channel_ids=("a", "b", "c", "d"),
        ordered_channel_units=("V", "V", "V", "V"),
        samples=values,
        read_completed_monotonic=1.0,
    )

    projected = _project_chunk(
        source,
        channel_position=2,
        session_id="segment",
        source_origin=144,
        sequence_number=1,
    )

    assert projected.session_id == "segment"
    assert projected.sequence_number == 1
    assert projected.start_sample_index == 6
    assert projected.ordered_channel_ids == ("c",)
    assert np.array_equal(projected.samples, values[2:3])
    assert projected.samples.flags.c_contiguous
    assert not projected.samples.flags.writeable

    multichannel = _project_chunk(
        source,
        channel_positions=(0, 2, 3),
        session_id="segment-all",
        source_origin=144,
        sequence_number=2,
    )
    assert multichannel.ordered_channel_ids == ("a", "c", "d")
    assert multichannel.samples.shape == (3, 6)
    assert np.array_equal(multichannel.samples, values[[0, 2, 3]])


def test_open_intent_requires_authorized_enabled_channel(
    synthetic_experiment: ExperimentConfig,
    baseline_profile: HardwareProfile,
) -> None:
    experiment = synthetic_experiment
    selected = experiment.acquisition.channel_ids[0]
    confirmation = RawDiagnosticConfirmation(
        operator="operator",
        confirmed_at=datetime.now(UTC),
        measured_supply_voltage_v=3.3,
        polarity_confirmed=True,
        ai3_connection="disconnected",
    )
    authorization = RawDiagnosticAuthorization(
        confirmation=confirmation,
        hardware_validation_sha256="a" * 64,
        selected_device_id="device",
        selected_module_id="module",
        ordered_physical_channels=("module/ai0",),
        workflow="open-acquisition",
        selected_channel_id=selected,
    )
    with pytest.raises(ValueError, match="SHA-256"):
        replace(authorization, hardware_validation_sha256="short")
    with pytest.raises(ValueError, match="selected channel"):
        replace(authorization, selected_channel_id=None)
    with pytest.raises(ValueError, match="does not select"):
        replace(authorization, workflow="single-run")
    with pytest.raises(ValueError, match="channels and units"):
        replace(authorization, recorded_channel_units=("V", "V"))

    OpenAcquisitionIntent(
        profile=baseline_profile,
        experiment=experiment,
        selected_channel_id=selected,
        authorization=authorization,
        eligibility_evaluated_at=datetime.now(UTC),
    )

    with pytest.raises(ValueError, match="match its authorization"):
        OpenAcquisitionIntent(
            profile=baseline_profile,
            experiment=experiment,
            selected_channel_id=experiment.acquisition.channel_ids[1],
            authorization=authorization,
            eligibility_evaluated_at=datetime.now(UTC),
        )
    with pytest.raises(ValueError, match="units must match"):
        OpenAcquisitionIntent(
            profile=baseline_profile,
            experiment=experiment,
            selected_channel_id=selected,
            authorization=replace(authorization, recorded_channel_units=("mV",)),
            eligibility_evaluated_at=datetime.now(UTC),
        )


def test_open_acquisition_value_objects_validate_ranges_and_times() -> None:
    now = datetime.now(UTC)
    provenance = OpenAcquisitionProvenance(
        monitor_run_id="monitor",
        monitor_started_at=now,
        configured_logical_channels=("a", "b"),
        configured_physical_channels=("module/ai0", "module/ai1"),
        selected_channel_id="b",
        source_start_sample_index=100,
        record_requested_at=now,
        maximum_history_seconds=10.0,
        visible_window_seconds=5.0,
        source_stop_sample_index=120,
        stop_requested_at=now + timedelta(seconds=1),
    )
    document = provenance.to_dict()
    assert document["recorded_channel_ids"] == ["b"]
    assert document["recorded_channel_units"] == ["V"]
    assert document["local_to_source_index_mapping"] == {
        "local_origin": 0,
        "source_origin": 100,
        "scale": 1,
    }

    invalid_provenance = (
        {"monitor_run_id": ""},
        {"monitor_started_at": now.replace(tzinfo=None)},
        {"record_requested_at": now.replace(tzinfo=None)},
        {"stop_requested_at": now.replace(tzinfo=None)},
        {"source_start_sample_index": -1},
        {"source_stop_sample_index": 99},
        {"maximum_history_seconds": 0.0},
        {"visible_window_seconds": 11.0},
        {"configured_physical_channels": ("module/ai0",)},
        {"selected_channel_id": "missing"},
        {"recorded_channel_units": ("V", "V")},
    )
    for changes in invalid_provenance:
        with pytest.raises(ValueError):
            replace(provenance, **changes)

    segment = RecordingSegmentCompletion(
        session_id="segment",
        started_at=now,
        stopped_at=now + timedelta(seconds=1),
        source_start_sample_index=10,
        source_stop_sample_index=20,
        total_recording_samples_accepted=10,
        stop_requested_at=now,
    )
    assert segment.total_acquired_samples_per_channel == 10
    invalid_segments = (
        {"session_id": ""},
        {"started_at": now.replace(tzinfo=None)},
        {"stopped_at": now - timedelta(seconds=1)},
        {"source_start_sample_index": -1},
        {"source_stop_sample_index": 9},
        {"total_recording_samples_accepted": 9},
        {"stop_requested_at": now.replace(tzinfo=None)},
    )
    for changes in invalid_segments:
        with pytest.raises(ValueError):
            replace(segment, **changes)

    status = RecordingSegmentStatus("segment", 10, 20, 10, 10, 10)
    assert status.persisted_samples_per_channel == 10
    with pytest.raises(ValueError):
        replace(status, session_id="")
    with pytest.raises(ValueError):
        replace(status, source_stop_sample_index=9)
    with pytest.raises(ValueError):
        replace(status, persisted_samples_per_channel=-1)


def test_open_status_and_idle_coordinator_contracts(
    synthetic_experiment: ExperimentConfig,
    baseline_profile: HardwareProfile,
) -> None:
    status = OpenAcquisitionStatus(
        monitor_state=OpenMonitorState.IDLE,
        recording_state=OpenRecordingState.INACTIVE,
        monitor_run_id=None,
        selected_channel_id=None,
        selected_channel_unit=None,
        monitor_started_at=None,
        elapsed_seconds=0.0,
        actual_sample_rate_hz=None,
        acquired_samples_per_channel=0,
        retained_start_sample_index=None,
        retained_stop_sample_index=None,
        display_frames_dropped=0,
        recording_session_id=None,
        recording_session_path=None,
        recording_source_start_sample_index=None,
        recording_source_stop_sample_index=None,
        recording_persisted_samples=0,
    )
    assert not status.active
    with pytest.raises(ValueError):
        replace(status, elapsed_seconds=-1.0)
    with pytest.raises(ValueError):
        replace(status, recording_persisted_samples=-1)
    with pytest.raises(ValueError):
        replace(status, selected_channel_id="a")

    class CloseOnlyDaq:
        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    daq = CloseOnlyDaq()
    coordinator = OpenAcquisitionCoordinator(
        daq,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
    )
    assert coordinator.configuration.visible_window_seconds == 5.0
    assert coordinator.build_plot_frame() is None
    assert coordinator.stop_recording().recording_state is OpenRecordingState.INACTIVE
    assert coordinator.set_visible_window(2.0).visible_window_seconds == 2.0
    with pytest.raises(ValueError):
        coordinator.set_visible_window(11.0)
    with pytest.raises(TimeoutError):
        coordinator.wait_for_recording(0.0)
    with pytest.raises(TimeoutError):
        coordinator.wait_for_terminal(0.0)
    with pytest.raises(Exception, match="requires active monitoring"):
        coordinator.start_recording()
    with pytest.raises(Exception, match="requires active monitoring"):
        coordinator.end_monitoring()

    selected = synthetic_experiment.acquisition.channel_ids[0]
    intent = OpenAcquisitionIntent(
        profile=baseline_profile,
        experiment=synthetic_experiment,
        selected_channel_id=selected,
        authorization=RawDiagnosticAuthorization(
            confirmation=RawDiagnosticConfirmation(
                operator="operator",
                confirmed_at=datetime.now(UTC),
                measured_supply_voltage_v=3.3,
                polarity_confirmed=True,
                ai3_connection="disconnected",
            ),
            hardware_validation_sha256="b" * 64,
            selected_device_id="device",
            selected_module_id="module",
            ordered_physical_channels=("module/ai0",),
            workflow="open-acquisition",
            selected_channel_id=selected,
        ),
        eligibility_evaluated_at=datetime.now(UTC),
    )
    coordinator.close()
    coordinator.close()
    assert daq.close_calls == 1
    with pytest.raises(Exception, match="closed"):
        coordinator.start_monitoring(intent)
