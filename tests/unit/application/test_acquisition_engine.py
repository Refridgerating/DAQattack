from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty
from threading import Event, Thread
from typing import TYPE_CHECKING

import numpy as np
import pytest

from labdaq.adapters.configuration_json import JsonConfigurationAdapter
from labdaq.adapters.simulated.adapter import SimulatedDaqAdapter
from labdaq.adapters.simulated.scenario import JsonSimulatorScenarioCodec
from labdaq.application.acquisition_engine import AcquisitionEngine
from labdaq.domain.enums import SamplingMode
from labdaq.errors import (
    AcquisitionStreamAccessError,
    AcquisitionWaitTimeoutError,
    ApplicationStateError,
    ErrorSeverity,
)
from labdaq.ports.acquisition import (
    AcquisitionState,
    AcquisitionStreamClosedError,
    CompletionReason,
)
from labdaq.ports.daq import (
    DaqCancellationResult,
    DaqEvent,
    DaqLifecycleState,
    DaqReadResult,
    DaqStopError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from labdaq.domain.models import ExperimentConfig, HardwareProfile
    from labdaq.ports.daq import DaqPort

FIXTURE = Path("tests/fixtures/configuration/valid-four-channel-synthetic-v1.json")


def _configuration(
    *,
    duration: float | None = 0.01,
    chunk_size: int = 128,
    recording_capacity: int = 32,
    display_capacity: int = 2,
    backpressure_timeout: float = 0.1,
    stop_timeout: float = 0.2,
    read_timeout: float = 1.0,
) -> tuple[HardwareProfile, ExperimentConfig]:
    codec = JsonConfigurationAdapter()
    experiment = codec.load_experiment_config(FIXTURE)
    acquisition = replace(
        experiment.acquisition,
        sampling_mode=(SamplingMode.FINITE if duration is not None else SamplingMode.CONTINUOUS),
        requested_duration_seconds=duration,
        chunk_config=replace(
            experiment.acquisition.chunk_config,
            samples_per_channel=chunk_size,
            read_timeout_seconds=read_timeout,
            stop_timeout_seconds=stop_timeout,
            recording_queue_capacity_chunks=recording_capacity,
            recording_backpressure_timeout_seconds=backpressure_timeout,
            display_queue_capacity_chunks=display_capacity,
            progress_event_interval_seconds=0.0001,
        ),
    )
    experiment = replace(experiment, acquisition=acquisition)
    profile = codec.load_bundled_hardware_profile(
        experiment.hardware_profile_id,
        experiment.compatible_profile_revision,
    )
    return profile, experiment


def _simulator(scenario: str = "clean-four-channel") -> SimulatedDaqAdapter:
    return SimulatedDaqAdapter(JsonSimulatorScenarioCodec().load_bundled(scenario))


def _engine(
    daq: DaqPort | None = None,
) -> AcquisitionEngine:
    return AcquisitionEngine(
        daq or _simulator(),
        session_id_factory=lambda: "00000000-0000-0000-0000-000000000004",
    )


def _drain(stream: object) -> list[object]:
    items: list[object] = []
    while True:
        try:
            items.append(stream.get_nowait())  # type: ignore[attr-defined]
        except AcquisitionStreamClosedError:
            return items
        except Empty:
            continue


def test_runtime_configuration_defaults_are_backward_compatible() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    chunk = payload["acquisition"]["chunk_config"]
    for key in (
        "stop_timeout_seconds",
        "recording_queue_capacity_chunks",
        "recording_backpressure_timeout_seconds",
        "display_queue_capacity_chunks",
        "progress_event_interval_seconds",
    ):
        del chunk[key]

    experiment = JsonConfigurationAdapter().decode_experiment_config(payload)
    runtime = experiment.acquisition.chunk_config

    assert runtime.stop_timeout_seconds == 5.0
    assert runtime.recording_queue_capacity_chunks == 32
    assert runtime.recording_backpressure_timeout_seconds == 2.0
    assert runtime.display_queue_capacity_chunks == 2
    assert runtime.progress_event_interval_seconds == 0.5


def test_effective_daq_configuration_is_read_only_and_requires_configuration() -> None:
    profile, experiment = _configuration()
    engine = _engine()
    with pytest.raises(AcquisitionStreamAccessError) as unavailable:
        engine.daq_configuration()
    assert unavailable.value.code == "DAQ.ACQUISITION.CONFIGURATION_UNAVAILABLE"

    engine.configure(profile, experiment)
    engine.arm()
    effective = engine.daq_configuration()
    assert effective.effective_sample_rate_hz == experiment.acquisition.sample_rate_hz
    assert effective.ordered_logical_channels == experiment.acquisition.channel_ids
    engine.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("stop_timeout_seconds", 0.0),
        ("recording_queue_capacity_chunks", 0),
        ("recording_backpressure_timeout_seconds", float("inf")),
        ("display_queue_capacity_chunks", True),
        ("progress_event_interval_seconds", -1.0),
    ],
)
def test_runtime_configuration_rejects_invalid_values(field: str, value: object) -> None:
    _, experiment = _configuration()
    with pytest.raises(ValueError):
        replace(experiment.acquisition.chunk_config, **{field: value})


def test_state_machine_rejects_illegal_transitions_without_changing_state() -> None:
    profile, experiment = _configuration()
    engine = _engine()
    events = engine.subscribe_events()

    with pytest.raises(ApplicationStateError):
        engine.start()
    assert engine.status().state is AcquisitionState.IDLE

    assert engine.configure(profile, experiment).state is AcquisitionState.CONFIGURED
    with pytest.raises(ApplicationStateError):
        engine.configure(profile, experiment)
    assert engine.status().state is AcquisitionState.CONFIGURED

    assert engine.arm().state is AcquisitionState.ARMED
    assert engine.start().state is AcquisitionState.RUNNING
    with pytest.raises(ApplicationStateError):
        engine.reset()
    completion = engine.wait_for_terminal(2.0)
    assert completion.terminal_state is AcquisitionState.COMPLETED
    with pytest.raises(ApplicationStateError):
        engine.start()
    assert engine.reset().state is AcquisitionState.IDLE

    codes = [event.code for event in _drain(events)]
    assert codes.count("DAQ.ACQUISITION.ILLEGAL_STATE_TRANSITION") >= 3
    engine.close()


def test_stream_access_wait_validation_armed_reset_and_closed_engine() -> None:
    profile, experiment = _configuration(duration=None, chunk_size=5000)
    engine = _engine()
    event_stream = engine.subscribe_events()

    with pytest.raises(AcquisitionStreamAccessError):
        engine.recording_stream()
    with pytest.raises(Empty):
        event_stream.get_nowait()
    with pytest.raises(ValueError):
        engine.wait_for_terminal(float("nan"))

    engine.configure(profile, experiment)
    engine.arm()
    assert engine.reset().state is AcquisitionState.IDLE
    _drain(event_stream)
    event_stream.close()
    assert event_stream.closed
    with pytest.raises(AcquisitionStreamClosedError):
        event_stream.get_nowait()

    engine.close()
    engine.close()
    with pytest.raises(ApplicationStateError):
        engine.configure(profile, experiment)


def test_exact_duration_has_continuous_indices_and_partial_final_chunk() -> None:
    profile, experiment = _configuration(duration=0.025, chunk_size=512)
    engine = _engine()
    events = engine.subscribe_events()
    engine.configure(profile, experiment)
    engine.arm()
    recording = engine.recording_stream()
    display = engine.display_stream()
    engine.start()

    completion = engine.wait_for_terminal(2.0)
    chunks = _drain(recording)
    display_chunks = _drain(display)

    assert completion.reason is CompletionReason.DURATION_REACHED
    assert completion.total_acquired_samples_per_channel == 1250
    assert completion.total_recording_samples_accepted == 1250
    assert [chunk.sample_count for chunk in chunks] == [512, 512, 226]
    assert [chunk.sequence_number for chunk in chunks] == [0, 1, 2]
    assert [chunk.start_sample_index for chunk in chunks] == [0, 512, 1024]
    assert all(chunk.samples.shape == (4, chunk.sample_count) for chunk in chunks)
    assert all(not chunk.samples.flags.writeable for chunk in chunks)
    assert display_chunks[-1] is chunks[-1]
    assert completion.display_frames_dropped == 1
    terminal_codes = [
        event.code
        for event in _drain(events)
        if event.code in {"DAQ.ACQUISITION.COMPLETED", "DAQ.ACQUISITION.FAULTED"}
    ]
    assert terminal_codes == ["DAQ.ACQUISITION.COMPLETED"]
    assert not engine.status().worker_alive
    engine.close()


def test_display_pressure_never_changes_lossless_recording_result() -> None:
    profile, experiment = _configuration(
        duration=0.05,
        chunk_size=500,
        recording_capacity=8,
        display_capacity=1,
    )
    engine = _engine()
    engine.configure(profile, experiment)
    engine.arm()
    recording = engine.recording_stream()
    display = engine.display_stream()
    engine.start()
    completion = engine.wait_for_terminal(2.0)

    chunks = _drain(recording)
    display_chunks = _drain(display)
    assert sum(chunk.sample_count for chunk in chunks) == 2500
    assert [chunk.sequence_number for chunk in chunks] == list(range(5))
    assert len(display_chunks) == 1
    assert display_chunks[0] is chunks[-1]
    assert completion.display_frames_dropped == 4
    assert completion.fault is None
    engine.close()


def test_recording_saturation_faults_without_silent_eviction() -> None:
    profile, experiment = _configuration(
        duration=0.03,
        chunk_size=500,
        recording_capacity=1,
        backpressure_timeout=0.01,
    )
    engine = _engine()
    engine.configure(profile, experiment)
    engine.arm()
    recording = engine.recording_stream()
    engine.start()

    completion = engine.wait_for_terminal(2.0)
    accepted = _drain(recording)

    assert completion.terminal_state is AcquisitionState.FAULTED
    assert completion.fault is not None
    assert completion.fault.code == "DAQ.ACQUISITION.RECORDING_QUEUE_SATURATED"
    assert completion.total_acquired_samples_per_channel == 1000
    assert completion.total_recording_samples_accepted == 500
    assert len(accepted) == 1
    assert accepted[0].sequence_number == 0
    context = completion.fault.context
    assert context["attempted_sequence"] == 1
    assert context["last_accepted_sequence"] == 0
    engine.close()


def test_concurrent_recording_consumer_preserves_fifo_order() -> None:
    profile, experiment = _configuration(
        duration=0.06,
        chunk_size=250,
        recording_capacity=2,
        backpressure_timeout=0.5,
    )
    engine = _engine()
    engine.configure(profile, experiment)
    engine.arm()
    recording = engine.recording_stream()
    received: list[object] = []

    def consume() -> None:
        while True:
            try:
                received.append(recording.get(1.0))
            except AcquisitionStreamClosedError:
                return

    consumer = Thread(target=consume)
    consumer.start()
    engine.start()
    completion = engine.wait_for_terminal(3.0)
    consumer.join(1.0)

    assert completion.fault is None
    assert sum(chunk.sample_count for chunk in received) == 3000
    assert [chunk.sequence_number for chunk in received] == list(range(12))
    assert [chunk.start_sample_index for chunk in received] == list(range(0, 3000, 250))
    assert not consumer.is_alive()
    engine.close()


class _PartialStopAdapter:
    def __init__(self, inner: DaqPort, partial_count: int) -> None:
        self._inner = inner
        self._partial_count = partial_count
        self.read_entered = Event()
        self.cancelled = Event()
        self.cancel_called = Event()
        self.allow_cancel = Event()

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def read(self, samples_per_channel: int, timeout_seconds: float) -> DaqReadResult:
        self.read_entered.set()
        if not self.cancelled.wait(timeout_seconds):
            return self._inner.read(samples_per_channel, timeout_seconds)
        return self._inner.read(min(self._partial_count, samples_per_channel), timeout_seconds)

    def cancel_pending_read(self) -> DaqCancellationResult:
        self.cancel_called.set()
        assert self.allow_cancel.wait(1.0)
        self.cancelled.set()
        return DaqCancellationResult(
            backend_id="test-partial",
            previous_state=DaqLifecycleState.RUNNING,
            state=DaqLifecycleState.RUNNING,
            requested_at=datetime.now(UTC),
            events=(),
        )


def test_manual_stop_accepts_one_available_partial_final_chunk() -> None:
    profile, experiment = _configuration(duration=None, chunk_size=500)
    adapter = _PartialStopAdapter(_simulator(), partial_count=123)
    engine = _engine(adapter)  # type: ignore[arg-type]
    events = engine.subscribe_events()
    engine.configure(profile, experiment)
    engine.arm()
    recording = engine.recording_stream()
    engine.start()
    assert adapter.read_entered.wait(1.0)

    first = engine.request_stop("test")
    assert adapter.cancel_called.wait(1.0)
    second = engine.request_stop("test-repeated")
    adapter.allow_cancel.set()
    completion = engine.wait_for_terminal(2.0)
    chunks = _drain(recording)

    assert first.state is AcquisitionState.STOPPING
    assert second.state is AcquisitionState.STOPPING
    assert completion.reason is CompletionReason.MANUAL_STOP
    assert completion.total_acquired_samples_per_channel == 123
    assert [chunk.sample_count for chunk in chunks] == [123]
    codes = [event.code for event in _drain(events)]
    assert codes.count("DAQ.ACQUISITION.FINAL_CHUNK_ACCEPTED") == 1
    assert codes.count("DAQ.ACQUISITION.COMPLETED") == 1
    assert codes.count("DAQ.ACQUISITION.STOP_REQUEST_REPEATED") == 1
    engine.close()


def test_concurrent_stop_requests_are_idempotent() -> None:
    profile, experiment = _configuration(duration=None, chunk_size=500)
    adapter = _PartialStopAdapter(_simulator(), partial_count=64)
    engine = _engine(adapter)  # type: ignore[arg-type]
    engine.configure(profile, experiment)
    engine.arm()
    engine.start()
    assert adapter.read_entered.wait(1.0)
    states: list[AcquisitionState] = []
    errors: list[BaseException] = []

    def stop(index: int) -> None:
        try:
            states.append(engine.request_stop(f"concurrent-{index}").state)
        except BaseException as error:
            errors.append(error)

    callers = [Thread(target=stop, args=(index,)) for index in range(8)]
    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join(1.0)
    adapter.allow_cancel.set()
    completion = engine.wait_for_terminal(2.0)

    assert not errors
    assert states == [AcquisitionState.STOPPING] * 8
    assert completion.reason is CompletionReason.MANUAL_STOP
    assert completion.total_recording_samples_accepted == 64
    engine.close()


class _ReadMutationAdapter:
    def __init__(
        self,
        inner: DaqPort,
        mutate: Callable[[DaqReadResult], DaqReadResult],
    ) -> None:
        self._inner = inner
        self._mutate = mutate

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def read(self, samples_per_channel: int, timeout_seconds: float) -> DaqReadResult:
        return self._mutate(self._inner.read(samples_per_channel, timeout_seconds))


def _zero_length_result(result: DaqReadResult) -> DaqReadResult:
    samples = np.empty((4, 0), dtype=np.float64)
    samples.flags.writeable = False
    return DaqReadResult(
        samples=samples,
        samples_read_per_channel=0,
        ordered_logical_channel_ids=result.ordered_logical_channel_ids,
        actual_sample_rate_hz=result.actual_sample_rate_hz,
        end_of_stream=False,
        events=(),
        read_sequence=result.read_sequence,
    )


def _unsafe_read_result(result: DaqReadResult, **changes: object) -> DaqReadResult:
    malformed = object.__new__(DaqReadResult)
    for field in (
        "samples",
        "samples_read_per_channel",
        "ordered_logical_channel_ids",
        "actual_sample_rate_hz",
        "end_of_stream",
        "events",
        "read_sequence",
    ):
        object.__setattr__(malformed, field, changes.get(field, getattr(result, field)))
    return malformed


def _wrong_shape_result(result: DaqReadResult) -> DaqReadResult:
    samples = np.zeros((3, result.samples_read_per_channel), dtype=np.float64)
    samples.flags.writeable = False
    return _unsafe_read_result(result, samples=samples)


def _nonfinite_result(result: DaqReadResult) -> DaqReadResult:
    samples = np.array(result.samples, copy=True)
    samples[0, 0] = np.nan
    samples.flags.writeable = False
    return _unsafe_read_result(result, samples=samples)


def _over_return_result(result: DaqReadResult) -> DaqReadResult:
    return _unsafe_read_result(
        result,
        samples_read_per_channel=result.samples_read_per_channel + 1,
    )


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (
            lambda result: replace(
                result,
                ordered_logical_channel_ids=tuple(reversed(result.ordered_logical_channel_ids)),
            ),
            "DAQ.ACQUISITION.CHANNEL_ORDER_CHANGED",
        ),
        (
            lambda result: replace(result, actual_sample_rate_hz=49_999.0),
            "DAQ.ACQUISITION.SAMPLE_CONTINUITY_VIOLATION",
        ),
        (
            _zero_length_result,
            "DAQ.ACQUISITION.MALFORMED_CHUNK",
        ),
        (_wrong_shape_result, "DAQ.ACQUISITION.MALFORMED_CHUNK"),
        (_nonfinite_result, "DAQ.ACQUISITION.MALFORMED_CHUNK"),
        (_over_return_result, "DAQ.ACQUISITION.MALFORMED_CHUNK"),
    ],
)
def test_invalid_adapter_results_fault_before_recording_enqueue(
    mutate: Callable[[DaqReadResult], DaqReadResult],
    code: str,
) -> None:
    profile, experiment = _configuration()
    adapter = _ReadMutationAdapter(_simulator(), mutate)
    engine = _engine(adapter)  # type: ignore[arg-type]
    engine.configure(profile, experiment)
    engine.arm()
    recording = engine.recording_stream()
    engine.start()
    completion = engine.wait_for_terminal(2.0)

    assert completion.fault is not None
    assert completion.fault.code == code
    assert completion.total_recording_samples_accepted == 0
    assert _drain(recording) == []
    engine.close()


def test_early_end_of_stream_preserves_valid_prefix_then_faults() -> None:
    profile, experiment = _configuration(duration=0.01, chunk_size=128)
    adapter = _ReadMutationAdapter(
        _simulator(),
        lambda result: replace(result, end_of_stream=True),
    )
    engine = _engine(adapter)  # type: ignore[arg-type]
    engine.configure(profile, experiment)
    engine.arm()
    recording = engine.recording_stream()
    engine.start()

    completion = engine.wait_for_terminal(2.0)
    chunks = _drain(recording)

    assert completion.fault is not None
    assert completion.fault.code == "DAQ.ACQUISITION.SAMPLE_CONTINUITY_VIOLATION"
    assert completion.total_recording_samples_accepted == 128
    assert [chunk.start_sample_index for chunk in chunks] == [0]
    engine.close()


def test_simulated_timeout_and_disconnect_are_typed_terminal_faults() -> None:
    profile, delayed_experiment = _configuration(
        duration=0.02,
        chunk_size=500,
        read_timeout=0.1,
    )
    delayed = _engine(_simulator("delayed-read"))
    delayed.configure(profile, delayed_experiment)
    delayed.arm()
    delayed.start()
    timeout = delayed.wait_for_terminal(2.0)
    assert timeout.fault is not None
    assert timeout.fault.code == "DAQ.ACQUISITION.READ_TIMEOUT"
    delayed.close()

    profile, disconnect_experiment = _configuration(duration=0.4, chunk_size=5000)
    disconnected = _engine(_simulator("disconnect"))
    disconnected.configure(profile, disconnect_experiment)
    disconnected.arm()
    disconnected.start()
    result = disconnected.wait_for_terminal(2.0)
    assert result.fault is not None
    assert result.fault.code == "DAQ.HARDWARE.DEVICE_DISCONNECTED"
    assert result.total_recording_samples_accepted == 12345

    assert disconnected.reset().state is AcquisitionState.IDLE
    disconnected.configure(profile, disconnect_experiment)
    assert disconnected.arm().state is AcquisitionState.ARMED
    assert disconnected.reset().state is AcquisitionState.IDLE
    disconnected.close()


class _UncooperativeStopAdapter:
    def __init__(self, inner: DaqPort) -> None:
        self._inner = inner
        self.read_entered = Event()

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def read(self, samples_per_channel: int, timeout_seconds: float) -> DaqReadResult:
        self.read_entered.set()
        return self._inner.read(samples_per_channel, timeout_seconds)

    def cancel_pending_read(self) -> DaqCancellationResult:
        return DaqCancellationResult(
            backend_id="uncooperative-test",
            previous_state=DaqLifecycleState.RUNNING,
            state=DaqLifecycleState.RUNNING,
            requested_at=datetime.now(UTC),
            events=(),
        )


class _CancellationFailingAdapter:
    def __init__(self, inner: DaqPort) -> None:
        self._inner = inner
        self.read_entered = Event()
        self.cancel_attempted = Event()

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def read(self, samples_per_channel: int, timeout_seconds: float) -> DaqReadResult:
        self.read_entered.set()
        assert self.cancel_attempted.wait(timeout_seconds)
        Event().wait(0.02)
        return self._inner.read(samples_per_channel, timeout_seconds)

    def cancel_pending_read(self) -> DaqCancellationResult:
        event = DaqEvent(
            code="DAQ.ACQUISITION.STOP_FAILED",
            severity=ErrorSeverity.ERROR,
            backend_id="test-cancellation-failure",
            operation="cancel_pending_read",
            message="The injected cancellation request failed.",
            timestamp=datetime.now(UTC),
            context={},
        )
        self.cancel_attempted.set()
        raise DaqStopError(event.message, event=event)


def test_cancellation_failure_becomes_a_terminal_fault() -> None:
    profile, experiment = _configuration(duration=None, chunk_size=100)
    adapter = _CancellationFailingAdapter(_simulator())
    engine = _engine(adapter)  # type: ignore[arg-type]
    engine.configure(profile, experiment)
    engine.arm()
    engine.start()
    assert adapter.read_entered.wait(1.0)

    engine.request_stop()
    completion = engine.wait_for_terminal(2.0)

    assert completion.terminal_state is AcquisitionState.FAULTED
    assert completion.fault is not None
    assert completion.fault.code == "DAQ.ACQUISITION.STOP_FAILED"
    assert completion.total_acquired_samples_per_channel == 100
    assert completion.total_recording_samples_accepted == 100
    engine.close()


def test_stop_timeout_forces_close_and_becomes_terminal_fault() -> None:
    profile, experiment = _configuration(
        duration=None,
        chunk_size=50_000,
        stop_timeout=0.01,
        read_timeout=2.0,
    )
    scenario = replace(
        JsonSimulatorScenarioCodec().load_bundled("clean-four-channel"),
        pacing_mode="real_time",
    )
    adapter = _UncooperativeStopAdapter(SimulatedDaqAdapter(scenario))
    engine = _engine(adapter)  # type: ignore[arg-type]
    engine.configure(profile, experiment)
    engine.arm()
    engine.start()
    assert adapter.read_entered.wait(1.0)

    engine.request_stop("timeout-test")
    completion = engine.wait_for_terminal(2.0)

    assert completion.terminal_state is AcquisitionState.FAULTED
    assert completion.fault is not None
    assert completion.fault.code == "DAQ.ACQUISITION.STOP_TIMEOUT"
    assert completion.cleanup.adapter_close_forced
    assert completion.cleanup.succeeded
    engine.close()


class _UnexpectedReadAdapter:
    def __init__(self, inner: DaqPort) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def read(self, samples_per_channel: int, timeout_seconds: float) -> DaqReadResult:
        del samples_per_channel, timeout_seconds
        raise RuntimeError("injected worker failure")


def test_unexpected_worker_failure_is_typed_and_wait_timeout_is_nonterminal() -> None:
    profile, experiment = _configuration(duration=None, chunk_size=5000)
    scenario = replace(
        JsonSimulatorScenarioCodec().load_bundled("clean-four-channel"),
        pacing_mode="real_time",
    )
    waiting = _engine(SimulatedDaqAdapter(scenario))
    waiting.configure(profile, experiment)
    waiting.arm()
    waiting.start()
    with pytest.raises(AcquisitionWaitTimeoutError):
        waiting.wait_for_terminal(0.001)
    assert waiting.status().state is AcquisitionState.RUNNING
    waiting.request_stop()
    assert waiting.wait_for_terminal(2.0).reason is CompletionReason.MANUAL_STOP
    waiting.close()

    failing = _engine(_UnexpectedReadAdapter(_simulator()))  # type: ignore[arg-type]
    failing.configure(profile, experiment)
    failing.arm()
    failing.start()
    completion = failing.wait_for_terminal(2.0)
    assert completion.fault is not None
    assert completion.fault.code == "DAQ.ACQUISITION.WORKER_FAILURE"
    assert completion.fault.context["exception_type"] == "RuntimeError"
    failing.close()


class _CleanupFailingAdapter:
    def __init__(self, inner: DaqPort) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def stop(self) -> object:
        result = self._inner.stop()
        raise RuntimeError(f"cleanup failed after {result.state.value}")


def test_cleanup_failure_becomes_primary_after_successful_acquisition() -> None:
    profile, experiment = _configuration(duration=0.002, chunk_size=100)
    engine = _engine(_CleanupFailingAdapter(_simulator()))  # type: ignore[arg-type]
    engine.configure(profile, experiment)
    engine.arm()
    engine.start()

    completion = engine.wait_for_terminal(2.0)

    assert completion.terminal_state is AcquisitionState.FAULTED
    assert completion.fault is not None
    assert completion.fault.code == "DAQ.ACQUISITION.CLEANUP_FAILED"
    assert completion.total_recording_samples_accepted == 100
    assert not completion.cleanup.succeeded
    engine.close()


def test_cleanup_failure_is_secondary_to_primary_acquisition_fault() -> None:
    profile, experiment = _configuration()
    changed_rate = _ReadMutationAdapter(
        _simulator(),
        lambda result: replace(result, actual_sample_rate_hz=49_999.0),
    )
    adapter = _CleanupFailingAdapter(changed_rate)  # type: ignore[arg-type]
    engine = _engine(adapter)  # type: ignore[arg-type]
    engine.configure(profile, experiment)
    engine.arm()
    engine.start()
    completion = engine.wait_for_terminal(2.0)

    assert completion.fault is not None
    assert completion.fault.code == "DAQ.ACQUISITION.SAMPLE_CONTINUITY_VIOLATION"
    cleanup = completion.fault.context["cleanup_fault"]
    assert cleanup["code"] == "DAQ.ACQUISITION.CLEANUP_FAILED"
    assert completion.cleanup.errors == ("RuntimeError",)
    assert not completion.cleanup.succeeded
    engine.close()
