from __future__ import annotations

from dataclasses import replace
from functools import partial
from threading import Event
from typing import TYPE_CHECKING

import numpy as np
import pytest

from labdaq.adapters.configuration_json import JsonConfigurationAdapter
from labdaq.adapters.ni.adapter import NIDaqmxAdapter
from labdaq.adapters.ni.driver import (
    NiDeviceSnapshot,
    NiModuleSnapshot,
    NiSystemSnapshot,
    NiWarningSnapshot,
)
from labdaq.application.acquisition_engine import AcquisitionEngine
from labdaq.ports.acquisition import AcquisitionState
from labdaq.ports.daq import (
    DaqCleanupError,
    DaqConfigurationError,
    DaqDisconnectedError,
    DaqDriverUnavailableError,
    DaqLifecycleError,
    DaqLifecycleState,
    DaqMalformedReadError,
    DaqReadError,
    DaqReadTimeoutError,
    DaqResetError,
    DaqResourceReservedError,
    DaqStartError,
    DaqStopError,
    DaqVerificationError,
)

if TYPE_CHECKING:
    from labdaq.domain.models import ExperimentConfig, HardwareProfile


class FakeNiError(Exception):
    def __init__(self, message: str, error_code: int) -> None:
        super().__init__(message)
        self.error_code = error_code


class FakeTask:
    def __init__(self) -> None:
        self.channels: list[str] = []
        self.ranges: list[tuple[float, float]] = []
        self.timing: tuple[float, str, int] | None = None
        self.actual_rate = 50_000.0
        self.read_count: int | None = None
        self.read_calls = 0
        self.started = False
        self.aborted = False
        self.stopped = False
        self.closed = False
        self.failures: dict[str, BaseException] = {}
        self.warnings = ()

    def _fail(self, operation: str) -> None:
        failure = self.failures.get(operation)
        if failure is not None:
            raise failure

    def add_voltage_channel(
        self,
        physical_channel: str,
        minimum_v: float,
        maximum_v: float,
    ) -> tuple[float, float]:
        self._fail("add")
        self.channels.append(physical_channel)
        self.ranges.append((minimum_v, maximum_v))
        return (-10.0, 10.0)

    def configure_timing(
        self,
        sample_rate_hz: float,
        sampling_mode: str,
        samples_per_channel: int,
    ) -> None:
        self._fail("timing")
        self.timing = (sample_rate_hz, sampling_mode, samples_per_channel)

    def verify_and_commit(self) -> float:
        self._fail("verify")
        return self.actual_rate

    def effective_input_ranges(self) -> tuple[tuple[float, float], ...]:
        return tuple((-10.0, 10.0) for _ in self.ranges)

    def start(self) -> None:
        self._fail("start")
        self.started = True

    def read(
        self,
        buffer: np.ndarray,
        samples_per_channel: int,
        timeout_seconds: float,
    ) -> int:
        del timeout_seconds
        self._fail("read")
        assert buffer.flags.c_contiguous
        assert buffer.flags.writeable
        count = samples_per_channel if self.read_count is None else self.read_count
        for row in range(buffer.shape[0]):
            buffer[row, : max(0, min(count, samples_per_channel))] = row + self.read_calls
        self.read_calls += 1
        return count

    def stop(self) -> None:
        self._fail("stop")
        self.stopped = True

    def abort(self) -> None:
        self._fail("abort")
        self.aborted = True

    def close(self) -> None:
        self._fail("close")
        self.closed = True

    def pop_warnings(self):
        warnings = self.warnings
        self.warnings = ()
        return warnings


class FakeDriver:
    def __init__(self, system: NiSystemSnapshot | None = None) -> None:
        self.system = system or _system()
        self.discover_error: BaseException | None = None
        self.create_error: BaseException | None = None
        self.next_tasks: list[FakeTask] = []
        self.tasks: list[FakeTask] = []
        self.reset_aliases: list[str] = []
        self.reset_error: BaseException | None = None

    def discover(self) -> NiSystemSnapshot:
        if self.discover_error is not None:
            raise self.discover_error
        return self.system

    def create_task(self) -> FakeTask:
        if self.create_error is not None:
            raise self.create_error
        task = self.next_tasks.pop(0) if self.next_tasks else FakeTask()
        self.tasks.append(task)
        return task

    def reset_device(self, alias: str) -> None:
        if self.reset_error is not None:
            raise self.reset_error
        self.reset_aliases.append(alias)


def _module(**changes: object) -> NiModuleSnapshot:
    module = NiModuleSnapshot(
        alias="cDAQ1Mod1",
        product_type="NI 9239 (BNC)",
        product_category="C_SERIES_MODULE",
        serial_number="0x00000002",
        simulated=False,
        chassis_alias="cDAQ1",
        reported_slot=1,
        physical_channels=tuple(f"cDAQ1Mod1/ai{index}" for index in range(4)),
        measurement_quantities=("voltage",),
        terminal_configurations=("differential",),
        input_range_v=(-10.0, 10.0),
        maximum_sample_rate_hz=50_000.0,
        sampling_modes=("finite", "continuous"),
    )
    return replace(module, **changes)


def _device(**changes: object) -> NiDeviceSnapshot:
    device = NiDeviceSnapshot(
        alias="cDAQ1",
        product_type="cDAQ-9171",
        product_category="COMPACT_DAQ_CHASSIS",
        serial_number="0x00000001",
        simulated=False,
        modules=(_module(),),
    )
    return replace(device, **changes)


def _system(*devices: NiDeviceSnapshot) -> NiSystemSnapshot:
    return NiSystemSnapshot(
        package_version="1.6.0",
        driver_version="24.5.0",
        devices=devices or (_device(),),
    )


def _accepted(
    driver: FakeDriver,
    profile: HardwareProfile,
    experiment: ExperimentConfig,
    *,
    tolerance: float = 0.0,
) -> NIDaqmxAdapter:
    adapter = NIDaqmxAdapter(driver, rate_tolerance_hz=tolerance)
    report = adapter.validate(profile, experiment)
    assert report.accepted, report.errors
    return adapter


def test_discovery_normalizes_bnc_model_and_preserves_driver_identity() -> None:
    adapter = NIDaqmxAdapter(FakeDriver())

    report = adapter.discover()
    device = report.devices[0]
    module = device.modules[0]

    assert report.driver.driver_version == "24.5.0"
    assert report.driver.package_version == "1.6.0"
    assert device.model == "cDAQ-9171"
    assert device.reported_model == "cDAQ-9171"
    assert module.model == "NI-9239"
    assert module.reported_model == "NI 9239 (BNC)"
    assert module.slot == 0
    assert module.reported_slot == 1
    assert module.available_channels[-1] == "cDAQ1Mod1/ai3"
    assert module.supported_sample_rates_hz == tuple(50_000.0 / divisor for divisor in range(1, 32))


def test_driver_runtime_failure_is_typed() -> None:
    driver = FakeDriver()
    driver.discover_error = FakeNiError("runtime unavailable", -1)

    with pytest.raises(DaqDriverUnavailableError) as captured:
        NIDaqmxAdapter(driver).discover()

    assert captured.value.code == "DAQ.HARDWARE.DRIVER_UNAVAILABLE"
    assert captured.value.cause is driver.discover_error


@pytest.mark.parametrize(
    ("system", "field_path"),
    [
        (NiSystemSnapshot("1.6.0", "24.5.0", ()), "devices[0]"),
        (_system(_device(product_type="cDAQ-9189")), "devices[0]"),
        (_system(_device(simulated=True)), "devices[0]"),
        (_system(_device(), replace(_device(), alias="cDAQ2")), "devices[0]"),
        (_system(_device(modules=())), "modules[0]"),
        (_system(_device(modules=(_module(product_type="NI 9238"),))), "modules[0]"),
        (_system(_device(modules=(_module(reported_slot=2),))), "modules[0].logical_slot"),
        (_system(_device(modules=(_module(simulated=True),))), "modules[0]"),
    ],
)
def test_validation_rejects_topology_mismatches(
    system: NiSystemSnapshot,
    field_path: str,
    baseline_profile: HardwareProfile,
    synthetic_experiment: ExperimentConfig,
) -> None:
    report = NIDaqmxAdapter(FakeDriver(system)).validate(
        baseline_profile,
        synthetic_experiment,
    )

    assert report.accepted is False
    assert any(issue.field_path == field_path for issue in report.errors)


def test_validation_accepts_alias_and_serial_constraints(
    baseline_profile: HardwareProfile,
    synthetic_experiment: ExperimentConfig,
) -> None:
    device = replace(
        baseline_profile.devices[0],
        alias="cDAQ1",
        expected_serial_number="0x1",
    )
    module = replace(baseline_profile.modules[0], expected_serial_number="2")
    profile = replace(baseline_profile, devices=(device,), modules=(module,))

    report = NIDaqmxAdapter(FakeDriver()).validate(profile, synthetic_experiment)

    assert report.accepted
    assert report.scenario_id is None
    assert report.ordered_physical_channels == tuple(f"cDAQ1Mod1/ai{index}" for index in range(4))


@pytest.mark.parametrize(
    "module",
    [
        _module(physical_channels=("cDAQ1Mod1/ai0",)),
        _module(measurement_quantities=("current",)),
        _module(terminal_configurations=("rse",)),
        _module(input_range_v=(-5.0, 5.0)),
        _module(maximum_sample_rate_hz=49_999.0),
        _module(sampling_modes=("continuous",)),
    ],
)
def test_validation_rejects_unsupported_module_capabilities(
    module: NiModuleSnapshot,
    baseline_profile: HardwareProfile,
    synthetic_experiment: ExperimentConfig,
) -> None:
    system = _system(_device(modules=(module,)))

    report = NIDaqmxAdapter(FakeDriver(system)).validate(
        baseline_profile,
        synthetic_experiment,
    )

    assert report.accepted is False
    assert report.errors


def test_validation_rejects_duplicate_physical_channel(
    baseline_profile: HardwareProfile,
    synthetic_experiment: ExperimentConfig,
) -> None:
    channels = list(synthetic_experiment.channels)
    channels[1] = replace(
        channels[1],
        physical_channel=channels[0].physical_channel,
    )
    experiment = replace(synthetic_experiment, channels=tuple(channels))

    report = NIDaqmxAdapter(FakeDriver()).validate(baseline_profile, experiment)

    assert report.accepted is False
    assert len(report.errors) >= 2


def test_configure_requires_accepted_validation(synthetic_experiment: ExperimentConfig) -> None:
    adapter = NIDaqmxAdapter(FakeDriver())

    with pytest.raises(DaqConfigurationError):
        adapter.configure(synthetic_experiment)


def test_complete_lifecycle_preserves_order_and_owned_buffers(
    baseline_profile: HardwareProfile,
    synthetic_experiment: ExperimentConfig,
) -> None:
    driver = FakeDriver()
    adapter = _accepted(driver, baseline_profile, synthetic_experiment)

    configured = adapter.configure(synthetic_experiment)
    started = adapter.start()
    first = adapter.read(257, 1.0)
    second = adapter.read(7, 1.0)
    stopped = adapter.stop()

    task = driver.tasks[0]
    assert configured.ordered_physical_channels == tuple(
        f"cDAQ1Mod1/ai{index}" for index in range(4)
    )
    assert task.channels == list(configured.ordered_physical_channels)
    assert task.timing == (50_000.0, "finite", 500_000)
    assert started.state is DaqLifecycleState.RUNNING
    assert first.samples.shape == (4, 257)
    assert first.samples.dtype == np.float64
    assert first.samples.flags.owndata
    assert first.samples.flags.writeable is False
    assert not np.shares_memory(first.samples, second.samples)
    assert first.samples[0, 0] == 0.0
    assert second.samples[0, 0] == 1.0
    assert stopped.samples_emitted_per_channel == 264
    assert task.stopped and task.closed
    assert adapter.health().state is DaqLifecycleState.STOPPED


def test_partial_and_finite_end_reads_are_explicit(
    baseline_profile: HardwareProfile,
    synthetic_experiment: ExperimentConfig,
) -> None:
    finite = replace(
        synthetic_experiment,
        acquisition=replace(
            synthetic_experiment.acquisition,
            requested_duration_seconds=0.01,
        ),
    )
    driver = FakeDriver()
    task = FakeTask()
    task.read_count = 100
    driver.next_tasks.append(task)
    adapter = _accepted(driver, baseline_profile, finite)
    adapter.configure(finite)
    adapter.start()

    partial = adapter.read(257, 1.0)
    task.read_count = None
    final = adapter.read(500, 1.0)
    empty = adapter.read(1, 1.0)

    assert partial.samples_read_per_channel == 100
    assert [event.code for event in partial.events] == ["DAQ.ACQUISITION.PARTIAL_READ"]
    assert final.samples_read_per_channel == 400
    assert final.end_of_stream
    assert empty.samples.shape == (4, 0)
    assert empty.end_of_stream


def test_only_floating_point_rate_noise_is_accepted(
    baseline_profile: HardwareProfile,
    synthetic_experiment: ExperimentConfig,
) -> None:
    accepted_driver = FakeDriver()
    accepted_task = FakeTask()
    accepted_task.actual_rate = 50_000.0 + 1e-10
    accepted_driver.next_tasks.append(accepted_task)
    accepted = _accepted(
        accepted_driver,
        baseline_profile,
        synthetic_experiment,
        tolerance=0.5,
    )

    result = accepted.configure(synthetic_experiment)

    assert result.effective_sample_rate_hz == 50_000.0 + 1e-10
    assert not result.warnings

    rejected_driver = FakeDriver()
    rejected_task = FakeTask()
    rejected_task.actual_rate = 49_999.75
    rejected_driver.next_tasks.append(rejected_task)
    rejected = _accepted(
        rejected_driver,
        baseline_profile,
        synthetic_experiment,
        tolerance=0.5,
    )
    with pytest.raises(DaqVerificationError):
        rejected.configure(synthetic_experiment)
    assert rejected_task.closed


@pytest.mark.parametrize("divisor", range(1, 32))
def test_all_revision_two_rates_configure_without_coercion(divisor: int) -> None:
    documents = JsonConfigurationAdapter()
    profile = documents.load_bundled_hardware_profile("cdaq-9171-ni-9239-four-channel", 2)
    experiment = documents.decode_experiment_draft(
        documents.load_bundled_experiment_template("four-channel-vibration-synthetic-v2")
    )
    rate = 50_000.0 / divisor
    experiment = replace(
        experiment,
        acquisition=replace(
            experiment.acquisition,
            sample_rate_hz=rate,
            chunk_config=replace(
                experiment.acquisition.chunk_config,
                samples_per_channel=int(rate * 0.1 + 0.5),
            ),
        ),
        draft_processing_recipe_id=("baseline" if rate >= 25_000.0 else "baseline-low-rate"),
    )
    driver = FakeDriver()
    task = FakeTask()
    task.actual_rate = rate
    driver.next_tasks.append(task)
    adapter = _accepted(driver, profile, experiment)

    configured = adapter.configure(experiment)

    assert configured.requested_sample_rate_hz == rate
    assert configured.effective_sample_rate_hz == rate


@pytest.mark.parametrize(
    ("failure", "error_type"),
    [
        (FakeNiError("resource reserved", -50103), DaqResourceReservedError),
        (FakeNiError("verification failed", -200077), DaqVerificationError),
    ],
)
def test_verification_errors_are_translated_and_cleaned(
    failure: BaseException,
    error_type: type[Exception],
    baseline_profile: HardwareProfile,
    synthetic_experiment: ExperimentConfig,
) -> None:
    driver = FakeDriver()
    task = FakeTask()
    task.failures["verify"] = failure
    driver.next_tasks.append(task)
    adapter = _accepted(driver, baseline_profile, synthetic_experiment)

    with pytest.raises(error_type):
        adapter.configure(synthetic_experiment)

    assert task.closed
    assert adapter.health().state is DaqLifecycleState.FAULTED


def test_configuration_failure_preserves_cleanup_context(
    baseline_profile: HardwareProfile,
    synthetic_experiment: ExperimentConfig,
) -> None:
    driver = FakeDriver()
    task = FakeTask()
    task.failures["add"] = FakeNiError("bad channel", -200170)
    task.failures["close"] = FakeNiError("close failed", -1)
    driver.next_tasks.append(task)
    adapter = _accepted(driver, baseline_profile, synthetic_experiment)

    with pytest.raises(DaqConfigurationError) as captured:
        adapter.configure(synthetic_experiment)

    cleanup = captured.value.event.context["cleanup_errors"]
    assert len(cleanup) == 1
    assert captured.value.cause is task.failures["add"]


def test_start_failure_is_typed_and_closed(
    baseline_profile: HardwareProfile,
    synthetic_experiment: ExperimentConfig,
) -> None:
    driver = FakeDriver()
    task = FakeTask()
    task.failures["start"] = FakeNiError("start failed", -1)
    driver.next_tasks.append(task)
    adapter = _accepted(driver, baseline_profile, synthetic_experiment)
    adapter.configure(synthetic_experiment)

    with pytest.raises(DaqStartError):
        adapter.start()

    assert task.closed


def test_cancel_pending_read_aborts_task_and_is_idempotent(
    baseline_profile: HardwareProfile,
    synthetic_experiment: ExperimentConfig,
) -> None:
    driver = FakeDriver()
    adapter = _accepted(driver, baseline_profile, synthetic_experiment)
    adapter.configure(synthetic_experiment)
    adapter.start()

    first = adapter.cancel_pending_read()
    second = adapter.cancel_pending_read()

    assert first.previous_state is DaqLifecycleState.RUNNING
    assert first.state is DaqLifecycleState.CONFIGURED
    assert second.previous_state is DaqLifecycleState.CONFIGURED
    assert driver.tasks[0].aborted is True
    adapter.stop()


def test_fake_ni_runs_engine_contract_across_repeated_cleanup(
    baseline_profile: HardwareProfile,
    synthetic_experiment: ExperimentConfig,
) -> None:
    experiment = replace(
        synthetic_experiment,
        acquisition=replace(
            synthetic_experiment.acquisition,
            requested_duration_seconds=0.04,
            chunk_config=replace(
                synthetic_experiment.acquisition.chunk_config,
                samples_per_channel=500,
            ),
        ),
    )
    driver = FakeDriver()
    engine = AcquisitionEngine(NIDaqmxAdapter(driver))

    for cycle in range(2):
        engine.configure(baseline_profile, experiment)
        engine.arm()
        recording = engine.recording_stream()
        engine.start()
        completion = engine.wait_for_terminal(2.0)
        chunks = []
        while not recording.empty():
            chunks.append(recording.get_nowait())

        assert completion.terminal_state is AcquisitionState.COMPLETED
        assert completion.total_acquired_samples_per_channel == 2000
        assert completion.total_recording_samples_accepted == 2000
        assert [chunk.sequence_number for chunk in chunks] == [0, 1, 2, 3]
        assert [chunk.start_sample_index for chunk in chunks] == [0, 500, 1000, 1500]
        assert driver.tasks[cycle].stopped and driver.tasks[cycle].closed
        if cycle == 0:
            assert engine.reset().state is AcquisitionState.IDLE

    engine.close()
    assert len(driver.tasks) == 2


def test_engine_stop_aborts_fake_ni_pending_read(
    baseline_profile: HardwareProfile,
    synthetic_experiment: ExperimentConfig,
) -> None:
    class BlockingTask(FakeTask):
        def __init__(self) -> None:
            super().__init__()
            self.read_entered = Event()
            self.read_released = Event()

        def read(
            self,
            buffer: np.ndarray,
            samples_per_channel: int,
            timeout_seconds: float,
        ) -> int:
            del buffer, samples_per_channel, timeout_seconds
            self.read_entered.set()
            assert self.read_released.wait(1.0)
            raise FakeNiError("task aborted", -200010)

        def abort(self) -> None:
            super().abort()
            self.read_released.set()

    task = BlockingTask()
    driver = FakeDriver()
    driver.next_tasks.append(task)
    engine = AcquisitionEngine(NIDaqmxAdapter(driver))
    engine.configure(baseline_profile, synthetic_experiment)
    engine.arm()
    engine.start()
    assert task.read_entered.wait(1.0)

    assert engine.request_stop().state is AcquisitionState.STOPPING
    completion = engine.wait_for_terminal(2.0)
    engine.close()

    assert completion.terminal_state is AcquisitionState.COMPLETED
    assert completion.total_acquired_samples_per_channel == 0
    assert task.aborted and task.closed


@pytest.mark.parametrize(
    ("failure", "error_type"),
    [
        (FakeNiError("samples are not yet available", -200284), DaqReadTimeoutError),
        (FakeNiError("device removed", -200220), DaqDisconnectedError),
        (FakeNiError("generic read failure", -1), DaqReadError),
    ],
)
def test_read_failures_are_typed_and_release_task(
    failure: BaseException,
    error_type: type[Exception],
    baseline_profile: HardwareProfile,
    synthetic_experiment: ExperimentConfig,
) -> None:
    driver = FakeDriver()
    task = FakeTask()
    task.failures["read"] = failure
    driver.next_tasks.append(task)
    adapter = _accepted(driver, baseline_profile, synthetic_experiment)
    adapter.configure(synthetic_experiment)
    adapter.start()

    with pytest.raises(error_type):
        adapter.read(10, 0.1)

    assert task.stopped and task.closed
    assert adapter.health().state is DaqLifecycleState.FAULTED
    if error_type is DaqDisconnectedError:
        assert adapter.health().device_available is False


def test_primary_read_failure_retains_stop_and_close_failures(
    baseline_profile: HardwareProfile,
    synthetic_experiment: ExperimentConfig,
) -> None:
    driver = FakeDriver()
    task = FakeTask()
    task.failures = {
        "read": FakeNiError("read failed", -1),
        "stop": FakeNiError("stop failed", -2),
        "close": FakeNiError("close failed", -3),
    }
    driver.next_tasks.append(task)
    adapter = _accepted(driver, baseline_profile, synthetic_experiment)
    adapter.configure(synthetic_experiment)
    adapter.start()

    with pytest.raises(DaqReadError) as captured:
        adapter.read(10, 1.0)

    assert captured.value.cause is task.failures["read"]
    assert len(captured.value.event.context["cleanup_errors"]) == 2


@pytest.mark.parametrize("read_count", [-1, 11])
def test_invalid_driver_read_count_is_malformed(
    read_count: int,
    baseline_profile: HardwareProfile,
    synthetic_experiment: ExperimentConfig,
) -> None:
    driver = FakeDriver()
    task = FakeTask()
    task.read_count = read_count
    driver.next_tasks.append(task)
    adapter = _accepted(driver, baseline_profile, synthetic_experiment)
    adapter.configure(synthetic_experiment)
    adapter.start()

    with pytest.raises(DaqMalformedReadError):
        adapter.read(10, 1.0)


def test_nonfinite_driver_values_are_malformed(
    baseline_profile: HardwareProfile,
    synthetic_experiment: ExperimentConfig,
) -> None:
    class NonfiniteTask(FakeTask):
        def read(
            self,
            buffer: np.ndarray,
            samples_per_channel: int,
            timeout_seconds: float,
        ) -> int:
            del timeout_seconds
            buffer[:, :samples_per_channel] = np.nan
            return samples_per_channel

    driver = FakeDriver()
    driver.next_tasks.append(NonfiniteTask())
    adapter = _accepted(driver, baseline_profile, synthetic_experiment)
    adapter.configure(synthetic_experiment)
    adapter.start()

    with pytest.raises(DaqMalformedReadError):
        adapter.read(10, 1.0)


@pytest.mark.parametrize(
    "operation",
    ["start-before-configure", "read-before-start", "start-twice", "configure-running"],
)
def test_lifecycle_misuse_is_typed(
    operation: str,
    baseline_profile: HardwareProfile,
    synthetic_experiment: ExperimentConfig,
) -> None:
    driver = FakeDriver()
    adapter = _accepted(driver, baseline_profile, synthetic_experiment)
    if operation == "start-before-configure":
        call = adapter.start
    else:
        adapter.configure(synthetic_experiment)
        if operation == "read-before-start":
            call = partial(adapter.read, 1, 1.0)
        else:
            adapter.start()
            call = (
                adapter.start
                if operation == "start-twice"
                else partial(adapter.configure, synthetic_experiment)
            )

    with pytest.raises(DaqLifecycleError):
        call()


def test_stop_and_close_failures_are_typed(
    baseline_profile: HardwareProfile,
    synthetic_experiment: ExperimentConfig,
) -> None:
    driver = FakeDriver()
    task = FakeTask()
    task.failures["stop"] = FakeNiError("stop failed", -1)
    task.failures["close"] = FakeNiError("close failed", -2)
    driver.next_tasks.append(task)
    adapter = _accepted(driver, baseline_profile, synthetic_experiment)
    adapter.configure(synthetic_experiment)
    adapter.start()

    with pytest.raises(DaqStopError) as captured:
        adapter.stop()
    assert len(captured.value.event.context["cleanup_errors"]) == 1

    close_driver = FakeDriver()
    close_task = FakeTask()
    close_task.failures["close"] = FakeNiError("close failed", -2)
    close_driver.next_tasks.append(close_task)
    close_adapter = _accepted(close_driver, baseline_profile, synthetic_experiment)
    close_adapter.configure(synthetic_experiment)
    with pytest.raises(DaqCleanupError):
        close_adapter.close()
    close_adapter.close()


def test_driver_warning_is_returned_as_structured_event(
    baseline_profile: HardwareProfile,
    synthetic_experiment: ExperimentConfig,
) -> None:
    driver = FakeDriver()
    task = FakeTask()
    driver.next_tasks.append(task)
    adapter = _accepted(driver, baseline_profile, synthetic_experiment)
    adapter.configure(synthetic_experiment)
    adapter.start()
    task.warnings = (
        NiWarningSnapshot(
            warning_type="DaqWarning",
            message="finite acquisition stopped early",
            error_code=200010,
        ),
    )

    stopped = adapter.stop()

    assert [event.code for event in stopped.events] == [
        "DAQ.ACQUISITION.STOPPED",
        "DAQ.HARDWARE.DRIVER_WARNING",
    ]
    assert stopped.events[1].context["ni_warning_code"] == 200010


def test_reset_is_explicit_and_failure_is_typed(
    baseline_profile: HardwareProfile,
    synthetic_experiment: ExperimentConfig,
) -> None:
    driver = FakeDriver()
    adapter = _accepted(driver, baseline_profile, synthetic_experiment)
    adapter.configure(synthetic_experiment)
    adapter.stop()

    result = adapter.reset()

    assert result.state is DaqLifecycleState.IDLE
    assert driver.reset_aliases == ["cDAQ1"]

    failing_driver = FakeDriver()
    failing_driver.reset_error = FakeNiError("reset failed", -1)
    failing = _accepted(failing_driver, baseline_profile, synthetic_experiment)
    failing.configure(synthetic_experiment)
    failing.stop()
    with pytest.raises(DaqResetError):
        failing.reset()


def test_context_manager_and_idempotent_close() -> None:
    adapter = NIDaqmxAdapter(FakeDriver())
    with adapter as entered:
        assert entered is adapter
    adapter.close()
    assert adapter.health().state is DaqLifecycleState.CLOSED
