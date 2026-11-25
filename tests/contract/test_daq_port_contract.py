import time
from dataclasses import replace
from datetime import UTC, datetime
from functools import partial
from threading import Thread

import numpy as np
import pytest

from labdaq.adapters.configuration_json import JsonConfigurationAdapter
from labdaq.adapters.simulated.adapter import SimulatedDaqAdapter
from labdaq.adapters.simulated.scenario import (
    DisconnectFault,
    JsonSimulatorScenarioCodec,
)
from labdaq.domain.enums import SamplingMode
from labdaq.domain.models import ExperimentConfig, HardwareProfile
from labdaq.ports.daq import (
    DaqDisconnectedError,
    DaqLifecycleError,
    DaqLifecycleState,
    DaqReadCancelledError,
    DaqReadTimeoutError,
    DaqResetError,
    DaqSampleLossError,
)


class FakeClock:
    def __init__(self) -> None:
        self.elapsed = 0.0
        self.sleeps: list[float] = []

    def wall_time(self) -> datetime:
        return datetime(2026, 7, 28, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.elapsed

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.elapsed += seconds


def _adapter(scenario_id: str = "clean-four-channel", *, clock: FakeClock | None = None):
    scenario = JsonSimulatorScenarioCodec().load_bundled(scenario_id)
    return SimulatedDaqAdapter(scenario, clock=clock)


def test_discovery_is_explicit_simulated_topology() -> None:
    adapter = _adapter()

    report = adapter.discover()
    device = report.devices[0]
    module = device.modules[0]

    assert report.backend_id == "simulated"
    assert device.model == "cDAQ-9171"
    assert device.simulated is True
    assert module.model == "NI-9239"
    assert module.available_channels == tuple(f"SimDev1Mod1/ai{i}" for i in range(4))
    assert module.simultaneous_sampling is True
    assert module.supported_sample_rates_hz == tuple(50_000.0 / divisor for divisor in range(1, 32))


def test_validation_accepts_baseline_and_aggregates_mismatches(
    baseline_profile: HardwareProfile,
    synthetic_experiment: ExperimentConfig,
) -> None:
    adapter = _adapter()
    accepted = adapter.validate(baseline_profile, synthetic_experiment)
    mismatched = adapter.validate(
        replace(
            baseline_profile,
            input_min_v=-20.0,
            input_max_v=20.0,
            maximum_channel_count=5,
        ),
        synthetic_experiment,
    )

    assert accepted.accepted is True
    assert accepted.ordered_logical_channels == synthetic_experiment.acquisition.channel_ids
    assert accepted.ordered_physical_channels[-1] == "SimDev1Mod1/ai3"
    assert mismatched.accepted is False
    assert any(issue.field_path == "input_range_v" for issue in mismatched.errors)


@pytest.mark.parametrize("divisor", [1, 2, 3, 31])
def test_representative_revision_two_rates_acquire_exact_samples(divisor: int) -> None:
    documents = JsonConfigurationAdapter()
    profile = documents.load_bundled_hardware_profile("cdaq-9171-ni-9239-four-channel", 2)
    experiment = documents.decode_experiment_draft(
        documents.load_bundled_experiment_template("four-channel-vibration-synthetic-v2")
    )
    rate = 50_000.0 / divisor
    read_size = int(rate * 0.1 + 0.5)
    experiment = replace(
        experiment,
        acquisition=replace(
            experiment.acquisition,
            sample_rate_hz=rate,
            requested_duration_seconds=0.1,
            chunk_config=replace(
                experiment.acquisition.chunk_config,
                samples_per_channel=read_size,
            ),
        ),
        draft_processing_recipe_id=("baseline" if rate >= 25_000.0 else "baseline-low-rate"),
    )
    adapter = _adapter()

    assert adapter.validate(profile, experiment).accepted
    configured = adapter.configure(experiment)
    adapter.start()
    result = adapter.read(read_size, 1.0)

    assert configured.requested_sample_rate_hz == rate
    assert configured.effective_sample_rate_hz == rate
    assert result.samples.shape == (4, read_size)


def test_read_contract_owns_read_only_float64_arrays_and_preserves_order(
    synthetic_experiment: ExperimentConfig,
) -> None:
    adapter = _adapter()
    configured = adapter.configure(synthetic_experiment)
    adapter.start()
    first = adapter.read(257, 1.0)
    second = adapter.read(7, 1.0)

    assert configured.ordered_logical_channels == (
        "accel-x",
        "accel-y",
        "accel-z",
        "charge-signal",
    )
    assert first.samples.shape == (4, 257)
    assert first.samples.dtype == np.float64
    assert first.samples.flags.c_contiguous
    assert first.samples.flags.owndata
    assert first.samples.flags.writeable is False
    assert not np.shares_memory(first.samples, second.samples)
    with pytest.raises(ValueError):
        first.samples[0, 0] = 0.0


@pytest.mark.parametrize(
    "operation",
    ["read-before-start", "start-twice", "configure-running", "read-after-close"],
)
def test_lifecycle_misuse_is_typed_and_does_not_corrupt_state(
    operation: str,
    synthetic_experiment: ExperimentConfig,
) -> None:
    adapter = _adapter()
    if operation == "read-before-start":
        adapter.configure(synthetic_experiment)
        call = partial(adapter.read, 1, 1.0)
        expected = DaqLifecycleState.CONFIGURED
    elif operation == "start-twice":
        adapter.configure(synthetic_experiment)
        adapter.start()
        call = adapter.start
        expected = DaqLifecycleState.RUNNING
    elif operation == "configure-running":
        adapter.configure(synthetic_experiment)
        adapter.start()
        call = partial(adapter.configure, synthetic_experiment)
        expected = DaqLifecycleState.RUNNING
    else:
        adapter.close()
        call = partial(adapter.read, 1, 1.0)
        expected = DaqLifecycleState.CLOSED

    with pytest.raises(DaqLifecycleError) as captured:
        call()
    assert captured.value.code == "DAQ.ACQUISITION.LIFECYCLE_INVALID"
    assert adapter.health().state is expected


def test_finite_final_partial_read_and_repeatable_end_condition(
    synthetic_experiment: ExperimentConfig,
) -> None:
    finite = replace(
        synthetic_experiment,
        acquisition=replace(
            synthetic_experiment.acquisition,
            requested_duration_seconds=0.01,
        ),
    )
    adapter = _adapter()
    adapter.configure(finite)
    adapter.start()

    first = adapter.read(257, 1.0)
    final = adapter.read(257, 1.0)
    empty = adapter.read(10, 1.0)

    assert first.samples_read_per_channel == 257
    assert first.end_of_stream is False
    assert final.samples_read_per_channel == 243
    assert final.end_of_stream is True
    assert empty.samples.shape == (4, 0)
    assert empty.end_of_stream is True
    assert adapter.health().backend_details["next_sample_index"] == 500


def test_stop_reset_and_reconfigure_reproduce_sample_zero(
    synthetic_experiment: ExperimentConfig,
) -> None:
    adapter = _adapter()
    adapter.configure(synthetic_experiment)
    adapter.start()
    first = adapter.read(100, 1.0).samples.copy()
    stopped = adapter.stop()
    reset = adapter.reset()
    adapter.configure(synthetic_experiment)
    adapter.start()
    repeated = adapter.read(100, 1.0).samples

    assert stopped.state is DaqLifecycleState.STOPPED
    assert reset.state is DaqLifecycleState.IDLE
    assert np.array_equal(first, repeated)
    adapter.close()
    adapter.close()
    assert adapter.health().state is DaqLifecycleState.CLOSED


def test_context_manager_closes_adapter() -> None:
    adapter = _adapter()
    with adapter as entered:
        assert entered is adapter
    assert adapter.health().state is DaqLifecycleState.CLOSED


def test_clipping_emits_one_aggregated_warning_and_continues(
    synthetic_experiment: ExperimentConfig,
) -> None:
    adapter = _adapter("clipping")
    adapter.configure(synthetic_experiment)
    adapter.start()

    first = adapter.read(1000, 1.0)
    second = adapter.read(1000, 1.0)

    assert np.min(first.samples[:3]) >= 1.6
    assert np.max(first.samples[:3]) <= 1.7
    assert np.min(first.samples[3]) >= -0.05
    assert np.max(first.samples[3]) <= 0.05
    assert [event.code for event in first.events] == [
        "DAQ.ACQUISITION.INPUT_CLIPPED",
        "DAQ.ACQUISITION.INPUT_CLIPPED",
    ]
    assert second.events == ()
    assert adapter.health().state is DaqLifecycleState.RUNNING


def test_dropout_returns_clean_prefix_then_faults_without_cursor_advance(
    synthetic_experiment: ExperimentConfig,
) -> None:
    adapter = _adapter("dropout")
    adapter.configure(synthetic_experiment)
    adapter.start()

    prefix = adapter.read(20_000, 1.0)
    assert prefix.samples_read_per_channel == 12_345
    with pytest.raises(DaqSampleLossError) as captured:
        adapter.read(100, 1.0)

    assert captured.value.code == "DAQ.ACQUISITION.SAMPLE_LOSS"
    assert adapter.health().state is DaqLifecycleState.FAULTED
    assert adapter.health().backend_details["next_sample_index"] == 12_345
    adapter.stop()
    adapter.reset()
    assert adapter.health().backend_details["next_sample_index"] == 0


def test_delay_below_timeout_warns_and_timeout_fault_does_not_advance(
    synthetic_experiment: ExperimentConfig,
) -> None:
    successful_clock = FakeClock()
    successful = _adapter("delayed-read", clock=successful_clock)
    successful.configure(synthetic_experiment)
    successful.start()
    successful.read(10, 1.0)
    delayed = successful.read(10, 0.5)

    assert successful_clock.sleeps == [0.25]
    assert [event.code for event in delayed.events] == ["DAQ.ACQUISITION.READ_DELAYED"]

    timeout_clock = FakeClock()
    timed_out = _adapter("delayed-read", clock=timeout_clock)
    timed_out.configure(synthetic_experiment)
    timed_out.start()
    timed_out.read(10, 1.0)
    with pytest.raises(DaqReadTimeoutError):
        timed_out.read(10, 0.1)
    assert timed_out.health().backend_details["next_sample_index"] == 10
    assert timeout_clock.sleeps == []


def test_disconnect_prefix_health_repeat_failure_and_reconnectable_reset(
    synthetic_experiment: ExperimentConfig,
) -> None:
    adapter = _adapter("disconnect")
    adapter.configure(synthetic_experiment)
    adapter.start()

    prefix = adapter.read(20_000, 1.0)
    assert prefix.samples_read_per_channel == 12_345
    with pytest.raises(DaqDisconnectedError):
        adapter.read(1, 1.0)
    assert adapter.health().device_available is False
    with pytest.raises(DaqDisconnectedError):
        adapter.read(1, 1.0)

    adapter.stop()
    adapter.reset()
    assert adapter.health().device_available is True
    assert adapter.health().state is DaqLifecycleState.IDLE


def test_nonreconnectable_disconnect_rejects_reset(
    synthetic_experiment: ExperimentConfig,
) -> None:
    scenario = JsonSimulatorScenarioCodec().load_bundled("disconnect")
    fault = scenario.fault_schedule[0]
    assert isinstance(fault, DisconnectFault)
    adapter = SimulatedDaqAdapter(
        replace(scenario, fault_schedule=(replace(fault, reconnectable=False),))
    )
    adapter.configure(synthetic_experiment)
    adapter.start()
    adapter.read(20_000, 1.0)
    with pytest.raises(DaqDisconnectedError):
        adapter.read(1, 1.0)
    adapter.stop()

    with pytest.raises(DaqResetError):
        adapter.reset()
    assert adapter.health().device_available is False


def test_real_time_pacing_uses_injected_clock(
    synthetic_experiment: ExperimentConfig,
) -> None:
    clock = FakeClock()
    scenario = replace(
        JsonSimulatorScenarioCodec().load_bundled("clean-four-channel"),
        pacing_mode="real_time",
    )
    adapter = SimulatedDaqAdapter(scenario, clock=clock)
    adapter.configure(synthetic_experiment)
    adapter.start()

    adapter.read(500, 1.0)
    adapter.read(500, 1.0)

    assert clock.sleeps == pytest.approx([0.01, 0.01])


def test_pending_simulated_read_can_be_cancelled_without_sample_advance(
    synthetic_experiment: ExperimentConfig,
) -> None:
    scenario = replace(
        JsonSimulatorScenarioCodec().load_bundled("clean-four-channel"),
        pacing_mode="real_time",
    )
    continuous = replace(
        synthetic_experiment,
        acquisition=replace(
            synthetic_experiment.acquisition,
            sampling_mode=SamplingMode.CONTINUOUS,
            requested_duration_seconds=None,
        ),
    )
    adapter = SimulatedDaqAdapter(scenario)
    adapter.configure(continuous)
    adapter.start()
    errors: list[BaseException] = []

    def read() -> None:
        try:
            adapter.read(50_000, 2.0)
        except BaseException as error:
            errors.append(error)

    worker = Thread(target=read)
    worker.start()
    time.sleep(0.01)
    result = adapter.cancel_pending_read()
    worker.join(1.0)

    assert result.previous_state is DaqLifecycleState.RUNNING
    assert result.state is DaqLifecycleState.RUNNING
    assert len(errors) == 1
    assert isinstance(errors[0], DaqReadCancelledError)
    assert adapter.health().backend_details["next_sample_index"] == 0
    assert adapter.health().state is DaqLifecycleState.CONFIGURED
    adapter.stop()
