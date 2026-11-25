import math

import numpy as np
import pytest

from labdaq.application.qualification_service import _simulated_assisted_outcome
from labdaq.bootstrap import load_qualification_plan
from labdaq.domain.qualification import (
    MetricEvaluation,
    MetricValueKind,
    QualificationMetric,
    QualificationStatus,
    SignalStage,
)
from labdaq.processing.qualification import NumPyQualificationAnalysis


def test_channel_diagnostics_include_robust_noise_and_exact_range_boundaries() -> None:
    analysis = NumPyQualificationAnalysis()
    values = np.array([-10.0, -1.0, 0.0, 1.0, 10.0, np.nan])
    summary = analysis.channel_diagnostics(values, input_range_v=(-10.0, 10.0))

    assert summary.sample_count == 6
    assert summary.nonfinite_count == 1
    assert summary.median_v == 0.0
    assert summary.robust_noise_v == pytest.approx(1.4826)
    assert summary.range_utilization == 1.0
    assert summary.clipping_fraction == pytest.approx(0.4)
    assert summary.near_clipping_fraction == pytest.approx(0.4)
    assert summary.peak_to_peak_v == 20.0


def test_frozen_run_alignment_phase_response_and_crosstalk() -> None:
    analysis = NumPyQualificationAnalysis()
    frozen = analysis.channel_diagnostics(
        np.array([1.0, 1.0, 1.0, 2.0, 2.0]),
        input_range_v=(-10.0, 10.0),
    )
    assert frozen.longest_repeated_value_run == 3

    reference = np.sin(2 * np.pi * np.arange(100) / 20.0)
    compared = np.roll(reference, 2)
    alignment = analysis.alignment(
        reference,
        compared,
        sample_rate_hz=1000.0,
        stimulus_frequency_hz=50.0,
    )
    assert alignment.lag_samples == 2
    assert alignment.normalized_correlation > 0.95
    assert alignment.phase_degrees == pytest.approx(36.0)

    response = analysis.response(np.array([0.0, 1.0]), np.array([0.0, 4.0]))
    assert response.response_ratio == pytest.approx(16.0)
    infinite = analysis.response(np.ones(4), np.array([-1.0, 1.0, -1.0, 1.0]))
    assert math.isinf(infinite.response_ratio)
    assert analysis.response(np.ones(4), np.ones(4)).response_ratio == 1.0

    crosstalk = analysis.crosstalk(reference, reference * 0.01)
    assert crosstalk.coupling_db == pytest.approx(-40.0)
    silent = analysis.crosstalk(reference, np.ones(reference.size))
    assert silent.coupling_db == -math.inf


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        (lambda a: a.channel_diagnostics(np.array([]), input_range_v=(-1.0, 1.0)), "empty"),
        (
            lambda a: a.channel_diagnostics(np.array([np.nan]), input_range_v=(-1.0, 1.0)),
            "finite",
        ),
        (
            lambda a: a.channel_diagnostics(np.array([0.0]), input_range_v=(1.0, -1.0)),
            "minimum",
        ),
        (lambda a: a.alignment(np.ones(2), np.ones(3), sample_rate_hz=1.0), "equal"),
        (
            lambda a: a.alignment(np.array([0.0, np.nan]), np.ones(2), sample_rate_hz=1.0),
            "finite",
        ),
        (
            lambda a: a.alignment(
                np.ones(2), np.ones(2), sample_rate_hz=0.0, stimulus_frequency_hz=1.0
            ),
            "positive",
        ),
        (lambda a: a.response(np.array([np.nan]), np.ones(1)), "finite"),
        (lambda a: a.crosstalk(np.ones(2), np.ones(2)), "non-zero"),
        (lambda a: a.response(np.ones((1, 1)), np.ones(1)), "one-dimensional"),
    ],
)
def test_analysis_rejects_ambiguous_or_invalid_inputs(operation: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        operation(NumPyQualificationAnalysis())  # type: ignore[operator]


def test_simulated_missing_response_and_alignment_map_to_blocking_codes() -> None:
    plan = load_qualification_plan("full-v1")

    def metric(name: str, value: float) -> QualificationMetric:
        return QualificationMetric(
            name,
            MetricValueKind.NUMBER,
            value,
            "ratio",
            SignalStage.RAW,
            MetricEvaluation.UNEVALUATED,
            "test",
            "1",
        )

    response = next(item for item in plan.tests if item.test_id == "axis-response-x")
    status, reasons = _simulated_assisted_outcome(
        response,
        (metric("response-energy-ratio", 1.0),),
    )
    assert status is QualificationStatus.FAIL
    assert reasons == ("DAQ.QUALIFICATION.SENSOR_RESPONSE_MISSING",)

    alignment = next(item for item in plan.tests if item.test_id == "cross-channel-alignment")
    status, reasons = _simulated_assisted_outcome(
        alignment,
        (metric("lag-samples", 1.0),),
    )
    assert status is QualificationStatus.FAIL
    assert reasons == ("DAQ.QUALIFICATION.CROSS_CHANNEL_MISALIGNMENT",)
