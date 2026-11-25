"""Hardware- and storage-neutral contracts for DAQ-M06 processing."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Protocol

import numpy as np

from labdaq.domain.enums import (
    ContinuityStatus,
    MetricAvailability,
    ProcessingDisposition,
    SignalStage,
    Unit,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from labdaq.domain.enums import ChannelRole, ProcessingMode
    from labdaq.domain.models import (
        CalibrationConfig,
        NominalCalibrationFallbackPolicy,
        ProcessingNotice,
        ProcessingProvenance,
        ProcessingRange,
        ProcessingRecipeEnvelope,
        ResolvedChannelCalibration,
    )
    from labdaq.ports.acquisition import AcquisitionChunk
    from labdaq.ports.session import ReplayStream


def _validate_vector(values: np.ndarray, expected: int, field: str) -> None:
    if not isinstance(values, np.ndarray) or values.dtype != np.float64:
        raise ValueError(f"{field} must be a float64 NumPy array")
    if values.ndim != 1 or values.shape != (expected,):
        raise ValueError(f"{field} must have shape ({expected},)")
    if values.flags.writeable:
        raise ValueError(f"{field} must be read-only")


@dataclass(frozen=True, slots=True)
class SessionChannelMetadata:
    channel_id: str
    display_name: str
    physical_channel: str
    raw_unit: Unit
    channel_role: ChannelRole
    sensor_id: str | None
    sensor_model: str | None
    sensor_serial_number: str | None
    engineering_unit: Unit
    calibration: CalibrationConfig | None
    processing_disposition: ProcessingDisposition = ProcessingDisposition.CALIBRATED


@dataclass(frozen=True, slots=True)
class ProcessingSourceMetadata:
    session_path: Path
    session_id: str
    completion_state: str
    raw_data_digest: str
    manifest_path: Path
    manifest_sha256: str
    source_schema_version: int
    source_application_version: str
    readable_sample_count: int
    sample_rate_hz: float
    ordered_channel_ids: tuple[str, ...]
    channels: tuple[SessionChannelMetadata, ...]
    nominal_calibration_fallback: NominalCalibrationFallbackPolicy | None
    session_type: str = "experiment"

    def __post_init__(self) -> None:
        if self.readable_sample_count <= 0 or not math.isfinite(self.sample_rate_hz):
            raise ValueError("processing source requires readable samples and a finite rate")
        if self.sample_rate_hz <= 0 or len(self.channels) != len(self.ordered_channel_ids):
            raise ValueError("processing source channel metadata does not align")
        if len(self.raw_data_digest) != 64 or len(self.manifest_sha256) != 64:
            raise ValueError("processing source requires SHA-256 integrity identities")


@dataclass(frozen=True, slots=True)
class RecordedSignalChunk:
    """One aligned source span at an explicitly requested processing stage."""

    session_id: str
    sequence_number: int
    start_sample_index: int
    sample_count: int
    sample_rate_hz: float
    ordered_channel_ids: tuple[str, ...]
    signal_stage: SignalStage
    output_units: tuple[Unit, ...]
    raw_samples: np.ndarray
    calibrated_samples: np.ndarray | None = None
    conditioned_samples: np.ndarray | None = None
    notices: tuple[ProcessingNotice, ...] = ()

    def __post_init__(self) -> None:
        channels = len(self.ordered_channel_ids)
        expected = (channels, self.sample_count)
        if self.sample_count <= 0 or self.start_sample_index < 0:
            raise ValueError("recorded signal chunk sample bounds are invalid")
        if len(self.output_units) != channels:
            raise ValueError("recorded signal units must align with channel order")
        for field in ("raw_samples", "calibrated_samples", "conditioned_samples"):
            values = getattr(self, field)
            if values is None:
                continue
            if not isinstance(values, np.ndarray) or values.dtype != np.float64:
                raise ValueError(f"{field} must be a float64 NumPy array")
            if values.shape != expected or values.flags.writeable:
                raise ValueError(f"{field} must be a read-only array with shape {expected}")
        if self.signal_stage is SignalStage.RAW:
            if self.calibrated_samples is not None or self.conditioned_samples is not None:
                raise ValueError("raw signal chunks must not claim derived samples")
        elif self.calibrated_samples is None:
            raise ValueError("calibrated and conditioned chunks require calibrated samples")
        elif self.signal_stage is SignalStage.CALIBRATED and self.conditioned_samples is not None:
            raise ValueError("calibrated chunks must not claim conditioned samples")
        elif self.signal_stage is SignalStage.CONDITIONED and self.conditioned_samples is None:
            raise ValueError("conditioned chunks require conditioned samples")

    @property
    def samples(self) -> np.ndarray:
        if self.signal_stage is SignalStage.RAW:
            return self.raw_samples
        if self.signal_stage is SignalStage.CALIBRATED:
            assert self.calibrated_samples is not None
            return self.calibrated_samples
        assert self.conditioned_samples is not None
        return self.conditioned_samples


@dataclass(frozen=True, slots=True)
class ProcessedChunk:
    session_id: str
    sequence_number: int
    start_sample_index: int
    sample_count: int
    sample_rate_hz: float
    ordered_channel_ids: tuple[str, ...]
    output_units: tuple[Unit, ...]
    calibration_ids: tuple[str, ...]
    calibration_hashes: tuple[str, ...]
    recipe_id: str
    recipe_hash: str
    calibrated_samples: np.ndarray
    conditioned_samples: np.ndarray
    continuity: ContinuityStatus
    notices: tuple[ProcessingNotice, ...] = ()

    def __post_init__(self) -> None:
        channels = len(self.ordered_channel_ids)
        expected = (channels, self.sample_count)
        if self.sample_count <= 0 or self.start_sample_index < 0:
            raise ValueError("processed chunk sample bounds are invalid")
        for field in ("output_units", "calibration_ids", "calibration_hashes"):
            if len(getattr(self, field)) != channels:
                raise ValueError(f"{field} must align with channel order")
        for field in ("calibrated_samples", "conditioned_samples"):
            values = getattr(self, field)
            if not isinstance(values, np.ndarray) or values.dtype != np.float64:
                raise ValueError(f"{field} must be a float64 NumPy array")
            if values.shape != expected or values.flags.writeable:
                raise ValueError(f"{field} must be a read-only array with shape {expected}")


@dataclass(frozen=True, slots=True)
class MetricValue:
    availability: MetricAvailability
    value: float | None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.availability is MetricAvailability.AVAILABLE:
            if self.value is None or not math.isfinite(self.value):
                raise ValueError("available metrics require a finite value")
        elif self.value is not None:
            raise ValueError("unavailable metrics must not carry a numeric value")

    def to_dict(self) -> dict[str, object]:
        return {
            "availability": self.availability.value,
            "value": self.value,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ChannelMetricResult:
    channel_id: str
    unit: Unit
    signal_stage: SignalStage
    sample_range: ProcessingRange
    sample_count: int
    sample_rate_hz: float
    recipe_hash: str
    calibration_hash: str
    mean: MetricValue
    rms: MetricValue
    minimum: MetricValue
    maximum: MetricValue
    absolute_peak: MetricValue
    peak_to_peak: MetricValue
    crest_factor: MetricValue
    clipping_fraction: MetricValue
    notices: tuple[ProcessingNotice, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "channel_id": self.channel_id,
            "unit": self.unit.value,
            "signal_stage": self.signal_stage.value,
            "start_sample_index": self.sample_range.start_sample_index,
            "stop_sample_index": self.sample_range.stop_sample_index,
            "sample_count": self.sample_count,
            "sample_rate_hz": self.sample_rate_hz,
            "recipe_hash": self.recipe_hash,
            "calibration_hash": self.calibration_hash,
            "mean": self.mean.to_dict(),
            "rms": self.rms.to_dict(),
            "minimum": self.minimum.to_dict(),
            "maximum": self.maximum.to_dict(),
            "absolute_peak": self.absolute_peak.to_dict(),
            "peak_to_peak": self.peak_to_peak.to_dict(),
            "crest_factor": self.crest_factor.to_dict(),
            "clipping_fraction": self.clipping_fraction.to_dict(),
            "notices": [asdict(notice) for notice in self.notices],
        }


@dataclass(frozen=True, slots=True)
class TransientDetectionSettings:
    """Operator-visible controls for deterministic transient suggestions."""

    mad_multiplier: float = 6.0
    pre_padding_seconds: float = 0.05
    post_padding_seconds: float = 0.05
    merge_gap_seconds: float = 0.02

    def __post_init__(self) -> None:
        if not math.isfinite(self.mad_multiplier) or self.mad_multiplier <= 0:
            raise ValueError("mad_multiplier must be finite and positive")
        for field in (
            "pre_padding_seconds",
            "post_padding_seconds",
            "merge_gap_seconds",
        ):
            value = getattr(self, field)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{field} must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class TransientChannelThreshold:
    channel_id: str
    unit: Unit
    baseline_median: float | None
    robust_scale: float | None
    threshold: float | None
    reason: str | None = None

    def __post_init__(self) -> None:
        values = (self.baseline_median, self.robust_scale, self.threshold)
        if self.reason is None:
            if any(value is None or not math.isfinite(value) for value in values):
                raise ValueError("available transient thresholds require finite values")
            assert self.robust_scale is not None and self.threshold is not None
            if self.robust_scale < 0 or self.threshold < 0:
                raise ValueError("transient scales and thresholds must be non-negative")
        elif any(value is not None for value in values):
            raise ValueError("unavailable transient thresholds must not carry values")


@dataclass(frozen=True, slots=True)
class DetectedTransientEvent:
    channel_id: str
    unit: Unit
    crossing_range: ProcessingRange
    analysis_range: ProcessingRange
    peak_sample_index: int
    peak_time_seconds: float
    peak_deviation: float
    normalized_peak_score: float

    def __post_init__(self) -> None:
        if not self.channel_id:
            raise ValueError("detected transient requires a channel identifier")
        if not (
            self.analysis_range.start_sample_index
            <= self.crossing_range.start_sample_index
            < self.crossing_range.stop_sample_index
            <= self.analysis_range.stop_sample_index
        ):
            raise ValueError("transient analysis range must contain its crossing range")
        if not (
            self.crossing_range.start_sample_index
            <= self.peak_sample_index
            < self.crossing_range.stop_sample_index
        ):
            raise ValueError("transient peak must be inside its crossing range")
        if (
            not math.isfinite(self.peak_time_seconds)
            or self.peak_time_seconds < 0
            or not math.isfinite(self.peak_deviation)
            or self.peak_deviation < 0
            or math.isnan(self.normalized_peak_score)
            or self.normalized_peak_score < 0
        ):
            raise ValueError("transient peak details are invalid")


@dataclass(frozen=True, slots=True)
class TransientDetectionResult:
    signal_stage: SignalStage
    sample_range: ProcessingRange
    sample_rate_hz: float
    settings: TransientDetectionSettings
    thresholds: tuple[TransientChannelThreshold, ...]
    events: tuple[DetectedTransientEvent, ...]
    notices: tuple[ProcessingNotice, ...] = ()

    def __post_init__(self) -> None:
        if not math.isfinite(self.sample_rate_hz) or self.sample_rate_hz <= 0:
            raise ValueError("transient result sample rate must be finite and positive")
        channel_ids = tuple(item.channel_id for item in self.thresholds)
        if not channel_ids or len(channel_ids) != len(set(channel_ids)):
            raise ValueError("transient thresholds require unique channels")
        if any(event.channel_id not in channel_ids for event in self.events):
            raise ValueError("transient events must belong to a threshold channel")


@dataclass(frozen=True, slots=True)
class LocatedExtremum:
    availability: MetricAvailability
    value: float | None
    magnitude: float | None
    sample_index: int | None
    relative_time_seconds: float | None
    reason: str | None = None

    def __post_init__(self) -> None:
        values = (self.value, self.magnitude, self.relative_time_seconds)
        if self.availability is MetricAvailability.AVAILABLE:
            if (
                any(value is None or not math.isfinite(value) for value in values)
                or self.sample_index is None
                or self.sample_index < 0
            ):
                raise ValueError("available extrema require finite values and a sample index")
            assert self.magnitude is not None and self.relative_time_seconds is not None
            if self.magnitude < 0 or self.relative_time_seconds < 0 or self.reason is not None:
                raise ValueError("available extremum metadata is invalid")
        elif any(value is not None for value in values) or self.sample_index is not None:
            raise ValueError("unavailable extrema must not carry sample details")


@dataclass(frozen=True, slots=True)
class ChannelExtremaResult:
    channel_id: str
    unit: Unit
    signal_stage: SignalStage
    sample_range: ProcessingRange
    sample_rate_hz: float
    maximum: LocatedExtremum
    minimum: LocatedExtremum
    absolute_peak: LocatedExtremum
    notices: tuple[ProcessingNotice, ...] = ()

    def __post_init__(self) -> None:
        if (
            not self.channel_id
            or not math.isfinite(self.sample_rate_hz)
            or self.sample_rate_hz <= 0
        ):
            raise ValueError("channel extrema identity is invalid")


@dataclass(frozen=True, slots=True)
class FftAmplitudeResult:
    channel_id: str
    unit: Unit
    sample_range: ProcessingRange
    frequencies_hz: np.ndarray
    amplitudes: np.ndarray
    recipe_hash: str
    calibration_hash: str | None
    signal_stage: SignalStage = SignalStage.CONDITIONED
    detrend: str = "mean"
    window: str = "hann-periodic"
    normalization: str = "coherent-gain-one-sided-amplitude"

    def __post_init__(self) -> None:
        _validate_vector(self.frequencies_hz, len(self.amplitudes), "frequencies_hz")
        _validate_vector(self.amplitudes, len(self.frequencies_hz), "amplitudes")
        _validate_spectral_calibration(self.signal_stage, self.calibration_hash)


@dataclass(frozen=True, slots=True)
class PsdResult:
    channel_id: str
    input_unit: Unit
    density_unit: str
    sample_range: ProcessingRange
    frequencies_hz: np.ndarray
    density: np.ndarray
    segment_sample_count: int
    overlap_sample_count: int
    recipe_hash: str
    calibration_hash: str | None
    signal_stage: SignalStage = SignalStage.CONDITIONED
    detrend: str = "constant"
    window: str = "hann-periodic"
    scaling: str = "density"

    def __post_init__(self) -> None:
        _validate_vector(self.frequencies_hz, len(self.density), "frequencies_hz")
        _validate_vector(self.density, len(self.frequencies_hz), "density")
        _validate_spectral_calibration(self.signal_stage, self.calibration_hash)


def _validate_spectral_calibration(stage: SignalStage, calibration_hash: str | None) -> None:
    if stage is SignalStage.RAW:
        if calibration_hash is not None:
            raise ValueError("raw spectra must not claim a calibration hash")
    elif calibration_hash is None or len(calibration_hash) != 64:
        raise ValueError("calibrated and conditioned spectra require a calibration hash")


@dataclass(frozen=True, slots=True)
class ProcessedSessionSummary:
    provenance: ProcessingProvenance
    metrics: tuple[ChannelMetricResult, ...]
    fft: tuple[FftAmplitudeResult, ...]
    psd: tuple[PsdResult, ...]

    def to_dict(self) -> dict[str, object]:
        fft_summary = []
        for result in self.fft:
            peak = int(np.argmax(result.amplitudes))
            fft_summary.append(
                {
                    "channel_id": result.channel_id,
                    "unit": result.unit.value,
                    "signal_stage": result.signal_stage.value,
                    "peak_frequency_hz": float(result.frequencies_hz[peak]),
                    "peak_amplitude": float(result.amplitudes[peak]),
                    "bin_count": len(result.frequencies_hz),
                }
            )
        psd_summary = []
        for psd_result in self.psd:
            peak = int(np.argmax(psd_result.density))
            integrated = float(np.trapezoid(psd_result.density, psd_result.frequencies_hz))
            psd_summary.append(
                {
                    "channel_id": psd_result.channel_id,
                    "density_unit": psd_result.density_unit,
                    "signal_stage": psd_result.signal_stage.value,
                    "peak_frequency_hz": float(psd_result.frequencies_hz[peak]),
                    "peak_density": float(psd_result.density[peak]),
                    "integrated_power": integrated,
                    "segment_sample_count": psd_result.segment_sample_count,
                }
            )
        return {
            "provenance": {
                "schema_version": self.provenance.schema_version,
                "provenance_hash": self.provenance.provenance_hash,
                "source_session_id": self.provenance.source_session_id,
                "source_completion_state": self.provenance.source_completion_state,
                "source_raw_digest": self.provenance.source_raw_digest,
                "start_sample_index": self.provenance.source_range.start_sample_index,
                "stop_sample_index": self.provenance.source_range.stop_sample_index,
                "ordered_channel_ids": list(self.provenance.ordered_channel_ids),
                "sample_rate_hz": self.provenance.sample_rate_hz,
                "calibration_ids": list(self.provenance.calibration_ids),
                "calibration_hashes": list(self.provenance.calibration_hashes),
                "calibration_qualities": [
                    quality.value for quality in self.provenance.calibration_qualities
                ],
                "recipe_id": self.provenance.recipe_id,
                "recipe_version": self.provenance.recipe_version,
                "recipe_hash": self.provenance.recipe_hash,
                "processing_mode": self.provenance.processing_mode.value,
                "application_version": self.provenance.application_version,
                "numpy_version": self.provenance.numpy_version,
                "scipy_version": self.provenance.scipy_version,
                "output_units": [unit.value for unit in self.provenance.output_units],
                "notices": [notice.code for notice in self.provenance.notices],
                "algorithm_details": dict(self.provenance.algorithm_details),
            },
            "metrics": [metric.to_dict() for metric in self.metrics],
            "fft": fft_summary,
            "psd": psd_summary,
        }


class StreamingProcessorPort(Protocol):
    @property
    def recipe_hash(self) -> str: ...

    @property
    def numpy_version(self) -> str: ...

    @property
    def scipy_version(self) -> str: ...

    @property
    def algorithm_details(self) -> Mapping[str, object]: ...

    def process(self, chunk: AcquisitionChunk) -> ProcessedChunk: ...

    def reset(self) -> ProcessingNotice: ...


class WelchAccumulatorPort(Protocol):
    def append(self, samples: np.ndarray) -> None: ...

    def finalize(self) -> PsdResult: ...


class ScientificProcessingPort(Protocol):
    def calibrate_samples(
        self,
        samples: np.ndarray,
        calibrations: tuple[ResolvedChannelCalibration, ...],
    ) -> np.ndarray: ...

    def create_stream(
        self,
        recipe: ProcessingRecipeEnvelope,
        calibrations: tuple[ResolvedChannelCalibration, ...],
        *,
        sample_rate_hz: float,
        mode: ProcessingMode,
    ) -> StreamingProcessorPort: ...

    def metrics(
        self,
        raw_samples: np.ndarray,
        signal_samples: np.ndarray,
        *,
        channel_id: str,
        unit: Unit,
        sample_range: ProcessingRange,
        sample_rate_hz: float,
        recipe_hash: str,
        calibration: ResolvedChannelCalibration,
        clipping_margin_v: float,
    ) -> ChannelMetricResult: ...

    def fft_amplitude(
        self,
        samples: np.ndarray,
        *,
        channel_id: str,
        unit: Unit,
        sample_range: ProcessingRange,
        sample_rate_hz: float,
        recipe_hash: str,
        calibration_hash: str | None,
        maximum_samples: int,
        signal_stage: SignalStage = SignalStage.CONDITIONED,
    ) -> FftAmplitudeResult: ...

    def welch_psd(
        self,
        samples: np.ndarray,
        *,
        channel_id: str,
        unit: Unit,
        sample_range: ProcessingRange,
        sample_rate_hz: float,
        recipe_hash: str,
        calibration_hash: str | None,
        segment_duration_seconds: float,
        overlap_fraction: float,
        signal_stage: SignalStage = SignalStage.CONDITIONED,
    ) -> PsdResult: ...

    def create_welch_accumulator(
        self,
        *,
        channel_id: str,
        unit: Unit,
        sample_range: ProcessingRange,
        sample_rate_hz: float,
        recipe_hash: str,
        calibration_hash: str | None,
        segment_duration_seconds: float,
        overlap_fraction: float,
        signal_stage: SignalStage = SignalStage.CONDITIONED,
    ) -> WelchAccumulatorPort: ...


class RecipeRepositoryPort(Protocol):
    def load_bundled_processing_recipe(self, recipe_id: str) -> ProcessingRecipeEnvelope: ...

    def load_processing_recipe(self, path: Path) -> ProcessingRecipeEnvelope: ...


class ProcessingRecordSerializerPort(Protocol):
    def dumps_calibration(self, value: ResolvedChannelCalibration) -> str: ...

    def loads_calibration(self, payload: str) -> ResolvedChannelCalibration: ...

    def dumps_provenance(self, value: ProcessingProvenance) -> str: ...

    def loads_provenance(self, payload: str) -> ProcessingProvenance: ...


class SessionProcessingSourcePort(Protocol):
    def processing_metadata(self, path: Path) -> ProcessingSourceMetadata: ...

    def open_replay(self, path: Path, *, chunk_size: int) -> ReplayStream: ...
