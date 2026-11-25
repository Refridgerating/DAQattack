import json
import math
from dataclasses import replace
from importlib.resources import files

import numpy as np
import pytest

from labdaq.adapters.simulated.generators import generate_samples
from labdaq.adapters.simulated.scenario import (
    ChannelSignal,
    JsonSimulatorScenarioCodec,
    NoiseComponent,
    SimulatorScenarioError,
)
from labdaq.ports.daq import canonical_sample_bytes, canonical_sha256

CHANNELS = ("accel-x", "accel-y", "accel-z", "charge-signal")


@pytest.mark.parametrize(
    "scenario_id",
    [
        "clean-four-channel",
        "analytical-sine-reference",
        "impulse-alignment",
        "swept-sine",
        "deterministic-noise-drift",
        "clipping",
        "dropout",
        "delayed-read",
        "disconnect",
        "qualification-full-healthy",
    ],
)
def test_all_bundled_scenarios_load_as_closed_version_one_documents(
    scenario_id: str,
) -> None:
    scenario = JsonSimulatorScenarioCodec().load_bundled(scenario_id)

    assert scenario.schema_version == 1
    assert scenario.generator_version == "sim-v1"
    assert tuple(channel.channel_id for channel in scenario.channel_signals) == CHANNELS


def test_scenario_codec_reports_json_schema_and_semantic_errors() -> None:
    codec = JsonSimulatorScenarioCodec()
    with pytest.raises(SimulatorScenarioError) as malformed:
        codec.loads("{", document="bad.json")
    assert malformed.value.code == "DAQ.SIMULATOR.SCENARIO_INVALID"
    assert malformed.value.cause is not None

    clean = codec.load_bundled("clean-four-channel")
    raw = json.loads(
        files("labdaq.resources")
        .joinpath("config/simulator-scenarios/clean-four-channel-v1.json")
        .read_text(encoding="utf-8")
    )
    raw["unknown"] = True
    with pytest.raises(SimulatorScenarioError) as schema_error:
        codec.loads(json.dumps(raw))
    assert schema_error.value.event.context["issues"]

    assert clean.seed == 20260728
    raw.pop("unknown")
    raw["generator_version"] = "future"
    with pytest.raises(SimulatorScenarioError) as semantic_error:
        codec.loads(json.dumps(raw))
    assert semantic_error.value.event.context["issues"]

    assert codec.loads(codec.dumps(clean)) == clean


def test_analytical_sine_matches_exact_formula_at_selected_indices() -> None:
    scenario = JsonSimulatorScenarioCodec().load_bundled("analytical-sine-reference")
    indices = np.array([0, 1, 25, 50, 1234], dtype=np.int64)

    generated = generate_samples(
        scenario,
        CHANNELS,
        start_sample=0,
        sample_count=1235,
        sample_rate_hz=50_000.0,
    )
    expected = 1.65 + 0.0063 * np.sin(2 * math.pi * 1000.0 * indices / 50_000.0)

    np.testing.assert_allclose(generated[0, indices], expected, rtol=0, atol=1e-15)
    assert generated.dtype == np.float64
    assert generated.flags.c_contiguous


def test_impulse_location_and_width_are_exact() -> None:
    scenario = JsonSimulatorScenarioCodec().load_bundled("impulse-alignment")
    generated = generate_samples(
        scenario,
        CHANNELS,
        start_sample=12_340,
        sample_count=11,
        sample_rate_hz=50_000.0,
    )

    assert np.flatnonzero(generated[0]).tolist() == [5]
    assert generated[:, 5].tolist() == [0.1, -0.2, 0.3, -0.4]


def test_noise_is_index_addressable_and_partition_invariant() -> None:
    scenario = JsonSimulatorScenarioCodec().load_bundled("deterministic-noise-drift")
    whole = generate_samples(
        scenario,
        CHANNELS,
        start_sample=0,
        sample_count=10_003,
        sample_rate_hz=50_000.0,
    )
    parts = [
        generate_samples(
            scenario,
            CHANNELS,
            start_sample=start,
            sample_count=count,
            sample_rate_hz=50_000.0,
        )
        for start, count in ((0, 1), (1, 7), (8, 257), (265, 4096), (4361, 5642))
    ]
    reconstructed = np.concatenate(parts, axis=1)

    assert np.array_equal(whole, reconstructed)
    assert canonical_sample_bytes(whole) == canonical_sample_bytes(reconstructed)
    assert canonical_sha256(whole) == canonical_sha256(reconstructed)


def test_noise_streams_are_stable_independent_and_statistically_scaled() -> None:
    codec = JsonSimulatorScenarioCodec()
    scenario = codec.load_bundled("deterministic-noise-drift")
    noise_only = replace(
        scenario,
        channel_signals=tuple(
            ChannelSignal(
                channel_id=channel.channel_id,
                raw_unit="V",
                bias_v=0.0,
                components=tuple(
                    component
                    for component in channel.components
                    if isinstance(component, NoiseComponent)
                ),
            )
            for channel in scenario.channel_signals
        ),
    )
    first = generate_samples(
        noise_only,
        CHANNELS,
        start_sample=0,
        sample_count=200_000,
        sample_rate_hz=50_000.0,
    )
    repeat = generate_samples(
        noise_only,
        CHANNELS,
        start_sample=0,
        sample_count=200_000,
        sample_rate_hz=50_000.0,
    )
    changed_seed = generate_samples(
        replace(noise_only, seed=noise_only.seed + 1),
        CHANNELS,
        start_sample=0,
        sample_count=200_000,
        sample_rate_hz=50_000.0,
    )

    assert np.array_equal(first, repeat)
    assert not np.array_equal(first, changed_seed)
    assert not np.array_equal(first[0], first[1])
    assert abs(float(np.mean(first[0]))) < 1.0e-5
    assert float(np.sqrt(np.mean(first[0] ** 2))) == pytest.approx(0.001, rel=0.01)


def test_chirp_and_drift_use_absolute_time_not_chunk_history() -> None:
    chirp = JsonSimulatorScenarioCodec().load_bundled("swept-sine")
    drift = JsonSimulatorScenarioCodec().load_bundled("deterministic-noise-drift")
    drift_only = replace(
        drift,
        channel_signals=tuple(
            replace(
                channel,
                components=tuple(
                    component
                    for component in channel.components
                    if not isinstance(component, NoiseComponent)
                ),
            )
            for channel in drift.channel_signals
        ),
    )

    chirp_a = generate_samples(
        chirp,
        CHANNELS,
        start_sample=4990,
        sample_count=20,
        sample_rate_hz=50_000.0,
    )
    chirp_b = generate_samples(
        chirp,
        CHANNELS,
        start_sample=5000,
        sample_count=10,
        sample_rate_hz=50_000.0,
    )
    drift_a = generate_samples(
        drift_only,
        CHANNELS,
        start_sample=50_000,
        sample_count=1,
        sample_rate_hz=50_000.0,
    )

    assert np.array_equal(chirp_a[:, 10:], chirp_b)
    assert drift_a[0, 0] == pytest.approx(1.651)
    assert drift_a[1, 0] == pytest.approx(1.649)


@pytest.mark.parametrize(("start", "count"), [(-1, 1), (0, -1)])
def test_generator_rejects_negative_bounds(start: int, count: int) -> None:
    scenario = JsonSimulatorScenarioCodec().load_bundled("clean-four-channel")
    with pytest.raises(ValueError, match="non-negative"):
        generate_samples(
            scenario,
            CHANNELS,
            start_sample=start,
            sample_count=count,
            sample_rate_hz=50_000.0,
        )
