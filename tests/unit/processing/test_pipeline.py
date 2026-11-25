from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from scipy import signal

from labdaq.adapters.configuration_json import JsonConfigurationAdapter
from labdaq.adapters.processing_json import JsonProcessingRecordAdapter
from labdaq.application.processing_service import (
    CalibrationResolutionService,
    StreamingProcessingService,
)
from labdaq.domain import (
    CalibrationQuality,
    CalibrationSourceKind,
    MetricAvailability,
    Polarity,
    ProcessingMode,
    ProcessingProvenance,
    ProcessingRange,
    Unit,
    canonical_sha256,
    canonical_sha256_excluding,
)
from labdaq.errors import ProcessingError
from labdaq.ports.acquisition import AcquisitionChunk
from labdaq.processing import BaselineScientificProcessing, design_butterworth_sos

FIXTURE = Path("tests/fixtures/configuration/valid-four-channel-synthetic-v1.json")


def _configuration():
    documents = JsonConfigurationAdapter()
    experiment = documents.load_experiment_config(FIXTURE)
    profile = documents.load_bundled_hardware_profile(
        experiment.hardware_profile_id,
        experiment.compatible_profile_revision,
    )
    recipe = documents.load_bundled_processing_recipe("baseline-v1")
    calibrations, notices = CalibrationResolutionService().resolve_experiment(
        profile,
        experiment,
        experiment.acquisition.channel_ids,
    )
    return profile, experiment, recipe, calibrations, notices


def _chunk(samples: np.ndarray, *, start: int = 0, sequence: int = 0) -> AcquisitionChunk:
    _, experiment, _, _, _ = _configuration()
    values = np.array(samples, dtype=np.float64, copy=True, order="C")
    values.flags.writeable = False
    return AcquisitionChunk(
        session_id="processing-session",
        sequence_number=sequence,
        start_sample_index=start,
        sample_count=values.shape[1],
        sample_rate_hz=50000.0,
        ordered_channel_ids=experiment.acquisition.channel_ids,
        ordered_channel_units=("V", "V", "V", "V"),
        samples=values,
        read_completed_monotonic=(start + values.shape[1]) / 50000.0,
    )


def _processor():
    _, _, recipe, calibrations, _ = _configuration()
    numerical = BaselineScientificProcessing()
    return (
        numerical,
        numerical.create_stream(
            recipe,
            calibrations,
            sample_rate_hz=50000.0,
            mode=ProcessingMode.LIVE,
        ),
        recipe,
        calibrations,
    )


def test_affine_calibration_formula_polarity_and_independent_sensitivity() -> None:
    profile, experiment, recipe, _, _ = _configuration()
    changed = replace(
        experiment,
        calibrations=(
            replace(experiment.calibrations[0], sensitivity_value=6.3),
            replace(experiment.calibrations[1], sensitivity_value=7.0),
            replace(
                experiment.calibrations[2],
                sensitivity_value=8.0,
                polarity=Polarity.NEGATIVE,
            ),
            experiment.calibrations[3],
        ),
    )
    calibrations, _ = CalibrationResolutionService().resolve_experiment(
        profile,
        changed,
        changed.acquisition.channel_ids,
    )
    processor = BaselineScientificProcessing().create_stream(
        recipe,
        calibrations,
        sample_rate_hz=50000.0,
        mode=ProcessingMode.LIVE,
    )
    samples = np.zeros((4, 2), dtype=np.float64)
    samples[0] = [0.0063, -0.0063]
    samples[1] = [0.007, -0.007]
    samples[2] = [0.008, -0.008]
    result = processor.process(_chunk(samples))

    np.testing.assert_allclose(result.calibrated_samples[0], [1.0, -1.0])
    np.testing.assert_allclose(result.calibrated_samples[1], [1.0, -1.0])
    np.testing.assert_allclose(result.calibrated_samples[2], [-1.0, 1.0])
    assert not result.calibrated_samples.flags.writeable
    assert not result.conditioned_samples.flags.writeable
    assert samples.flags.writeable


def test_nominal_fallback_requires_exact_profile_permission_and_warns() -> None:
    profile, experiment, _, _, _ = _configuration()
    nominal = replace(
        experiment.calibrations[0],
        source_kind=CalibrationSourceKind.MANUFACTURER_NOMINAL,
        source_reference="TE datasheet nominal",
    )
    experiment = replace(
        experiment,
        calibrations=(nominal, *experiment.calibrations[1:]),
        sensors=(
            replace(experiment.sensors[0], model="830M1-0200"),
            experiment.sensors[1],
        ),
    )
    calibrations, notices = CalibrationResolutionService().resolve_experiment(
        profile,
        experiment,
        experiment.acquisition.channel_ids,
    )

    assert calibrations[0].quality is CalibrationQuality.NOMINAL
    assert notices[0].code == "DAQ.PROCESSING.NOMINAL_CALIBRATION_FALLBACK"
    with pytest.raises(ProcessingError, match="could not be resolved"):
        CalibrationResolutionService().resolve_experiment(
            replace(profile, nominal_calibration_fallback=None),
            experiment,
            experiment.acquisition.channel_ids,
        )


def test_missing_offset_and_span_fail_as_complete_channel_status() -> None:
    profile, experiment, _, _, _ = _configuration()
    invalid = replace(experiment.calibrations[3], input_offset=None)
    with pytest.raises(ProcessingError) as captured:
        CalibrationResolutionService().resolve_experiment(
            profile,
            replace(experiment, calibrations=(*experiment.calibrations[:3], invalid)),
            experiment.acquisition.channel_ids,
        )
    assert captured.value.code == "DAQ.PROCESSING.CALIBRATION_INVALID"
    assert "charge-signal" in str(captured.value.to_dict()["context"])


def test_missing_input_span_preserves_calibration_but_clipping_is_not_evaluated() -> None:
    profile, experiment, recipe, _, _ = _configuration()
    without_span = replace(
        experiment.calibrations[0],
        valid_input_min=None,
        valid_input_max=None,
    )
    changed = replace(
        experiment,
        calibrations=(without_span, *experiment.calibrations[1:]),
    )
    calibrations, _ = CalibrationResolutionService().resolve_experiment(
        profile,
        changed,
        changed.acquisition.channel_ids,
    )
    numerical = BaselineScientificProcessing()
    processor = numerical.create_stream(
        recipe,
        calibrations,
        sample_rate_hz=50000.0,
        mode=ProcessingMode.LIVE,
    )
    result = numerical.metrics(
        np.array([0.0, 1.0]),
        np.array([0.0, 1.0]),
        channel_id="accel-x",
        unit=Unit.STANDARD_GRAVITY,
        sample_range=ProcessingRange(0, 2),
        sample_rate_hz=50000.0,
        recipe_hash=processor.recipe_hash,
        calibration=calibrations[0],
        clipping_margin_v=0.0,
    )

    assert result.clipping_fraction.availability is MetricAvailability.NOT_EVALUATED
    assert calibrations[0].valid_input_min_v is None
    assert (
        JsonProcessingRecordAdapter().loads_calibration(
            JsonProcessingRecordAdapter().dumps_calibration(calibrations[0])
        )
        == calibrations[0]
    )


def test_filter_response_meets_baseline_targets() -> None:
    high = design_butterworth_sos(4, 1.0, high_pass=True, sample_rate_hz=50000.0)
    low = design_butterworth_sos(6, 12000.0, high_pass=False, sample_rate_hz=50000.0)

    _, high_response = signal.sosfreqz(high, worN=np.array([1.0, 2.0]), fs=50000.0)
    _, low_response = signal.sosfreqz(
        low,
        worN=np.array([10000.0, 12000.0, 15000.0]),
        fs=50000.0,
    )
    high_db = 20 * np.log10(np.abs(high_response))
    low_db = 20 * np.log10(np.abs(low_response))

    assert high_db[0] == pytest.approx(-3.0103, abs=0.15)
    assert high_db[1] > -0.05
    assert low_db[0] > -0.30
    assert low_db[1] == pytest.approx(-3.0103, abs=0.15)
    assert low_db[2] <= -18.0


def test_streaming_filter_is_chunk_boundary_invariant_and_channel_isolated() -> None:
    generator = np.random.default_rng(20260813)
    samples = generator.normal(0.0, 0.02, size=(4, 20000))
    _, whole, _, _ = _processor()
    whole_result = whole.process(_chunk(samples)).conditioned_samples.copy()

    _, chunked, _, _ = _processor()
    pieces = []
    start = 0
    for sequence, size in enumerate((1, 37, 4096, 3, 7000, 8863)):
        stop = start + size
        pieces.append(
            chunked.process(
                _chunk(samples[:, start:stop], start=start, sequence=sequence)
            ).conditioned_samples.copy()
        )
        start = stop
    reconstructed = np.concatenate(pieces, axis=1)

    np.testing.assert_allclose(reconstructed, whole_result, rtol=0.0, atol=2e-12)
    assert not np.array_equal(reconstructed[0], reconstructed[1])


def test_discontinuity_faults_before_stale_state_and_reset_is_explicit() -> None:
    _, processor, _, _ = _processor()
    processor.process(_chunk(np.zeros((4, 10))))
    with pytest.raises(ProcessingError) as captured:
        processor.process(_chunk(np.zeros((4, 2)), start=11, sequence=1))
    assert captured.value.code == "DAQ.PROCESSING.PROCESSING_CHUNK_GAP"
    notice = processor.reset()
    result = processor.process(_chunk(np.zeros((4, 2)), start=11, sequence=1))
    assert notice.code == "DAQ.PROCESSING.FILTER_STATE_RESET"
    assert result.continuity.value == "reset"


def test_nonfinite_and_context_faults_do_not_advance_filter_state() -> None:
    _, processor, _, _ = _processor()
    first = np.zeros((4, 8))
    processor.process(_chunk(first))
    invalid = np.zeros((4, 5))
    invalid[2, 2] = np.nan
    with pytest.raises(ProcessingError) as captured:
        processor.process(_chunk(invalid, start=8, sequence=1))
    assert captured.value.code == "DAQ.PROCESSING.PROCESSING_NONFINITE_INPUT"
    assert captured.value.context["channel_ids"] == ("accel-z",)

    continuation = np.full((4, 5), 0.01)
    after_fault = processor.process(_chunk(continuation, start=8, sequence=1))
    _, reference, _, _ = _processor()
    reference.process(_chunk(first))
    expected = reference.process(_chunk(continuation, start=8, sequence=1))
    np.testing.assert_array_equal(after_fault.conditioned_samples, expected.conditioned_samples)

    with pytest.raises(ProcessingError) as overlap:
        processor.process(_chunk(continuation, start=12, sequence=2))
    assert overlap.value.code == "DAQ.PROCESSING.PROCESSING_CHUNK_OVERLAP"


def test_recipe_and_calibration_context_are_validated_before_stream_creation() -> None:
    profile, experiment, recipe, calibrations, _ = _configuration()
    numerical = BaselineScientificProcessing()
    documents = JsonConfigurationAdapter()
    draft = documents.load_bundled_processing_recipe("baseline-v1-draft")
    with pytest.raises(ProcessingError) as inactive:
        numerical.create_stream(
            draft,
            calibrations,
            sample_rate_hz=50000.0,
            mode=ProcessingMode.LIVE,
        )
    assert inactive.value.code == "DAQ.PROCESSING.RECIPE_SCHEMA_UNSUPPORTED"

    with pytest.raises(ProcessingError) as incompatible_rate:
        numerical.create_stream(
            recipe,
            calibrations,
            sample_rate_hz=50_000.0 / 3,
            mode=ProcessingMode.LIVE,
        )
    assert incompatible_rate.value.code == "DAQ.PROCESSING.RECIPE_SAMPLE_RATE_INCOMPATIBLE"

    low_rate_recipe = documents.load_bundled_processing_recipe("baseline-low-rate-v1")
    low_rate_processor = numerical.create_stream(
        low_rate_recipe,
        calibrations,
        sample_rate_hz=50_000.0 / 3,
        mode=ProcessingMode.LIVE,
    )
    assert low_rate_processor.recipe_hash == canonical_sha256(low_rate_recipe)

    corrupted = replace(calibrations[0], calibration_hash="0" * 64)
    with pytest.raises(ProcessingError) as mismatched:
        numerical.create_stream(
            recipe,
            (corrupted, *calibrations[1:]),
            sample_rate_hz=50000.0,
            mode=ProcessingMode.LIVE,
        )
    assert mismatched.value.code == "DAQ.PROCESSING.CALIBRATION_INVALID"

    service = StreamingProcessingService(numerical)
    with pytest.raises(ProcessingError) as recipe_context:
        service.create(
            profile,
            experiment,
            recipe,
            ordered_channel_ids=experiment.acquisition.channel_ids,
            sample_rate_hz=50000.0,
            expected_recipe_hash="f" * 64,
        )
    assert recipe_context.value.code == "DAQ.PROCESSING.RECIPE_HASH_MISMATCH"
    with pytest.raises(ProcessingError) as calibration_context:
        service.create(
            profile,
            experiment,
            recipe,
            ordered_channel_ids=experiment.acquisition.channel_ids,
            sample_rate_hz=50000.0,
            expected_calibration_hashes=("f" * 64,) * 4,
        )
    assert calibration_context.value.code == "DAQ.PROCESSING.CALIBRATION_INVALID"


def test_metrics_clipping_zero_crest_and_nonfinite_policy() -> None:
    numerical, processor, recipe, calibrations = _processor()
    assert recipe.metrics is not None
    sample_range = ProcessingRange(0, 3)
    result = numerical.metrics(
        np.array([-10.0, 0.0, 10.0]),
        np.array([0.0, 0.0, 0.0]),
        channel_id="accel-x",
        unit=Unit.STANDARD_GRAVITY,
        sample_range=sample_range,
        sample_rate_hz=50000.0,
        recipe_hash=processor.recipe_hash,
        calibration=calibrations[0],
        clipping_margin_v=recipe.metrics.clipping_margin_v,
    )
    assert result.rms.value == 0.0
    assert result.crest_factor.availability is MetricAvailability.UNAVAILABLE
    assert result.clipping_fraction.value == pytest.approx(2 / 3)

    nonfinite = numerical.metrics(
        np.array([0.0, np.nan, 0.0]),
        np.array([0.0, np.nan, 0.0]),
        channel_id="accel-x",
        unit=Unit.STANDARD_GRAVITY,
        sample_range=sample_range,
        sample_rate_hz=50000.0,
        recipe_hash=processor.recipe_hash,
        calibration=calibrations[0],
        clipping_margin_v=0.0,
    )
    assert nonfinite.mean.availability is MetricAvailability.UNAVAILABLE
    assert nonfinite.clipping_fraction.availability is MetricAvailability.NOT_EVALUATED

    empty = numerical.metrics(
        np.array([], dtype=np.float64),
        np.array([], dtype=np.float64),
        channel_id="accel-x",
        unit=Unit.STANDARD_GRAVITY,
        sample_range=ProcessingRange(3, 3),
        sample_rate_hz=50000.0,
        recipe_hash=processor.recipe_hash,
        calibration=calibrations[0],
        clipping_margin_v=0.0,
    )
    assert empty.rms.availability is MetricAvailability.UNAVAILABLE
    assert empty.notices[0].code == "DAQ.PROCESSING.METRIC_NOT_EVALUATED"


@pytest.mark.parametrize(
    ("values", "expected_mean", "expected_rms", "expected_peak", "expected_peak_to_peak"),
    [
        (np.full(8, 3.0), 3.0, 3.0, 3.0, 0.0),
        (np.array([0.0, 2.0, 0.0, -2.0]), 0.0, np.sqrt(2.0), 2.0, 4.0),
        (np.array([0.0, 0.0, -4.0, 0.0]), -1.0, 2.0, 4.0, 4.0),
    ],
)
def test_metric_reference_vectors(
    values: np.ndarray,
    expected_mean: float,
    expected_rms: float,
    expected_peak: float,
    expected_peak_to_peak: float,
) -> None:
    numerical, processor, recipe, calibrations = _processor()
    assert recipe.metrics is not None
    result = numerical.metrics(
        np.zeros(len(values)),
        values,
        channel_id="accel-x",
        unit=Unit.STANDARD_GRAVITY,
        sample_range=ProcessingRange(0, len(values)),
        sample_rate_hz=50000.0,
        recipe_hash=processor.recipe_hash,
        calibration=calibrations[0],
        clipping_margin_v=recipe.metrics.clipping_margin_v,
    )
    assert result.mean.value == pytest.approx(expected_mean)
    assert result.rms.value == pytest.approx(expected_rms)
    assert result.absolute_peak.value == pytest.approx(expected_peak)
    assert result.peak_to_peak.value == pytest.approx(expected_peak_to_peak)


def test_fft_amplitude_and_welch_psd_match_analytical_sine() -> None:
    numerical, processor, recipe, calibrations = _processor()
    assert recipe.fft is not None and recipe.psd is not None
    count = 50000
    frequency = 1000.0
    amplitude = 2.5
    time = np.arange(count, dtype=np.float64) / 50000.0
    values = amplitude * np.sin(2 * np.pi * frequency * time)
    sample_range = ProcessingRange(0, count)
    fft = numerical.fft_amplitude(
        values,
        channel_id="accel-x",
        unit=Unit.STANDARD_GRAVITY,
        sample_range=sample_range,
        sample_rate_hz=50000.0,
        recipe_hash=processor.recipe_hash,
        calibration_hash=calibrations[0].calibration_hash,
        maximum_samples=recipe.fft.maximum_samples_per_channel,
    )
    peak = int(np.argmax(fft.amplitudes))
    assert fft.frequencies_hz[peak] == frequency
    assert fft.amplitudes[peak] == pytest.approx(amplitude, rel=0.005)

    psd = numerical.welch_psd(
        values,
        channel_id="accel-x",
        unit=Unit.STANDARD_GRAVITY,
        sample_range=sample_range,
        sample_rate_hz=50000.0,
        recipe_hash=processor.recipe_hash,
        calibration_hash=calibrations[0].calibration_hash,
        segment_duration_seconds=recipe.psd.segment_duration_seconds,
        overlap_fraction=recipe.psd.overlap_fraction,
    )
    psd_peak = int(np.argmax(psd.density))
    assert abs(psd.frequencies_hz[psd_peak] - frequency) <= 1.0
    assert np.trapezoid(psd.density, psd.frequencies_hz) == pytest.approx(
        amplitude**2 / 2,
        rel=0.02,
    )
    assert psd.density_unit == "g^2/Hz"

    charge_psd = numerical.welch_psd(
        values,
        channel_id="charge-signal",
        unit=Unit.PICOCOULOMB,
        sample_range=sample_range,
        sample_rate_hz=50000.0,
        recipe_hash=processor.recipe_hash,
        calibration_hash=calibrations[3].calibration_hash,
        segment_duration_seconds=recipe.psd.segment_duration_seconds,
        overlap_fraction=recipe.psd.overlap_fraction,
    )
    assert charge_psd.density_unit == "pC^2/Hz"
    assert np.trapezoid(charge_psd.density, charge_psd.frequencies_hz) == pytest.approx(
        amplitude**2 / 2,
        rel=0.02,
    )


def test_fixed_numpy_scipy_reference_hashes_and_realized_sos() -> None:
    numerical, processor, recipe, calibrations = _processor()
    assert recipe.fft is not None and recipe.psd is not None
    count = 4096
    time = np.arange(count, dtype=np.float64) / 50000.0
    samples = np.vstack(
        [
            0.02 * np.sin(2 * np.pi * frequency * time + phase)
            for frequency, phase in ((37, 0.1), (251, 0.2), (1000, 0.3), (731, 0.4))
        ]
    )
    processed = processor.process(_chunk(samples))
    assert processor.recipe_hash == (
        "1388cb65b3b64f5f13ea71887c46c5afb7773ef7d9188ef48c6c324fee3f2737"
    )
    assert tuple(item.calibration_hash for item in calibrations) == (
        "87a74b05fbe2acdf3a666a7776a354945f7e249666a40faded4f83f2d8644f60",
        "7c2d4b707e1af74cc34681a989ede96647ceac31255798a045aa2dcb91c93d7a",
        "a53af546131c1ad48b53d8ea770b3bfd19828019ebe453e8800f02227ea790da",
        "3f193d70ec73487bb897054f3fa7e85af6bcec09b0230ae789cc54d1978d5419",
    )
    fft = numerical.fft_amplitude(
        processed.conditioned_samples[0],
        channel_id="accel-x",
        unit=Unit.STANDARD_GRAVITY,
        sample_range=ProcessingRange(0, count),
        sample_rate_hz=50000.0,
        recipe_hash=processor.recipe_hash,
        calibration_hash=calibrations[0].calibration_hash,
        maximum_samples=recipe.fft.maximum_samples_per_channel,
    )
    psd = numerical.welch_psd(
        processed.conditioned_samples[3],
        channel_id="charge-signal",
        unit=Unit.PICOCOULOMB,
        sample_range=ProcessingRange(0, count),
        sample_rate_hz=50000.0,
        recipe_hash=processor.recipe_hash,
        calibration_hash=calibrations[3].calibration_hash,
        segment_duration_seconds=recipe.psd.segment_duration_seconds,
        overlap_fraction=recipe.psd.overlap_fraction,
    )

    def digest(values: np.ndarray) -> str:
        return hashlib.sha256(np.asarray(values, dtype="<f8").tobytes()).hexdigest()

    assert digest(processed.calibrated_samples) == (
        "6a6f73f8ea8a832104cdc925b54443e1bf6ef7b5d5a5f4cf056180a5fb6074f9"
    )
    assert digest(processed.conditioned_samples) == (
        "08b5698ccc6487a8abac6ec1dec243c5c9720425b9dcf7ac6bdcdd4b72f79593"
    )
    assert digest(fft.amplitudes) == (
        "edb2f75729ad68b1b7b13d74bd56a1cf7d7bd87aa491de11563adc880f31c380"
    )
    assert digest(psd.density) == (
        "b0084bc5a69be4bf508fe50b4fcaf08bfb34a8969c5c5c8c1520c655c8b80e9b"
    )
    details = processor.algorithm_details
    channels = details["channels"]
    assert isinstance(channels, list)
    assert channels[0]["stages"][0]["sos"][0] == pytest.approx(
        [
            0.9998358259334342,
            -1.9996716518668685,
            0.9998358259334342,
            1.0,
            -1.9997678149133267,
            0.9997678307028605,
        ]
    )


def test_bounded_welch_accumulator_is_rechunking_invariant() -> None:
    numerical, processor, recipe, calibrations = _processor()
    assert recipe.psd is not None
    count = 125000
    time = np.arange(count, dtype=np.float64) / 50000.0
    values = np.sin(2 * np.pi * 731.0 * time) + 0.25 * np.sin(2 * np.pi * 93.0 * time)
    expected = numerical.welch_psd(
        values,
        channel_id="charge-signal",
        unit=Unit.PICOCOULOMB,
        sample_range=ProcessingRange(0, count),
        sample_rate_hz=50000.0,
        recipe_hash=processor.recipe_hash,
        calibration_hash=calibrations[3].calibration_hash,
        segment_duration_seconds=recipe.psd.segment_duration_seconds,
        overlap_fraction=recipe.psd.overlap_fraction,
    )
    accumulator = numerical.create_welch_accumulator(
        channel_id="charge-signal",
        unit=Unit.PICOCOULOMB,
        sample_range=ProcessingRange(0, count),
        sample_rate_hz=50000.0,
        recipe_hash=processor.recipe_hash,
        calibration_hash=calibrations[3].calibration_hash,
        segment_duration_seconds=recipe.psd.segment_duration_seconds,
        overlap_fraction=recipe.psd.overlap_fraction,
    )
    start = 0
    for size in (1, 49999, 7, 13000, 60993, 1000):
        accumulator.append(values[start : start + size])
        start += size
    actual = accumulator.finalize()

    np.testing.assert_array_equal(actual.frequencies_hz, expected.frequencies_hz)
    np.testing.assert_array_equal(actual.density, expected.density)


def test_spectral_short_and_oversized_ranges_are_structured() -> None:
    numerical, processor, _, calibrations = _processor()
    with pytest.raises(ProcessingError) as short:
        numerical.fft_amplitude(
            np.array([1.0]),
            channel_id="accel-x",
            unit=Unit.STANDARD_GRAVITY,
            sample_range=ProcessingRange(0, 1),
            sample_rate_hz=50000.0,
            recipe_hash=processor.recipe_hash,
            calibration_hash=calibrations[0].calibration_hash,
            maximum_samples=10,
        )
    assert short.value.code == "DAQ.PROCESSING.SPECTRAL_RANGE_TOO_SHORT"
    with pytest.raises(ProcessingError) as oversized:
        numerical.fft_amplitude(
            np.ones(11),
            channel_id="accel-x",
            unit=Unit.STANDARD_GRAVITY,
            sample_range=ProcessingRange(0, 11),
            sample_rate_hz=50000.0,
            recipe_hash=processor.recipe_hash,
            calibration_hash=calibrations[0].calibration_hash,
            maximum_samples=10,
        )
    assert oversized.value.code == "DAQ.PROCESSING.SPECTRAL_SELECTION_TOO_LARGE"


def test_canonical_hash_is_independent_of_mapping_order_and_recipe_changes() -> None:
    _, _, recipe, _, _ = _configuration()
    assert canonical_sha256({"a": 1, "b": 2}) == canonical_sha256({"b": 2, "a": 1})
    changed_stage = replace(recipe.stages[1], cutoff_hz=2.0)
    changed = replace(recipe, stages=(recipe.stages[0], changed_stage, recipe.stages[2]))
    assert canonical_sha256(recipe) != canonical_sha256(changed)


def test_calibration_and_provenance_json_round_trip_without_information_loss() -> None:
    _, processor, recipe, calibrations = _processor()
    records = JsonProcessingRecordAdapter()
    calibration = calibrations[0]
    assert records.loads_calibration(records.dumps_calibration(calibration)) == calibration

    provenance = ProcessingProvenance(
        schema_version=1,
        provenance_hash="a" * 64,
        source_session_id="session",
        source_completion_state="completed",
        source_raw_digest="b" * 64,
        source_range=ProcessingRange(0, 10),
        ordered_channel_ids=tuple(item.logical_channel_id for item in calibrations),
        sample_rate_hz=50000.0,
        calibration_ids=tuple(item.calibration_id for item in calibrations),
        calibration_hashes=tuple(item.calibration_hash for item in calibrations),
        calibration_qualities=tuple(item.quality for item in calibrations),
        recipe_id=recipe.recipe_id,
        recipe_version=recipe.recipe_version,
        recipe_hash=processor.recipe_hash,
        processing_mode=ProcessingMode.REPLAY,
        application_version="test",
        numpy_version=np.__version__,
        scipy_version="test",
        output_units=tuple(item.output_unit for item in calibrations),
        notices=(),
        algorithm_details={"window": "hann-periodic", "overlap": 0.5},
    )
    provenance = replace(
        provenance,
        provenance_hash=canonical_sha256_excluding(provenance, "provenance_hash"),
    )
    assert records.loads_provenance(records.dumps_provenance(provenance)) == provenance

    corrupted = records.dumps_calibration(calibration).replace(
        calibration.calibration_hash,
        "0" * 64,
    )
    with pytest.raises(ProcessingError) as captured:
        records.loads_calibration(corrupted)
    assert captured.value.code == "DAQ.PROCESSING.CALIBRATION_INVALID"
