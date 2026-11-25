from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Thread

import pytest

from labdaq.adapters.configuration_json import JsonConfigurationAdapter
from labdaq.adapters.simulated.adapter import SimulatedDaqAdapter
from labdaq.adapters.simulated.scenario import JsonSimulatorScenarioCodec
from labdaq.application.acquisition_engine import AcquisitionEngine
from labdaq.bootstrap import create_acquisition_engine
from labdaq.ports.acquisition import AcquisitionState, AcquisitionStreamClosedError

pytestmark = pytest.mark.integration


def test_bootstrap_creates_simulated_acquisition_engine() -> None:
    engine = create_acquisition_engine(backend="simulated")

    assert engine.status().state is AcquisitionState.IDLE
    engine.close()


def _configured_engine() -> tuple[AcquisitionEngine, object, object]:
    documents = JsonConfigurationAdapter()
    experiment = documents.load_experiment_config(
        Path("tests/fixtures/configuration/valid-four-channel-synthetic-v1.json")
    )
    experiment = replace(
        experiment,
        acquisition=replace(
            experiment.acquisition,
            requested_duration_seconds=0.2,
            chunk_config=replace(
                experiment.acquisition.chunk_config,
                samples_per_channel=1000,
                recording_queue_capacity_chunks=4,
                display_queue_capacity_chunks=1,
            ),
        ),
    )
    profile = documents.load_bundled_hardware_profile(
        experiment.hardware_profile_id,
        experiment.compatible_profile_revision,
    )
    adapter = SimulatedDaqAdapter(JsonSimulatorScenarioCodec().load_bundled("clean-four-channel"))
    return AcquisitionEngine(adapter), profile, experiment


def test_complete_four_channel_engine_pipeline_and_repeated_cycles() -> None:
    engine, profile, experiment = _configured_engine()

    for cycle in range(3):
        engine.configure(profile, experiment)  # type: ignore[arg-type]
        engine.arm()
        recording = engine.recording_stream()
        received = []

        def consume(
            stream: object = recording,
            destination: list[object] = received,
        ) -> None:
            while True:
                try:
                    destination.append(stream.get(1.0))  # type: ignore[attr-defined]
                except AcquisitionStreamClosedError:
                    return

        consumer = Thread(target=consume)
        consumer.start()
        engine.start()
        completion = engine.wait_for_terminal(3.0)
        consumer.join(1.0)

        assert completion.terminal_state is AcquisitionState.COMPLETED
        assert completion.total_acquired_samples_per_channel == 10_000
        assert completion.total_recording_samples_accepted == 10_000
        assert sum(chunk.sample_count for chunk in received) == 10_000
        assert [chunk.sequence_number for chunk in received] == list(range(10))
        assert [chunk.start_sample_index for chunk in received] == list(range(0, 10_000, 1000))
        assert all(chunk.samples.shape == (4, 1000) for chunk in received)
        assert all(
            chunk.ordered_channel_ids == experiment.acquisition.channel_ids for chunk in received
        )
        assert not consumer.is_alive()
        assert not engine.status().worker_alive
        if cycle < 2:
            assert engine.reset().state is AcquisitionState.IDLE

    engine.close()
