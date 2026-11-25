"""Deterministic numerical implementation of the DAQ-M06 baseline recipe."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

import numpy as np
import scipy  # type: ignore[import-untyped]
from scipy import signal

from labdaq.domain.enums import (
    ChannelRole,
    ContinuityStatus,
    MetricAvailability,
    ProcessingMode,
    ProcessingStageKind,
    RecipeStatus,
    SignalStage,
)
from labdaq.domain.models import ProcessingNotice, ProcessingRange
from labdaq.domain.serialization import canonical_sha256, canonical_sha256_excluding
from labdaq.errors import ErrorSeverity, ProcessingError
from labdaq.ports.processing import (
    ChannelMetricResult,
    FftAmplitudeResult,
    MetricValue,
    ProcessedChunk,
    PsdResult,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from labdaq.domain.enums import Unit
    from labdaq.domain.models import ProcessingRecipeEnvelope, ResolvedChannelCalibration
    from labdaq.ports.acquisition import AcquisitionChunk


_ACCELEROMETER_ROLES = {
    ChannelRole.ACCELEROMETER_X,
    ChannelRole.ACCELEROMETER_Y,
    ChannelRole.ACCELEROMETER_Z,
}


@dataclass(slots=True)
class _FilterStageState:
    stage_id: str
    kind: ProcessingStageKind
    order: int
    cutoff_hz: float
    sos: np.ndarray
    zi: np.ndarray | None = None


class BaselineScientificProcessing:
    """Create stateful streams and deterministic analysis products."""

    def calibrate_samples(
        self,
        samples: np.ndarray,
        calibrations: tuple[ResolvedChannelCalibration, ...],
    ) -> np.ndarray:
        return _calibrate_samples(samples, calibrations)

    def create_stream(
        self,
        recipe: ProcessingRecipeEnvelope,
        calibrations: tuple[ResolvedChannelCalibration, ...],
        *,
        sample_rate_hz: float,
        mode: ProcessingMode,
    ) -> BaselineStreamingProcessor:
        return BaselineStreamingProcessor(
            recipe,
            calibrations,
            sample_rate_hz=sample_rate_hz,
            mode=mode,
        )

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
    ) -> ChannelMetricResult:
        raw = _readonly_vector_input(raw_samples, channel_id)
        values = _readonly_vector_input(signal_samples, channel_id)
        if len(raw) != len(values) or len(values) != sample_range.sample_count:
            raise ProcessingError(
                "Metric inputs do not match the declared sample range.",
                code="DAQ.PROCESSING.METRIC_NOT_EVALUATED",
                context={"channel_id": channel_id},
                remediation="Supply aligned raw and conditioned samples for the requested range.",
            )
        notices: tuple[ProcessingNotice, ...] = ()
        unavailable = len(values) == 0 or not np.all(np.isfinite(values))
        if unavailable:
            reason = "signal is empty" if len(values) == 0 else "signal contains non-finite samples"
            value = MetricValue(
                MetricAvailability.UNAVAILABLE,
                None,
                reason,
            )
            mean = rms = minimum = maximum = absolute_peak = peak_to_peak = crest = value
            notices = (
                ProcessingNotice(
                    code="DAQ.PROCESSING.METRIC_NOT_EVALUATED",
                    severity=ErrorSeverity.WARNING.value,
                    message=f"Metrics are unavailable because the {reason}.",
                    channel_id=channel_id,
                    start_sample_index=sample_range.start_sample_index,
                    stop_sample_index=sample_range.stop_sample_index,
                ),
            )
        else:
            mean_number = float(np.mean(values))
            rms_number = float(np.sqrt(np.mean(np.square(values))))
            minimum_number = float(np.min(values))
            maximum_number = float(np.max(values))
            absolute_peak_number = float(np.max(np.abs(values)))
            mean = _available(mean_number)
            rms = _available(rms_number)
            minimum = _available(minimum_number)
            maximum = _available(maximum_number)
            absolute_peak = _available(absolute_peak_number)
            peak_to_peak = _available(maximum_number - minimum_number)
            crest = (
                MetricValue(MetricAvailability.UNAVAILABLE, None, "RMS is zero")
                if rms_number == 0.0
                else _available(absolute_peak_number / rms_number)
            )
        if len(raw) == 0 or not np.all(np.isfinite(raw)):
            clipping = MetricValue(
                MetricAvailability.NOT_EVALUATED,
                None,
                "raw input is empty or contains non-finite samples",
            )
        elif calibration.valid_input_min_v is None or calibration.valid_input_max_v is None:
            clipping = MetricValue(
                MetricAvailability.NOT_EVALUATED,
                None,
                "calibration has no declared valid input span",
            )
        else:
            lower = calibration.valid_input_min_v + clipping_margin_v
            upper = calibration.valid_input_max_v - clipping_margin_v
            clipping = _available(float(np.mean((raw <= lower) | (raw >= upper))))
        return ChannelMetricResult(
            channel_id=channel_id,
            unit=unit,
            signal_stage=SignalStage.CONDITIONED,
            sample_range=sample_range,
            sample_count=len(values),
            sample_rate_hz=sample_rate_hz,
            recipe_hash=recipe_hash,
            calibration_hash=calibration.calibration_hash,
            mean=mean,
            rms=rms,
            minimum=minimum,
            maximum=maximum,
            absolute_peak=absolute_peak,
            peak_to_peak=peak_to_peak,
            crest_factor=crest,
            clipping_fraction=clipping,
            notices=notices,
        )

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
    ) -> FftAmplitudeResult:
        values = _finite_analysis_input(samples, channel_id, sample_range)
        count = len(values)
        if count > maximum_samples:
            raise ProcessingError(
                "The selected FFT range exceeds the qualified memory bound.",
                code="DAQ.PROCESSING.SPECTRAL_SELECTION_TOO_LARGE",
                context={
                    "channel_id": channel_id,
                    "sample_count": count,
                    "maximum_samples": maximum_samples,
                },
                remediation="Select at most 1,048,576 samples for FFT analysis.",
            )
        if count < 2:
            _raise_spectral_short(channel_id, count)
        centered = values - float(np.mean(values))
        window = signal.windows.hann(count, sym=False)
        coherent_gain = float(np.sum(window) / count)
        transformed = np.fft.rfft(centered * window)
        amplitude = np.abs(transformed) / (count * coherent_gain)
        if count > 2:
            amplitude[1:-1] *= 2.0
        if count % 2:
            amplitude[-1] *= 2.0
        frequencies = np.fft.rfftfreq(count, d=1.0 / sample_rate_hz)
        return FftAmplitudeResult(
            channel_id=channel_id,
            unit=unit,
            sample_range=sample_range,
            frequencies_hz=_readonly(frequencies),
            amplitudes=_readonly(amplitude),
            recipe_hash=recipe_hash,
            calibration_hash=calibration_hash,
            signal_stage=signal_stage,
        )

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
    ) -> PsdResult:
        values = _finite_analysis_input(samples, channel_id, sample_range)
        accumulator = self.create_welch_accumulator(
            channel_id=channel_id,
            unit=unit,
            sample_range=sample_range,
            sample_rate_hz=sample_rate_hz,
            recipe_hash=recipe_hash,
            calibration_hash=calibration_hash,
            segment_duration_seconds=segment_duration_seconds,
            overlap_fraction=overlap_fraction,
            signal_stage=signal_stage,
        )
        accumulator.append(values)
        return accumulator.finalize()

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
    ) -> BoundedWelchAccumulator:
        return BoundedWelchAccumulator(
            channel_id=channel_id,
            unit=unit,
            sample_range=sample_range,
            sample_rate_hz=sample_rate_hz,
            recipe_hash=recipe_hash,
            calibration_hash=calibration_hash,
            segment_duration_seconds=segment_duration_seconds,
            overlap_fraction=overlap_fraction,
            signal_stage=signal_stage,
        )


class BoundedWelchAccumulator:
    """Accumulate periodic-Hann Welch segments with one bounded carry buffer."""

    def __init__(
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
    ) -> None:
        self._channel_id = channel_id
        self._unit = unit
        self._sample_range = sample_range
        self._sample_rate_hz = sample_rate_hz
        self._recipe_hash = recipe_hash
        self._calibration_hash = calibration_hash
        self._signal_stage = signal_stage
        self._target_count = max(
            2,
            int(
                (
                    Decimal(str(sample_rate_hz)) * Decimal(str(segment_duration_seconds))
                ).to_integral_value(rounding=ROUND_HALF_UP)
            ),
        )
        self._overlap_fraction = overlap_fraction
        self._target_overlap = _rounded_overlap(self._target_count, overlap_fraction)
        self._buffer = np.empty(self._target_count, dtype=np.float64)
        self._buffer_count = 0
        self._density_sum: np.ndarray | None = None
        self._segment_total = 0
        self._sample_total = 0
        self._finalized = False

    def append(self, samples: np.ndarray) -> None:
        if self._finalized:
            raise RuntimeError("Welch accumulator is already finalized")
        values = _readonly_vector_input(samples, self._channel_id)
        if not np.all(np.isfinite(values)):
            raise ProcessingError(
                "Welch input contains non-finite samples.",
                code="DAQ.PROCESSING.PROCESSING_NONFINITE_INPUT",
                context={"channel_id": self._channel_id, "sample_count": len(values)},
                remediation="Supply finite conditioned samples.",
            )
        self._sample_total += len(values)
        offset = 0
        while offset < len(values):
            required = self._target_count - self._buffer_count
            take = min(required, len(values) - offset)
            if take:
                self._buffer[self._buffer_count : self._buffer_count + take] = values[
                    offset : offset + take
                ]
                self._buffer_count += take
                offset += take
            if self._buffer_count == self._target_count:
                self._accumulate_segment(self._buffer)
                if self._target_overlap:
                    tail = self._buffer[-self._target_overlap :].copy()
                    self._buffer[: self._target_overlap] = tail
                self._buffer_count = self._target_overlap

    def finalize(self) -> PsdResult:
        if self._finalized:
            raise RuntimeError("Welch accumulator is already finalized")
        self._finalized = True
        if self._sample_total != self._sample_range.sample_count:
            raise ProcessingError(
                "Welch input does not match the declared sample range.",
                code="DAQ.PROCESSING.PROCESSING_RANGE_INVALID",
                context={
                    "channel_id": self._channel_id,
                    "received_sample_count": self._sample_total,
                    "expected_sample_count": self._sample_range.sample_count,
                },
                remediation="Append the complete selected range exactly once.",
            )
        if self._sample_total < 2:
            _raise_spectral_short(self._channel_id, self._sample_total)
        if self._segment_total == 0:
            segment_count = self._buffer_count
            self._accumulate_segment(self._buffer[:segment_count])
            overlap_count = _rounded_overlap(segment_count, self._overlap_fraction)
        else:
            segment_count = self._target_count
            overlap_count = self._target_overlap
        assert self._density_sum is not None
        density = self._density_sum / self._segment_total
        frequencies = np.fft.rfftfreq(segment_count, d=1.0 / self._sample_rate_hz)
        return PsdResult(
            channel_id=self._channel_id,
            input_unit=self._unit,
            density_unit=f"{self._unit.value}^2/Hz",
            sample_range=self._sample_range,
            frequencies_hz=_readonly(frequencies),
            density=_readonly(density),
            segment_sample_count=segment_count,
            overlap_sample_count=overlap_count,
            recipe_hash=self._recipe_hash,
            calibration_hash=self._calibration_hash,
            signal_stage=self._signal_stage,
        )

    def _accumulate_segment(self, values: np.ndarray) -> None:
        count = len(values)
        window = signal.windows.hann(count, sym=False)
        centered = values - float(np.mean(values))
        transformed = np.fft.rfft(centered * window)
        density = np.square(np.abs(transformed)) / (
            self._sample_rate_hz * float(np.sum(np.square(window)))
        )
        if count > 2:
            density[1:-1] *= 2.0
        if count % 2:
            density[-1] *= 2.0
        if self._density_sum is None:
            self._density_sum = density
        else:
            self._density_sum += density
        self._segment_total += 1


class BaselineStreamingProcessor:
    """Apply affine calibration and causal SOS filters with persistent state."""

    def __init__(
        self,
        recipe: ProcessingRecipeEnvelope,
        calibrations: tuple[ResolvedChannelCalibration, ...],
        *,
        sample_rate_hz: float,
        mode: ProcessingMode,
    ) -> None:
        if not math.isfinite(sample_rate_hz) or sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive and finite")
        if not calibrations:
            raise ProcessingError(
                "No channel calibrations were supplied.",
                code="DAQ.PROCESSING.CALIBRATION_MISSING",
                remediation="Resolve every channel calibration before processing.",
            )
        if recipe.status is not RecipeStatus.ACTIVE:
            raise ProcessingError(
                "The numerical baseline processor requires an active recipe.",
                code="DAQ.PROCESSING.RECIPE_SCHEMA_UNSUPPORTED",
                context={
                    "recipe_id": recipe.recipe_id,
                    "recipe_version": recipe.recipe_version,
                    "status": recipe.status.value,
                },
                remediation="Use the qualified baseline-v1 recipe.",
            )
        if mode is ProcessingMode.LIVE and any(not stage.causal for stage in recipe.stages):
            raise ProcessingError(
                "A noncausal stage cannot run in the live pipeline.",
                code="DAQ.PROCESSING.NONCAUSAL_STAGE_IN_LIVE_PIPELINE",
                remediation="Use the causal baseline-v1 recipe for live processing.",
            )
        self._recipe = recipe
        self._recipe_hash = canonical_sha256(recipe)
        self._calibrations = calibrations
        self._sample_rate_hz = sample_rate_hz
        self._mode = mode
        self._session_id: str | None = None
        self._next_sample_index: int | None = None
        self._next_sequence_number: int | None = None
        self._ordered_ids = tuple(cal.logical_channel_id for cal in calibrations)
        calibration_stages = tuple(
            stage for stage in recipe.stages if stage.kind is ProcessingStageKind.AFFINE_CALIBRATION
        )
        incompatible = [
            calibration.logical_channel_id
            for calibration in calibrations
            if not any(calibration.channel_role in stage.applies_to for stage in calibration_stages)
        ]
        if incompatible:
            raise ProcessingError(
                "The recipe calibration stage does not apply to every channel.",
                code="DAQ.PROCESSING.RECIPE_CHANNEL_INCOMPATIBLE",
                context={"channel_ids": incompatible},
                remediation="Use baseline-v1 with its complete role applicability.",
            )
        self._validate_calibration_hashes()
        self._states = self._design_filters()
        self._reset_pending = False

    @property
    def recipe_hash(self) -> str:
        return self._recipe_hash

    @property
    def numpy_version(self) -> str:
        return np.__version__

    @property
    def scipy_version(self) -> str:
        return str(scipy.__version__)

    @property
    def algorithm_details(self) -> Mapping[str, object]:
        return {
            "calibration_formula": (
                "polarity_multiplier*(raw_voltage-input_offset_v)/sensitivity_v_per_output_unit"
            ),
            "filter_implementation": "scipy.signal.sosfilt",
            "filter_initialization": self._recipe.initialization_policy.value
            if self._recipe.initialization_policy
            else None,
            "channels": [
                {
                    "channel_id": calibration.logical_channel_id,
                    "stages": [
                        {
                            "stage_id": state.stage_id,
                            "kind": state.kind.value,
                            "order": state.order,
                            "cutoff_hz": state.cutoff_hz,
                            "sos": state.sos.tolist(),
                        }
                        for state in channel_states
                    ],
                }
                for calibration, channel_states in zip(
                    self._calibrations, self._states, strict=True
                )
            ],
        }

    def reset(self) -> ProcessingNotice:
        for channel in self._states:
            for stage_state in channel:
                stage_state.zi = None
        self._session_id = None
        self._next_sample_index = None
        self._next_sequence_number = None
        self._reset_pending = True
        return ProcessingNotice(
            code="DAQ.PROCESSING.FILTER_STATE_RESET",
            severity=ErrorSeverity.WARNING.value,
            message="Filter state was explicitly reset; the next chunk begins a new segment.",
        )

    def process(self, chunk: AcquisitionChunk) -> ProcessedChunk:
        self._validate_chunk(chunk)
        continuity = self._continuity(chunk)
        calibrated = np.array(_calibrate_samples(chunk.samples, self._calibrations), copy=True)
        conditioned = np.empty_like(chunk.samples)
        notices: list[ProcessingNotice] = []
        for index, calibration in enumerate(self._calibrations):
            raw = chunk.samples[index]
            calibrated_channel = calibrated[index]
            conditioned_channel = calibrated_channel
            for stage_state in self._states[index]:
                zi = stage_state.zi
                if zi is None:
                    zi = signal.sosfilt_zi(stage_state.sos) * conditioned_channel[0]
                conditioned_channel, stage_state.zi = signal.sosfilt(
                    stage_state.sos,
                    conditioned_channel,
                    zi=zi,
                )
            conditioned[index] = conditioned_channel
            if (
                calibration.valid_input_min_v is not None
                and calibration.valid_input_max_v is not None
                and np.any(
                    (raw < calibration.valid_input_min_v) | (raw > calibration.valid_input_max_v)
                )
            ):
                notices.append(
                    ProcessingNotice(
                        code="DAQ.PROCESSING.CALIBRATION_INPUT_OUT_OF_RANGE",
                        severity=ErrorSeverity.WARNING.value,
                        message="Raw samples exceed the calibration's declared valid input span.",
                        channel_id=calibration.logical_channel_id,
                        start_sample_index=chunk.start_sample_index,
                        stop_sample_index=chunk.start_sample_index + chunk.sample_count,
                    )
                )
        calibrated.flags.writeable = False
        conditioned.flags.writeable = False
        self._session_id = chunk.session_id
        self._next_sample_index = chunk.start_sample_index + chunk.sample_count
        self._next_sequence_number = chunk.sequence_number + 1
        self._reset_pending = False
        return ProcessedChunk(
            session_id=chunk.session_id,
            sequence_number=chunk.sequence_number,
            start_sample_index=chunk.start_sample_index,
            sample_count=chunk.sample_count,
            sample_rate_hz=chunk.sample_rate_hz,
            ordered_channel_ids=chunk.ordered_channel_ids,
            output_units=tuple(cal.output_unit for cal in self._calibrations),
            calibration_ids=tuple(cal.calibration_id for cal in self._calibrations),
            calibration_hashes=tuple(cal.calibration_hash for cal in self._calibrations),
            recipe_id=self._recipe.recipe_id,
            recipe_hash=self._recipe_hash,
            calibrated_samples=calibrated,
            conditioned_samples=conditioned,
            continuity=continuity,
            notices=tuple(notices),
        )

    def _validate_chunk(self, chunk: AcquisitionChunk) -> None:
        context = {
            "session_id": chunk.session_id,
            "start_sample_index": chunk.start_sample_index,
            "stop_sample_index": chunk.start_sample_index + chunk.sample_count,
        }
        if chunk.ordered_channel_ids != self._ordered_ids:
            self._fault(
                "Channel order changed before processing.",
                "PROCESSING_CHANNEL_ORDER_CHANGED",
                context,
            )
        if chunk.ordered_channel_units != tuple("V" for _ in self._ordered_ids):
            self._fault(
                "The baseline processor requires raw voltage input units.",
                "RECIPE_CHANNEL_INCOMPATIBLE",
                context,
            )
        if chunk.sample_rate_hz != self._sample_rate_hz:
            self._fault(
                "Sample rate changed before processing.",
                "RECIPE_SAMPLE_RATE_INCOMPATIBLE",
                {**context, "expected_rate_hz": self._sample_rate_hz},
            )
        if not np.all(np.isfinite(chunk.samples)):
            invalid_channels = [
                channel_id
                for channel_id, values in zip(chunk.ordered_channel_ids, chunk.samples, strict=True)
                if not np.all(np.isfinite(values))
            ]
            self._fault(
                "The raw chunk contains non-finite samples.",
                "PROCESSING_NONFINITE_INPUT",
                {**context, "channel_ids": invalid_channels},
            )
        if self._session_id is not None and chunk.session_id != self._session_id:
            self._fault(
                "Session identity changed before processing.", "PROCESSING_CHUNK_GAP", context
            )
        if self._next_sample_index is not None:
            if chunk.start_sample_index > self._next_sample_index:
                self._fault("A processing chunk gap was detected.", "PROCESSING_CHUNK_GAP", context)
            if chunk.start_sample_index < self._next_sample_index:
                self._fault(
                    "A processing chunk overlap was detected.",
                    "PROCESSING_CHUNK_OVERLAP",
                    context,
                )
        if (
            self._next_sequence_number is not None
            and chunk.sequence_number != self._next_sequence_number
        ):
            self._fault(
                "The processing chunk sequence is not contiguous.",
                "PROCESSING_CHUNK_GAP",
                {**context, "expected_sequence_number": self._next_sequence_number},
            )

    def _continuity(self, chunk: AcquisitionChunk) -> ContinuityStatus:
        if self._reset_pending:
            return ContinuityStatus.RESET
        if self._next_sample_index is None:
            return ContinuityStatus.NEW_SEGMENT
        return ContinuityStatus.CONTIGUOUS

    def _design_filters(self) -> list[list[_FilterStageState]]:
        states: list[list[_FilterStageState]] = []
        for calibration in self._calibrations:
            channel_states: list[_FilterStageState] = []
            for stage in self._recipe.stages:
                if calibration.channel_role not in stage.applies_to:
                    continue
                if stage.kind is ProcessingStageKind.AFFINE_CALIBRATION:
                    continue
                assert stage.order is not None and stage.cutoff_hz is not None
                if stage.cutoff_hz >= self._sample_rate_hz / 2:
                    raise ProcessingError(
                        "The sample rate cannot support a declared filter cutoff.",
                        code="DAQ.PROCESSING.RECIPE_SAMPLE_RATE_INCOMPATIBLE",
                        context={
                            "stage_id": stage.stage_id,
                            "cutoff_hz": stage.cutoff_hz,
                            "sample_rate_hz": self._sample_rate_hz,
                        },
                        remediation="Use a sample rate above the cutoff Nyquist limit or a compatible recipe.",
                    )
                sos = design_butterworth_sos(
                    stage.order,
                    stage.cutoff_hz,
                    high_pass=stage.kind is ProcessingStageKind.BUTTERWORTH_HIGH_PASS,
                    sample_rate_hz=self._sample_rate_hz,
                )
                channel_states.append(
                    _FilterStageState(
                        stage_id=stage.stage_id,
                        kind=stage.kind,
                        order=stage.order,
                        cutoff_hz=stage.cutoff_hz,
                        sos=sos,
                    )
                )
            states.append(channel_states)
        return states

    def _validate_calibration_hashes(self) -> None:
        for calibration in self._calibrations:
            expected = canonical_sha256_excluding(calibration, "calibration_hash")
            if calibration.calibration_hash != expected:
                raise ProcessingError(
                    "A resolved calibration hash does not match its scientific content.",
                    code="DAQ.PROCESSING.CALIBRATION_INVALID",
                    context={
                        "channel_id": calibration.logical_channel_id,
                        "expected_calibration_hash": expected,
                        "received_calibration_hash": calibration.calibration_hash,
                    },
                    remediation="Resolve the channel calibration again before processing.",
                )

    @staticmethod
    def _fault(message: str, suffix: str, context: dict[str, object]) -> None:
        raise ProcessingError(
            message,
            code=f"DAQ.PROCESSING.{suffix}",
            context=context,
            remediation="Reset the processor and begin an explicit new processing segment.",
        )


def _readonly(values: np.ndarray) -> np.ndarray:
    result = np.ascontiguousarray(values, dtype=np.float64)
    result.flags.writeable = False
    return result


def _calibrate_samples(
    samples: np.ndarray,
    calibrations: tuple[ResolvedChannelCalibration, ...],
) -> np.ndarray:
    if (
        not isinstance(samples, np.ndarray)
        or samples.dtype != np.float64
        or samples.ndim != 2
        or samples.shape[0] != len(calibrations)
        or not calibrations
    ):
        raise ValueError("calibration input must align with channel calibrations")
    if not np.all(np.isfinite(samples)):
        raise ProcessingError(
            "Calibration input contains non-finite samples.",
            code="DAQ.PROCESSING.PROCESSING_NONFINITE_INPUT",
            remediation="Supply finite verified raw samples.",
        )
    calibrated = np.empty_like(samples)
    for index, calibration in enumerate(calibrations):
        calibrated[index] = (
            calibration.polarity_multiplier
            * (samples[index] - calibration.input_offset_v)
            / calibration.sensitivity_v_per_output_unit
        )
    calibrated.flags.writeable = False
    return calibrated


def design_butterworth_sos(
    order: int,
    cutoff_hz: float,
    *,
    high_pass: bool,
    sample_rate_hz: float,
) -> np.ndarray:
    """Design the qualified digital Butterworth stage in SOS form."""
    if order <= 0 or cutoff_hz <= 0 or cutoff_hz >= sample_rate_hz / 2:
        raise ValueError("filter order, cutoff, and sample rate are incompatible")
    return np.asarray(
        signal.butter(
            order,
            cutoff_hz,
            btype="highpass" if high_pass else "lowpass",
            fs=sample_rate_hz,
            output="sos",
        ),
        dtype=np.float64,
    )


def _readonly_vector_input(values: np.ndarray, channel_id: str) -> np.ndarray:
    if not isinstance(values, np.ndarray) or values.ndim != 1:
        raise ProcessingError(
            "Analysis input must be a one-dimensional NumPy array.",
            code="DAQ.PROCESSING.METRIC_NOT_EVALUATED",
            context={"channel_id": channel_id},
            remediation="Select one channel and a non-empty sample range.",
        )
    return values


def _finite_analysis_input(
    values: np.ndarray,
    channel_id: str,
    sample_range: ProcessingRange,
) -> np.ndarray:
    result = _readonly_vector_input(values, channel_id)
    if len(result) != sample_range.sample_count or not np.all(np.isfinite(result)):
        raise ProcessingError(
            "Spectral input is non-finite or disagrees with the selected range.",
            code="DAQ.PROCESSING.PROCESSING_NONFINITE_INPUT",
            context={"channel_id": channel_id, "sample_count": len(result)},
            remediation="Select a finite, verified sample range.",
        )
    return result


def _available(value: float) -> MetricValue:
    return MetricValue(MetricAvailability.AVAILABLE, value)


def _rounded_overlap(segment_count: int, overlap_fraction: float) -> int:
    overlap = int(
        (Decimal(segment_count) * Decimal(str(overlap_fraction))).to_integral_value(
            rounding=ROUND_HALF_UP
        )
    )
    return min(overlap, segment_count - 1)


def _raise_spectral_short(channel_id: str, count: int) -> None:
    raise ProcessingError(
        "The selected spectral range is too short.",
        code="DAQ.PROCESSING.SPECTRAL_RANGE_TOO_SHORT",
        context={"channel_id": channel_id, "sample_count": count},
        remediation="Select at least two samples.",
    )
