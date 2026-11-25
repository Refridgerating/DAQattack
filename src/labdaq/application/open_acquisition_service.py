"""Continuous raw monitoring with detachable multichannel recording."""

from __future__ import annotations

from dataclasses import dataclass, replace
from queue import Empty, Full, Queue
from threading import Event, RLock, Thread, current_thread
from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

import numpy as np

from labdaq.application.acquisition_engine import AcquisitionEngine, SystemAcquisitionClock
from labdaq.application.live_service import (
    _resolve_experiment_recipe,
    _SignalRingBuffer,
    vectorized_min_max,
)
from labdaq.domain.enums import SamplingMode, SignalStage
from labdaq.errors import ApplicationStateError, ErrorSeverity, LabDAQError, StorageError
from labdaq.ports.acquisition import (
    AcquisitionChunk,
    AcquisitionEvent,
    AcquisitionState,
    AcquisitionStreamClosedError,
)
from labdaq.ports.operator import (
    LiveDisplayConfiguration,
    LivePlotFrame,
    LivePlotSeries,
    OpenAcquisitionStatus,
    OpenMonitorState,
    OpenRecordingState,
)
from labdaq.ports.session import (
    OpenAcquisitionProvenance,
    RecordedSessionCompletion,
    RecordingPolicy,
    RecordingSegmentCompletion,
    RecordingSegmentStatus,
    SessionFaultSummary,
    SessionFinalization,
    SessionInitialization,
    SessionType,
)
from labdaq.version import __version__

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from labdaq.application.readiness_service import OpenAcquisitionIntent
    from labdaq.domain.models import ExperimentConfig, ProcessingRecipeEnvelope
    from labdaq.ports.acquisition import AcquisitionChunkStream
    from labdaq.ports.configuration import BundledRecipeResolverPort
    from labdaq.ports.daq import DaqConfigurationResult, DaqDiscoveryReport, DaqPort
    from labdaq.ports.session import (
        SessionPathPlannerPort,
        SessionPlan,
        SessionWriterFactoryPort,
        SessionWriterPort,
    )


class OpenAcquisitionClock(Protocol):
    def wall_time(self) -> datetime: ...

    def monotonic(self) -> float: ...


@dataclass(slots=True)
class _SegmentRuntime:
    plan: SessionPlan
    provenance: OpenAcquisitionProvenance
    queue: Queue[AcquisitionChunk]
    writer: SessionWriterPort | None = None
    thread: Thread | None = None
    stop_source_index: int | None = None
    persisted_samples: int = 0
    next_sequence: int = 0
    storage_fault: SessionFaultSummary | None = None


class OpenAcquisitionCoordinator:
    """Own one continuous DAQ task and zero or more diagnostic record segments."""

    def __init__(
        self,
        daq: DaqPort,
        planner: SessionPathPlannerPort,
        writer_factory: SessionWriterFactoryPort,
        recipe: ProcessingRecipeEnvelope | BundledRecipeResolverPort,
        *,
        policy: RecordingPolicy | None = None,
        display: LiveDisplayConfiguration | None = None,
        clock: OpenAcquisitionClock | None = None,
    ) -> None:
        self._daq = daq
        self._planner = planner
        self._writer_factory = writer_factory
        self._recipe_source = recipe
        self._recipe: ProcessingRecipeEnvelope | None = None
        self._policy = policy or RecordingPolicy()
        self._display = display or LiveDisplayConfiguration()
        self._clock = clock or SystemAcquisitionClock()
        self._lock = RLock()
        self._monitor_terminal = Event()
        self._recording_terminal = Event()
        self._monitor_state = OpenMonitorState.IDLE
        self._recording_state = OpenRecordingState.INACTIVE
        self._engine: AcquisitionEngine | None = None
        self._source_stream: AcquisitionChunkStream | None = None
        self._display_stream: AcquisitionChunkStream | None = None
        self._source_thread: Thread | None = None
        self._display_thread: Thread | None = None
        self._terminal_thread: Thread | None = None
        self._intent: OpenAcquisitionIntent | None = None
        self._runtime_experiment: ExperimentConfig | None = None
        self._discovery: DaqDiscoveryReport | None = None
        self._configuration: DaqConfigurationResult | None = None
        self._ring: _SignalRingBuffer | None = None
        self._channel_positions: tuple[int, ...] = ()
        self._channel_names: tuple[str, ...] = ()
        self._channel_units: tuple[str, ...] = ()
        self._source_dispatched_stop = 0
        self._frame_sequence = 0
        self._segment: _SegmentRuntime | None = None
        self._last_recording: RecordedSessionCompletion | None = None
        self._last_storage_fault_code: str | None = None
        self._closed = False

    @property
    def configuration(self) -> LiveDisplayConfiguration:
        return self._display

    def set_visible_window(self, seconds: float) -> LiveDisplayConfiguration:
        if seconds <= 0 or seconds > self._display.maximum_history_seconds:
            raise ValueError("visible window must be inside retained monitor history")
        with self._lock:
            self._display = replace(self._display, visible_window_seconds=float(seconds))
            return self._display

    def start_monitoring(self, intent: OpenAcquisitionIntent) -> OpenAcquisitionStatus:
        with self._lock:
            if self._closed:
                raise ApplicationStateError(
                    "The open-acquisition coordinator is closed.",
                    code="DAQ.APPLICATION.COORDINATOR_CLOSED",
                )
            if self._monitor_state not in {
                OpenMonitorState.IDLE,
                OpenMonitorState.COMPLETED,
                OpenMonitorState.FAULTED,
            }:
                raise ApplicationStateError(
                    "Open acquisition is already active or terminal.",
                    code="DAQ.APPLICATION.OPEN_MONITOR_DUPLICATE",
                    remediation="End and close the active monitor before starting another.",
                )
            previous_engine = self._engine
            self._monitor_state = OpenMonitorState.STARTING
        runtime = _continuous_experiment(intent.experiment)
        if previous_engine is not None:
            previous_engine.reset()
            engine = previous_engine
        else:
            engine = AcquisitionEngine(
                self._daq,
                clock=self._clock,
                session_id_factory=lambda: str(uuid4()),
            )
        try:
            discovery = self._daq.discover()
            engine.configure(intent.profile, runtime)
            engine.arm()
            configuration = engine.daq_configuration()
            source_stream = engine.recording_stream()
            display_stream = engine.display_stream()
            positions = tuple(
                configuration.ordered_logical_channels.index(channel_id)
                for channel_id in intent.recorded_channel_ids
            )
            by_id = {channel.channel_id: channel for channel in runtime.channels}
            names = tuple(
                by_id[channel_id].logical_name for channel_id in intent.recorded_channel_ids
            )
            units = tuple(
                by_id[channel_id].raw_unit.value for channel_id in intent.recorded_channel_ids
            )
            capacity = max(
                1,
                int(
                    round(
                        configuration.effective_sample_rate_hz
                        * self._display.maximum_history_seconds
                    )
                ),
            )
            with self._lock:
                self._monitor_terminal.clear()
                self._recording_terminal.set()
                self._engine = engine
                self._source_stream = source_stream
                self._display_stream = display_stream
                self._intent = intent
                self._recipe = None
                self._runtime_experiment = runtime
                self._discovery = discovery
                self._configuration = configuration
                self._ring = _SignalRingBuffer(len(positions), capacity)
                self._channel_positions = positions
                self._channel_names = names
                self._channel_units = units
                self._source_dispatched_stop = 0
                self._frame_sequence = 0
                self._segment = None
                self._last_recording = None
                self._last_storage_fault_code = None
                self._source_thread = Thread(
                    target=self._drain_source,
                    name=f"labdaq-open-source-{engine.status().session_id}",
                    daemon=True,
                )
                self._display_thread = Thread(
                    target=self._consume_display,
                    name=f"labdaq-open-display-{engine.status().session_id}",
                    daemon=True,
                )
                self._source_thread.start()
                self._display_thread.start()
            engine.start()
            with self._lock:
                self._monitor_state = OpenMonitorState.MONITORING
                self._terminal_thread = Thread(
                    target=self._wait_for_monitor_terminal,
                    name=f"labdaq-open-terminal-{engine.status().session_id}",
                    daemon=True,
                )
                self._terminal_thread.start()
            return self.status()
        except BaseException:
            try:
                engine.close()
            finally:
                with self._lock:
                    self._monitor_state = OpenMonitorState.FAULTED
                    self._monitor_terminal.set()
            raise

    def start_recording(
        self, *, parent_output_directory: Path | None = None
    ) -> OpenAcquisitionStatus:
        with self._lock:
            self._require_monitoring("start_recording")
            if self._recording_state is not OpenRecordingState.INACTIVE:
                raise ApplicationStateError(
                    "A diagnostic recording is already active.",
                    code="DAQ.APPLICATION.OPEN_RECORD_DUPLICATE",
                    remediation="Stop and finalize the active recording first.",
                )
            assert self._engine is not None
            assert self._intent is not None
            assert self._configuration is not None
            assert self._runtime_experiment is not None
            source_start = self._engine.status().acquired_samples_per_channel
            requested_at = self._clock.wall_time()
            session = self._runtime_experiment.session
            if parent_output_directory is not None:
                session = replace(session, parent_output_directory=parent_output_directory)
            try:
                self._recipe = _resolve_experiment_recipe(
                    self._recipe_source, self._runtime_experiment
                )
                plan = self._planner.plan_session(
                    session,
                    session_id=str(uuid4()),
                    created_at=requested_at,
                    policy=self._policy,
                )
            except Exception as cause:
                self._last_storage_fault_code = _storage_fault(
                    cause, "DAQ.STORAGE.OPEN_SEGMENT_INITIALIZATION_FAILED"
                ).code
                raise
            provenance = OpenAcquisitionProvenance(
                monitor_run_id=self._engine.status().session_id or "unknown-monitor",
                monitor_started_at=self._engine.status().started_at or requested_at,
                configured_logical_channels=self._configuration.ordered_logical_channels,
                configured_physical_channels=self._configuration.ordered_physical_channels,
                selected_channel_id=self._intent.selected_channel_id,
                source_start_sample_index=source_start,
                record_requested_at=requested_at,
                maximum_history_seconds=self._display.maximum_history_seconds,
                visible_window_seconds=self._display.visible_window_seconds,
                recorded_channel_ids=self._intent.recorded_channel_ids,
                recorded_channel_units=self._intent.recorded_channel_units,
            )
            segment = _SegmentRuntime(
                plan=plan,
                provenance=provenance,
                queue=Queue(
                    maxsize=self._runtime_experiment.acquisition.chunk_config.recording_queue_capacity_chunks
                ),
            )
            self._segment = segment
            self._recording_state = OpenRecordingState.PREPARING
            self._recording_terminal.clear()
            initialization = self._segment_initialization(segment)
        writer: SessionWriterPort | None = None
        recorded: RecordedSessionCompletion | None = None
        try:
            writer = self._writer_factory.create_writer(initialization, self._policy)
            writer.append_event(self._segment_event(segment, "STARTED", requested_at, 0))
        except BaseException as cause:
            fault = _storage_fault(cause, "DAQ.STORAGE.OPEN_SEGMENT_INITIALIZATION_FAILED")
            if writer is not None:
                try:
                    recorded = writer.abort(fault)
                except StorageError:
                    recorded = None
                finally:
                    writer.close()
            with self._lock:
                segment.storage_fault = fault
                self._last_storage_fault_code = fault.code
                if recorded is not None:
                    self._last_recording = recorded
                self._recording_state = OpenRecordingState.INACTIVE
                self._segment = None
                self._recording_terminal.set()
            raise
        with self._lock:
            assert writer is not None
            segment.writer = writer
            if segment.storage_fault is not None:
                self._recording_state = OpenRecordingState.FINALIZING
            else:
                self._recording_state = OpenRecordingState.RECORDING
            segment.thread = Thread(
                target=self._record_segment,
                args=(segment,),
                name=f"labdaq-open-recorder-{plan.session_id}",
                daemon=True,
            )
            segment.thread.start()
        return self.status()

    def stop_recording(self) -> OpenAcquisitionStatus:
        with self._lock:
            if self._recording_state is OpenRecordingState.INACTIVE:
                return self.status()
            if self._recording_state is OpenRecordingState.FINALIZING:
                return self.status()
            assert self._engine is not None and self._segment is not None
            stop_source_index = self._engine.status().acquired_samples_per_channel
            self._segment.stop_source_index = stop_source_index
            self._segment.provenance = replace(
                self._segment.provenance,
                source_stop_sample_index=stop_source_index,
                stop_requested_at=self._clock.wall_time(),
            )
            self._recording_state = OpenRecordingState.FINALIZING
            return self.status()

    def end_monitoring(self) -> OpenAcquisitionStatus:
        with self._lock:
            if self._monitor_state in {OpenMonitorState.COMPLETED, OpenMonitorState.FAULTED}:
                return self.status()
            self._require_monitoring("end_monitoring")
        if self._recording_state is not OpenRecordingState.INACTIVE:
            self.stop_recording()
            self.wait_for_recording()
        with self._lock:
            assert self._engine is not None
            self._monitor_state = OpenMonitorState.STOPPING
            if self._engine.status().state is AcquisitionState.RUNNING:
                self._engine.request_stop("open_monitor_end")
        return self.status()

    def wait_for_recording(self, timeout: float | None = None) -> RecordedSessionCompletion | None:
        if not self._recording_terminal.wait(timeout):
            raise TimeoutError("open-acquisition recording did not finalize before the timeout")
        with self._lock:
            return self._last_recording

    def wait_for_terminal(self, timeout: float | None = None) -> OpenAcquisitionStatus:
        if not self._monitor_terminal.wait(timeout):
            raise TimeoutError("open acquisition did not terminate before the timeout")
        return self.status()

    def status(self) -> OpenAcquisitionStatus:
        with self._lock:
            engine = self._engine
            ring = self._ring
            segment = self._segment
            recording_state = self._recording_state
            monitor_state = self._monitor_state
            last = self._last_recording
            storage_fault = self._last_storage_fault_code
        acquisition = engine.status() if engine is not None else None
        return OpenAcquisitionStatus(
            monitor_state=monitor_state,
            recording_state=recording_state,
            monitor_run_id=acquisition.session_id if acquisition else None,
            selected_channel_id=(self._intent.selected_channel_id if self._intent else None),
            selected_channel_unit=(self._channel_units[0] if self._intent else None),
            monitor_started_at=acquisition.started_at if acquisition else None,
            elapsed_seconds=acquisition.elapsed_seconds if acquisition else 0.0,
            actual_sample_rate_hz=acquisition.actual_sample_rate_hz if acquisition else None,
            acquired_samples_per_channel=(
                acquisition.acquired_samples_per_channel if acquisition else 0
            ),
            retained_start_sample_index=(ring.start_index if ring and ring.count else None),
            retained_stop_sample_index=(ring.stop_index if ring and ring.count else None),
            display_frames_dropped=(acquisition.display_frames_dropped if acquisition else 0),
            recording_session_id=(segment.plan.session_id if segment else None),
            recording_session_path=(segment.plan.session_path if segment else None),
            recording_source_start_sample_index=(
                segment.provenance.source_start_sample_index if segment else None
            ),
            recording_source_stop_sample_index=(segment.stop_source_index if segment else None),
            recording_persisted_samples=(segment.persisted_samples if segment else 0),
            last_recording=last,
            monitor_fault=acquisition.current_fault if acquisition else None,
            storage_fault_code=storage_fault,
            ordered_channel_ids=(self._intent.recorded_channel_ids if self._intent else ()),
            ordered_channel_units=(self._channel_units if self._intent else ()),
            requested_sample_rate_hz=(
                acquisition.requested_sample_rate_hz if acquisition else None
            ),
        )

    def build_plot_frame(self) -> LivePlotFrame | None:
        with self._lock:
            if self._ring is None or self._intent is None or self._configuration is None:
                return None
            sample_rate = self._configuration.effective_sample_rate_hz
            sample_limit = max(1, int(round(sample_rate * self._display.visible_window_seconds)))
            snapshot = self._ring.snapshot(sample_limit)
            if snapshot is None:
                return None
            start, values = snapshot
            sequence = self._frame_sequence
            self._frame_sequence += 1
            monitor_id = self._engine.status().session_id if self._engine else None
        reduced = vectorized_min_max(
            values,
            start_sample_index=start,
            maximum_points=self._display.maximum_points_per_channel,
        )
        series: list[LivePlotSeries] = []
        for channel_id, name, unit, (indices, channel_values) in zip(
            self._intent.recorded_channel_ids,
            self._channel_names,
            self._channel_units,
            reduced,
            strict=True,
        ):
            times = indices.astype("float64") / sample_rate
            times.flags.writeable = False
            series.append(
                LivePlotSeries(
                    channel_id=channel_id,
                    display_name=name,
                    unit=unit,
                    source_sample_indices=indices,
                    relative_time_seconds=times,
                    values=channel_values,
                )
            )
        return LivePlotFrame(
            session_id=monitor_id or "unknown-monitor",
            frame_sequence=sequence,
            signal_stage=SignalStage.RAW,
            start_sample_index=start,
            stop_sample_index=start + values.shape[1],
            sample_rate_hz=sample_rate,
            source_sample_count=values.shape[1],
            series=tuple(series),
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            active = self._monitor_state in {
                OpenMonitorState.MONITORING,
                OpenMonitorState.STOPPING,
            }
        if active:
            try:
                self.end_monitoring()
                self._monitor_terminal.wait(10.0)
            except LabDAQError:
                pass
        with self._lock:
            engine = self._engine
            self._closed = True
        if engine is not None:
            engine.close()
        else:
            self._daq.close()
        for thread in (self._source_thread, self._display_thread, self._terminal_thread):
            if thread is not None and thread is not current_thread():
                thread.join(2.0)

    def _drain_source(self) -> None:
        assert self._source_stream is not None
        while True:
            try:
                chunk = self._source_stream.get(0.2)
            except Empty:
                continue
            except AcquisitionStreamClosedError:
                return
            with self._lock:
                segment = self._segment
                state = self._recording_state
                positions = self._channel_positions
            if segment is not None and state is not OpenRecordingState.INACTIVE:
                start = segment.provenance.source_start_sample_index
                stop = segment.stop_source_index
                if chunk.start_sample_index >= start and (
                    stop is None or chunk.start_sample_index < stop
                ):
                    projected = _project_chunk(
                        chunk,
                        channel_positions=positions,
                        session_id=segment.plan.session_id,
                        source_origin=start,
                        sequence_number=segment.next_sequence,
                    )
                    try:
                        segment.queue.put_nowait(projected)
                        segment.next_sequence += 1
                    except Full:
                        with self._lock:
                            if segment.storage_fault is None:
                                segment.storage_fault = SessionFaultSummary(
                                    code="DAQ.STORAGE.OPEN_SEGMENT_QUEUE_SATURATED",
                                    message="The open-acquisition recording queue saturated.",
                                    context={
                                        "session_id": segment.plan.session_id,
                                        "capacity_chunks": segment.queue.maxsize,
                                    },
                                )
                                segment.stop_source_index = chunk.start_sample_index
                                self._last_storage_fault_code = segment.storage_fault.code
                                self._recording_state = OpenRecordingState.FINALIZING
            with self._lock:
                self._source_dispatched_stop = chunk.start_sample_index + chunk.sample_count

    def _consume_display(self) -> None:
        assert self._display_stream is not None
        while True:
            try:
                chunk = self._display_stream.get(0.2)
            except Empty:
                continue
            except AcquisitionStreamClosedError:
                return
            selected = np.ascontiguousarray(
                chunk.samples[list(self._channel_positions)],
                dtype=np.float64,
            )
            with self._lock:
                assert self._ring is not None
                if self._ring.count and self._ring.stop_index != chunk.start_sample_index:
                    self._ring.clear(next_start_index=chunk.start_sample_index)
                self._ring.append(chunk.start_sample_index, selected)

    def _record_segment(self, segment: _SegmentRuntime) -> None:
        assert segment.writer is not None
        writer = segment.writer
        try:
            while True:
                try:
                    chunk = segment.queue.get(timeout=0.1)
                except Empty:
                    chunk = None
                if chunk is not None and segment.storage_fault is None:
                    try:
                        writer.append_chunk(chunk)
                        segment.persisted_samples += chunk.sample_count
                        writer.checkpoint(
                            RecordingSegmentStatus(
                                session_id=segment.plan.session_id,
                                source_start_sample_index=(
                                    segment.provenance.source_start_sample_index
                                ),
                                source_stop_sample_index=segment.stop_source_index,
                                acquired_samples_per_channel=segment.persisted_samples,
                                recording_samples_enqueued=segment.persisted_samples,
                                persisted_samples_per_channel=segment.persisted_samples,
                                display_frames_dropped=0,
                            )
                        )
                    except StorageError as cause:
                        with self._lock:
                            segment.storage_fault = _storage_fault(
                                cause, "DAQ.STORAGE.OPEN_SEGMENT_WRITE_FAILED"
                            )
                            segment.stop_source_index = (
                                segment.provenance.source_start_sample_index
                                + segment.persisted_samples
                            )
                            self._last_storage_fault_code = segment.storage_fault.code
                            self._recording_state = OpenRecordingState.FINALIZING
                with self._lock:
                    stop = segment.stop_source_index
                    dispatched = self._source_dispatched_stop
                    finished = stop is not None and dispatched >= stop and segment.queue.empty()
                if finished:
                    break
            stopped_at = self._clock.wall_time()
            source_stop = (
                segment.provenance.source_start_sample_index + segment.persisted_samples
                if segment.storage_fault is not None
                else segment.stop_source_index
            )
            assert source_stop is not None
            if segment.storage_fault is None:
                writer.append_event(
                    self._segment_event(segment, "STOPPED", stopped_at, segment.persisted_samples)
                )
            completion = RecordingSegmentCompletion(
                session_id=segment.plan.session_id,
                started_at=segment.provenance.record_requested_at,
                stopped_at=stopped_at,
                source_start_sample_index=segment.provenance.source_start_sample_index,
                source_stop_sample_index=source_stop,
                total_recording_samples_accepted=segment.persisted_samples,
                stop_requested_at=segment.provenance.stop_requested_at,
                fault=segment.storage_fault,
            )
            recorded = writer.finalize(
                SessionFinalization(
                    acquisition=None,
                    storage_fault=segment.storage_fault,
                    recording_segment=completion,
                )
            )
        except BaseException as cause:
            fault = _storage_fault(cause, "DAQ.STORAGE.OPEN_SEGMENT_FINALIZATION_FAILED")
            try:
                recorded = writer.abort(fault)
            except StorageError:
                recorded = None
            with self._lock:
                self._last_storage_fault_code = fault.code
        finally:
            writer.close()
            with self._lock:
                if recorded is not None:
                    self._last_recording = recorded
                if self._segment is segment:
                    self._segment = None
                self._recording_state = OpenRecordingState.INACTIVE
                self._recording_terminal.set()

    def _wait_for_monitor_terminal(self) -> None:
        assert self._engine is not None
        completion = self._engine.wait_for_terminal()
        for thread in (self._source_thread, self._display_thread):
            if thread is not None and thread is not current_thread():
                thread.join(2.0)
        with self._lock:
            segment_active = self._recording_state is not OpenRecordingState.INACTIVE
            if segment_active and self._segment is not None:
                if self._segment.storage_fault is None and completion.fault is not None:
                    self._segment.storage_fault = SessionFaultSummary(
                        code="DAQ.SESSION.OPEN_MONITOR_FAULT",
                        message="The monitor faulted while a recording was active.",
                        context={"acquisition_fault_code": completion.fault.code},
                    )
                self._segment.stop_source_index = completion.total_acquired_samples_per_channel
                self._recording_state = OpenRecordingState.FINALIZING
        if segment_active:
            self._recording_terminal.wait(10.0)
        with self._lock:
            self._monitor_state = (
                OpenMonitorState.FAULTED
                if completion.terminal_state is AcquisitionState.FAULTED
                else OpenMonitorState.COMPLETED
            )
            self._monitor_terminal.set()

    def _segment_initialization(self, segment: _SegmentRuntime) -> SessionInitialization:
        assert self._intent is not None
        assert self._runtime_experiment is not None
        assert self._discovery is not None
        assert self._configuration is not None
        assert self._recipe is not None
        channel_ids = self._intent.recorded_channel_ids
        positions = tuple(
            self._configuration.ordered_logical_channels.index(channel_id)
            for channel_id in channel_ids
        )
        projected_configuration = replace(
            self._configuration,
            ordered_logical_channels=channel_ids,
            ordered_physical_channels=tuple(
                self._configuration.ordered_physical_channels[position] for position in positions
            ),
            requested_input_ranges_v=tuple(
                self._configuration.requested_input_ranges_v[position] for position in positions
            ),
            effective_input_ranges_v=tuple(
                self._configuration.effective_input_ranges_v[position] for position in positions
            ),
        )
        projected_experiment = replace(
            self._runtime_experiment,
            session=replace(
                self._runtime_experiment.session,
                parent_output_directory=segment.plan.parent_directory,
            ),
            channels=tuple(
                replace(channel, enabled=channel.channel_id in channel_ids)
                for channel in self._runtime_experiment.channels
            ),
            acquisition=replace(self._runtime_experiment.acquisition, channel_ids=channel_ids),
        )
        return SessionInitialization(
            plan=segment.plan,
            profile=self._intent.profile,
            experiment=projected_experiment,
            recipe=self._recipe,
            discovery=self._discovery,
            daq_configuration=projected_configuration,
            application_version=__version__,
            session_type=SessionType.RAW_DIAGNOSTIC,
            diagnostic_authorization=(
                self._intent.authorization.to_dict() if self._intent.authorization else None
            ),
            open_acquisition=segment.provenance,
        )

    def _segment_event(
        self,
        segment: _SegmentRuntime,
        action: str,
        timestamp: datetime,
        local_sample_index: int,
    ) -> AcquisitionEvent:
        return AcquisitionEvent(
            event_sequence=0 if action == "STARTED" else 1,
            code=f"DAQ.OPEN_ACQUISITION.RECORDING_{action}",
            severity=ErrorSeverity.INFO,
            session_id=segment.plan.session_id,
            state=(AcquisitionState.RUNNING if action == "STARTED" else AcquisitionState.STOPPING),
            timestamp=timestamp,
            monotonic_timestamp=self._clock.monotonic(),
            message=f"Open-acquisition recording {action.casefold()}.",
            context={
                "monitor_run_id": segment.provenance.monitor_run_id,
                "selected_channel_id": segment.provenance.selected_channel_id,
                "recorded_channel_ids": list(segment.provenance.recorded_channel_ids),
                "source_start_sample_index": segment.provenance.source_start_sample_index,
                "local_sample_index": local_sample_index,
            },
            sample_index=local_sample_index,
        )

    def _require_monitoring(self, operation: str) -> None:
        if self._monitor_state is not OpenMonitorState.MONITORING:
            raise ApplicationStateError(
                f"Open-acquisition operation {operation} requires active monitoring.",
                code="DAQ.APPLICATION.OPEN_MONITOR_ILLEGAL_STATE",
                context={"operation": operation, "state": self._monitor_state.value},
            )


def _continuous_experiment(experiment: ExperimentConfig) -> ExperimentConfig:
    return replace(
        experiment,
        acquisition=replace(
            experiment.acquisition,
            sampling_mode=SamplingMode.CONTINUOUS,
            requested_duration_seconds=None,
        ),
    )


def _project_chunk(
    chunk: AcquisitionChunk,
    *,
    channel_positions: tuple[int, ...] | None = None,
    channel_position: int | None = None,
    session_id: str,
    source_origin: int,
    sequence_number: int,
) -> AcquisitionChunk:
    if channel_positions is None:
        if channel_position is None:
            raise ValueError("recording projection requires at least one channel")
        channel_positions = (channel_position,)
    if not channel_positions or len(channel_positions) != len(set(channel_positions)):
        raise ValueError("recording projection requires unique channel positions")
    samples = np.ascontiguousarray(
        chunk.samples[list(channel_positions)],
        dtype=np.float64,
    )
    samples.flags.writeable = False
    local_start = chunk.start_sample_index - source_origin
    return AcquisitionChunk(
        session_id=session_id,
        sequence_number=sequence_number,
        start_sample_index=local_start,
        sample_count=chunk.sample_count,
        sample_rate_hz=chunk.sample_rate_hz,
        ordered_channel_ids=tuple(chunk.ordered_channel_ids[index] for index in channel_positions),
        ordered_channel_units=tuple(
            chunk.ordered_channel_units[index] for index in channel_positions
        ),
        samples=samples,
        read_completed_monotonic=chunk.read_completed_monotonic,
        read_duration_seconds=chunk.read_duration_seconds,
    )


def _storage_fault(cause: BaseException, fallback_code: str) -> SessionFaultSummary:
    if isinstance(cause, LabDAQError) and cause.code.startswith(("DAQ.STORAGE.", "DAQ.SESSION.")):
        code = cause.code
        message = cause.message
        context = dict(cause.context)
    else:
        code = fallback_code
        message = str(cause) or type(cause).__name__
        context = {"exception_type": type(cause).__name__}
    return SessionFaultSummary(code=code, message=message, context=context)
