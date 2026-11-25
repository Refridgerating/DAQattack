from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

import numpy as np
import pytest

from labdaq.adapters.configuration_json import JsonConfigurationAdapter
from labdaq.adapters.simulated.adapter import SimulatedDaqAdapter
from labdaq.adapters.simulated.generators import generate_samples
from labdaq.adapters.simulated.scenario import JsonSimulatorScenarioCodec
from labdaq.adapters.tdms.session import TdmsSessionStore
from labdaq.application.open_acquisition_service import OpenAcquisitionCoordinator
from labdaq.application.plotting_service import PlotDataService
from labdaq.application.readiness_service import (
    OpenAcquisitionIntent,
    RawDiagnosticAuthorization,
    RawDiagnosticConfirmation,
)
from labdaq.domain.enums import SignalStage
from labdaq.domain.plotting import ChannelSelection, PlotRepresentation, PlotRequest, PlotViewport
from labdaq.errors import ApplicationStateError, SessionWriteError
from labdaq.ports.operator import LiveDisplayConfiguration, OpenMonitorState
from labdaq.ports.session import IntegrityStatus, RecordingPolicy, VerificationLevel
from labdaq.processing import BaselineScientificProcessing

if TYPE_CHECKING:
    from collections.abc import Callable

    from labdaq.ports.acquisition import AcquisitionChunk, AcquisitionEvent
    from labdaq.ports.daq import DaqCancellationResult, DaqReadResult, DaqStopResult
    from labdaq.ports.session import (
        RecordedSessionCompletion,
        RecordingSegmentStatus,
        SessionFaultSummary,
        SessionFinalization,
        SessionInitialization,
        SessionWriterPort,
    )

pytestmark = pytest.mark.integration

FIXTURE = Path("tests/fixtures/configuration/valid-four-channel-synthetic-v1.json")


def test_open_monitor_records_rebased_multichannel_segments(tmp_path: Path) -> None:
    documents = JsonConfigurationAdapter()
    experiment = documents.load_experiment_config(FIXTURE)
    experiment = replace(
        experiment,
        session=replace(experiment.session, parent_output_directory=tmp_path),
        acquisition=replace(
            experiment.acquisition,
            chunk_config=replace(
                experiment.acquisition.chunk_config,
                samples_per_channel=250,
                # Retain the fixture's 160,000-sample recording buffer while
                # exercising small chunks. Keeping only 32 chunks would allow
                # 160 ms for filesystem initialization and fault on slower disks.
                recording_queue_capacity_chunks=(
                    experiment.acquisition.chunk_config.recording_queue_capacity_chunks
                    * experiment.acquisition.chunk_config.samples_per_channel
                    // 250
                ),
            ),
        ),
    )
    profile = documents.load_bundled_hardware_profile(
        experiment.hardware_profile_id,
        experiment.compatible_profile_revision,
    )
    scenario = replace(
        JsonSimulatorScenarioCodec().load_bundled("clean-four-channel"),
        pacing_mode="real_time",
    )
    channel_ids = experiment.acquisition.channel_ids
    channel_units = tuple(channel.raw_unit.value for channel in experiment.channels)
    selected = channel_ids[0]
    intent = OpenAcquisitionIntent(
        profile=profile,
        experiment=experiment,
        selected_channel_id=selected,
        eligibility_evaluated_at=datetime.now(UTC),
        recorded_channel_ids=channel_ids,
        recorded_channel_units=channel_units,
    )
    store = TdmsSessionStore()
    coordinator = OpenAcquisitionCoordinator(
        SimulatedDaqAdapter(scenario),
        store,
        store,
        documents.load_bundled_processing_recipe("baseline-v1"),
        policy=RecordingPolicy(),
        display=LiveDisplayConfiguration(
            visible_window_seconds=0.01,
            maximum_history_seconds=0.02,
            maximum_points_per_channel=400,
            metric_window_seconds=0.01,
        ),
    )
    try:
        coordinator.start_monitoring(intent)
        with pytest.raises(ApplicationStateError) as duplicate_monitor:
            coordinator.start_monitoring(intent)
        assert duplicate_monitor.value.code == "DAQ.APPLICATION.OPEN_MONITOR_DUPLICATE"
        _wait_until(lambda: coordinator.status().acquired_samples_per_channel >= 500)
        assert list(tmp_path.iterdir()) == []
        before = coordinator.status().acquired_samples_per_channel

        coordinator.start_recording()
        with pytest.raises(ApplicationStateError) as duplicate_recording:
            coordinator.start_recording()
        assert duplicate_recording.value.code == "DAQ.APPLICATION.OPEN_RECORD_DUPLICATE"
        _wait_until(lambda: coordinator.status().recording_persisted_samples >= 750)
        coordinator.stop_recording()
        coordinator.stop_recording()
        first = coordinator.wait_for_recording(10.0)
        assert first is not None
        after_first = coordinator.status().acquired_samples_per_channel
        assert after_first > before
        assert coordinator.status().monitor_state is OpenMonitorState.MONITORING

        alternate = tmp_path / "alternate"
        alternate.mkdir()
        coordinator.start_recording(parent_output_directory=alternate)
        _wait_until(lambda: coordinator.status().recording_persisted_samples >= 500)
        coordinator.stop_recording()
        second = coordinator.wait_for_recording(10.0)
        assert second is not None
        assert second.session_id != first.session_id
        assert second.session_path != first.session_path
        assert second.session_path.parent == alternate

        for recorded in (first, second):
            assert recorded.recording_segment_completion is not None
            assert recorded.acquisition_completion is None
            report = store.inspect_session(recorded.session_path, level=VerificationLevel.FULL)
            assert report.overall is IntegrityStatus.PASS
            summary = store.summarize_session(recorded.session_path, level=VerificationLevel.FULL)
            assert summary.ordered_channel_ids == channel_ids
            replay = store.open_replay(recorded.session_path, chunk_size=333)
            chunks = [chunk.samples for chunk in replay]
            replay.close()
            assert chunks
            values = np.concatenate(chunks, axis=1)
            assert values.shape == (len(channel_ids), recorded.persisted_samples_per_channel)
            plots = PlotDataService(store, documents, BaselineScientificProcessing())
            for representation in (PlotRepresentation.FFT_AMPLITUDE, PlotRepresentation.PSD):
                bundle = plots.build_plot_data(
                    PlotRequest(
                        recorded.session_path,
                        representation,
                        ChannelSelection(channel_ids),
                        plots.resolve_range(recorded.session_path),
                        PlotViewport(2000),
                        recipe_id=summary.recipe.recipe_id,
                        spectral_input_stage=SignalStage.RAW,
                    )
                )
                assert tuple(series.channel_id for series in bundle.series) == channel_ids
                assert all(
                    series.spectral_input_stage is SignalStage.RAW for series in bundle.series
                )
                assert all(series.calibration is None for series in bundle.series)
            manifest = json.loads(
                (recorded.session_path / "manifest.json").read_text(encoding="utf-8")
            )
            assert manifest["diagnostic_authorization"] is None
            provenance = manifest["open_acquisition"]
            assert provenance["selected_channel_id"] == selected
            assert tuple(provenance["recorded_channel_ids"]) == channel_ids
            assert tuple(provenance["recorded_channel_units"]) == channel_units
            assert provenance["local_sample_index_origin"] == 0
            assert provenance["local_to_source_index_mapping"] == {
                "local_origin": 0,
                "source_origin": provenance["source_start_sample_index"],
                "scale": 1,
            }
            assert provenance["stop_requested_at"] is not None
            assert provenance["source_stop_sample_index"] > provenance["source_start_sample_index"]
            expected = generate_samples(
                scenario,
                channel_ids,
                start_sample=provenance["source_start_sample_index"],
                sample_count=values.shape[1],
                sample_rate_hz=experiment.acquisition.sample_rate_hz,
            )
            np.testing.assert_array_equal(values, expected)
            assert (
                provenance["source_stop_sample_index"] - provenance["source_start_sample_index"]
                == values.shape[1]
            )

        frame = coordinator.build_plot_frame()
        assert frame is not None
        assert tuple(series.channel_id for series in frame.series) == channel_ids
        assert frame.signal_stage is SignalStage.RAW
        for series in frame.series:
            assert series.unit == "V"
            np.testing.assert_array_equal(
                series.relative_time_seconds,
                series.source_sample_indices / frame.sample_rate_hz,
            )
        assert frame.source_sample_count <= 1000
        bounded = coordinator.status()
        assert bounded.retained_start_sample_index is not None
        assert bounded.retained_stop_sample_index is not None
        assert bounded.retained_start_sample_index > 0
        assert bounded.retained_stop_sample_index - bounded.retained_start_sample_index <= 1000

        coordinator.start_recording()
        _wait_until(lambda: coordinator.status().recording_persisted_samples >= 250)
        coordinator.end_monitoring()
        terminal = coordinator.wait_for_terminal(10.0)
        assert terminal.monitor_state is OpenMonitorState.COMPLETED
        assert terminal.last_recording is not None
        assert terminal.last_recording.session_id not in {first.session_id, second.session_id}
        assert coordinator.end_monitoring().monitor_state is OpenMonitorState.COMPLETED

        restarted = coordinator.start_monitoring(intent)
        assert restarted.monitor_state is OpenMonitorState.MONITORING
        _wait_until(lambda: coordinator.status().acquired_samples_per_channel >= 250)
        coordinator.end_monitoring()
        assert coordinator.wait_for_terminal(10.0).monitor_state is OpenMonitorState.COMPLETED
    finally:
        coordinator.close()


def test_disconnect_faults_active_segment_and_releases_task_once(tmp_path: Path) -> None:
    documents = JsonConfigurationAdapter()
    experiment = documents.load_experiment_config(FIXTURE)
    experiment = replace(
        experiment,
        session=replace(experiment.session, parent_output_directory=tmp_path),
        acquisition=replace(
            experiment.acquisition,
            chunk_config=replace(
                experiment.acquisition.chunk_config,
                samples_per_channel=250,
            ),
        ),
    )
    profile = documents.load_bundled_hardware_profile(
        experiment.hardware_profile_id,
        experiment.compatible_profile_revision,
    )
    selected = experiment.acquisition.channel_ids[2]
    confirmation = RawDiagnosticConfirmation(
        operator="integration-operator",
        confirmed_at=datetime.now(UTC),
        measured_supply_voltage_v=3.3,
        polarity_confirmed=True,
        ai3_connection="disconnected",
    )
    intent = OpenAcquisitionIntent(
        profile=profile,
        experiment=experiment,
        selected_channel_id=selected,
        authorization=RawDiagnosticAuthorization(
            confirmation=confirmation,
            hardware_validation_sha256="c" * 64,
            selected_device_id="sim-cdaq-9171",
            selected_module_id="sim-ni-9239",
            ordered_physical_channels=tuple(
                f"cDAQ1Mod1/ai{index}" for index in range(len(experiment.acquisition.channel_ids))
            ),
            workflow="open-acquisition",
            selected_channel_id=selected,
        ),
        eligibility_evaluated_at=datetime.now(UTC),
    )
    scenario = replace(
        JsonSimulatorScenarioCodec().load_bundled("disconnect"),
        pacing_mode="real_time",
    )

    class CountingSimulator(SimulatedDaqAdapter):
        def __init__(self) -> None:
            super().__init__(scenario)
            self.stop_calls = 0

        def stop(self) -> DaqStopResult:
            self.stop_calls += 1
            return super().stop()

    adapter = CountingSimulator()
    store = TdmsSessionStore()
    coordinator = OpenAcquisitionCoordinator(
        adapter,
        store,
        store,
        documents.load_bundled_processing_recipe("baseline-v1"),
    )
    try:
        coordinator.start_monitoring(intent)
        _wait_until(lambda: coordinator.status().acquired_samples_per_channel >= 500)
        coordinator.start_recording()

        terminal = coordinator.wait_for_terminal(10.0)
        recorded = coordinator.wait_for_recording(10.0)

        assert terminal.monitor_state is OpenMonitorState.FAULTED
        assert terminal.monitor_fault is not None
        assert recorded is not None
        assert recorded.fault is not None
        assert recorded.recording_segment_completion is not None
        assert recorded.recording_segment_completion.fault is not None
        assert adapter.stop_calls == 1
    finally:
        coordinator.close()


def test_immediate_stop_preserves_auditable_zero_sample_session(tmp_path: Path) -> None:
    documents = JsonConfigurationAdapter()
    experiment = documents.load_experiment_config(FIXTURE)
    experiment = replace(
        experiment,
        session=replace(experiment.session, parent_output_directory=tmp_path),
        acquisition=replace(
            experiment.acquisition,
            chunk_config=replace(
                experiment.acquisition.chunk_config,
                samples_per_channel=250,
            ),
        ),
    )
    profile = documents.load_bundled_hardware_profile(
        experiment.hardware_profile_id,
        experiment.compatible_profile_revision,
    )
    selected = experiment.acquisition.channel_ids[0]
    confirmation = RawDiagnosticConfirmation(
        operator="integration-operator",
        confirmed_at=datetime.now(UTC),
        measured_supply_voltage_v=3.3,
        polarity_confirmed=True,
        ai3_connection="disconnected",
    )
    intent = OpenAcquisitionIntent(
        profile=profile,
        experiment=experiment,
        selected_channel_id=selected,
        authorization=RawDiagnosticAuthorization(
            confirmation=confirmation,
            hardware_validation_sha256="d" * 64,
            selected_device_id="sim-cdaq-9171",
            selected_module_id="sim-ni-9239",
            ordered_physical_channels=tuple(
                f"cDAQ1Mod1/ai{index}" for index in range(len(experiment.acquisition.channel_ids))
            ),
            workflow="open-acquisition",
            selected_channel_id=selected,
        ),
        eligibility_evaluated_at=datetime.now(UTC),
    )
    scenario = JsonSimulatorScenarioCodec().load_bundled("clean-four-channel")

    class GatedSimulator(SimulatedDaqAdapter):
        def __init__(self) -> None:
            super().__init__(scenario)
            self.read_gate = Event()
            self.read_gate.set()
            self.read_calls = 0

        def read(self, samples_per_channel: int, timeout_seconds: float) -> DaqReadResult:
            if self.read_calls:
                self.read_gate.wait(timeout_seconds)
            result = super().read(samples_per_channel, timeout_seconds)
            self.read_calls += 1
            if self.read_calls == 1:
                self.read_gate.clear()
            return result

        def cancel_pending_read(self) -> DaqCancellationResult:
            self.read_gate.set()
            return super().cancel_pending_read()

    adapter = GatedSimulator()
    store = TdmsSessionStore()
    coordinator = OpenAcquisitionCoordinator(
        adapter,
        store,
        store,
        documents.load_bundled_processing_recipe("baseline-v1"),
    )
    try:
        coordinator.start_monitoring(intent)
        _wait_until(lambda: coordinator.status().acquired_samples_per_channel == 250)

        coordinator.start_recording()
        coordinator.stop_recording()
        recorded = coordinator.wait_for_recording(10.0)

        assert recorded is not None
        assert recorded.persisted_samples_per_channel == 0
        assert recorded.recording_segment_completion is not None
        segment = recorded.recording_segment_completion
        assert segment.source_start_sample_index == segment.source_stop_sample_index
        manifest = json.loads((recorded.session_path / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["session"]["completion_state"] == "stopped"
        assert manifest["session"]["completion_reason"] == "manual_stop"
        assert manifest["counts"]["persisted_samples_per_channel"] == 0

        adapter.read_gate.set()
        _wait_until(lambda: coordinator.status().acquired_samples_per_channel > 250)
        coordinator.end_monitoring()
        assert coordinator.wait_for_terminal(10.0).monitor_state is OpenMonitorState.COMPLETED
    finally:
        coordinator.close()


def test_open_recording_storage_fault_does_not_stop_monitor(tmp_path: Path) -> None:
    documents = JsonConfigurationAdapter()
    experiment = documents.load_experiment_config(FIXTURE)
    experiment = replace(
        experiment,
        session=replace(experiment.session, parent_output_directory=tmp_path),
        acquisition=replace(
            experiment.acquisition,
            chunk_config=replace(
                experiment.acquisition.chunk_config,
                samples_per_channel=250,
                recording_queue_capacity_chunks=64,
            ),
        ),
    )
    profile = documents.load_bundled_hardware_profile(
        experiment.hardware_profile_id,
        experiment.compatible_profile_revision,
    )
    selected = experiment.acquisition.channel_ids[0]
    confirmation = RawDiagnosticConfirmation(
        operator="integration-operator",
        confirmed_at=datetime.now(UTC),
        measured_supply_voltage_v=3.3,
        polarity_confirmed=True,
        ai3_connection="disconnected",
    )
    intent = OpenAcquisitionIntent(
        profile=profile,
        experiment=experiment,
        selected_channel_id=selected,
        authorization=RawDiagnosticAuthorization(
            confirmation=confirmation,
            hardware_validation_sha256="b" * 64,
            selected_device_id="sim-cdaq-9171",
            selected_module_id="sim-ni-9239",
            ordered_physical_channels=tuple(
                f"cDAQ1Mod1/ai{index}" for index in range(len(experiment.acquisition.channel_ids))
            ),
            workflow="open-acquisition",
            selected_channel_id=selected,
        ),
        eligibility_evaluated_at=datetime.now(UTC),
    )
    injected = False
    fail_finalization = False

    def fail_first_write(phase: str) -> None:
        nonlocal fail_finalization, injected
        if phase == "before_data_write" and not injected:
            injected = True
            raise OSError("injected open-segment write failure")
        if phase == "during_finalization" and fail_finalization:
            fail_finalization = False
            raise OSError("injected open-segment finalization failure")

    store = TdmsSessionStore(hdf5_fault_injector=fail_first_write)

    class FailingStartEventWriter:
        def __init__(self, inner: SessionWriterPort) -> None:
            self.inner = inner

        def append_event(self, _event: AcquisitionEvent) -> None:
            raise SessionWriteError(
                "injected start-event failure",
                code="DAQ.STORAGE.OPEN_TEST_START_EVENT_FAILED",
            )

        def abort(self, fault: SessionFaultSummary) -> RecordedSessionCompletion:
            return self.inner.abort(fault)

        def close(self) -> None:
            self.inner.close()

    class FaultingSegmentWriter:
        def __init__(self, inner: SessionWriterPort, failure: str) -> None:
            self.inner = inner
            self.failure = failure

        def append_event(self, event: AcquisitionEvent) -> None:
            self.inner.append_event(event)

        def append_chunk(self, chunk: AcquisitionChunk) -> None:
            if self.failure == "disk-full":
                raise SessionWriteError(
                    "injected disk full",
                    code="DAQ.STORAGE.DISK_FULL",
                )
            self.inner.append_chunk(chunk)

        def checkpoint(self, status: RecordingSegmentStatus) -> None:
            if self.failure == "checkpoint":
                raise SessionWriteError(
                    "injected checkpoint failure",
                    code="DAQ.STORAGE.MANIFEST_UPDATE_FAILED",
                )
            self.inner.checkpoint(status)

        def finalize(self, finalization: SessionFinalization) -> RecordedSessionCompletion:
            return self.inner.finalize(finalization)

        def abort(self, fault: SessionFaultSummary) -> RecordedSessionCompletion:
            return self.inner.abort(fault)

        def close(self) -> None:
            self.inner.close()

    class DelayedFirstWriterFactory:
        def __init__(self) -> None:
            self.calls = 0

        def create_writer(
            self,
            initialization: SessionInitialization,
            policy: RecordingPolicy,
        ) -> SessionWriterPort:
            self.calls += 1
            if self.calls == 1:
                time.sleep(0.5)
            if self.calls == 3:
                raise SessionWriteError(
                    "injected writer initialization failure",
                    code="DAQ.STORAGE.OPEN_TEST_INITIALIZATION_FAILED",
                )
            if self.calls == 5:
                return FailingStartEventWriter(  # type: ignore[return-value]
                    store.create_writer(initialization, policy)
                )
            if self.calls == 6:
                return FaultingSegmentWriter(  # type: ignore[return-value]
                    store.create_writer(initialization, policy), "checkpoint"
                )
            if self.calls == 7:
                return FaultingSegmentWriter(  # type: ignore[return-value]
                    store.create_writer(initialization, policy), "disk-full"
                )
            return store.create_writer(initialization, policy)

    writer_factory = DelayedFirstWriterFactory()
    scenario = replace(
        JsonSimulatorScenarioCodec().load_bundled("clean-four-channel"),
        pacing_mode="real_time",
    )
    coordinator = OpenAcquisitionCoordinator(
        SimulatedDaqAdapter(scenario),
        store,
        writer_factory,
        documents.load_bundled_processing_recipe("baseline-v1"),
    )
    try:
        coordinator.start_monitoring(intent)
        _wait_until(lambda: coordinator.status().acquired_samples_per_channel >= 250)
        coordinator.start_recording()
        saturated = coordinator.wait_for_recording(10.0)
        assert saturated is not None
        assert saturated.fault is not None
        assert saturated.fault.code == "DAQ.STORAGE.OPEN_SEGMENT_QUEUE_SATURATED"
        count_after_saturation = coordinator.status().acquired_samples_per_channel
        _wait_until(
            lambda: coordinator.status().acquired_samples_per_channel > count_after_saturation
        )

        coordinator.start_recording()
        recorded = coordinator.wait_for_recording(10.0)
        assert recorded is not None
        assert recorded.fault is not None
        assert recorded.session_id != saturated.session_id
        status = coordinator.status()
        count_after_fault = status.acquired_samples_per_channel
        assert status.monitor_state is OpenMonitorState.MONITORING
        assert status.storage_fault_code is not None
        _wait_until(lambda: coordinator.status().acquired_samples_per_channel > count_after_fault)

        with pytest.raises(SessionWriteError):
            coordinator.start_recording()
        assert coordinator.status().monitor_state is OpenMonitorState.MONITORING
        assert (
            coordinator.status().storage_fault_code == "DAQ.STORAGE.OPEN_TEST_INITIALIZATION_FAILED"
        )

        fail_finalization = True
        coordinator.start_recording()
        _wait_until(lambda: coordinator.status().recording_persisted_samples >= 250)
        coordinator.stop_recording()
        finalization_fault = coordinator.wait_for_recording(10.0)
        assert finalization_fault is not None
        assert finalization_fault.fault is not None
        assert coordinator.status().monitor_state is OpenMonitorState.MONITORING

        with pytest.raises(SessionWriteError):
            coordinator.start_recording()
        start_event_fault = coordinator.wait_for_recording(0.0)
        assert start_event_fault is not None
        assert start_event_fault.fault is not None
        assert start_event_fault.fault.code == "DAQ.STORAGE.OPEN_TEST_START_EVENT_FAILED"
        assert coordinator.status().monitor_state is OpenMonitorState.MONITORING

        coordinator.start_recording()
        checkpoint_fault = coordinator.wait_for_recording(10.0)
        assert checkpoint_fault is not None
        assert checkpoint_fault.fault is not None
        assert checkpoint_fault.fault.code == "DAQ.STORAGE.MANIFEST_UPDATE_FAILED"
        assert coordinator.status().monitor_state is OpenMonitorState.MONITORING

        coordinator.start_recording()
        disk_full = coordinator.wait_for_recording(10.0)
        assert disk_full is not None
        assert disk_full.fault is not None
        assert disk_full.fault.code == "DAQ.STORAGE.DISK_FULL"
        assert coordinator.status().monitor_state is OpenMonitorState.MONITORING
        coordinator.end_monitoring()
        assert coordinator.wait_for_terminal(10.0).monitor_state is OpenMonitorState.COMPLETED
    finally:
        coordinator.close()


def _wait_until(predicate: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition was not reached before timeout")
