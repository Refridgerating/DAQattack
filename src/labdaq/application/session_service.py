"""Application services for durable recording, inspection, recovery, and replay."""

from __future__ import annotations

import time
from enum import StrEnum
from queue import Empty
from threading import RLock, Thread
from typing import TYPE_CHECKING
from uuid import uuid4

from labdaq.application.acquisition_engine import AcquisitionEngine, SystemAcquisitionClock
from labdaq.errors import (
    ApplicationStateError,
    LabDAQError,
    SessionWriteError,
    StorageError,
)
from labdaq.ports.acquisition import AcquisitionState, AcquisitionStreamClosedError
from labdaq.ports.session import (
    RecordedSessionCompletion,
    RecordingPolicy,
    RecordingStatus,
    SessionFaultSummary,
    SessionFinalization,
    SessionInitialization,
    SessionIntegrityReport,
    SessionPlan,
    SessionSummary,
    SessionType,
    VerificationLevel,
)
from labdaq.version import __version__

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from labdaq.domain.models import (
        ExperimentConfig,
        HardwareProfile,
        ProcessingRecipeEnvelope,
        SessionConfig,
    )
    from labdaq.ports.acquisition import AcquisitionChunkStream, AcquisitionEventStream
    from labdaq.ports.daq import DaqPort
    from labdaq.ports.session import (
        QualificationAttachment,
        ReplaySourcePort,
        ReplayStream,
        SessionPathPlannerPort,
        SessionReaderPort,
        SessionWriterFactoryPort,
        SessionWriterPort,
    )


class RecordingServiceState(StrEnum):
    IDLE = "idle"
    PREPARED = "prepared"
    RUNNING = "running"
    TERMINAL = "terminal"
    CLOSED = "closed"


class SessionPlanningService:
    def __init__(
        self,
        planner: SessionPathPlannerPort,
        *,
        policy: RecordingPolicy | None = None,
        clock: SystemAcquisitionClock | None = None,
    ) -> None:
        self._planner = planner
        self._policy = policy or RecordingPolicy()
        self._clock = clock or SystemAcquisitionClock()

    def preview(self, session: SessionConfig) -> SessionPlan:
        return self._planner.plan_session(
            session,
            session_id=str(uuid4()),
            created_at=self._clock.wall_time(),
            policy=self._policy,
        )


class RecordingSessionService:
    """Coordinate one M04 engine with one durable session writer."""

    def __init__(
        self,
        daq: DaqPort,
        planner: SessionPathPlannerPort,
        writer_factory: SessionWriterFactoryPort,
        reader: SessionReaderPort,
        *,
        policy: RecordingPolicy | None = None,
        clock: SystemAcquisitionClock | None = None,
    ) -> None:
        self._daq = daq
        self._planner = planner
        self._writer_factory = writer_factory
        self._reader = reader
        self._policy = policy or RecordingPolicy()
        self._clock = clock or SystemAcquisitionClock()
        self._lock = RLock()
        self._state = RecordingServiceState.IDLE
        self._plan: SessionPlan | None = None
        self._engine: AcquisitionEngine | None = None
        self._writer: SessionWriterPort | None = None
        self._recording_stream: AcquisitionChunkStream | None = None
        self._event_stream: AcquisitionEventStream | None = None
        self._recording_thread: Thread | None = None
        self._event_thread: Thread | None = None
        self._storage_fault: SessionFaultSummary | None = None
        self._completion: RecordedSessionCompletion | None = None
        self._closed = False
        self._stop_timeout_seconds = 5.0
        self._write_count = 0
        self._write_duration_total = 0.0
        self._write_duration_max = 0.0

    def preview(self, session: SessionConfig) -> SessionPlan:
        with self._lock:
            self._require_state("preview", {RecordingServiceState.IDLE})
            return self._planner.plan_session(
                session,
                session_id=str(uuid4()),
                created_at=self._clock.wall_time(),
                policy=self._policy,
            )

    def prepare(
        self,
        plan: SessionPlan,
        profile: HardwareProfile,
        experiment: ExperimentConfig,
        recipe: ProcessingRecipeEnvelope,
        *,
        session_type: SessionType = SessionType.EXPERIMENT,
        qualification_attachment: QualificationAttachment | None = None,
        diagnostic_authorization: Mapping[str, object] | None = None,
    ) -> RecordingStatus:
        with self._lock:
            self._require_state("prepare", {RecordingServiceState.IDLE})
            expected = self._planner.plan_session(
                experiment.session,
                session_id=plan.session_id,
                created_at=plan.created_at,
                policy=self._policy,
            )
            if expected != plan:
                raise SessionWriteError(
                    "The session preview no longer matches the selected experiment configuration.",
                    code="DAQ.SESSION.PLAN_MISMATCH",
                    context={
                        "planned_path": str(plan.session_path),
                        "expected_path": str(expected.session_path),
                    },
                    remediation="Generate a new preview after changing session configuration.",
                )
            if experiment.draft_processing_recipe_id != recipe.recipe_id:
                raise SessionWriteError(
                    "The processing-recipe snapshot does not match the experiment configuration.",
                    code="DAQ.SESSION.RECIPE_MISMATCH",
                    context={
                        "configured_recipe_id": experiment.draft_processing_recipe_id,
                        "received_recipe_id": recipe.recipe_id,
                    },
                    remediation="Use the configured bundled baseline recipe snapshot.",
                )
            discovery = self._daq.discover()
            engine = AcquisitionEngine(
                self._daq,
                clock=self._clock,
                session_id_factory=lambda: plan.session_id,
            )
            event_stream = engine.subscribe_events()
            try:
                engine.configure(profile, experiment)
                engine.arm()
                initialization = SessionInitialization(
                    plan=plan,
                    profile=profile,
                    experiment=experiment,
                    recipe=recipe,
                    discovery=discovery,
                    daq_configuration=engine.daq_configuration(),
                    application_version=__version__,
                    session_type=session_type,
                    qualification_attachment=qualification_attachment,
                    diagnostic_authorization=diagnostic_authorization,
                )
                writer = self._writer_factory.create_writer(initialization, self._policy)
            except BaseException:
                event_stream.close()
                try:
                    if engine.status().state in {
                        AcquisitionState.CONFIGURED,
                        AcquisitionState.ARMED,
                        AcquisitionState.FAULTED,
                    }:
                        engine.reset()
                finally:
                    engine.close()
                raise
            self._plan = plan
            self._engine = engine
            self._writer = writer
            self._recording_stream = engine.recording_stream()
            self._event_stream = event_stream
            self._state = RecordingServiceState.PREPARED
            self._stop_timeout_seconds = experiment.acquisition.chunk_config.stop_timeout_seconds
            return self._status_locked()

    def start(self) -> RecordingStatus:
        with self._lock:
            self._require_state("start", {RecordingServiceState.PREPARED})
            assert self._engine is not None
            assert self._recording_stream is not None
            assert self._event_stream is not None
            recording_thread = Thread(
                target=self._consume_recording,
                name=f"labdaq-recorder-{self._plan.session_id if self._plan else 'unknown'}",
                daemon=True,
            )
            event_thread = Thread(
                target=self._consume_events,
                name=f"labdaq-events-{self._plan.session_id if self._plan else 'unknown'}",
                daemon=True,
            )
            self._recording_thread = recording_thread
            self._event_thread = event_thread
            recording_thread.start()
            event_thread.start()
            self._engine.start()
            self._state = RecordingServiceState.RUNNING
            return self._status_locked()

    def request_stop(self, reason: str = "operator") -> RecordingStatus:
        with self._lock:
            self._require_state("request_stop", {RecordingServiceState.RUNNING})
            assert self._engine is not None
            self._engine.request_stop(reason)
            return self._status_locked()

    def wait_for_terminal(self, timeout: float | None = None) -> RecordedSessionCompletion:
        with self._lock:
            if self._completion is not None:
                return self._completion
            self._require_state("wait_for_terminal", {RecordingServiceState.RUNNING})
            assert self._engine is not None
            engine = self._engine
            writer = self._writer
            assert writer is not None
        deadline = None if timeout is None else time.monotonic() + timeout
        acquisition = engine.wait_for_terminal(timeout)
        for thread in (self._recording_thread, self._event_thread):
            if thread is not None:
                remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
                thread.join(remaining)
                if thread.is_alive():
                    self._set_storage_fault(
                        SessionWriteError(
                            "A durable-session consumer did not terminate after stream closure.",
                            code="DAQ.STORAGE.CONSUMER_STOP_TIMEOUT",
                            context={"thread": thread.name},
                            remediation="Retain the partial session and inspect storage diagnostics.",
                        )
                    )
        if (
            self._storage_fault is None
            and writer.persisted_samples_per_channel != acquisition.total_recording_samples_accepted
        ):
            self._set_storage_fault(
                SessionWriteError(
                    "Persisted and recording-accepted sample counts disagree.",
                    code="DAQ.STORAGE.PERSISTED_COUNT_MISMATCH",
                    context={
                        "persisted_samples_per_channel": writer.persisted_samples_per_channel,
                        "recording_accepted_samples_per_channel": (
                            acquisition.total_recording_samples_accepted
                        ),
                    },
                    remediation="Retain and verify the synchronized TDMS prefix.",
                )
            )
        try:
            completion = writer.finalize(SessionFinalization(acquisition, self._storage_fault))
        except StorageError as error:
            self._set_storage_fault(error)
            assert self._storage_fault is not None
            try:
                completion = writer.abort(self._storage_fault)
            except StorageError as abort_error:
                raise error from abort_error
        with self._lock:
            self._completion = completion
            self._state = RecordingServiceState.TERMINAL
            return completion

    def status(self) -> RecordingStatus:
        with self._lock:
            self._require_state(
                "status",
                {
                    RecordingServiceState.PREPARED,
                    RecordingServiceState.RUNNING,
                    RecordingServiceState.TERMINAL,
                },
            )
            return self._status_locked()

    def display_stream(self) -> AcquisitionChunkStream:
        """Expose the engine's existing bounded best-effort display stream."""
        with self._lock:
            self._require_state(
                "display_stream",
                {RecordingServiceState.PREPARED, RecordingServiceState.RUNNING},
            )
            assert self._engine is not None
            return self._engine.display_stream()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            state = self._state
            engine = self._engine
            writer = self._writer
        if state is RecordingServiceState.RUNNING and engine is not None:
            if engine.status().state is AcquisitionState.RUNNING:
                engine.request_stop("service_close")
            try:
                self.wait_for_terminal(self._stop_timeout_seconds + 1.0)
            except LabDAQError:
                pass
        elif state is RecordingServiceState.PREPARED and writer is not None:
            try:
                writer.abort(
                    SessionFaultSummary(
                        code="DAQ.SESSION.CANCELLED_BEFORE_START",
                        message="The prepared session was closed before acquisition started.",
                        context={},
                    )
                )
            except StorageError:
                writer.close()
        if engine is not None:
            engine.close()
        else:
            self._daq.close()
        with self._lock:
            self._closed = True
            self._state = RecordingServiceState.CLOSED

    def __enter__(self) -> RecordingSessionService:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _consume_recording(self) -> None:
        assert self._recording_stream is not None
        assert self._writer is not None
        assert self._engine is not None
        while True:
            try:
                chunk = self._recording_stream.get(0.2)
            except Empty:
                continue
            except AcquisitionStreamClosedError:
                return
            try:
                started = self._clock.monotonic()
                self._writer.append_chunk(chunk)
                self._writer.checkpoint(self._engine.status())
                duration = max(0.0, self._clock.monotonic() - started)
                with self._lock:
                    self._write_count += 1
                    self._write_duration_total += duration
                    self._write_duration_max = max(self._write_duration_max, duration)
            except StorageError as error:
                self._set_storage_fault(error)
                return

    def _consume_events(self) -> None:
        assert self._event_stream is not None
        assert self._writer is not None
        while True:
            try:
                event = self._event_stream.get(0.2)
            except Empty:
                continue
            except AcquisitionStreamClosedError:
                return
            try:
                self._writer.append_event(event)
            except StorageError as error:
                self._set_storage_fault(error)
                return

    def _set_storage_fault(self, error: LabDAQError) -> None:
        with self._lock:
            if self._storage_fault is None or (
                self._storage_fault.code == "DAQ.STORAGE.WRITER_FAULTED"
                and error.code != "DAQ.STORAGE.WRITER_FAULTED"
            ):
                self._storage_fault = SessionFaultSummary(
                    code=error.code,
                    message=error.message,
                    context=dict(error.context),
                )
            engine = self._engine
        if engine is not None and engine.status().state is AcquisitionState.RUNNING:
            try:
                engine.request_stop("storage_fault")
            except LabDAQError:
                pass

    def _status_locked(self) -> RecordingStatus:
        assert self._plan is not None
        assert self._engine is not None
        assert self._writer is not None
        return RecordingStatus(
            plan=self._plan,
            session_state=self._writer.state,
            acquisition=self._engine.status(),
            persisted_samples_per_channel=self._writer.persisted_samples_per_channel,
            checkpoint_sequence=self._writer.checkpoint_sequence,
            storage_fault=self._storage_fault,
            write_count=self._write_count,
            write_duration_total_seconds=self._write_duration_total,
            write_duration_max_seconds=self._write_duration_max,
        )

    def _require_state(self, operation: str, allowed: set[RecordingServiceState]) -> None:
        if self._state not in allowed:
            raise ApplicationStateError(
                f"Recording service operation {operation} is invalid in state {self._state.value}.",
                code="DAQ.SESSION.ILLEGAL_STATE_TRANSITION",
                context={
                    "operation": operation,
                    "state": self._state.value,
                    "allowed_states": sorted(state.value for state in allowed),
                },
                remediation="Follow preview, prepare, start, stop, and terminal order.",
            )


class SessionInspectionService:
    def __init__(self, reader: SessionReaderPort) -> None:
        self._reader = reader

    def inspect(
        self,
        path: Path,
        *,
        level: VerificationLevel = VerificationLevel.FAST,
    ) -> SessionIntegrityReport:
        return self._reader.inspect_session(path, level=level)

    def summary(
        self,
        path: Path,
        *,
        level: VerificationLevel = VerificationLevel.FAST,
    ) -> SessionSummary:
        return self._reader.summarize_session(path, level=level)


class SessionRecoveryService:
    def __init__(self, reader: SessionReaderPort) -> None:
        self._reader = reader

    def recover(self, path: Path) -> SessionIntegrityReport:
        return self._reader.recover_session(path)


class SessionReplayService:
    def __init__(self, source: ReplaySourcePort) -> None:
        self._source = source

    def open(self, path: Path, *, chunk_size: int) -> ReplayStream:
        return self._source.open_replay(path, chunk_size=chunk_size)
