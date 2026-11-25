from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from labdaq.application.processing_service import RecordedSessionAnalysisService
from labdaq.domain.enums import (
    ChannelRole,
    MetricAvailability,
    ProcessingDisposition,
    SignalStage,
    Unit,
)
from labdaq.domain.models import ProcessingRange
from labdaq.ports.acquisition import AcquisitionChunk
from labdaq.ports.processing import (
    ChannelExtremaResult,
    DetectedTransientEvent,
    LocatedExtremum,
    ProcessingSourceMetadata,
    SessionChannelMetadata,
    TransientChannelThreshold,
    TransientDetectionResult,
    TransientDetectionSettings,
)


class _Replay:
    def __init__(self, samples: np.ndarray, chunk_size: int) -> None:
        self._samples = samples
        self._chunk_size = chunk_size
        self._cancelled = False

    def __iter__(self):
        for sequence, start in enumerate(range(0, self._samples.shape[1], self._chunk_size)):
            if self._cancelled:
                return
            values = np.array(
                self._samples[:, start : start + self._chunk_size],
                dtype=np.float64,
                copy=True,
                order="C",
            )
            values.flags.writeable = False
            yield AcquisitionChunk(
                session_id="review-analysis",
                sequence_number=sequence,
                start_sample_index=start,
                sample_count=values.shape[1],
                sample_rate_hz=1000.0,
                ordered_channel_ids=("a", "b"),
                ordered_channel_units=("V", "V"),
                samples=values,
                read_completed_monotonic=(start + values.shape[1]) / 1000.0,
            )

    def cancel(self) -> None:
        self._cancelled = True

    def close(self) -> None:
        self._cancelled = True


class _Source:
    def __init__(self, samples: np.ndarray) -> None:
        self.samples = samples

    def processing_metadata(self, path: Path) -> ProcessingSourceMetadata:
        del path
        channels = tuple(
            SessionChannelMetadata(
                channel_id=channel_id,
                display_name=channel_id.upper(),
                physical_channel=f"cDAQ/ai{position}",
                raw_unit=Unit.VOLT,
                channel_role=role,
                sensor_id=f"sensor-{channel_id}",
                sensor_model="synthetic",
                sensor_serial_number=channel_id,
                engineering_unit=Unit.STANDARD_GRAVITY,
                calibration=None,
                processing_disposition=ProcessingDisposition.CALIBRATED,
            )
            for position, (channel_id, role) in enumerate(
                (
                    ("a", ChannelRole.ACCELEROMETER_X),
                    ("b", ChannelRole.ACCELEROMETER_Y),
                )
            )
        )
        return ProcessingSourceMetadata(
            session_path=Path("session"),
            session_id="review-analysis",
            completion_state="completed",
            raw_data_digest="a" * 64,
            manifest_path=Path("session/manifest.json"),
            manifest_sha256="b" * 64,
            source_schema_version=2,
            source_application_version="0.1.0",
            readable_sample_count=self.samples.shape[1],
            sample_rate_hz=1000.0,
            ordered_channel_ids=("a", "b"),
            channels=channels,
            nominal_calibration_fallback=None,
        )

    def open_replay(self, path: Path, *, chunk_size: int) -> _Replay:
        del path
        return _Replay(self.samples, chunk_size)


def _service(samples: np.ndarray) -> RecordedSessionAnalysisService:
    return RecordedSessionAnalysisService(
        _Source(samples),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
    )


def test_transient_settings_reject_invalid_values() -> None:
    with pytest.raises(ValueError, match="mad_multiplier"):
        TransientDetectionSettings(mad_multiplier=0)
    with pytest.raises(ValueError, match="pre_padding"):
        TransientDetectionSettings(pre_padding_seconds=-0.1)
    with pytest.raises(ValueError, match="post_padding"):
        TransientDetectionSettings(post_padding_seconds=float("inf"))


def test_review_analysis_records_fail_closed_on_invalid_provenance() -> None:
    settings = TransientDetectionSettings()
    threshold = TransientChannelThreshold("a", Unit.VOLT, 0.0, 1.0, 6.0)
    event = DetectedTransientEvent(
        "a",
        Unit.VOLT,
        ProcessingRange(10, 20),
        ProcessingRange(5, 25),
        12,
        0.012,
        8.0,
        8.0,
    )
    result = TransientDetectionResult(
        SignalStage.RAW,
        ProcessingRange(0, 100),
        1000.0,
        settings,
        (threshold,),
        (event,),
    )
    available = LocatedExtremum(MetricAvailability.AVAILABLE, -2.0, 2.0, 12, 0.012)

    with pytest.raises(ValueError, match="finite values"):
        TransientChannelThreshold("a", Unit.VOLT, None, 1.0, 6.0)
    with pytest.raises(ValueError, match="non-negative"):
        TransientChannelThreshold("a", Unit.VOLT, 0.0, -1.0, 6.0)
    with pytest.raises(ValueError, match="must not carry"):
        TransientChannelThreshold("a", Unit.VOLT, 0.0, 1.0, 6.0, "unavailable")
    with pytest.raises(ValueError, match="channel identifier"):
        replace(event, channel_id="")
    with pytest.raises(ValueError, match="contain"):
        replace(event, analysis_range=ProcessingRange(11, 25))
    with pytest.raises(ValueError, match="peak must be"):
        replace(event, peak_sample_index=20)
    with pytest.raises(ValueError, match="peak details"):
        replace(event, normalized_peak_score=float("nan"))
    with pytest.raises(ValueError, match="sample rate"):
        replace(result, sample_rate_hz=0.0)
    with pytest.raises(ValueError, match="unique channels"):
        replace(result, thresholds=())
    with pytest.raises(ValueError, match="threshold channel"):
        replace(result, events=(replace(event, channel_id="b"),))
    with pytest.raises(ValueError, match="finite values"):
        LocatedExtremum(MetricAvailability.AVAILABLE, None, None, None, None)
    with pytest.raises(ValueError, match="metadata"):
        replace(available, magnitude=-1.0)
    with pytest.raises(ValueError, match="must not carry"):
        replace(available, availability=MetricAvailability.UNAVAILABLE)
    with pytest.raises(ValueError, match="identity"):
        ChannelExtremaResult(
            "",
            Unit.VOLT,
            SignalStage.RAW,
            ProcessingRange(0, 1),
            1000.0,
            available,
            available,
            available,
        )


def test_transient_detection_is_per_channel_padded_and_chunk_invariant() -> None:
    samples = np.zeros((2, 100), dtype=np.float64)
    samples[0, 10:12] = [3.0, 5.0]
    samples[0, 13] = 4.0
    samples[1, 50:52] = [-7.0, -2.0]
    settings = TransientDetectionSettings(
        mad_multiplier=6.0,
        pre_padding_seconds=0.002,
        post_padding_seconds=0.003,
        merge_gap_seconds=0.001,
    )
    service = _service(samples)

    first = service.detect_transients(
        Path("session"),
        ordered_channel_ids=("a", "b"),
        signal_stage=SignalStage.RAW,
        settings=settings,
        chunk_size=7,
    )
    second = service.detect_transients(
        Path("session"),
        ordered_channel_ids=("a", "b"),
        signal_stage=SignalStage.RAW,
        settings=settings,
        chunk_size=31,
    )

    assert first == second
    assert [event.channel_id for event in first.events] == ["a", "b"]
    assert first.events[0].crossing_range == ProcessingRange(10, 14)
    assert first.events[0].analysis_range == ProcessingRange(8, 17)
    assert first.events[0].peak_sample_index == 11
    assert first.events[0].normalized_peak_score == float("inf")
    assert first.events[1].analysis_range == ProcessingRange(48, 55)


def test_extrema_retain_signed_absolute_peak_and_earliest_ties() -> None:
    samples = np.zeros((2, 12), dtype=np.float64)
    samples[0, 2:9] = [1.0, 5.0, 5.0, -8.0, -8.0, 2.0, 0.0]
    samples[1, 2:9] = [-1.0, -2.0, 3.0, 3.0, -2.0, 1.0, 0.0]

    results = _service(samples).compute_extrema(
        Path("session"),
        ordered_channel_ids=("a", "b"),
        signal_stage=SignalStage.RAW,
        sample_range=ProcessingRange(2, 9),
        chunk_size=3,
    )

    first = results[0]
    assert first.maximum.value == 5.0
    assert first.maximum.sample_index == 3
    assert first.minimum.value == -8.0
    assert first.minimum.sample_index == 5
    assert first.absolute_peak.value == -8.0
    assert first.absolute_peak.magnitude == 8.0
    assert first.absolute_peak.relative_time_seconds == 0.005


def test_non_finite_channel_does_not_hide_valid_extrema() -> None:
    samples = np.arange(20, dtype=np.float64).reshape(2, 10)
    samples[1, 4] = np.nan

    results = _service(samples).compute_extrema(
        Path("session"),
        ordered_channel_ids=("a", "b"),
        signal_stage=SignalStage.RAW,
        sample_range=ProcessingRange(0, 10),
        chunk_size=4,
    )

    assert results[0].maximum.availability is MetricAvailability.AVAILABLE
    assert results[0].maximum.sample_index == 9
    assert results[1].maximum.availability is MetricAvailability.UNAVAILABLE
    assert results[1].maximum.reason == "signal contains non-finite samples"


def test_detection_marks_non_finite_samples_omitted_from_baseline_sample() -> None:
    sample_count = 262_145
    samples = np.zeros((2, sample_count), dtype=np.float64)
    sampled = (np.arange(262_144, dtype=np.int64) * (sample_count - 1)) // (262_144 - 1)
    missing_index = int(np.setdiff1d(np.arange(sample_count), sampled)[0])
    samples[0, missing_index] = np.nan

    result = _service(samples).detect_transients(
        Path("session"),
        ordered_channel_ids=("a", "b"),
        signal_stage=SignalStage.RAW,
        settings=TransientDetectionSettings(),
        chunk_size=8192,
    )

    assert result.thresholds[0].reason == "signal contains non-finite samples"
    assert result.thresholds[1].reason is None
    assert not result.events
