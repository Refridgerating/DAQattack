from dataclasses import replace
from datetime import UTC, datetime

import numpy as np
import pytest

from labdaq.errors import ErrorSeverity
from labdaq.ports.acquisition import (
    AcquisitionChunk,
    AcquisitionCompletion,
    AcquisitionEvent,
    AcquisitionFaultSummary,
    AcquisitionState,
    AcquisitionStatus,
    CleanupSummary,
    CompletionReason,
)


def _samples(shape: tuple[int, int] = (2, 3)) -> np.ndarray:
    values = np.zeros(shape, dtype=np.float64)
    values.flags.writeable = False
    return values


def _chunk() -> AcquisitionChunk:
    return AcquisitionChunk(
        session_id="session",
        sequence_number=0,
        start_sample_index=0,
        sample_count=3,
        sample_rate_hz=50_000.0,
        ordered_channel_ids=("a", "b"),
        ordered_channel_units=("V", "V"),
        samples=_samples(),
        read_completed_monotonic=1.0,
    )


@pytest.mark.parametrize(
    "change",
    [
        {"session_id": ""},
        {"sequence_number": -1},
        {"start_sample_index": -1},
        {"sample_count": 0},
        {"sample_rate_hz": float("nan")},
        {"ordered_channel_ids": ()},
        {"ordered_channel_units": ("V",)},
        {"samples": [[0.0]]},
        {"samples": np.zeros((2, 3), dtype=np.float32)},
        {"samples": _samples((2, 2))},
        {"samples": np.zeros((2, 3), dtype=np.float64)},
        {"read_completed_monotonic": -1.0},
    ],
)
def test_acquisition_chunk_rejects_invalid_publication(change: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        replace(_chunk(), **change)


def test_event_status_fault_cleanup_and_completion_are_serializable() -> None:
    event = AcquisitionEvent(
        event_sequence=4,
        code="DAQ.ACQUISITION.PROGRESS",
        severity=ErrorSeverity.WARNING,
        session_id="session",
        state=AcquisitionState.RUNNING,
        timestamp=datetime(2026, 8, 12, tzinfo=UTC),
        monotonic_timestamp=1.5,
        message="progress",
        context={"count": 3},
        sample_index=3,
    )
    fault = AcquisitionFaultSummary(
        "DAQ.ACQUISITION.WORKER_FAILURE",
        "failed",
        {"operation": "read"},
    )
    cleanup = CleanupSummary(True, True, False, True)
    status = AcquisitionStatus(
        state=AcquisitionState.RUNNING,
        session_id="session",
        requested_sample_rate_hz=50_000.0,
        actual_sample_rate_hz=50_000.0,
        requested_duration_seconds=1.0,
        target_sample_count=50_000,
        acquired_samples_per_channel=3,
        recording_samples_enqueued=3,
        display_samples_published=3,
        display_frames_published=1,
        display_frames_dropped=0,
        recording_queue_occupancy=1,
        recording_queue_capacity=32,
        display_queue_occupancy=1,
        display_queue_capacity=2,
        stop_requested=False,
        worker_alive=True,
        last_warning_or_fault_code=event.code,
        started_at=datetime(2026, 8, 12, tzinfo=UTC),
        elapsed_seconds=0.25,
        ordered_channel_ids=("a", "b"),
        ordered_channel_units=("V", "V"),
        warning_count=1,
        current_fault=fault,
    )
    completion = AcquisitionCompletion(
        terminal_state=AcquisitionState.FAULTED,
        reason=CompletionReason.ACQUISITION_FAULT,
        session_id="session",
        started_at=datetime(2026, 8, 12, tzinfo=UTC),
        stopped_at=datetime(2026, 8, 12, 0, 0, 1, tzinfo=UTC),
        total_acquired_samples_per_channel=3,
        total_recording_samples_accepted=3,
        final_sequence_number=0,
        display_frames_published=1,
        display_frames_dropped=0,
        warnings=(event,),
        fault=fault,
        cleanup=cleanup,
    )

    assert event.to_dict()["context"] == {"count": 3}
    assert fault.to_dict()["code"] == fault.code
    assert cleanup.succeeded
    assert cleanup.to_dict()["succeeded"] is True
    assert status.to_dict()["state"] == "running"
    assert status.to_dict()["ordered_channel_ids"] == ["a", "b"]
    assert status.to_dict()["current_fault"]["code"] == fault.code
    payload = completion.to_dict()
    assert payload["terminal_state"] == "faulted"
    assert payload["fault"]["code"] == fault.code
    assert payload["warnings"][0]["event_sequence"] == 4


def test_acquisition_status_rejects_invalid_m10_fields() -> None:
    base = AcquisitionStatus(
        state=AcquisitionState.IDLE,
        session_id=None,
        requested_sample_rate_hz=None,
        actual_sample_rate_hz=None,
        requested_duration_seconds=None,
        target_sample_count=None,
        acquired_samples_per_channel=0,
        recording_samples_enqueued=0,
        display_samples_published=0,
        display_frames_published=0,
        display_frames_dropped=0,
        recording_queue_occupancy=0,
        recording_queue_capacity=0,
        display_queue_occupancy=0,
        display_queue_capacity=0,
        stop_requested=False,
        worker_alive=False,
        last_warning_or_fault_code=None,
    )
    with pytest.raises(ValueError, match="elapsed"):
        replace(base, elapsed_seconds=-1.0)
    with pytest.raises(ValueError, match="align"):
        replace(base, ordered_channel_ids=("a",))
    with pytest.raises(ValueError, match="timezone"):
        replace(base, started_at=datetime(2026, 8, 12))


@pytest.mark.parametrize(
    "change",
    [
        {"event_sequence": -1},
        {"code": "INVALID"},
        {"session_id": ""},
        {"timestamp": datetime(2026, 8, 12)},
        {"monotonic_timestamp": -1.0},
        {"message": ""},
        {"context": {"samples": np.zeros(1)}},
    ],
)
def test_acquisition_event_rejects_invalid_serialization_fields(
    change: dict[str, object],
) -> None:
    event = AcquisitionEvent(
        event_sequence=0,
        code="DAQ.ACQUISITION.PROGRESS",
        severity=ErrorSeverity.INFO,
        session_id="session",
        state=AcquisitionState.RUNNING,
        timestamp=datetime(2026, 8, 12, tzinfo=UTC),
        monotonic_timestamp=0.0,
        message="progress",
        context={},
    )
    with pytest.raises(ValueError):
        replace(event, **change)


def test_completion_requires_terminal_state_and_aware_timestamps() -> None:
    cleanup = CleanupSummary(True, False, True, True)
    assert cleanup.succeeded
    base = AcquisitionCompletion(
        terminal_state=AcquisitionState.COMPLETED,
        reason=CompletionReason.DURATION_REACHED,
        session_id="session",
        started_at=None,
        stopped_at=datetime(2026, 8, 12, tzinfo=UTC),
        total_acquired_samples_per_channel=0,
        total_recording_samples_accepted=0,
        final_sequence_number=None,
        display_frames_published=0,
        display_frames_dropped=0,
        warnings=(),
        fault=None,
        cleanup=cleanup,
    )

    with pytest.raises(ValueError):
        replace(base, terminal_state=AcquisitionState.RUNNING)
    with pytest.raises(ValueError):
        replace(base, stopped_at=datetime(2026, 8, 12))
    with pytest.raises(ValueError):
        replace(base, started_at=datetime(2026, 8, 12))
