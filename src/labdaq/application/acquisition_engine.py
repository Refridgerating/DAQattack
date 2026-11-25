"""Headless M04 acquisition state machine and single-reader fan-out engine."""

from __future__ import annotations

import math
import time
from collections import deque
from datetime import UTC, datetime
from queue import Empty
from threading import Condition, Event, RLock, Thread, current_thread
from typing import TYPE_CHECKING, Generic, Protocol, TypeVar
from uuid import uuid4

import numpy as np

from labdaq.domain.timing import rates_equivalent, rounded_sample_count
from labdaq.errors import (
    AcquisitionError,
    AcquisitionStreamAccessError,
    AcquisitionWaitTimeoutError,
    ApplicationStateError,
    ErrorSeverity,
)
from labdaq.ports.acquisition import (
    AcquisitionChunk,
    AcquisitionChunkStream,
    AcquisitionCompletion,
    AcquisitionEvent,
    AcquisitionEventStream,
    AcquisitionFaultSummary,
    AcquisitionState,
    AcquisitionStatus,
    AcquisitionStreamClosedError,
    AcquisitionTelemetry,
    CleanupSummary,
    CompletionReason,
)
from labdaq.ports.daq import (
    DaqError,
    DaqEvent,
    DaqLifecycleState,
    DaqProfileMismatchError,
    DaqReadCancelledError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from types import TracebackType

    from labdaq.domain.models import DataChunkConfig, ExperimentConfig, HardwareProfile
    from labdaq.ports.daq import DaqConfigurationResult, DaqPort, DaqReadResult

_T = TypeVar("_T")


class AcquisitionClock(Protocol):
    def wall_time(self) -> datetime: ...

    def monotonic(self) -> float: ...


class SystemAcquisitionClock:
    def wall_time(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()


class _ClosableBuffer(Generic[_T]):
    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self._items: deque[_T] = deque()
        self._condition = Condition()
        self._closed = False

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed

    def qsize(self) -> int:
        with self._condition:
            return len(self._items)

    def empty(self) -> bool:
        return self.qsize() == 0

    def put_lossless(self, item: _T, timeout: float) -> tuple[bool, float, bool]:
        started = time.monotonic()
        deadline = started + timeout
        with self._condition:
            pressure = len(self._items) >= self.capacity
            while len(self._items) >= self.capacity and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False, time.monotonic() - started, pressure
                self._condition.wait(remaining)
            if self._closed:
                return False, time.monotonic() - started, pressure
            self._items.append(item)
            self._condition.notify_all()
            return True, time.monotonic() - started, pressure

    def put_latest(self, item: _T) -> bool:
        with self._condition:
            if self._closed:
                return False
            dropped = len(self._items) >= self.capacity
            if dropped:
                self._items.popleft()
            self._items.append(item)
            self._condition.notify_all()
            return dropped

    def get(self, timeout: float | None) -> _T:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while not self._items:
                if self._closed:
                    raise AcquisitionStreamClosedError("the acquisition stream is closed")
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Empty
                self._condition.wait(remaining)
            item = self._items.popleft()
            self._condition.notify_all()
            return item

    def get_nowait(self) -> _T:
        return self.get(0.0)

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()


class _ChunkReader(AcquisitionChunkStream):
    def __init__(self, buffer: _ClosableBuffer[AcquisitionChunk]) -> None:
        self._buffer = buffer

    @property
    def capacity(self) -> int:
        return self._buffer.capacity

    @property
    def closed(self) -> bool:
        return self._buffer.closed

    def qsize(self) -> int:
        return self._buffer.qsize()

    def empty(self) -> bool:
        return self._buffer.empty()

    def get(self, timeout: float | None = None) -> AcquisitionChunk:
        return self._buffer.get(timeout)

    def get_nowait(self) -> AcquisitionChunk:
        return self._buffer.get_nowait()


class _EventMailbox:
    def __init__(self) -> None:
        self._items: deque[AcquisitionEvent] = deque()
        self._condition = Condition()
        self._closed = False

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed

    def qsize(self) -> int:
        with self._condition:
            return len(self._items)

    def publish(self, event: AcquisitionEvent) -> bool:
        with self._condition:
            if self._closed:
                return False
            coalescible = event.code in {
                "DAQ.ACQUISITION.PROGRESS",
                "DAQ.ACQUISITION.DISPLAY_FRAME_SUPERSEDED",
                "DAQ.ACQUISITION.RECORDING_QUEUE_PRESSURE",
                "DAQ.ACQUISITION.STOP_REQUEST_REPEATED",
            }
            if coalescible:
                for index in range(len(self._items) - 1, -1, -1):
                    if self._items[index].code == event.code:
                        del self._items[index]
                        break
            self._items.append(event)
            self._condition.notify_all()
            return True

    def get(self, timeout: float | None) -> AcquisitionEvent:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while not self._items:
                if self._closed:
                    raise AcquisitionStreamClosedError("the acquisition event stream is closed")
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Empty
                self._condition.wait(remaining)
            return self._items.popleft()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()


class _EventReader(AcquisitionEventStream):
    def __init__(self, mailbox: _EventMailbox) -> None:
        self._mailbox = mailbox

    @property
    def closed(self) -> bool:
        return self._mailbox.closed

    def qsize(self) -> int:
        return self._mailbox.qsize()

    def get(self, timeout: float | None = None) -> AcquisitionEvent:
        return self._mailbox.get(timeout)

    def get_nowait(self) -> AcquisitionEvent:
        return self._mailbox.get(0.0)

    def close(self) -> None:
        self._mailbox.close()


class AcquisitionEngine:
    """Own one experiment lifecycle and one blocking DAQ read worker."""

    def __init__(
        self,
        daq: DaqPort,
        *,
        clock: AcquisitionClock | None = None,
        session_id_factory: Callable[[], object] | None = None,
    ) -> None:
        self._daq = daq
        self._clock = clock or SystemAcquisitionClock()
        self._session_id_factory = session_id_factory or uuid4
        self._lock = RLock()
        self._terminal_event = Event()
        self._state = AcquisitionState.IDLE
        self._closed = False
        self._session_id: str | None = None
        self._profile: HardwareProfile | None = None
        self._experiment: ExperimentConfig | None = None
        self._configuration: DaqConfigurationResult | None = None
        self._recording_buffer: _ClosableBuffer[AcquisitionChunk] | None = None
        self._display_buffer: _ClosableBuffer[AcquisitionChunk] | None = None
        self._recording_reader: _ChunkReader | None = None
        self._display_reader: _ChunkReader | None = None
        self._event_mailboxes: list[_EventMailbox] = []
        self._worker: Thread | None = None
        self._cancel_worker: Thread | None = None
        self._completion: AcquisitionCompletion | None = None
        self._requested_rate: float | None = None
        self._actual_rate: float | None = None
        self._requested_duration: float | None = None
        self._target_samples: int | None = None
        self._ordered_channel_ids: tuple[str, ...] = ()
        self._ordered_channel_units: tuple[str, ...] = ()
        self._acquired_samples = 0
        self._recording_samples = 0
        self._display_samples = 0
        self._display_frames = 0
        self._display_drops = 0
        self._next_sequence = 0
        self._final_sequence: int | None = None
        self._event_sequence = 0
        self._warnings: list[AcquisitionEvent] = []
        self._last_warning_or_fault: str | None = None
        self._fault: AcquisitionFaultSummary | None = None
        self._pending_fault: AcquisitionFaultSummary | None = None
        self._started_at: datetime | None = None
        self._started_monotonic: float | None = None
        self._stopped_monotonic: float | None = None
        self._stop_requested = False
        self._stop_reason: str | None = None
        self._stop_requested_at_sample: int | None = None
        self._last_progress_monotonic: float | None = None
        self._adapter_close_forced = False
        self._terminalized = False
        self._read_count = 0
        self._read_duration_total = 0.0
        self._read_duration_max = 0.0
        self._recording_queue_high_water = 0
        self._recording_backpressure_events = 0
        self._source_missing_samples = 0
        self._source_duplicated_samples = 0
        self._source_reordered_reads = 0
        self._last_source_start: int | None = None
        self._stop_requested_monotonic: float | None = None

    def configure(
        self,
        profile: HardwareProfile,
        experiment: ExperimentConfig,
    ) -> AcquisitionStatus:
        with self._lock:
            self._require_open_locked("configure")
            self._require_state_locked("configure", (AcquisitionState.IDLE,))
            validation = self._daq.validate(profile, experiment)
            if not validation.accepted:
                event = DaqEvent(
                    code="DAQ.HARDWARE.PROFILE_MISMATCH",
                    severity=ErrorSeverity.ERROR,
                    backend_id=validation.backend_id,
                    operation="configure",
                    message="DAQ validation rejected the requested acquisition.",
                    timestamp=self._clock.wall_time(),
                    context={"issues": [issue.to_dict() for issue in validation.errors]},
                )
                raise DaqProfileMismatchError(
                    event.message,
                    event=event,
                    remediation="Correct the hardware profile or experiment configuration.",
                )
            self._reset_run_fields_locked()
            self._session_id = str(self._session_id_factory())
            if not self._session_id:
                raise ValueError("session_id_factory returned an empty value")
            self._profile = profile
            self._experiment = experiment
            self._requested_rate = experiment.acquisition.sample_rate_hz
            self._requested_duration = experiment.acquisition.requested_duration_seconds
            self._state = AcquisitionState.CONFIGURED
            self._emit_locked(
                "DAQ.ACQUISITION.CONFIGURED",
                ErrorSeverity.INFO,
                "The acquisition configuration was accepted.",
                {
                    "backend_id": validation.backend_id,
                    "experiment_config_id": experiment.experiment_config_id,
                    "hardware_profile_id": profile.hardware_profile_id,
                },
            )
            return self._status_locked()

    def arm(self) -> AcquisitionStatus:
        with self._lock:
            self._require_open_locked("arm")
            self._require_state_locked("arm", (AcquisitionState.CONFIGURED,))
            assert self._experiment is not None
            try:
                configuration = self._daq.configure(self._experiment)
                self._configuration = configuration
                self._actual_rate = configuration.effective_sample_rate_hz
                self._ordered_channel_ids = configuration.ordered_logical_channels
                by_id = {channel.channel_id: channel for channel in self._experiment.channels}
                self._ordered_channel_units = tuple(
                    by_id[channel_id].raw_unit.value for channel_id in self._ordered_channel_ids
                )
                self._target_samples = self._derive_target_locked()
                runtime = self._runtime_config_locked()
                self._recording_buffer = _ClosableBuffer(runtime.recording_queue_capacity_chunks)
                self._display_buffer = _ClosableBuffer(runtime.display_queue_capacity_chunks)
                self._recording_reader = _ChunkReader(self._recording_buffer)
                self._display_reader = _ChunkReader(self._display_buffer)
                self._state = AcquisitionState.ARMED
                for warning in configuration.warnings:
                    self._emit_adapter_event_locked(warning)
                self._emit_locked(
                    "DAQ.ACQUISITION.ARMED",
                    ErrorSeverity.INFO,
                    "The DAQ task is reserved and acquisition is armed.",
                    {
                        "actual_sample_rate_hz": self._actual_rate,
                        "channel_count": len(self._ordered_channel_ids),
                        "recording_queue_capacity": runtime.recording_queue_capacity_chunks,
                        "display_queue_capacity": runtime.display_queue_capacity_chunks,
                    },
                )
                return self._status_locked()
            except DaqError as error:
                self._complete_without_worker_locked(self._fault_from_daq(error.event))
                raise
            except Exception as cause:
                fault = self._unexpected_fault("arm", cause)
                self._complete_without_worker_locked(fault)
                raise AcquisitionError(
                    fault.message,
                    code=fault.code,
                    context=dict(fault.context),
                    cause=cause,
                ) from cause

    def start(self) -> AcquisitionStatus:
        with self._lock:
            self._require_open_locked("start")
            self._require_state_locked("start", (AcquisitionState.ARMED,))
            try:
                started = self._daq.start()
                self._state = AcquisitionState.RUNNING
                self._started_at = started.started_at
                self._started_monotonic = self._clock.monotonic()
                for event in started.events:
                    self._emit_adapter_event_locked(event)
                if not started.events:
                    self._emit_locked(
                        "DAQ.ACQUISITION.STARTED",
                        ErrorSeverity.INFO,
                        "Acquisition started.",
                        {},
                    )
                worker = Thread(
                    target=self._worker_main,
                    name=f"labdaq-acquisition-{self._session_id}",
                    daemon=True,
                )
                self._worker = worker
                worker.start()
                return self._status_locked()
            except DaqError as error:
                self._complete_without_worker_locked(self._fault_from_daq(error.event))
                raise
            except Exception as cause:
                fault = self._unexpected_fault("start", cause)
                self._complete_without_worker_locked(fault)
                raise AcquisitionError(
                    fault.message,
                    code=fault.code,
                    context=dict(fault.context),
                    cause=cause,
                ) from cause

    def request_stop(self, reason: str = "operator") -> AcquisitionStatus:
        with self._lock:
            self._require_open_locked("request_stop")
            if self._state is AcquisitionState.STOPPING and self._stop_requested:
                self._emit_locked(
                    "DAQ.ACQUISITION.STOP_REQUEST_REPEATED",
                    ErrorSeverity.WARNING,
                    "A repeated stop request was ignored.",
                    {"reason": reason, "original_reason": self._stop_reason},
                )
                return self._status_locked()
            if self._state in {AcquisitionState.COMPLETED, AcquisitionState.FAULTED} and (
                self._stop_requested
            ):
                return self._status_locked()
            self._require_state_locked("request_stop", (AcquisitionState.RUNNING,))
            self._stop_requested = True
            self._stop_requested_monotonic = self._clock.monotonic()
            self._stop_reason = reason
            self._stop_requested_at_sample = self._acquired_samples
            self._state = AcquisitionState.STOPPING
            self._emit_locked(
                "DAQ.ACQUISITION.STOP_REQUESTED",
                ErrorSeverity.INFO,
                "An orderly acquisition stop was requested.",
                {"reason": reason, "acquired_samples_per_channel": self._acquired_samples},
                sample_index=self._acquired_samples,
            )
            cancel_worker = Thread(
                target=self._cancel_and_watch,
                name=f"labdaq-stop-{self._session_id}",
                daemon=True,
            )
            self._cancel_worker = cancel_worker
            cancel_worker.start()
            return self._status_locked()

    def wait_for_terminal(self, timeout: float | None = None) -> AcquisitionCompletion:
        if timeout is not None and (
            isinstance(timeout, bool) or not math.isfinite(timeout) or timeout < 0
        ):
            raise ValueError("timeout must be finite and non-negative")
        deadline = None if timeout is None else time.monotonic() + timeout
        if not self._terminal_event.wait(timeout):
            with self._lock:
                state = self._state.value
                session_id = self._session_id
            raise AcquisitionWaitTimeoutError(
                "Acquisition did not reach a terminal state before the wait timeout.",
                code="DAQ.ACQUISITION.WAIT_TIMEOUT",
                context={"timeout_seconds": timeout, "state": state, "session_id": session_id},
                remediation="Continue observing the engine or request an orderly stop.",
            )
        worker = self._worker
        if worker is not None and worker is not current_thread():
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            worker.join(remaining)
        with self._lock:
            assert self._completion is not None
            return self._completion

    def status(self) -> AcquisitionStatus:
        with self._lock:
            return self._status_locked()

    def daq_configuration(self) -> DaqConfigurationResult:
        """Return the effective adapter configuration while the run is retained."""
        with self._lock:
            if self._configuration is None:
                raise AcquisitionStreamAccessError(
                    "The effective DAQ configuration is available only after arming.",
                    code="DAQ.ACQUISITION.CONFIGURATION_UNAVAILABLE",
                    context={"state": self._state.value},
                    remediation="Configure and arm the engine before requesting runtime metadata.",
                )
            return self._configuration

    def reset(self) -> AcquisitionStatus:
        with self._lock:
            self._require_open_locked("reset")
            self._require_state_locked(
                "reset",
                (
                    AcquisitionState.CONFIGURED,
                    AcquisitionState.ARMED,
                    AcquisitionState.COMPLETED,
                    AcquisitionState.FAULTED,
                ),
            )
            if self._state is AcquisitionState.ARMED:
                try:
                    self._daq.stop()
                except DaqError as error:
                    raise AcquisitionError(
                        "The armed DAQ task could not be released during reset.",
                        code="DAQ.ACQUISITION.RESET_FAILED",
                        context={"adapter_error": error.to_dict()},
                        cause=error,
                    ) from error
            elif self._state is AcquisitionState.FAULTED:
                try:
                    health = self._daq.health()
                    if health.state is DaqLifecycleState.FAULTED:
                        self._daq.stop()
                        health = self._daq.health()
                    if not health.device_available:
                        result = self._daq.reset()
                        for event in result.events:
                            self._emit_adapter_event_locked(event)
                except DaqError as error:
                    raise AcquisitionError(
                        "The faulted DAQ adapter could not be recovered during reset.",
                        code="DAQ.ACQUISITION.RESET_FAILED",
                        context={"adapter_error": error.to_dict()},
                        cause=error,
                    ) from error
            self._state = AcquisitionState.IDLE
            self._emit_locked(
                "DAQ.ACQUISITION.RESET_COMPLETED",
                ErrorSeverity.INFO,
                "The acquisition engine reset to idle.",
                {},
            )
            self._close_event_mailboxes_locked()
            self._clear_configuration_locked()
            self._reset_run_fields_locked()
            return self._status_locked()

    def subscribe_events(self) -> AcquisitionEventStream:
        with self._lock:
            self._require_open_locked("subscribe_events")
            mailbox = _EventMailbox()
            self._event_mailboxes.append(mailbox)
            return _EventReader(mailbox)

    def recording_stream(self) -> AcquisitionChunkStream:
        with self._lock:
            if self._recording_reader is None:
                raise AcquisitionStreamAccessError(
                    "The recording stream is available only after acquisition is armed.",
                    code="DAQ.ACQUISITION.STREAM_UNAVAILABLE",
                    context={"stream": "recording", "state": self._state.value},
                    remediation="Configure and arm the engine before attaching a consumer.",
                )
            return self._recording_reader

    def display_stream(self) -> AcquisitionChunkStream:
        with self._lock:
            if self._display_reader is None:
                raise AcquisitionStreamAccessError(
                    "The display stream is available only after acquisition is armed.",
                    code="DAQ.ACQUISITION.STREAM_UNAVAILABLE",
                    context={"stream": "display", "state": self._state.value},
                    remediation="Configure and arm the engine before attaching a consumer.",
                )
            return self._display_reader

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            running = self._state in {AcquisitionState.RUNNING, AcquisitionState.STOPPING}
            stop_timeout = self._runtime_config_locked().stop_timeout_seconds if running else 0.0
        if running:
            if self.status().state is AcquisitionState.RUNNING:
                self.request_stop("service_close")
            try:
                self.wait_for_terminal(stop_timeout + 0.5)
            except AcquisitionWaitTimeoutError:
                pass
        try:
            self._daq.close()
        finally:
            with self._lock:
                self._closed = True
                if self._recording_buffer is not None:
                    self._recording_buffer.close()
                if self._display_buffer is not None:
                    self._display_buffer.close()
                self._close_event_mailboxes_locked()

    def __enter__(self) -> AcquisitionEngine:
        with self._lock:
            self._require_open_locked("enter")
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _worker_main(self) -> None:
        fault: AcquisitionFaultSummary | None = None
        reason = CompletionReason.MANUAL_STOP
        try:
            fault, reason = self._acquire_loop()
        except BaseException as cause:
            fault = self._unexpected_fault("worker", cause)
            reason = CompletionReason.ACQUISITION_FAULT
        self._finish_worker(fault, reason)

    def _acquire_loop(
        self,
    ) -> tuple[AcquisitionFaultSummary | None, CompletionReason]:
        while True:
            with self._lock:
                if self._pending_fault is not None:
                    return self._pending_fault, CompletionReason.ACQUISITION_FAULT
                if self._stop_requested:
                    return None, CompletionReason.MANUAL_STOP
                remaining = (
                    None
                    if self._target_samples is None
                    else self._target_samples - self._acquired_samples
                )
                if remaining is not None and remaining <= 0:
                    self._state = AcquisitionState.STOPPING
                    return None, CompletionReason.DURATION_REACHED
                runtime = self._runtime_config_locked()
                requested = runtime.samples_per_channel
                if remaining is not None:
                    requested = min(requested, remaining)
            try:
                read_started = self._clock.monotonic()
                result = self._daq.read(requested, runtime.read_timeout_seconds)
            except DaqReadCancelledError as error:
                with self._lock:
                    self._emit_adapter_event_locked(error.event)
                    if self._pending_fault is not None:
                        return self._pending_fault, CompletionReason.ACQUISITION_FAULT
                    if self._stop_requested:
                        return None, CompletionReason.MANUAL_STOP
                return self._fault_from_daq(error.event), CompletionReason.ACQUISITION_FAULT
            except DaqError as error:
                with self._lock:
                    self._emit_adapter_event_locked(error.event)
                return self._fault_from_daq(error.event), CompletionReason.ACQUISITION_FAULT
            except Exception as cause:
                return self._unexpected_fault("read", cause), CompletionReason.ACQUISITION_FAULT

            read_completed = self._clock.monotonic()
            read_duration = max(0.0, read_completed - read_started)
            with self._lock:
                self._read_count += 1
                self._read_duration_total += read_duration
                self._read_duration_max = max(self._read_duration_max, read_duration)
                for event in result.events:
                    self._emit_adapter_event_locked(event)
                validation_fault = self._validate_read_locked(result, requested)
                if validation_fault is not None:
                    return validation_fault, CompletionReason.ACQUISITION_FAULT
                if result.samples_read_per_channel == 0:
                    if self._stop_requested:
                        return None, CompletionReason.MANUAL_STOP
                    return (
                        AcquisitionFaultSummary(
                            code="DAQ.ACQUISITION.MALFORMED_CHUNK",
                            message="The DAQ returned a zero-length read before completion.",
                            context={
                                "requested_samples": requested,
                                "end_of_stream": result.end_of_stream,
                                "acquired_samples": self._acquired_samples,
                                "target_samples": self._target_samples,
                            },
                        ),
                        CompletionReason.ACQUISITION_FAULT,
                    )
                assert self._session_id is not None
                assert self._actual_rate is not None
                chunk = AcquisitionChunk(
                    session_id=self._session_id,
                    sequence_number=self._next_sequence,
                    start_sample_index=self._acquired_samples,
                    sample_count=result.samples_read_per_channel,
                    sample_rate_hz=self._actual_rate,
                    ordered_channel_ids=self._ordered_channel_ids,
                    ordered_channel_units=self._ordered_channel_units,
                    samples=result.samples,
                    read_completed_monotonic=read_completed,
                    read_duration_seconds=read_duration,
                )
                self._acquired_samples += chunk.sample_count
                recording = self._recording_buffer
                assert recording is not None
                was_full = recording.qsize() >= recording.capacity
                if was_full:
                    self._recording_backpressure_events += 1
                    self._emit_locked(
                        "DAQ.ACQUISITION.RECORDING_QUEUE_PRESSURE",
                        ErrorSeverity.WARNING,
                        "The recording queue is applying bounded backpressure.",
                        {
                            "occupancy": recording.qsize(),
                            "capacity": recording.capacity,
                        },
                        sample_index=chunk.start_sample_index,
                    )
            accepted, blocked_seconds, _ = recording.put_lossless(
                chunk,
                runtime.recording_backpressure_timeout_seconds,
            )
            with self._lock:
                if not accepted:
                    return (
                        AcquisitionFaultSummary(
                            code="DAQ.ACQUISITION.RECORDING_QUEUE_SATURATED",
                            message="The recording queue remained full beyond its limit.",
                            context={
                                "capacity": recording.capacity,
                                "occupancy": recording.qsize(),
                                "blocked_duration_seconds": blocked_seconds,
                                "last_accepted_sequence": self._final_sequence,
                                "attempted_sequence": chunk.sequence_number,
                                "acquired_samples_per_channel": self._acquired_samples,
                                "recording_samples_accepted": self._recording_samples,
                            },
                        ),
                        CompletionReason.ACQUISITION_FAULT,
                    )
                self._recording_samples += chunk.sample_count
                self._recording_queue_high_water = max(
                    self._recording_queue_high_water,
                    recording.qsize(),
                )
                self._final_sequence = chunk.sequence_number
                self._next_sequence += 1
                display = self._display_buffer
                assert display is not None
                dropped = display.put_latest(chunk)
                self._display_samples += chunk.sample_count
                self._display_frames += 1
                if dropped:
                    self._display_drops += 1
                    self._emit_locked(
                        "DAQ.ACQUISITION.DISPLAY_FRAME_SUPERSEDED",
                        ErrorSeverity.WARNING,
                        "An older unpublished display frame was superseded.",
                        {
                            "dropped_display_frames": self._display_drops,
                            "display_queue_capacity": display.capacity,
                        },
                        sample_index=chunk.start_sample_index,
                    )
                stop_marker = self._stop_requested_at_sample
                if (
                    self._stop_requested
                    and stop_marker is not None
                    and chunk.start_sample_index + chunk.sample_count > stop_marker
                ):
                    self._emit_locked(
                        "DAQ.ACQUISITION.FINAL_CHUNK_ACCEPTED",
                        ErrorSeverity.INFO,
                        "A final chunk completed after the stop request.",
                        {
                            "sequence_number": chunk.sequence_number,
                            "sample_count": chunk.sample_count,
                        },
                        sample_index=chunk.start_sample_index,
                    )
                target_reached = (
                    self._target_samples is not None
                    and self._acquired_samples >= self._target_samples
                )
                self._emit_progress_locked(force=target_reached)
                if target_reached:
                    self._state = AcquisitionState.STOPPING
                    return None, CompletionReason.DURATION_REACHED
                if result.end_of_stream:
                    return (
                        AcquisitionFaultSummary(
                            code="DAQ.ACQUISITION.SAMPLE_CONTINUITY_VIOLATION",
                            message="The DAQ stream ended before the requested target.",
                            context={
                                "acquired_samples": self._acquired_samples,
                                "target_samples": self._target_samples,
                            },
                        ),
                        CompletionReason.ACQUISITION_FAULT,
                    )
                if self._pending_fault is not None:
                    return self._pending_fault, CompletionReason.ACQUISITION_FAULT
                if self._stop_requested:
                    return None, CompletionReason.MANUAL_STOP

    def _finish_worker(
        self,
        fault: AcquisitionFaultSummary | None,
        reason: CompletionReason,
    ) -> None:
        cleanup, cleanup_fault = self._cleanup_adapter()
        with self._lock:
            if fault is None and self._pending_fault is not None:
                fault = self._pending_fault
            if cleanup_fault is not None:
                if fault is None:
                    fault = cleanup_fault
                else:
                    context = dict(fault.context)
                    context["cleanup_fault"] = cleanup_fault.to_dict()
                    fault = AcquisitionFaultSummary(fault.code, fault.message, context)
            if fault is not None:
                reason = CompletionReason.ACQUISITION_FAULT
            self._terminalize_locked(fault, reason, cleanup)
        self._terminal_event.set()

    def _complete_without_worker_locked(self, fault: AcquisitionFaultSummary) -> None:
        cleanup, cleanup_fault = self._cleanup_adapter()
        if cleanup_fault is not None:
            context = dict(fault.context)
            context["cleanup_fault"] = cleanup_fault.to_dict()
            fault = AcquisitionFaultSummary(fault.code, fault.message, context)
        self._terminalize_locked(
            fault,
            CompletionReason.ACQUISITION_FAULT,
            cleanup,
        )
        self._terminal_event.set()

    def _cleanup_adapter(self) -> tuple[CleanupSummary, AcquisitionFaultSummary | None]:
        errors: list[str] = []
        attempted = True
        stop_succeeded = False
        try:
            health = self._daq.health()
            if health.state is DaqLifecycleState.CLOSED:
                stop_succeeded = False
            elif health.state is DaqLifecycleState.IDLE:
                stop_succeeded = True
            else:
                result = self._daq.stop()
                stop_succeeded = True
                with self._lock:
                    for event in result.events:
                        self._emit_adapter_event_locked(event)
        except DaqError as error:
            errors.append(error.code)
            with self._lock:
                self._emit_adapter_event_locked(error.event)
        except Exception as cause:
            errors.append(type(cause).__name__)
        cleanup = CleanupSummary(
            attempted=attempted,
            adapter_stop_succeeded=stop_succeeded,
            adapter_close_forced=self._adapter_close_forced,
            worker_terminated=True,
            errors=tuple(errors),
        )
        if cleanup.succeeded:
            return cleanup, None
        return (
            cleanup,
            AcquisitionFaultSummary(
                code="DAQ.ACQUISITION.CLEANUP_FAILED",
                message="DAQ cleanup did not complete successfully.",
                context={
                    "errors": errors,
                    "adapter_close_forced": self._adapter_close_forced,
                },
            ),
        )

    def _terminalize_locked(
        self,
        fault: AcquisitionFaultSummary | None,
        reason: CompletionReason,
        cleanup: CleanupSummary,
    ) -> None:
        if self._terminalized:
            return
        self._terminalized = True
        self._stopped_monotonic = self._clock.monotonic()
        self._fault = fault
        self._state = AcquisitionState.FAULTED if fault is not None else AcquisitionState.COMPLETED
        if fault is not None:
            self._last_warning_or_fault = fault.code
            self._emit_locked(
                fault.code,
                ErrorSeverity.ERROR,
                fault.message,
                dict(fault.context),
                sample_index=self._acquired_samples,
            )
            terminal_code = "DAQ.ACQUISITION.FAULTED"
            terminal_message = "Acquisition terminated with a fault."
        else:
            terminal_code = "DAQ.ACQUISITION.COMPLETED"
            terminal_message = "Acquisition completed normally."
        self._emit_locked(
            terminal_code,
            ErrorSeverity.ERROR if fault is not None else ErrorSeverity.INFO,
            terminal_message,
            {
                "reason": reason.value,
                "acquired_samples_per_channel": self._acquired_samples,
                "recording_samples_accepted": self._recording_samples,
                "display_frames_dropped": self._display_drops,
            },
            sample_index=self._acquired_samples,
        )
        assert self._session_id is not None
        self._completion = AcquisitionCompletion(
            terminal_state=self._state,
            reason=reason,
            session_id=self._session_id,
            started_at=self._started_at,
            stopped_at=self._clock.wall_time(),
            total_acquired_samples_per_channel=self._acquired_samples,
            total_recording_samples_accepted=self._recording_samples,
            final_sequence_number=self._final_sequence,
            display_frames_published=self._display_frames,
            display_frames_dropped=self._display_drops,
            warnings=tuple(self._warnings),
            fault=fault,
            cleanup=cleanup,
            telemetry=self._telemetry_locked(),
        )
        if self._recording_buffer is not None:
            self._recording_buffer.close()
        if self._display_buffer is not None:
            self._display_buffer.close()
        self._close_event_mailboxes_locked()

    def _cancel_and_watch(self) -> None:
        with self._lock:
            timeout = self._runtime_config_locked().stop_timeout_seconds
        deadline = time.monotonic() + timeout
        if self._terminal_event.is_set():
            return
        try:
            cancelled = self._daq.cancel_pending_read()
            with self._lock:
                for event in cancelled.events:
                    self._emit_adapter_event_locked(event)
        except DaqError as error:
            if not self._terminal_event.is_set():
                cancellation_unneeded = False
                try:
                    cancellation_unneeded = self._daq.health().state in {
                        DaqLifecycleState.STOPPED,
                        DaqLifecycleState.CLOSED,
                    }
                except DaqError:
                    pass
                if not cancellation_unneeded:
                    with self._lock:
                        self._emit_adapter_event_locked(error.event)
                        self._set_pending_fault_locked(self._fault_from_daq(error.event))
        except Exception as cause:
            if not self._terminal_event.is_set():
                with self._lock:
                    self._set_pending_fault_locked(self._unexpected_fault("cancel", cause))
        remaining = max(0.0, deadline - time.monotonic())
        if self._terminal_event.wait(remaining):
            return
        with self._lock:
            timeout_fault = AcquisitionFaultSummary(
                code="DAQ.ACQUISITION.STOP_TIMEOUT",
                message="The acquisition worker did not stop before the configured deadline.",
                context={
                    "stop_timeout_seconds": timeout,
                    "acquired_samples_per_channel": self._acquired_samples,
                    "state": self._state.value,
                },
            )
            self._set_pending_fault_locked(timeout_fault)
        with self._lock:
            self._adapter_close_forced = True
        try:
            self._daq.close()
        except DaqError as error:
            with self._lock:
                self._emit_adapter_event_locked(error.event)
        except Exception:
            pass

    def _validate_read_locked(
        self,
        result: DaqReadResult,
        requested: int,
    ) -> AcquisitionFaultSummary | None:
        count = result.samples_read_per_channel
        malformed_context: dict[str, object] = {
            "requested_samples": requested,
            "returned_samples": count,
            "shape": list(result.samples.shape),
        }
        if isinstance(count, bool) or count < 0 or count > requested:
            return AcquisitionFaultSummary(
                "DAQ.ACQUISITION.MALFORMED_CHUNK",
                "The DAQ returned an invalid sample count.",
                malformed_context,
            )
        if result.samples.ndim != 2 or result.samples.shape != (
            len(self._ordered_channel_ids),
            count,
        ):
            return AcquisitionFaultSummary(
                "DAQ.ACQUISITION.MALFORMED_CHUNK",
                "The DAQ returned a malformed multichannel array.",
                malformed_context,
            )
        if result.samples.dtype != np.dtype(np.float64) or not result.samples.flags.c_contiguous:
            return AcquisitionFaultSummary(
                "DAQ.ACQUISITION.MALFORMED_CHUNK",
                "The DAQ returned an unsupported sample representation.",
                malformed_context,
            )
        if result.samples.flags.writeable or not np.all(np.isfinite(result.samples)):
            return AcquisitionFaultSummary(
                "DAQ.ACQUISITION.MALFORMED_CHUNK",
                "The DAQ returned mutable or nonfinite samples.",
                malformed_context,
            )
        if result.ordered_logical_channel_ids != self._ordered_channel_ids:
            return AcquisitionFaultSummary(
                "DAQ.ACQUISITION.CHANNEL_ORDER_CHANGED",
                "The DAQ channel order changed during acquisition.",
                {
                    "expected": list(self._ordered_channel_ids),
                    "received": list(result.ordered_logical_channel_ids),
                },
            )
        if self._actual_rate is None or not rates_equivalent(
            result.actual_sample_rate_hz, self._actual_rate
        ):
            return AcquisitionFaultSummary(
                "DAQ.ACQUISITION.SAMPLE_CONTINUITY_VIOLATION",
                "The actual sample rate changed during acquisition.",
                {
                    "expected_sample_rate_hz": self._actual_rate,
                    "received_sample_rate_hz": result.actual_sample_rate_hz,
                },
            )
        source_start = result.source_start_sample_index
        if source_start is not None and source_start != self._acquired_samples:
            expected = self._acquired_samples
            if source_start > expected:
                self._source_missing_samples += source_start - expected
            elif self._last_source_start == source_start:
                self._source_duplicated_samples += count
            else:
                self._source_reordered_reads += 1
            return AcquisitionFaultSummary(
                "DAQ.ACQUISITION.SAMPLE_CONTINUITY_VIOLATION",
                "The DAQ source-relative sample index is discontinuous.",
                {
                    "expected_source_start_sample_index": expected,
                    "received_source_start_sample_index": source_start,
                    "samples_read_per_channel": count,
                    "missing_samples": self._source_missing_samples,
                    "duplicated_samples": self._source_duplicated_samples,
                    "reordered_reads": self._source_reordered_reads,
                },
            )
        if source_start is not None:
            self._last_source_start = source_start
        return None

    def _derive_target_locked(self) -> int | None:
        assert self._experiment is not None
        if self._requested_duration is None:
            return None
        assert self._actual_rate is not None
        try:
            return rounded_sample_count(self._actual_rate, self._requested_duration)
        except ValueError as cause:
            raise AcquisitionError(
                "Actual sample rate and duration do not produce a positive target count.",
                code="DAQ.ACQUISITION.TARGET_COUNT_INVALID",
                context={
                    "actual_sample_rate_hz": self._actual_rate,
                    "requested_duration_seconds": self._requested_duration,
                },
                remediation="Increase the duration so it resolves to at least one sample.",
                cause=cause,
            ) from cause

    def _emit_progress_locked(self, *, force: bool) -> None:
        now = self._clock.monotonic()
        interval = self._runtime_config_locked().progress_event_interval_seconds
        if (
            not force
            and self._last_progress_monotonic is not None
            and now - self._last_progress_monotonic < interval
        ):
            return
        self._last_progress_monotonic = now
        self._emit_locked(
            "DAQ.ACQUISITION.PROGRESS",
            ErrorSeverity.INFO,
            "Acquisition progress was updated.",
            {
                "acquired_samples_per_channel": self._acquired_samples,
                "recording_samples_accepted": self._recording_samples,
                "target_samples": self._target_samples,
                "recording_queue_occupancy": (
                    self._recording_buffer.qsize() if self._recording_buffer else 0
                ),
                "display_queue_occupancy": (
                    self._display_buffer.qsize() if self._display_buffer else 0
                ),
            },
            sample_index=self._acquired_samples,
        )

    def _emit_adapter_event_locked(self, event: DaqEvent) -> AcquisitionEvent:
        context = dict(event.context)
        context.update(
            {
                "adapter_backend_id": event.backend_id,
                "adapter_operation": event.operation,
                "adapter_read_sequence": event.read_sequence,
            }
        )
        return self._emit_locked(
            event.code,
            event.severity,
            event.message,
            context,
            sample_index=event.sample_index,
            timestamp=event.timestamp,
        )

    def _emit_locked(
        self,
        code: str,
        severity: ErrorSeverity,
        message: str,
        context: Mapping[str, object],
        *,
        sample_index: int | None = None,
        timestamp: datetime | None = None,
    ) -> AcquisitionEvent:
        event = AcquisitionEvent(
            event_sequence=self._event_sequence,
            code=code,
            severity=severity,
            session_id=self._session_id or "unassigned",
            state=self._state,
            timestamp=timestamp or self._clock.wall_time(),
            monotonic_timestamp=self._clock.monotonic(),
            message=message,
            context=context,
            sample_index=sample_index,
        )
        self._event_sequence += 1
        if severity is ErrorSeverity.WARNING:
            self._warnings.append(event)
            self._last_warning_or_fault = event.code
        elif severity in {ErrorSeverity.ERROR, ErrorSeverity.CRITICAL}:
            self._last_warning_or_fault = event.code
        active: list[_EventMailbox] = []
        for mailbox in self._event_mailboxes:
            if mailbox.publish(event):
                active.append(mailbox)
        self._event_mailboxes = active
        return event

    def _set_pending_fault_locked(self, fault: AcquisitionFaultSummary) -> None:
        if self._pending_fault is None:
            self._pending_fault = fault
            self._last_warning_or_fault = fault.code

    def _fault_from_daq(self, event: DaqEvent) -> AcquisitionFaultSummary:
        return AcquisitionFaultSummary(event.code, event.message, event.to_dict())

    def _unexpected_fault(
        self,
        operation: str,
        cause: BaseException,
    ) -> AcquisitionFaultSummary:
        return AcquisitionFaultSummary(
            code="DAQ.ACQUISITION.WORKER_FAILURE",
            message=f"Unexpected acquisition {operation} failure.",
            context={"operation": operation, "exception_type": type(cause).__name__},
        )

    def _status_locked(self) -> AcquisitionStatus:
        recording = self._recording_buffer
        display = self._display_buffer
        worker = self._worker
        elapsed_seconds = (
            max(
                0.0,
                (
                    self._stopped_monotonic
                    if self._stopped_monotonic is not None
                    else self._clock.monotonic()
                )
                - self._started_monotonic,
            )
            if self._started_monotonic is not None
            else 0.0
        )
        return AcquisitionStatus(
            state=self._state,
            session_id=self._session_id,
            requested_sample_rate_hz=self._requested_rate,
            actual_sample_rate_hz=self._actual_rate,
            requested_duration_seconds=self._requested_duration,
            target_sample_count=self._target_samples,
            acquired_samples_per_channel=self._acquired_samples,
            recording_samples_enqueued=self._recording_samples,
            display_samples_published=self._display_samples,
            display_frames_published=self._display_frames,
            display_frames_dropped=self._display_drops,
            recording_queue_occupancy=recording.qsize() if recording else 0,
            recording_queue_capacity=recording.capacity if recording else 0,
            display_queue_occupancy=display.qsize() if display else 0,
            display_queue_capacity=display.capacity if display else 0,
            stop_requested=self._stop_requested,
            worker_alive=worker.is_alive() if worker else False,
            last_warning_or_fault_code=self._last_warning_or_fault,
            telemetry=self._telemetry_locked(),
            started_at=self._started_at,
            elapsed_seconds=elapsed_seconds,
            ordered_channel_ids=self._ordered_channel_ids,
            ordered_channel_units=self._ordered_channel_units,
            warning_count=len(self._warnings),
            current_fault=self._fault or self._pending_fault,
        )

    def _telemetry_locked(self) -> AcquisitionTelemetry:
        recording = self._recording_buffer
        capacity = recording.capacity if recording else 0
        stop_latency = None
        if self._stop_requested_monotonic is not None and self._terminalized:
            stop_latency = max(0.0, self._clock.monotonic() - self._stop_requested_monotonic)
        return AcquisitionTelemetry(
            read_count=self._read_count,
            read_duration_total_seconds=self._read_duration_total,
            read_duration_max_seconds=self._read_duration_max,
            recording_queue_high_water_chunks=self._recording_queue_high_water,
            recording_queue_max_utilization=(
                self._recording_queue_high_water / capacity if capacity else 0.0
            ),
            recording_backpressure_events=self._recording_backpressure_events,
            source_missing_samples=self._source_missing_samples,
            source_duplicated_samples=self._source_duplicated_samples,
            source_reordered_reads=self._source_reordered_reads,
            stop_latency_seconds=stop_latency,
        )

    def _runtime_config_locked(self) -> DataChunkConfig:
        assert self._experiment is not None
        return self._experiment.acquisition.chunk_config

    def _require_state_locked(
        self,
        operation: str,
        allowed: tuple[AcquisitionState, ...],
    ) -> None:
        if self._state in allowed:
            return
        expected = [state.value for state in allowed]
        self._emit_locked(
            "DAQ.ACQUISITION.ILLEGAL_STATE_TRANSITION",
            ErrorSeverity.ERROR,
            f"Operation {operation} is invalid while acquisition is {self._state.value}.",
            {"operation": operation, "state": self._state.value, "expected_states": expected},
        )
        raise ApplicationStateError(
            f"Operation {operation} is invalid while acquisition is {self._state.value}.",
            code="DAQ.ACQUISITION.ILLEGAL_STATE_TRANSITION",
            context={
                "operation": operation,
                "state": self._state.value,
                "expected_states": expected,
            },
            remediation="Call acquisition operations in the documented state order.",
        )

    def _require_open_locked(self, operation: str) -> None:
        if self._closed:
            raise ApplicationStateError(
                f"Operation {operation} is invalid after the engine is closed.",
                code="DAQ.ACQUISITION.ENGINE_CLOSED",
                context={"operation": operation},
            )

    def _reset_run_fields_locked(self) -> None:
        self._terminal_event.clear()
        self._completion = None
        self._configuration = None
        self._recording_buffer = None
        self._display_buffer = None
        self._recording_reader = None
        self._display_reader = None
        self._worker = None
        self._cancel_worker = None
        self._actual_rate = None
        self._target_samples = None
        self._ordered_channel_ids = ()
        self._ordered_channel_units = ()
        self._acquired_samples = 0
        self._recording_samples = 0
        self._display_samples = 0
        self._display_frames = 0
        self._display_drops = 0
        self._next_sequence = 0
        self._final_sequence = None
        self._event_sequence = 0
        self._warnings.clear()
        self._last_warning_or_fault = None
        self._fault = None
        self._pending_fault = None
        self._started_at = None
        self._started_monotonic = None
        self._stopped_monotonic = None
        self._stop_requested = False
        self._stop_reason = None
        self._stop_requested_at_sample = None
        self._last_progress_monotonic = None
        self._adapter_close_forced = False
        self._terminalized = False
        self._read_count = 0
        self._read_duration_total = 0.0
        self._read_duration_max = 0.0
        self._recording_queue_high_water = 0
        self._recording_backpressure_events = 0
        self._source_missing_samples = 0
        self._source_duplicated_samples = 0
        self._source_reordered_reads = 0
        self._last_source_start = None
        self._stop_requested_monotonic = None

    def _clear_configuration_locked(self) -> None:
        self._session_id = None
        self._profile = None
        self._experiment = None
        self._configuration = None
        self._requested_rate = None
        self._requested_duration = None
        self._actual_rate = None
        self._target_samples = None
        self._ordered_channel_ids = ()
        self._ordered_channel_units = ()
        self._recording_buffer = None
        self._display_buffer = None
        self._recording_reader = None
        self._display_reader = None
        self._worker = None
        self._cancel_worker = None
        self._completion = None
        self._stop_requested = False
        self._terminal_event.clear()

    def _close_event_mailboxes_locked(self) -> None:
        for mailbox in self._event_mailboxes:
            mailbox.close()
        self._event_mailboxes.clear()
