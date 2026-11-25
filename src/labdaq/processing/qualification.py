"""Bounded numerical diagnostics for DAQ-M08 qualification."""

from __future__ import annotations

import math

import numpy as np

from labdaq.ports.qualification import (
    AlignmentDiagnosticSummary,
    ChannelDiagnosticSummary,
    CrosstalkDiagnosticSummary,
    ResponseDiagnosticSummary,
)


class NumPyQualificationAnalysis:
    """Calculate diagnostics without retaining more than the caller's bounded window."""

    def channel_diagnostics(
        self,
        samples: np.ndarray,
        *,
        input_range_v: tuple[float, float],
    ) -> ChannelDiagnosticSummary:
        values = _one_dimensional(samples, "samples")
        minimum_range, maximum_range = input_range_v
        if not minimum_range < maximum_range:
            raise ValueError("input_range_v minimum must be less than maximum")
        finite_mask = np.isfinite(values)
        finite = values[finite_mask]
        nonfinite_count = int(values.size - finite.size)
        if finite.size == 0:
            raise ValueError("at least one finite sample is required")
        median = float(np.median(finite))
        robust_noise = float(1.4826 * np.median(np.abs(finite - median)))
        absolute_limit = max(abs(minimum_range), abs(maximum_range))
        peak = float(np.max(np.abs(finite)))
        utilization = peak / absolute_limit
        near_clipping = np.logical_or(
            finite <= minimum_range + 0.02 * (maximum_range - minimum_range),
            finite >= maximum_range - 0.02 * (maximum_range - minimum_range),
        )
        clipping = np.logical_or(finite <= minimum_range, finite >= maximum_range)
        return ChannelDiagnosticSummary(
            sample_count=int(values.size),
            mean_v=float(np.mean(finite)),
            median_v=median,
            minimum_v=float(np.min(finite)),
            maximum_v=float(np.max(finite)),
            peak_to_peak_v=float(np.ptp(finite)),
            standard_deviation_v=float(np.std(finite)),
            rms_v=float(np.sqrt(np.mean(np.square(finite)))),
            robust_noise_v=robust_noise,
            range_utilization=utilization,
            near_clipping_fraction=float(np.mean(near_clipping)),
            clipping_fraction=float(np.mean(clipping)),
            nonfinite_count=nonfinite_count,
            longest_repeated_value_run=_longest_repeated_run(values),
        )

    def alignment(
        self,
        reference: np.ndarray,
        compared: np.ndarray,
        *,
        sample_rate_hz: float,
        stimulus_frequency_hz: float | None = None,
    ) -> AlignmentDiagnosticSummary:
        left = _one_dimensional(reference, "reference")
        right = _one_dimensional(compared, "compared")
        if left.size != right.size or left.size == 0:
            raise ValueError("alignment inputs must have equal non-zero lengths")
        if not np.all(np.isfinite(left)) or not np.all(np.isfinite(right)):
            raise ValueError("alignment inputs must contain only finite values")
        left_centered = left - np.mean(left)
        right_centered = right - np.mean(right)
        transform_size = 1 << (2 * left.size - 1).bit_length()
        circular = np.fft.irfft(
            np.fft.rfft(right_centered, transform_size)
            * np.conjugate(np.fft.rfft(left_centered, transform_size)),
            transform_size,
        )
        negative_lags = (
            circular[-(left.size - 1) :] if left.size > 1 else np.empty(0, dtype=np.float64)
        )
        correlation = np.concatenate((negative_lags, circular[: left.size]))
        lag = int(np.argmax(correlation) - (left.size - 1))
        denominator = float(np.linalg.norm(left_centered) * np.linalg.norm(right_centered))
        normalized = 0.0 if denominator == 0.0 else float(np.max(correlation) / denominator)
        phase: float | None = None
        if stimulus_frequency_hz is not None:
            if sample_rate_hz <= 0 or stimulus_frequency_hz <= 0:
                raise ValueError("sample and stimulus frequencies must be positive")
            phase = float(360.0 * stimulus_frequency_hz * lag / sample_rate_hz)
            phase = ((phase + 180.0) % 360.0) - 180.0
        return AlignmentDiagnosticSummary(
            lag_samples=lag,
            normalized_correlation=normalized,
            phase_degrees=phase,
        )

    def response(
        self,
        baseline: np.ndarray,
        stimulus: np.ndarray,
    ) -> ResponseDiagnosticSummary:
        baseline_values = _finite(_one_dimensional(baseline, "baseline"), "baseline")
        stimulus_values = _finite(_one_dimensional(stimulus, "stimulus"), "stimulus")
        baseline_energy = float(np.mean(np.square(baseline_values - np.mean(baseline_values))))
        stimulus_energy = float(np.mean(np.square(stimulus_values - np.mean(stimulus_values))))
        if baseline_energy == 0.0:
            ratio = math.inf if stimulus_energy > 0 else 1.0
        else:
            ratio = stimulus_energy / baseline_energy
        return ResponseDiagnosticSummary(
            baseline_energy=baseline_energy,
            stimulus_energy=stimulus_energy,
            response_ratio=ratio,
        )

    def crosstalk(
        self,
        stimulated: np.ndarray,
        unstimulated: np.ndarray,
    ) -> CrosstalkDiagnosticSummary:
        excited = _finite(_one_dimensional(stimulated, "stimulated"), "stimulated")
        coupled = _finite(_one_dimensional(unstimulated, "unstimulated"), "unstimulated")
        stimulated_rms = _ac_rms(excited)
        coupled_rms = _ac_rms(coupled)
        if stimulated_rms == 0.0:
            raise ValueError("stimulated signal must have non-zero AC energy")
        coupling_db = (
            -math.inf if coupled_rms == 0.0 else 20.0 * math.log10(coupled_rms / stimulated_rms)
        )
        return CrosstalkDiagnosticSummary(
            stimulated_rms=stimulated_rms,
            coupled_rms=coupled_rms,
            coupling_db=coupling_db,
        )


def _one_dimensional(samples: np.ndarray, field: str) -> np.ndarray:
    if not isinstance(samples, np.ndarray) or samples.ndim != 1:
        raise ValueError(f"{field} must be a one-dimensional NumPy array")
    if samples.size == 0:
        raise ValueError(f"{field} must not be empty")
    return np.asarray(samples, dtype=np.float64)


def _finite(samples: np.ndarray, field: str) -> np.ndarray:
    if not np.all(np.isfinite(samples)):
        raise ValueError(f"{field} must contain only finite values")
    return samples


def _longest_repeated_run(values: np.ndarray) -> int:
    if values.size == 0:
        return 0
    longest = 1
    current = 1
    previous = values[0]
    for value in values[1:]:
        if value == previous or (np.isnan(value) and np.isnan(previous)):
            current += 1
            longest = max(longest, current)
        else:
            current = 1
        previous = value
    return longest


def _ac_rms(values: np.ndarray) -> float:
    centered = values - np.mean(values)
    return float(np.sqrt(np.mean(np.square(centered))))
