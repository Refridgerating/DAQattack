"""Application-owned DAQ-M10 live acquisition and display orchestration."""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import UTC, datetime
from queue import Empty
from threading import Event, RLock, Thread, current_thread
from typing import TYPE_CHECKING, Protocol, cast

from labdaq.application.processing_service import CalibrationResolutionService
from labdaq.application.readiness_service import (
    AcquisitionLaunchIntent,
    FunctionalPreviewLaunchIntent,
    RawDiagnosticLaunchIntent,
)
from labdaq.domain.enums import ProcessingDisposition
from labdaq.errors import ApplicationStateError, LabDAQError, ProcessingError
from labdaq.ports.acquisition import (
    AcquisitionState,
    AcquisitionStreamClosedError,
)
from labdaq.ports.operator import (
    DisplayDegradation,
    LiveDisplayConfiguration,
    LiveMetricSnapshot,
    LivePlotFrame,
    LivePlotSeries,
    OperatorRunStatus,
    OperatorRunSummary,
)
from labdaq.ports.session import SessionType, VerificationLevel

if TYPE_CHECKING:
    import numpy as np

    from labdaq.application.session_service import (
        RecordingSessionService,
        SessionInspectionService,
    )
    from labdaq.domain.enums import ProcessingMode, SignalStage
    from labdaq.domain.models import (
        ExperimentConfig,
        ProcessingRecipeEnvelope,
        ResolvedChannelCalibration,
    )
    from labdaq.ports.acquisition import AcquisitionChunk, AcquisitionChunkStream
    from labdaq.ports.configuration import BundledRecipeResolverPort
    from labdaq.ports.processing import ScientificProcessingPort, StreamingProcessorPort


class OperatorClock(Protocol):
    def wall_time(self) -> datetime: ...

    def monotonic(self) -> float: ...


class SystemOperatorClock:
    def wall_time(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()


class RecordingSessionFactory(Protocol):
    def __call__(self) -> RecordingSessionService: ...


class _SignalRingBuffer:
    """Preallocated channel-major ring retaining one contiguous source suffix."""

    def __init__(self, channel_count: int, capacity_samples: int) -> None:
        import numpy as np

        if channel_count <= 0 or capacity_samples <= 0:
            raise ValueError("ring-buffer dimensions must be positive")
        self._values = np.empty((channel_count, capacity_samples), dtype=np.float64)
        self._capacity = capacity_samples
        self._count = 0
        self._write_position = 0
        self._start_index = 0

    @property
    def count(self) -> int:
        return self._count

    @property
    def start_index(self) -> int:
        return self._start_index

    @property
    def stop_index(self) -> int:
        return self._start_index + self._count

    def clear(self, *, next_start_index: int = 0) -> None:
        self._count = 0
        self._write_position = 0
        self._start_index = next_start_index

    def append(self, start_index: int, values: np.ndarray) -> None:
        import numpy as np

        if (
            not isinstance(values, np.ndarray)
            or values.dtype != np.float64
            or values.ndim != 2
            or values.shape[0] != self._values.shape[0]
            or values.shape[1] == 0
        ):
            raise ValueError("ring-buffer values must be a non-empty float64 matrix")
        if self._count and start_index != self.stop_index:
            raise ValueError("ring-buffer chunks must be source-contiguous")
        if not self._count:
            self._start_index = start_index
        sample_count = values.shape[1]
        if sample_count >= self._capacity:
            retained = np.asarray(values[:, -self._capacity :], dtype=np.float64)
            self._values[:, :] = retained
            self._count = self._capacity
            self._write_position = 0
            self._start_index = start_index + sample_count - self._capacity
            return
        overflow = max(0, self._count + sample_count - self._capacity)
        if overflow:
            self._start_index += overflow
            self._count -= overflow
        first = min(sample_count, self._capacity - self._write_position)
        self._values[:, self._write_position : self._write_position + first] = values[:, :first]
        remaining = sample_count - first
        if remaining:
            self._values[:, :remaining] = values[:, first:]
        self._write_position = (self._write_position + sample_count) % self._capacity
        self._count += sample_count

    def snapshot(self, maximum_samples: int) -> tuple[int, np.ndarray] | None:
        if self._count == 0:
            return None
        count = min(self._count, maximum_samples)
        stop = self.stop_index
        return stop - count, self.slice(stop - count, stop)

    def slice(self, start_index: int, stop_index: int) -> np.ndarray:
        import numpy as np

        if (
            start_index < self.start_index
            or stop_index > self.stop_index
            or stop_index <= start_index
        ):
            raise ValueError("ring-buffer slice is outside the retained source range")
        chronological_start = (self._write_position - self._count) % self._capacity
        offset = start_index - self.start_index
        count = stop_index - start_index
        position = (chronological_start + offset) % self._capacity
        first = min(count, self._capacity - position)
        if first == count:
            result = np.array(self._values[:, position : position + count], copy=True)
        else:
            result = np.concatenate(
                (self._values[:, position:], self._values[:, : count - first]),
                axis=1,
            )
        result = np.ascontiguousarray(result, dtype=np.float64)
        result.flags.writeable = False
        return result


def vectorized_min_max(
    values: np.ndarray,
    *,
    start_sample_index: int,
    maximum_points: int,
) -> tuple[tuple[np.ndarray, np.ndarray], ...]:
    """Apply the accepted min-max-v1 buckets without per-source-sample Python work."""
    import numpy as np

    if values.dtype != np.float64 or values.ndim != 2 or values.shape[1] == 0:
        raise ValueError("live decimation requires a non-empty float64 matrix")
    if not np.all(np.isfinite(values)):
        raise ValueError("live decimation cannot bridge nonfinite source values")
    if maximum_points < 4:
        raise ValueError("live decimation requires at least four points")
    sample_count = values.shape[1]
    if sample_count <= maximum_points:
        indices = np.arange(
            start_sample_index,
            start_sample_index + sample_count,
            dtype=np.int64,
        )
        indices.flags.writeable = False
        return tuple((indices, _readonly(values[channel])) for channel in range(values.shape[0]))
    bucket_count = (maximum_points - 2) // 2
    interior_count = sample_count - 2
    results: list[tuple[np.ndarray, np.ndarray]] = []
    for channel in range(values.shape[0]):
        positions: list[int] = [0]
        row = values[channel]
        for bucket in range(bucket_count):
            left = 1 + (bucket * interior_count + bucket_count - 1) // bucket_count
            right = 1 + ((bucket + 1) * interior_count + bucket_count - 1) // bucket_count
            if right <= left:
                continue
            segment = row[left:right]
            low = left + int(np.argmin(segment))
            high = left + int(np.argmax(segment))
            positions.extend(sorted({low, high}))
        positions.append(sample_count - 1)
        selected = np.asarray(positions, dtype=np.int64)
        source_indices = selected + start_sample_index
        selected_values = np.asarray(row[selected], dtype=np.float64)
        source_indices.flags.writeable = False
        selected_values.flags.writeable = False
        results.append((source_indices, selected_values))
    return tuple(results)


def _readonly(values: np.ndarray) -> np.ndarray:
    import numpy as np

    result = np.asarray(values, dtype=np.float64)
    if result.flags.writeable:
        result = np.array(result, copy=True)
        result.flags.writeable = False
    return result


class LiveAcquisitionCoordinator:
    """Own one M10 acquisition workflow without exposing adapters or threads to Qt."""

    def __init__(
        self,
        recording_factory: RecordingSessionFactory,
        inspection: SessionInspectionService,
        numerical: ScientificProcessingPort,
        recipe: ProcessingRecipeEnvelope | BundledRecipeResolverPort,
        *,
        display: LiveDisplayConfiguration | None = None,
        clock: OperatorClock | None = None,
    ) -> None:
        self._recording_factory = recording_factory
        self._inspection = inspection
        self._numerical = numerical
        self._recipe_source = recipe
        self._recipe: ProcessingRecipeEnvelope | None = None
        self._display = display or LiveDisplayConfiguration()
        self._clock = clock or SystemOperatorClock()
        self._lock = RLock()
        self._terminal = Event()
        self._recording: RecordingSessionService | None = None
        self._display_stream: AcquisitionChunkStream | None = None
        self._display_thread: Thread | None = None
        self._finalizer_thread: Thread | None = None
        self._processor: StreamingProcessorPort | None = None
        self._calibrations: tuple[ResolvedChannelCalibration, ...] = ()
        self._processed_positions: tuple[int, ...] = ()
        self._processed_channel_ids: tuple[str, ...] = ()
        self._raw: _SignalRingBuffer | None = None
        self._conditioned: _SignalRingBuffer | None = None
        self._intent: (
            AcquisitionLaunchIntent
            | RawDiagnosticLaunchIntent
            | FunctionalPreviewLaunchIntent
            | None
        ) = None
        self._summary: OperatorRunSummary | None = None
        self._terminal_error: BaseException | None = None
        self._metric_snapshot: LiveMetricSnapshot | None = None
        self._last_metric_monotonic: float | None = None
        self._frame_sequence = 0
        self._display_degradation: DisplayDegradation | None = None
        self._raw_channel_names: tuple[str, ...] = ()
        self._conditioned_channel_names: tuple[str, ...] = ()
        self._raw_units: tuple[str, ...] = ()
        self._conditioned_units: tuple[str, ...] = ()
        self._sample_rate_hz: float | None = None
        self._raw_diagnostic = False
        self._closed = False

    @property
    def configuration(self) -> LiveDisplayConfiguration:
        return self._display

    def set_visible_window(self, seconds: float) -> LiveDisplayConfiguration:
        if not seconds or seconds <= 0 or seconds > self._display.maximum_history_seconds:
            raise ValueError("visible window must be inside the configured live history")
        with self._lock:
            self._display = replace(self._display, visible_window_seconds=float(seconds))
            return self._display

    def start(
        self,
        intent: AcquisitionLaunchIntent | RawDiagnosticLaunchIntent | FunctionalPreviewLaunchIntent,
    ) -> OperatorRunStatus:
        with self._lock:
            if self._closed:
                raise ApplicationStateError(
                    "The live acquisition coordinator is closed.",
                    code="DAQ.APPLICATION.COORDINATOR_CLOSED",
                )
            if self._recording is not None and self._summary is None:
                raise ApplicationStateError(
                    "An acquisition is already active.",
                    code="DAQ.APPLICATION.START_DUPLICATE",
                    remediation="Stop and finalize the active session before starting another.",
                )
            previous = self._recording
        if previous is not None:
            previous.close()
        recipe = _resolve_experiment_recipe(self._recipe_source, intent.experiment)
        self._recipe = recipe
        recording = self._recording_factory()
        raw_diagnostic = isinstance(intent, RawDiagnosticLaunchIntent)
        if isinstance(intent, RawDiagnosticLaunchIntent):
            status = recording.prepare(
                intent.session_plan,
                intent.profile,
                intent.experiment,
                recipe,
                session_type=SessionType.RAW_DIAGNOSTIC,
                diagnostic_authorization=intent.authorization.to_dict(),
            )
        elif isinstance(intent, FunctionalPreviewLaunchIntent):
            status = recording.prepare(
                intent.session_plan,
                intent.profile,
                intent.experiment,
                recipe,
            )
        else:
            attachment = intent.qualification_attachment
            if attachment is None:
                recording.close()
                raise ApplicationStateError(
                    "Acquisition launch lacks attached qualification evidence.",
                    code="DAQ.APPLICATION.START_INELIGIBLE",
                    remediation="Refresh readiness and qualification before starting.",
                )
            status = recording.prepare(
                intent.session_plan,
                intent.profile,
                intent.experiment,
                recipe,
                qualification_attachment=attachment,
            )
        stream = recording.display_stream()
        actual_rate = status.acquisition.actual_sample_rate_hz
        assert actual_rate is not None
        ordered_ids = status.acquisition.ordered_channel_ids
        calibrations: tuple[ResolvedChannelCalibration, ...]
        processor: StreamingProcessorPort | None
        if raw_diagnostic:
            calibrations = ()
            processor = None
        else:
            resolver = CalibrationResolutionService()
            calibrated_ids = tuple(
                channel_id
                for channel_id in ordered_ids
                if next(
                    channel
                    for channel in intent.experiment.channels
                    if channel.channel_id == channel_id
                ).processing_disposition
                is ProcessingDisposition.CALIBRATED
            )
            calibrations, _ = resolver.resolve_experiment(
                intent.profile,
                intent.experiment,
                calibrated_ids,
            )
            processor = self._numerical.create_stream(
                recipe,
                calibrations,
                sample_rate_hz=actual_rate,
                mode=_live_processing_mode(),
            )
        capacity = max(1, int(round(actual_rate * self._display.maximum_history_seconds)))
        enabled = sorted(
            (channel for channel in intent.experiment.channels if channel.enabled),
            key=lambda channel: channel.channel_order,
        )
        processed_ids = tuple(item.logical_channel_id for item in calibrations)
        processed_positions = tuple(ordered_ids.index(channel_id) for channel_id in processed_ids)
        enabled_by_id = {channel.channel_id: channel for channel in enabled}
        with self._lock:
            self._terminal.clear()
            self._recording = recording
            self._display_stream = stream
            self._processor = processor
            self._calibrations = calibrations
            self._processed_positions = processed_positions
            self._processed_channel_ids = processed_ids
            self._raw = _SignalRingBuffer(len(ordered_ids), capacity)
            self._conditioned = (
                None if raw_diagnostic else _SignalRingBuffer(len(processed_ids), capacity)
            )
            self._intent = intent
            self._summary = None
            self._terminal_error = None
            self._metric_snapshot = None
            self._last_metric_monotonic = None
            self._frame_sequence = 0
            self._display_degradation = None
            self._raw_channel_names = tuple(channel.logical_name for channel in enabled)
            self._conditioned_channel_names = tuple(
                enabled_by_id[channel_id].logical_name for channel_id in processed_ids
            )
            self._raw_units = status.acquisition.ordered_channel_units
            self._conditioned_units = tuple(
                calibration.output_unit.value for calibration in calibrations
            )
            self._sample_rate_hz = actual_rate
            self._raw_diagnostic = raw_diagnostic
            display_thread = Thread(
                target=self._consume_display,
                name=f"labdaq-live-display-{intent.session_plan.session_id}",
                daemon=True,
            )
            self._display_thread = display_thread
            display_thread.start()
        try:
            recording.start()
        except BaseException:
            recording.close()
            display_thread.join(2.0)
            with self._lock:
                self._recording = None
            raise
        finalizer = Thread(
            target=self._finalize,
            name=f"labdaq-live-finalize-{intent.session_plan.session_id}",
            daemon=True,
        )
        with self._lock:
            self._finalizer_thread = finalizer
            finalizer.start()
        return self.status()

    def request_stop(self) -> OperatorRunStatus:
        with self._lock:
            recording = self._recording
            summary = self._summary
        if recording is None:
            return self.status()
        if summary is not None:
            return self.status()
        current = recording.status().acquisition.state
        if current in {AcquisitionState.RUNNING, AcquisitionState.STOPPING}:
            recording.request_stop("operator")
        return self.status()

    def status(self) -> OperatorRunStatus:
        with self._lock:
            recording = self._recording
            intent = self._intent
            summary = self._summary
            frame_count = self._frame_sequence
            metric = self._metric_snapshot
            degradation = self._display_degradation
        if recording is None:
            return OperatorRunStatus(
                state=AcquisitionState.IDLE,
                finalization_state=None,
                session_id=None,
                session_path=None,
                experiment_name=None,
                started_at=None,
                elapsed_seconds=0.0,
                requested_duration_seconds=None,
                actual_sample_rate_hz=None,
                ordered_channel_ids=(),
                ordered_channel_units=(),
                acquired_samples_per_channel=0,
                persisted_samples_per_channel=0,
                recording_queue_occupancy=0,
                recording_queue_capacity=0,
                display_queue_occupancy=0,
                display_queue_capacity=0,
                display_frames_produced=0,
                display_frames_dropped=0,
                acquisition_gap_count=0,
                persistence_loss_samples=None,
                warning_count=0,
            )
        current = recording.status()
        acquisition = current.acquisition
        gaps = (
            acquisition.telemetry.source_missing_samples
            + acquisition.telemetry.source_duplicated_samples
            + acquisition.telemetry.source_reordered_reads
        )
        persistence_loss = (
            max(0, acquisition.acquired_samples_per_channel - current.persisted_samples_per_channel)
            if summary is not None
            else None
        )
        metric_stale = metric is None and not self._raw_diagnostic
        if metric is not None and self._last_metric_monotonic is not None:
            metric_stale = (
                self._clock.monotonic() - self._last_metric_monotonic
                > self._display.metric_update_interval_seconds * 2
            )
        return OperatorRunStatus(
            state=acquisition.state,
            finalization_state=current.session_state,
            session_id=acquisition.session_id,
            session_path=current.plan.session_path,
            experiment_name=(intent.experiment.session.experiment_name if intent else None),
            started_at=acquisition.started_at,
            elapsed_seconds=acquisition.elapsed_seconds,
            requested_duration_seconds=acquisition.requested_duration_seconds,
            actual_sample_rate_hz=acquisition.actual_sample_rate_hz,
            ordered_channel_ids=acquisition.ordered_channel_ids,
            ordered_channel_units=acquisition.ordered_channel_units,
            acquired_samples_per_channel=acquisition.acquired_samples_per_channel,
            persisted_samples_per_channel=current.persisted_samples_per_channel,
            recording_queue_occupancy=acquisition.recording_queue_occupancy,
            recording_queue_capacity=acquisition.recording_queue_capacity,
            display_queue_occupancy=acquisition.display_queue_occupancy,
            display_queue_capacity=acquisition.display_queue_capacity,
            display_frames_produced=frame_count,
            display_frames_dropped=acquisition.display_frames_dropped,
            acquisition_gap_count=gaps,
            persistence_loss_samples=persistence_loss,
            warning_count=acquisition.warning_count + (1 if degradation else 0),
            fault=acquisition.current_fault,
            storage_fault_code=(current.storage_fault.code if current.storage_fault else None),
            metric_stale=metric_stale,
            display_degradation=degradation,
            requested_sample_rate_hz=acquisition.requested_sample_rate_hz,
        )

    def build_plot_frame(
        self,
        signal_stage: SignalStage,
        *,
        channel_ids: tuple[str, ...] | None = None,
    ) -> LivePlotFrame | None:
        from labdaq.domain.enums import SignalStage

        if signal_stage not in {SignalStage.RAW, SignalStage.CONDITIONED}:
            raise ValueError("live plots support raw or conditioned signals")
        with self._lock:
            intent = self._intent
            ring = self._raw if signal_stage is SignalStage.RAW else self._conditioned
            if intent is None or ring is None:
                return None
            assert self._sample_rate_hz is not None
            sample_rate = self._sample_rate_hz
            sample_limit = max(1, int(round(sample_rate * self._display.visible_window_seconds)))
            snapshot = ring.snapshot(sample_limit)
            if snapshot is None:
                return None
            start, values = snapshot
            all_ids = (
                intent.experiment.acquisition.channel_ids
                if signal_stage is SignalStage.RAW
                else self._processed_channel_ids
            )
            selected_ids = channel_ids or all_ids
            positions = tuple(all_ids.index(channel_id) for channel_id in selected_ids)
            selected_values = values[list(positions), :]
            units = self._raw_units if signal_stage is SignalStage.RAW else self._conditioned_units
            names = (
                self._raw_channel_names
                if signal_stage is SignalStage.RAW
                else self._conditioned_channel_names
            )
            sequence = self._frame_sequence
            degradation = self._display_degradation
            recipe = self._recipe
            self._frame_sequence += 1
        reduced = vectorized_min_max(
            selected_values,
            start_sample_index=start,
            maximum_points=self._display.maximum_points_per_channel,
        )
        series = []
        for output, position in zip(reduced, positions, strict=True):
            indices, channel_values = output
            times = indices.astype("float64") / sample_rate
            times.flags.writeable = False
            series.append(
                LivePlotSeries(
                    channel_id=all_ids[position],
                    display_name=names[position],
                    unit=units[position],
                    source_sample_indices=indices,
                    relative_time_seconds=times,
                    values=channel_values,
                )
            )
        return LivePlotFrame(
            session_id=intent.session_plan.session_id,
            frame_sequence=sequence,
            signal_stage=signal_stage,
            start_sample_index=start,
            stop_sample_index=start + values.shape[1],
            sample_rate_hz=sample_rate,
            source_sample_count=values.shape[1],
            series=tuple(series),
            recipe_id=(
                recipe.recipe_id
                if signal_stage is SignalStage.CONDITIONED and recipe is not None
                else None
            ),
            recipe_hash=(
                self._processor.recipe_hash
                if signal_stage is SignalStage.CONDITIONED and self._processor
                else None
            ),
            degradation=degradation,
        )

    def metrics(self) -> LiveMetricSnapshot | None:
        with self._lock:
            snapshot = self._metric_snapshot
            last = self._last_metric_monotonic
        if snapshot is None or last is None:
            return snapshot
        stale = self._clock.monotonic() - last > self._display.metric_update_interval_seconds * 2
        return replace(snapshot, stale=stale) if stale != snapshot.stale else snapshot

    def wait_for_terminal(self, timeout: float | None = None) -> OperatorRunSummary:
        if not self._terminal.wait(timeout):
            raise TimeoutError("live acquisition did not finalize before the timeout")
        with self._lock:
            error = self._terminal_error
            summary = self._summary
        if error is not None:
            raise error
        assert summary is not None
        return summary

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            recording = self._recording
        if recording is not None:
            try:
                state = recording.status().acquisition.state
                if state in {AcquisitionState.RUNNING, AcquisitionState.STOPPING}:
                    self.request_stop()
                    self._terminal.wait(self._display.maximum_history_seconds + 5.0)
            except LabDAQError:
                pass
            recording.close()
        for thread in (self._display_thread, self._finalizer_thread):
            if thread is not None and thread is not current_thread():
                thread.join(2.0)

    def _consume_display(self) -> None:
        assert self._display_stream is not None
        while True:
            try:
                chunk = self._display_stream.get(0.2)
            except Empty:
                continue
            except AcquisitionStreamClosedError:
                return
            with self._lock:
                raw = self._raw
                conditioned = self._conditioned
                processor = self._processor
            assert raw is not None
            if processor is None or conditioned is None:
                with self._lock:
                    if raw.count and raw.stop_index != chunk.start_sample_index:
                        raw.clear(next_start_index=chunk.start_sample_index)
                    raw.append(chunk.start_sample_index, chunk.samples)
                continue
            try:
                selected = _select_live_chunk(
                    chunk, self._processed_positions, self._processed_channel_ids
                )
                processed = processor.process(selected)
            except ProcessingError as error:
                degradation = DisplayDegradation(
                    code="DAQ.DISPLAY.PROCESSING_SOURCE_GAP",
                    message=error.message,
                    source_start_sample_index=chunk.start_sample_index,
                    detected_at=self._clock.wall_time(),
                )
                processor.reset()
                with self._lock:
                    conditioned.clear(next_start_index=chunk.start_sample_index)
                    self._display_degradation = degradation
                try:
                    processed = processor.process(selected)
                except ProcessingError:
                    with self._lock:
                        if raw.count and raw.stop_index != chunk.start_sample_index:
                            raw.clear(next_start_index=chunk.start_sample_index)
                        raw.append(chunk.start_sample_index, chunk.samples)
                    continue
            with self._lock:
                if raw.count and raw.stop_index != chunk.start_sample_index:
                    raw.clear(next_start_index=chunk.start_sample_index)
                raw.append(chunk.start_sample_index, chunk.samples)
                if conditioned.count and conditioned.stop_index != chunk.start_sample_index:
                    conditioned.clear(next_start_index=chunk.start_sample_index)
                conditioned.append(chunk.start_sample_index, processed.conditioned_samples)
                self._maybe_update_metrics_locked(chunk.sample_rate_hz)

    def _maybe_update_metrics_locked(self, sample_rate_hz: float) -> None:
        from labdaq.domain.enums import SignalStage
        from labdaq.domain.models import ProcessingRange

        now = self._clock.monotonic()
        if (
            self._last_metric_monotonic is not None
            and now - self._last_metric_monotonic < self._display.metric_update_interval_seconds
        ):
            return
        assert self._conditioned is not None and self._raw is not None and self._intent is not None
        window = max(1, int(round(sample_rate_hz * self._display.metric_window_seconds)))
        conditioned_snapshot = self._conditioned.snapshot(window)
        if conditioned_snapshot is None:
            return
        start, conditioned = conditioned_snapshot
        stop = start + conditioned.shape[1]
        try:
            raw = self._raw.slice(start, stop)
        except ValueError:
            return
        assert self._recipe is not None
        assert self._recipe.metrics is not None and self._processor is not None
        raw_positions = self._processed_positions
        results = tuple(
            self._numerical.metrics(
                raw[raw_positions[index]],
                conditioned[index],
                channel_id=calibration.logical_channel_id,
                unit=calibration.output_unit,
                sample_range=ProcessingRange(start, stop),
                sample_rate_hz=sample_rate_hz,
                recipe_hash=self._processor.recipe_hash,
                calibration=calibration,
                clipping_margin_v=self._recipe.metrics.clipping_margin_v,
            )
            for index, calibration in enumerate(self._calibrations)
        )
        updated = self._clock.wall_time()
        self._metric_snapshot = LiveMetricSnapshot(
            session_id=self._intent.session_plan.session_id,
            start_sample_index=start,
            stop_sample_index=stop,
            updated_at=updated,
            update_interval_seconds=self._display.metric_update_interval_seconds,
            window_seconds=self._display.metric_window_seconds,
            signal_stage=SignalStage.CONDITIONED,
            effective_bandwidth=_bandwidth_label(self._recipe),
            metrics=results,
            degradation=self._display_degradation,
        )
        self._last_metric_monotonic = now

    def _finalize(self) -> None:
        assert self._recording is not None
        recording = self._recording
        try:
            completed = recording.wait_for_terminal()
            display_thread = self._display_thread
            if display_thread is not None:
                display_thread.join(2.0)
            session = self._inspection.summary(
                completed.session_path,
                level=VerificationLevel.FULL,
            )
            with self._lock:
                status = recording.status().acquisition
                self._summary = OperatorRunSummary(
                    recorded=completed,
                    session=session,
                    display_frames_produced=self._frame_sequence,
                    display_frames_dropped=status.display_frames_dropped,
                    display_degradation=self._display_degradation,
                )
        except BaseException as error:
            with self._lock:
                self._terminal_error = error
        finally:
            self._terminal.set()


def _live_processing_mode() -> ProcessingMode:
    from labdaq.domain.enums import ProcessingMode

    return ProcessingMode.LIVE


def _resolve_experiment_recipe(
    source: ProcessingRecipeEnvelope | BundledRecipeResolverPort,
    experiment: ExperimentConfig,
) -> ProcessingRecipeEnvelope:
    selected = experiment.draft_processing_recipe_id
    if selected is None:
        raise ApplicationStateError(
            "The experiment does not name a processing recipe.",
            code="DAQ.PROCESSING.RECIPE_REQUIRED",
            remediation="Select the rate-compatible bundled recipe before acquisition.",
        )
    loader = getattr(source, "load_bundled_processing_recipe", None)
    recipe = loader(selected) if callable(loader) else cast("ProcessingRecipeEnvelope", source)
    accepted_ids = {recipe.recipe_id, f"{recipe.recipe_id}-{recipe.recipe_version}"}
    if selected not in accepted_ids:
        raise ApplicationStateError(
            "The injected processing recipe does not match the experiment.",
            code="DAQ.PROCESSING.RECIPE_MISMATCH",
            context={"selected_recipe_id": selected, "resolved_recipe_id": recipe.recipe_id},
            remediation="Resolve the recipe named by the experiment configuration.",
        )
    return recipe


def _select_live_chunk(
    chunk: AcquisitionChunk,
    positions: tuple[int, ...],
    channel_ids: tuple[str, ...],
) -> AcquisitionChunk:
    import numpy as np

    from labdaq.ports.acquisition import AcquisitionChunk

    samples = np.ascontiguousarray(chunk.samples[list(positions)], dtype=np.float64)
    samples.flags.writeable = False
    return AcquisitionChunk(
        session_id=chunk.session_id,
        sequence_number=chunk.sequence_number,
        start_sample_index=chunk.start_sample_index,
        sample_count=chunk.sample_count,
        sample_rate_hz=chunk.sample_rate_hz,
        ordered_channel_ids=channel_ids,
        ordered_channel_units=tuple(chunk.ordered_channel_units[index] for index in positions),
        samples=samples,
        read_completed_monotonic=chunk.read_completed_monotonic,
        read_duration_seconds=chunk.read_duration_seconds,
    )


def _bandwidth_label(recipe: ProcessingRecipeEnvelope) -> str:
    stages = []
    for stage in recipe.stages:
        if stage.cutoff_hz is not None:
            stages.append(f"{stage.kind.value} {stage.cutoff_hz:g} Hz")
    return ", ".join(stages) or "affine calibration only"
