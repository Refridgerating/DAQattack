from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from labdaq.adapters.configuration_json import JsonConfigurationAdapter
from labdaq.adapters.ni import create_ni_adapter
from labdaq.adapters.tdms.session import TdmsSessionStore
from labdaq.application.acquisition_engine import AcquisitionEngine
from labdaq.application.session_service import RecordingSessionService
from labdaq.bootstrap import load_daq_configuration
from labdaq.ports.acquisition import AcquisitionState
from labdaq.ports.daq import DaqReadTimeoutError
from labdaq.ports.session import IntegrityStatus, SessionCompletionState, VerificationLevel

pytestmark = pytest.mark.hardware

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE = (
    PROJECT_ROOT / "src/labdaq/resources/config/hardware-profiles/"
    "cdaq-9171-ni-9239-four-channel-v1.json"
)
DEFAULT_EXPERIMENT = (
    PROJECT_ROOT / "tests/fixtures/configuration/valid-four-channel-synthetic-v1.json"
)


def _require_opt_in() -> None:
    if os.environ.get("LABDAQ_RUN_NI_HARDWARE") != "1":
        pytest.skip("set LABDAQ_RUN_NI_HARDWARE=1 to run physical NI tests")


def _configuration():
    _require_opt_in()
    profile_path = Path(os.environ.get("LABDAQ_HARDWARE_PROFILE", DEFAULT_PROFILE))
    experiment_path = Path(os.environ.get("LABDAQ_EXPERIMENT_CONFIG", DEFAULT_EXPERIMENT))
    return load_daq_configuration(
        profile_path=profile_path,
        experiment_path=experiment_path,
    )


def test_physical_discovery_and_profile_validation() -> None:
    profile, experiment = _configuration()
    with create_ni_adapter() as adapter:
        discovery = adapter.discover()
        validation = adapter.validate(profile, experiment)

    assert discovery.driver.package_version is not None
    assert discovery.driver.driver_version is not None
    assert len(discovery.devices) == 1
    device = discovery.devices[0]
    assert device.model == "cDAQ-9171"
    assert device.simulated is False
    assert len(device.modules) == 1
    module = device.modules[0]
    assert module.model == "NI-9239"
    assert module.simultaneous_sampling is True
    assert module.reported_slot == 1
    assert len(module.available_channels) == 4
    assert validation.accepted, validation.errors


def test_two_second_physical_acquisition() -> None:
    profile, experiment = _configuration()
    with create_ni_adapter() as adapter:
        validation = adapter.validate(profile, experiment)
        assert validation.accepted, validation.errors
        configuration = adapter.configure(experiment)
        adapter.start()
        chunks = []
        acquired = 0
        while acquired < 100_000:
            result = adapter.read(min(5_000, 100_000 - acquired), 2.0)
            chunks.append(result.samples)
            acquired += result.samples_read_per_channel
        stopped = adapter.stop()

    samples = np.concatenate(chunks, axis=1)
    assert configuration.requested_sample_rate_hz == 50_000.0
    assert configuration.effective_sample_rate_hz == 50_000.0
    assert configuration.ordered_physical_channels == tuple(
        f"cDAQ1Mod1/ai{index}" for index in range(4)
    )
    assert samples.shape == (4, 100_000)
    assert samples.dtype == np.float64
    assert np.all(np.isfinite(samples))
    assert stopped.samples_emitted_per_channel == 100_000


def test_twenty_physical_lifecycle_cycles() -> None:
    profile, experiment = _configuration()
    for _ in range(20):
        with create_ni_adapter() as adapter:
            validation = adapter.validate(profile, experiment)
            assert validation.accepted, validation.errors
            adapter.configure(experiment)
            adapter.start()
            result = adapter.read(100, 1.0)
            assert result.samples.shape == (4, 100)
            adapter.stop()


def test_physical_durable_session_round_trip(tmp_path: Path) -> None:
    profile, experiment = _configuration()
    experiment = replace(
        experiment,
        session=replace(experiment.session, parent_output_directory=tmp_path),
        acquisition=replace(
            experiment.acquisition,
            requested_duration_seconds=0.2,
        ),
    )
    recipe = JsonConfigurationAdapter().load_bundled_processing_recipe(
        experiment.draft_processing_recipe_id
    )
    store = TdmsSessionStore()
    adapter = create_ni_adapter()
    service = RecordingSessionService(adapter, store, store, store)
    plan = service.preview(experiment.session)
    service.prepare(plan, profile, experiment, recipe)
    service.start()
    completion = service.wait_for_terminal(10.0)

    assert completion.state is SessionCompletionState.COMPLETED
    assert completion.acquired_samples_per_channel == completion.persisted_samples_per_channel
    assert completion.integrity.overall is IntegrityStatus.PASS
    summary = store.summarize_session(plan.session_path, level=VerificationLevel.FULL)
    replay = list(store.open_replay(plan.session_path, chunk_size=997))
    replayed = np.concatenate([chunk.samples for chunk in replay], axis=1)
    assert replayed.shape == (4, completion.persisted_samples_per_channel)
    assert np.all(np.isfinite(replayed))
    assert len(set(summary.per_channel_sample_counts.values())) == 1
    assert summary.actual_sample_rate_hz > 0
    service.close()

    with create_ni_adapter() as released:
        validation = released.validate(profile, experiment)
        assert validation.accepted, validation.errors


def test_physical_timeout_cleanup_and_fresh_recovery() -> None:
    profile, experiment = _configuration()
    with create_ni_adapter() as adapter:
        validation = adapter.validate(profile, experiment)
        assert validation.accepted, validation.errors
        adapter.configure(experiment)
        adapter.start()
        with pytest.raises(DaqReadTimeoutError):
            adapter.read(5_000, 1e-9)
        adapter.stop()

    with create_ni_adapter() as recovered:
        validation = recovered.validate(profile, experiment)
        assert validation.accepted, validation.errors
        recovered.configure(experiment)
        recovered.start()
        result = recovered.read(100, 1.0)
        assert result.samples.shape == (4, 100)
        recovered.stop()


def test_short_physical_acquisition_engine_contract() -> None:
    profile, experiment = _configuration()
    experiment = replace(
        experiment,
        acquisition=replace(
            experiment.acquisition,
            requested_duration_seconds=0.1,
            chunk_config=replace(
                experiment.acquisition.chunk_config,
                samples_per_channel=1000,
            ),
        ),
    )
    engine = AcquisitionEngine(create_ni_adapter())
    try:
        engine.configure(profile, experiment)
        engine.arm()
        recording = engine.recording_stream()
        engine.start()
        completion = engine.wait_for_terminal(5.0)
        chunks = []
        while not recording.empty():
            chunks.append(recording.get_nowait())
    finally:
        engine.close()

    assert completion.terminal_state is AcquisitionState.COMPLETED
    assert completion.total_acquired_samples_per_channel == 5000
    assert completion.total_recording_samples_accepted == 5000
    assert [chunk.sequence_number for chunk in chunks] == list(range(5))
    assert [chunk.start_sample_index for chunk in chunks] == [0, 1000, 2000, 3000, 4000]
