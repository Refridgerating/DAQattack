import math
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from labdaq.adapters.configuration_json import JsonConfigurationAdapter
from labdaq.adapters.simulated.adapter import SimulatedDaqAdapter
from labdaq.adapters.simulated.generators import generate_samples
from labdaq.adapters.simulated.scenario import JsonSimulatorScenarioCodec
from labdaq.application.daq_service import DaqService
from labdaq.bootstrap import create_desktop_presenter
from labdaq.domain.models import ExperimentConfig, HardwareProfile
from labdaq.ports.daq import canonical_sha256

pytestmark = pytest.mark.integration

CHANNELS = ("accel-x", "accel-y", "accel-z", "charge-signal")


def test_desktop_default_monitor_is_paced_and_explicit_scenario_is_preserved() -> None:
    scenario_path = Path(
        "src/labdaq/resources/config/simulator-scenarios/clean-four-channel-v1.json"
    )
    with patch(
        "labdaq.adapters.simulated.adapter.SimulatedDaqAdapter", wraps=SimulatedDaqAdapter
    ) as factory:
        presenter = create_desktop_presenter(backend="simulated")
        try:
            assert factory.call_args.args[0].pacing_mode == "real_time"
        finally:
            presenter.close()
        explicit = create_desktop_presenter(backend="simulated", scenario_path=scenario_path)
        try:
            assert factory.call_args.args[0].pacing_mode == "immediate"
        finally:
            explicit.close()


def test_generic_raw_channels_use_selected_physical_scenario_inputs() -> None:
    documents = JsonConfigurationAdapter()
    raw = documents.decode_experiment_draft(
        documents.load_bundled_experiment_template("raw-voltage-v1")
    )
    channels = tuple(
        replace(channel, enabled=channel.channel_id in {"ai1", "ai3"}) for channel in raw.channels
    )
    experiment = replace(
        raw, channels=channels, acquisition=replace(raw.acquisition, channel_ids=("ai1", "ai3"))
    )
    profile = documents.load_bundled_hardware_profile(raw.hardware_profile_id, 1)
    scenario = JsonSimulatorScenarioCodec().load_bundled("clean-four-channel")
    adapter = SimulatedDaqAdapter(scenario)
    assert adapter.validate(profile, experiment).accepted
    configuration = adapter.configure(experiment)
    assert configuration.ordered_logical_channels == ("ai1", "ai3")
    adapter.start()
    try:
        result = adapter.read(128, 1.0)
        expected = generate_samples(
            scenario,
            ("accel-y", "charge-signal"),
            start_sample=0,
            sample_count=128,
            sample_rate_hz=50_000.0,
        )
        np.testing.assert_array_equal(result.samples, expected)
    finally:
        adapter.stop()
        adapter.close()

    clipping = SimulatedDaqAdapter(JsonSimulatorScenarioCodec().load_bundled("clipping"))
    assert clipping.validate(profile, experiment).accepted
    clipping.configure(experiment)
    clipping.start()
    try:
        result = clipping.read(128, 1.0)
        clipped = [
            event for event in result.events if event.code == "DAQ.ACQUISITION.INPUT_CLIPPED"
        ]
        assert {channel for event in clipped for channel in event.context["channels"]} == {
            "ai1",
            "ai3",
        }
    finally:
        clipping.stop()
        clipping.close()


def _read_plan(
    experiment: ExperimentConfig,
    scenario_id: str,
    plan: list[int],
) -> tuple[np.ndarray, list[str], int]:
    adapter = SimulatedDaqAdapter(JsonSimulatorScenarioCodec().load_bundled(scenario_id))
    adapter.configure(experiment)
    adapter.start()
    chunks: list[np.ndarray] = []
    event_codes: list[str] = []
    for size in plan:
        result = adapter.read(size, 1.0)
        chunks.append(result.samples)
        event_codes.extend(event.code for event in result.events)
    final_index = int(adapter.health().backend_details["next_sample_index"])
    adapter.stop()
    adapter.close()
    return np.concatenate(chunks, axis=1), event_codes, final_index


def test_adapter_stream_is_byte_identical_across_chunk_plans(
    synthetic_experiment: ExperimentConfig,
) -> None:
    total = 8191
    full, full_events, full_index = _read_plan(
        synthetic_experiment,
        "deterministic-noise-drift",
        [total],
    )
    plans = [
        [1] * total,
        [7] * (total // 7) + [total % 7],
        [64] * (total // 64) + [total % 64],
        [257] * (total // 257) + [total % 257],
        [1000] * 8 + [191],
        [4096, 4095],
        [8191],
        [3, 11, 509, 1, 2048, 77, 4096, 1446],
    ]

    for plan in plans:
        reconstructed, events, final_index = _read_plan(
            synthetic_experiment,
            "deterministic-noise-drift",
            [size for size in plan if size],
        )
        assert np.array_equal(reconstructed, full)
        assert canonical_sha256(reconstructed) == canonical_sha256(full)
        assert events == full_events
        assert final_index == full_index == total


def test_analytical_sine_recovery_meets_declared_tolerances() -> None:
    scenario = JsonSimulatorScenarioCodec().load_bundled("analytical-sine-reference")
    samples = generate_samples(
        scenario,
        CHANNELS,
        start_sample=0,
        sample_count=50_000,
        sample_rate_hz=50_000.0,
    )
    references = scenario.expected_reference_values
    assert references is not None
    expected_amplitudes = references["amplitudes_v"]
    expected_phases = references["phases_rad"]
    expected_biases = references["biases_v"]
    assert isinstance(expected_amplitudes, list)
    assert isinstance(expected_phases, list)
    assert isinstance(expected_biases, list)
    time = np.arange(samples.shape[1], dtype=np.float64) / 50_000.0
    projection = np.exp(-2j * math.pi * 1000.0 * time)

    for row in range(4):
        centered = samples[row] - float(expected_biases[row])
        coefficient = 2.0 * np.sum(centered * projection) / centered.size
        amplitude = abs(coefficient)
        recovered_phase = math.atan2(coefficient.imag, coefficient.real) + math.pi / 2
        phase_error = math.atan2(
            math.sin(recovered_phase - float(expected_phases[row])),
            math.cos(recovered_phase - float(expected_phases[row])),
        )
        spectrum = np.fft.rfft(centered)
        frequency = np.fft.rfftfreq(centered.size, d=1 / 50_000.0)[int(np.argmax(np.abs(spectrum)))]

        assert abs(frequency - 1000.0) <= 0.5
        assert amplitude == pytest.approx(
            float(expected_amplitudes[row]),
            abs=max(1e-6, 0.001 * float(expected_amplitudes[row])),
        )
        assert abs(math.degrees(phase_error)) <= 0.1


def test_impulse_alignment_survives_boundaries() -> None:
    scenario = JsonSimulatorScenarioCodec().load_bundled("impulse-alignment")
    for split in (12_344, 12_345, 12_346):
        first = generate_samples(
            scenario,
            CHANNELS,
            start_sample=0,
            sample_count=split,
            sample_rate_hz=50_000.0,
        )
        second = generate_samples(
            scenario,
            CHANNELS,
            start_sample=split,
            sample_count=20_000 - split,
            sample_rate_hz=50_000.0,
        )
        samples = np.concatenate((first, second), axis=1)
        assert [int(np.argmax(np.abs(row))) for row in samples] == [12_345] * 4


def test_bounded_service_reports_exact_baseline_and_fault_evidence(
    baseline_profile: HardwareProfile,
    synthetic_experiment: ExperimentConfig,
) -> None:
    clean_service = DaqService(
        SimulatedDaqAdapter(JsonSimulatorScenarioCodec().load_bundled("clean-four-channel"))
    )
    clean = clean_service.acquire_test(
        baseline_profile,
        synthetic_experiment,
        sample_limit=100_000,
        chunk_size=4096,
        timeout_seconds=1.0,
    )
    clean_service.close()

    assert clean.overall == "ok"
    assert clean.samples_per_channel == 100_000
    assert clean.array_shape == (4, 100_000)
    assert clean.chunk_count == 25
    assert len(clean.canonical_sha256) == 64
    assert clean.cleanup_succeeded

    fault_service = DaqService(
        SimulatedDaqAdapter(JsonSimulatorScenarioCodec().load_bundled("dropout"))
    )
    faulted = fault_service.acquire_test(
        baseline_profile,
        synthetic_experiment,
        sample_limit=20_000,
        chunk_size=20_000,
        timeout_seconds=1.0,
    )
    fault_service.close()

    assert faulted.overall == "error"
    assert faulted.samples_per_channel == 12_345
    assert faulted.fault is not None
    assert faulted.fault.code == "DAQ.ACQUISITION.SAMPLE_LOSS"
    assert faulted.final_health.state.value == "stopped"
    assert faulted.cleanup_succeeded


def test_scenario_file_codec_and_bundled_codec_agree() -> None:
    path = Path("src/labdaq/resources/config/simulator-scenarios/clean-four-channel-v1.json")
    codec = JsonSimulatorScenarioCodec()
    assert codec.load(path) == codec.load_bundled("clean-four-channel")
