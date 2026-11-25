from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import h5py
import numpy as np
import pytest

from labdaq.adapters.configuration_json import JsonConfigurationAdapter
from labdaq.adapters.simulated.adapter import SimulatedDaqAdapter
from labdaq.adapters.simulated.generators import generate_samples
from labdaq.adapters.simulated.scenario import JsonSimulatorScenarioCodec, SimulatorScenario
from labdaq.adapters.tdms import session as tdms_session_module
from labdaq.adapters.tdms.session import TdmsSessionStore, TdmsSessionWriter
from labdaq.application.processing_service import (
    CalibrationResolutionService,
    RecordedSessionProcessingService,
)
from labdaq.application.session_service import RecordingSessionService
from labdaq.cli.main import EXIT_PROCESSING_FAILURE, EXIT_SESSION_FAILURE, EXIT_SUCCESS, main
from labdaq.domain.enums import CalibrationSourceKind, ProcessingDisposition, ProcessingMode
from labdaq.errors import (
    ApplicationStateError,
    ErrorSeverity,
    ExportError,
    InvalidSessionParentError,
    ProcessingError,
    SessionIntegrityError,
    SessionNotReplayableError,
    SessionOwnedError,
    SessionPathCollisionError,
    SessionSchemaError,
    SessionWriteError,
)
from labdaq.ports.acquisition import (
    AcquisitionChunk,
    AcquisitionCompletion,
    AcquisitionEvent,
    AcquisitionState,
    CleanupSummary,
    CompletionReason,
)
from labdaq.ports.session import (
    IntegrityStatus,
    RecordingPolicy,
    Recoverability,
    ReplayState,
    SessionCompletionState,
    SessionFaultSummary,
    SessionFinalization,
    SessionInitialization,
    VerificationLevel,
)
from labdaq.processing import BaselineScientificProcessing

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path("tests/fixtures/configuration/valid-four-channel-synthetic-v1.json")


def _policy(**changes: object) -> RecordingPolicy:
    return replace(
        RecordingPolicy(
            metadata_margin_bytes=1024,
            metadata_reserve_bytes=128,
            warning_free_bytes=2048,
            hard_stop_free_bytes=1024,
            capacity_check_interval_seconds=0.001,
            checkpoint_interval_seconds=0.001,
        ),
        **changes,
    )


def _configuration(tmp_path: Path, *, duration: float = 0.02, chunk_size: int = 333):
    documents = JsonConfigurationAdapter()
    experiment = documents.load_experiment_config(FIXTURE)
    experiment = replace(
        experiment,
        session=replace(experiment.session, parent_output_directory=tmp_path),
        acquisition=replace(
            experiment.acquisition,
            requested_duration_seconds=duration,
            chunk_config=replace(
                experiment.acquisition.chunk_config,
                samples_per_channel=chunk_size,
            ),
        ),
    )
    profile = documents.load_bundled_hardware_profile(
        experiment.hardware_profile_id,
        experiment.compatible_profile_revision,
    )
    recipe = documents.load_bundled_processing_recipe("baseline")
    return documents, profile, experiment, recipe


@dataclass
class DirectSession:
    store: TdmsSessionStore
    writer: TdmsSessionWriter
    adapter: SimulatedDaqAdapter
    scenario: SimulatorScenario
    initialization: SessionInitialization

    def close(self) -> None:
        self.writer.close()
        self.adapter.close()


def _direct_session(
    parent: Path,
    *,
    store: TdmsSessionStore | None = None,
    policy: RecordingPolicy | None = None,
    suffix: str = "",
    raw_without_sensors: bool = False,
) -> DirectSession:
    _, profile, experiment, recipe = _configuration(parent)
    if raw_without_sensors:
        experiment = replace(
            experiment,
            channels=tuple(
                replace(
                    channel,
                    sensor_id=None,
                    calibration_id=None,
                    conditioning_chain_id=None,
                    processing_disposition=ProcessingDisposition.RAW_ONLY_DIAGNOSTIC,
                )
                for channel in experiment.channels
            ),
            sensors=(),
            calibrations=(),
            conditioning_chains=(),
        )
    scenario = JsonSimulatorScenarioCodec().load_bundled("clean-four-channel")
    adapter = SimulatedDaqAdapter(scenario)
    discovery = adapter.discover()
    configuration = adapter.configure(experiment)
    actual_store = store or TdmsSessionStore()
    actual_policy = policy or _policy()
    plan = actual_store.plan_session(
        experiment.session,
        session_id=str(uuid4()),
        created_at=datetime.now(UTC),
        policy=actual_policy,
    )
    if suffix:
        plan = replace(
            plan,
            directory_name=plan.directory_name + suffix,
            session_path=plan.session_path.with_name(plan.directory_name + suffix),
        )
    initialization = SessionInitialization(
        plan,
        profile,
        experiment,
        recipe,
        discovery,
        configuration,
        "test-version",
    )
    writer = actual_store.create_writer(initialization, actual_policy)
    return DirectSession(actual_store, writer, adapter, scenario, initialization)


def _chunk(session: DirectSession, *, start: int = 0, count: int = 10, sequence: int = 0):
    configuration = session.initialization.daq_configuration
    samples = generate_samples(
        session.scenario,
        configuration.ordered_logical_channels,
        start_sample=start,
        sample_count=count,
        sample_rate_hz=configuration.effective_sample_rate_hz,
    )
    samples.flags.writeable = False
    return AcquisitionChunk(
        session.initialization.plan.session_id,
        sequence,
        start,
        count,
        configuration.effective_sample_rate_hz,
        configuration.ordered_logical_channels,
        tuple("V" for _ in configuration.ordered_logical_channels),
        samples,
        float(sequence + 1),
    )


def _completion(session: DirectSession, count: int, *, reason=CompletionReason.DURATION_REACHED):
    state = AcquisitionState.COMPLETED
    return AcquisitionCompletion(
        state,
        reason,
        session.initialization.plan.session_id,
        datetime.now(UTC),
        datetime.now(UTC),
        count,
        count,
        0 if count else None,
        1 if count else 0,
        0,
        (),
        None,
        CleanupSummary(True, True, False, True),
    )


def _event(session: DirectSession, *, sequence: int = 0) -> AcquisitionEvent:
    return AcquisitionEvent(
        sequence,
        "DAQ.ACQUISITION.PROGRESS",
        ErrorSeverity.INFO,
        session.initialization.plan.session_id,
        AcquisitionState.RUNNING,
        datetime.now(UTC),
        float(sequence),
        "progress",
        {"count": sequence},
        sequence,
    )


def test_raw_session_without_sensor_metadata_round_trips_and_exports(tmp_path: Path) -> None:
    from nptdms import TdmsFile

    from labdaq.adapters.export import (
        CsvArtifactWriter,
        FileSystemArtifactStore,
        JsonArtifactWriter,
        MatplotlibStaticRenderer,
    )
    from labdaq.adapters.tdms.export import TdmsArtifactWriter
    from labdaq.application.export_service import ExportCoordinator, ExportService
    from labdaq.application.plotting_service import PlotDataService
    from labdaq.domain.plotting import (
        ChannelSelection,
        ExportFormat,
        ExportJobState,
        ExportProduct,
        ExportRequest,
        PlotRepresentation,
        PlotRequest,
        PlotViewport,
        resolve_sample_range,
    )

    session = _direct_session(tmp_path, raw_without_sensors=True)
    try:
        samples = _chunk(session, count=10)
        session.writer.append_chunk(samples)
        session.writer.finalize(SessionFinalization(_completion(session, 10)))
        path = session.initialization.plan.session_path
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        assert all(channel["sensor"] is None for channel in manifest["channels"])
        metadata = session.store.processing_metadata(path)
        assert all(
            channel.sensor_id is None and channel.sensor_model is None
            for channel in metadata.channels
        )
        assert (
            session.store.inspect_session(path, level=VerificationLevel.FULL).overall
            is IntegrityStatus.PASS
        )
        replay = list(session.store.open_replay(path, chunk_size=128))
        np.testing.assert_array_equal(replay[0].samples, samples.samples)
        plotter = PlotDataService(
            session.store, JsonConfigurationAdapter(), BaselineScientificProcessing()
        )
        selection = ChannelSelection(metadata.ordered_channel_ids)
        source_range = resolve_sample_range(
            sample_rate_hz=metadata.sample_rate_hz,
            readable_sample_count=10,
            start_index=0,
            stop_index=10,
        )
        plot = plotter.build_plot_data(
            PlotRequest(
                path,
                PlotRepresentation.RAW_TIME,
                selection,
                source_range,
                PlotViewport(100),
            )
        )
        assert plot.series[0].sensor_model is None
        assert plot.series[0].warning == "RAW VOLTAGE / SENSOR UNKNOWN"
        coordinator = ExportCoordinator(
            ExportService(
                plotter,
                FileSystemArtifactStore(),
                CsvArtifactWriter(),
                JsonArtifactWriter(),
                MatplotlibStaticRenderer(),
            )
        )
        result = coordinator.run_export_job(
            (
                ExportRequest(
                    path,
                    ExportProduct.RAW_TIME_DATA,
                    ExportFormat.CSV,
                    selection,
                    source_range,
                    PlotRepresentation.RAW_TIME,
                    base_name="raw-voltage",
                ),
            )
        )
        assert result.final_state is ExportJobState.COMPLETED
        sidecar = json.loads(
            (path / "exports" / result.artifacts[0].sidecar_relative_path).read_text(
                encoding="utf-8"
            )
        )
        assert sidecar["channels"][0]["sensor_id"] is None
        assert sidecar["channels"][0]["sensor_model"] is None
        destination = tmp_path / "raw-export.tdms"
        TdmsArtifactWriter().write(
            destination,
            chunks=replay,
            metadata=metadata,
            summary=session.store.summarize_session(path),
            cancelled=lambda: False,
            progressed=lambda _: None,
        )
        with TdmsFile.open(destination) as tdms:
            assert "sensor_id" not in tdms["raw"][metadata.ordered_channel_ids[0]].properties
            np.testing.assert_array_equal(
                tdms["raw"][metadata.ordered_channel_ids[0]][:], samples.samples[0]
            )
    finally:
        session.close()


def test_simulator_record_verify_replay_and_cli_round_trip(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, profile, experiment, recipe = _configuration(tmp_path)
    experiment = replace(
        experiment,
        sensors=(replace(experiment.sensors[0], model="830M1-0200"), *experiment.sensors[1:]),
        calibrations=(
            replace(
                experiment.calibrations[0],
                source_kind=CalibrationSourceKind.MANUFACTURER_NOMINAL,
                source_reference="TE 830M1-0200 nominal test policy",
            ),
            *experiment.calibrations[1:],
        ),
    )
    scenario = JsonSimulatorScenarioCodec().load_bundled("clean-four-channel")
    adapter = SimulatedDaqAdapter(scenario)
    store = TdmsSessionStore()
    service = RecordingSessionService(adapter, store, store, store, policy=_policy())
    plan = service.preview(experiment.session)

    prepared = service.prepare(plan, profile, experiment, recipe)
    assert prepared.session_state is SessionCompletionState.RECORDING
    service.start()
    completion = service.wait_for_terminal(5.0)

    assert completion.state is SessionCompletionState.COMPLETED
    assert completion.persisted_samples_per_channel == 1000
    assert completion.integrity.overall is IntegrityStatus.PASS
    assert sorted(path.name for path in plan.session_path.iterdir()) == [
        "events.jsonl",
        "exports",
        "manifest.json",
        "plots",
        "processed",
        "processing-recipe.json",
        "raw.h5",
    ]
    replay = list(store.open_replay(plan.session_path, chunk_size=128))
    reconstructed = np.concatenate([chunk.samples for chunk in replay], axis=1)
    expected = generate_samples(
        scenario,
        experiment.acquisition.channel_ids,
        start_sample=0,
        sample_count=1000,
        sample_rate_hz=50_000.0,
    )
    assert np.array_equal(reconstructed, expected)
    assert [chunk.sample_count for chunk in replay][-1] == 104
    assert all(not chunk.samples.flags.writeable for chunk in replay)

    manifest = json.loads((plan.session_path / "manifest.json").read_text(encoding="utf-8"))
    recipe_payload = json.loads(
        (plan.session_path / "processing-recipe.json").read_text(encoding="utf-8")
    )
    assert manifest["counts"]["persisted_samples_per_channel"] == 1000
    assert manifest["schema_version"] == 2
    assert manifest["raw_container"] == {
        "axis_order": ["channel", "sample_index"],
        "chunk_checksum": "fletcher32",
        "chunk_shape": [4, 333],
        "compression": "none",
        "dataset_path": "/raw/samples",
        "dtype": "float64-le",
        "filename": "raw.h5",
        "format": "hdf5",
        "layout_version": 1,
        "swmr": True,
    }
    assert manifest["hardware"]["profile"]["hardware_profile_id"] == profile.hardware_profile_id
    assert manifest["processing_recipe"]["applies_calibration"] is False
    assert manifest["processing_recipe"]["source_units"] == "V"
    assert manifest["processing_recipe"]["output_units"] == "V"
    assert manifest["integrity"]["verification_status"] == "pass"
    assert manifest["integrity"]["verification_timestamp"] is not None
    assert recipe_payload == JsonConfigurationAdapter().encode(recipe)
    assert not (plan.session_path / ".recording.lock").exists()
    assert not (plan.session_path / ".metadata-reserve").exists()

    with h5py.File(plan.session_path / "raw.h5", "r", swmr=True) as hdf5:
        assert hdf5.attrs["session_id"] == plan.session_id
        assert hdf5.attrs["actual_sample_rate_hz"] == 50_000.0
        assert hdf5["/raw/samples"].shape == (4, 1000)
        assert hdf5["/raw/samples"].dtype == np.dtype("<f8")
        assert hdf5["/raw/samples"].fletcher32 is True
        assert len(hdf5["/journal/commits"]) == 4
        assert int(hdf5["/journal/commits"][-1]["stop_sample_index"]) == 1000

    assert main(["session", "summary", str(plan.session_path), "--json"]) == EXIT_SUCCESS
    machine = json.loads(capsys.readouterr().out)
    assert machine["integrity"]["verification_level"] == "fast"
    assert main(["session", "summary", str(plan.session_path), "--verify"]) == EXIT_SUCCESS
    human = capsys.readouterr().out
    assert "Integrity: pass (full)" in human
    assert "accel-x: 1000 samples [V]" in human

    source_before = {
        name: (plan.session_path / name).read_bytes()
        for name in ("raw.h5", "manifest.json", "events.jsonl", "processing-recipe.json")
    }
    assert (
        main(["export", "tdms", str(plan.session_path), "--chunk-size", "127", "--json"])
        == EXIT_SUCCESS
    )
    tdms_export = json.loads(capsys.readouterr().out)
    from nptdms import TdmsFile

    with TdmsFile.open(tdms_export["output_path"]) as tdms:
        exported = np.vstack(
            [tdms["raw"][channel_id][:] for channel_id in experiment.acquisition.channel_ids]
        )
    assert np.array_equal(exported, expected)
    sidecar = json.loads(Path(tdms_export["sidecar_path"]).read_text(encoding="utf-8"))
    assert sidecar["source_format"] == "hdf5"
    assert sidecar["exported_sample_count"] == 1000
    assert [channel["channel_id"] for channel in sidecar["channels"]] == list(
        experiment.acquisition.channel_ids
    )
    assert sidecar["output_sha256"] == tdms_export["sha256"]
    assert {
        name: (plan.session_path / name).read_bytes() for name in source_before
    } == source_before
    processor = RecordedSessionProcessingService(
        store,
        JsonConfigurationAdapter(),
        BaselineScientificProcessing(),
    )
    first = processor.process(plan.session_path, chunk_size=128)
    second = processor.process(plan.session_path, chunk_size=37)
    assert first.provenance.provenance_hash == second.provenance.provenance_hash
    assert first.provenance.source_raw_digest == manifest["integrity"]["logical_data_sha256"]
    assert first.provenance.source_completion_state == "completed"
    assert first.provenance.calibration_qualities[0].value == "nominal"
    assert "DAQ.PROCESSING.NOMINAL_CALIBRATION_FALLBACK" in {
        notice.code for notice in first.provenance.notices
    }
    assert [metric.channel_id for metric in first.metrics] == list(
        experiment.acquisition.channel_ids
    )
    assert [result.input_unit.value for result in first.psd] == ["g", "g", "g", "pC"]
    for first_fft, second_fft in zip(first.fft, second.fft, strict=True):
        np.testing.assert_allclose(first_fft.amplitudes, second_fft.amplitudes, atol=2e-12)
    for first_psd, second_psd in zip(first.psd, second.psd, strict=True):
        np.testing.assert_array_equal(first_psd.density, second_psd.density)

    calibrations, _ = CalibrationResolutionService().resolve_experiment(
        profile,
        experiment,
        experiment.acquisition.channel_ids,
    )
    numerical = BaselineScientificProcessing()
    live = numerical.create_stream(
        recipe,
        calibrations,
        sample_rate_hz=50000.0,
        mode=ProcessingMode.LIVE,
    )
    replay_processor = numerical.create_stream(
        recipe,
        calibrations,
        sample_rate_hz=50000.0,
        mode=ProcessingMode.REPLAY,
    )
    live_values = np.concatenate(
        [
            live.process(chunk).conditioned_samples.copy()
            for chunk in store.open_replay(plan.session_path, chunk_size=128)
        ],
        axis=1,
    )
    replay_values = np.concatenate(
        [
            replay_processor.process(chunk).conditioned_samples.copy()
            for chunk in store.open_replay(plan.session_path, chunk_size=37)
        ],
        axis=1,
    )
    np.testing.assert_allclose(live_values, replay_values, rtol=0.0, atol=2e-12)

    changed_recipe = replace(
        recipe,
        stages=(recipe.stages[0], replace(recipe.stages[1], cutoff_hz=2.0), recipe.stages[2]),
    )

    class ChangedRecipeRepository(JsonConfigurationAdapter):
        def load_bundled_processing_recipe(self, recipe_id: str):
            del recipe_id
            return changed_recipe

    changed = RecordedSessionProcessingService(
        store,
        ChangedRecipeRepository(),
        BaselineScientificProcessing(),
    ).process(plan.session_path, chunk_size=128)
    assert changed.provenance.recipe_hash != first.provenance.recipe_hash
    assert changed.provenance.provenance_hash != first.provenance.provenance_hash
    assert "sos" in str(first.provenance.algorithm_details)

    assert {
        name: (plan.session_path / name).read_bytes() for name in source_before
    } == source_before

    assert (
        main(
            [
                "process-session",
                str(plan.session_path),
                "--recipe",
                "baseline-v1",
                "--summary",
                "--chunk-size",
                "257",
                "--json",
            ]
        )
        == EXIT_SUCCESS
    )
    processed_cli = json.loads(capsys.readouterr().out)
    assert processed_cli["provenance"]["provenance_hash"] == first.provenance.provenance_hash
    assert len(processed_cli["metrics"]) == 4
    assert not any((plan.session_path / "processed").iterdir())
    assert (
        main(
            [
                "process-session",
                str(plan.session_path),
                "--summary",
                "--start-sample",
                "1000",
                "--json",
            ]
        )
        == EXIT_PROCESSING_FAILURE
    )
    processing_error = json.loads(capsys.readouterr().out)
    assert processing_error["error"]["code"] == "DAQ.PROCESSING.PROCESSING_RANGE_INVALID"
    service.close()


def test_schema_v1_tdms_sessions_remain_readable_processable_and_exportable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    session = _direct_session(tmp_path)
    expected = _chunk(session, count=10).samples.copy()
    session.writer.append_chunk(_chunk(session, count=10))
    session.writer.finalize(SessionFinalization(_completion(session, 10)))
    session.adapter.close()
    path = session.initialization.plan.session_path
    _convert_to_schema_v1_tdms(path, expected)

    report = session.store.inspect_session(path, level=VerificationLevel.FULL)
    assert report.overall is IntegrityStatus.PASS
    summary = session.store.summarize_session(path, level=VerificationLevel.FULL)
    assert summary.raw_container.format == "tdms"
    assert summary.raw_container.filename == "raw.tdms"
    replayed = np.concatenate(
        [chunk.samples for chunk in session.store.open_replay(path, chunk_size=3)], axis=1
    )
    assert np.array_equal(replayed, expected)

    source_before = {
        name: (path / name).read_bytes()
        for name in ("manifest.json", "raw.tdms", "events.jsonl", "processing-recipe.json")
    }
    commands = (
        ["session", "summary", str(path), "--verify", "--json"],
        ["process-session", str(path), "--summary", "--json"],
        ["plot-data", str(path), "--view", "raw", "--json"],
        ["export", "csv", str(path), "--view", "raw", "--name", "legacy-raw", "--json"],
        [
            "export",
            "figure",
            str(path),
            "--view",
            "raw",
            "--kind",
            "overlay",
            "--format",
            "svg",
            "--name",
            "legacy-figure",
            "--json",
        ],
        ["export", "tdms", str(path), "--name", "legacy-normalized", "--json"],
    )
    for command in commands:
        assert main(command) == EXIT_SUCCESS
        capsys.readouterr()
    for name, content in source_before.items():
        assert (path / name).read_bytes() == content

    from nptdms import TdmsFile

    normalized = TdmsFile.read(path / "exports" / "legacy-normalized.tdms")
    normalized_samples = np.vstack(
        [normalized["raw"][channel_id][:] for channel_id in summary.ordered_channel_ids]
    )
    assert np.array_equal(normalized_samples, expected)


def _convert_to_schema_v1_tdms(path: Path, samples: np.ndarray) -> None:
    from nptdms import ChannelObject, GroupObject, RootObject, TdmsWriter

    from labdaq.storage.integrity import file_sha256

    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    session = manifest["session"]
    software = manifest["software"]
    acquisition = manifest["acquisition"]
    ordered_ids = acquisition["ordered_channel_ids"]
    root_properties = {
        "session_id": session["session_id"],
        "session_schema_version": 1,
        "tdms_layout_version": 1,
        "application_version": software["application_version"],
        "requested_sample_rate_hz": acquisition["requested_sample_rate_hz"],
        "actual_sample_rate_hz": acquisition["actual_sample_rate_hz"],
        "channel_count": len(ordered_ids),
        "ordered_channel_ids_json": json.dumps(ordered_ids),
        "session_created_at_utc": session["created_at"],
        "timezone": session["timezone"],
        "canonical_sample_dtype": acquisition["sample_dtype"],
        "source_classification": acquisition["source_classification"],
    }
    raw_path = path / "raw.tdms"
    with TdmsWriter(raw_path, index_file=False) as writer:
        writer.write_segment(
            [
                RootObject(root_properties),
                GroupObject("raw", {"channel_order_is_authoritative": True}),
                *[
                    ChannelObject(
                        "raw",
                        channel_id,
                        samples[index],
                        properties=tdms_session_module._tdms_channel_properties(
                            manifest["channels"][index]
                        ),
                    )
                    for index, channel_id in enumerate(ordered_ids)
                ],
            ]
        )
    manifest["schema_version"] = 1
    manifest["tdms_layout_version"] = 1
    del manifest["raw_container"]
    manifest["integrity"]["file_checksums"].pop("raw.h5", None)
    manifest["integrity"]["file_checksums"]["raw.tdms"] = file_sha256(raw_path)
    (path / "raw.h5").unlink()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def test_tdms_export_rejects_collisions_and_cleans_cancelled_temporaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from labdaq.bootstrap import create_raw_container_export_service

    session = _direct_session(tmp_path)
    session.writer.append_chunk(_chunk(session, count=10))
    session.writer.finalize(SessionFinalization(_completion(session, 10)))
    session.adapter.close()
    path = session.initialization.plan.session_path
    service = create_raw_container_export_service()

    with pytest.raises(ValueError, match="chunk_size"):
        service.export_tdms(path, base_name="invalid", chunk_size=0)
    progress: list[tuple[int, int]] = []
    first = service.export_tdms(
        path,
        base_name="verified-raw",
        chunk_size=4,
        progress=lambda processed, total: progress.append((processed, total)),
    )
    assert first.output_path.is_file()
    assert first.sidecar_path.is_file()
    assert progress[-1] == (10, 10)
    with pytest.raises(ExportError) as collision:
        service.export_tdms(path, base_name="verified-raw", chunk_size=4)
    assert collision.value.code == "DAQ.EXPORT.PATH_COLLISION"

    calls = 0

    def cancelled() -> bool:
        nonlocal calls
        calls += 1
        return calls > 1

    with pytest.raises(ExportError) as cancellation:
        service.export_tdms(
            path,
            base_name="cancelled-raw",
            chunk_size=4,
            cancelled=cancelled,
        )
    assert cancellation.value.code == "DAQ.EXPORT.CANCELLED"
    assert not (path / "exports" / "cancelled-raw.tdms").exists()
    assert not (path / "exports" / "cancelled-raw.tdms.metadata.json").exists()
    assert not list((path / "exports").glob(".*cancelled-raw*.tmp"))

    def fail_sidecar(destination: Path, payload: object) -> None:
        raise ValueError("injected sidecar failure")

    monkeypatch.setattr(service._json, "write", fail_sidecar)
    with pytest.raises(ExportError) as sidecar_failure:
        service.export_tdms(path, base_name="sidecar-failure", chunk_size=4)
    assert sidecar_failure.value.code == "DAQ.EXPORT.TDMS_WRITE_FAILED"
    assert not (path / "exports" / "sidecar-failure.tdms").exists()
    assert not (path / "exports" / "sidecar-failure.tdms.metadata.json").exists()


def test_recorded_processing_reports_missing_charge_calibration(tmp_path: Path) -> None:
    _, profile, experiment, recipe = _configuration(tmp_path, duration=0.002, chunk_size=50)
    experiment = replace(
        experiment,
        calibrations=(
            *experiment.calibrations[:3],
            replace(experiment.calibrations[3], input_offset=None),
        ),
    )
    scenario = JsonSimulatorScenarioCodec().load_bundled("clean-four-channel")
    adapter = SimulatedDaqAdapter(scenario)
    store = TdmsSessionStore()
    recording = RecordingSessionService(adapter, store, store, store, policy=_policy())
    plan = recording.preview(experiment.session)
    recording.prepare(plan, profile, experiment, recipe)
    recording.start()
    assert recording.wait_for_terminal(5.0).state is SessionCompletionState.COMPLETED

    with pytest.raises(ProcessingError) as captured:
        RecordedSessionProcessingService(
            store,
            JsonConfigurationAdapter(),
            BaselineScientificProcessing(),
        ).process(plan.session_path)
    assert captured.value.code == "DAQ.PROCESSING.CALIBRATION_INVALID"
    assert "charge-signal" in str(captured.value.context["channel_failures"])
    recording.close()


def test_manual_stop_maps_to_stopped_and_remains_replayable(tmp_path: Path) -> None:
    _, profile, experiment, recipe = _configuration(tmp_path, duration=0.5, chunk_size=500)
    scenario = replace(
        JsonSimulatorScenarioCodec().load_bundled("clean-four-channel"),
        pacing_mode="real_time",
    )
    adapter = SimulatedDaqAdapter(scenario)
    store = TdmsSessionStore()
    service = RecordingSessionService(adapter, store, store, store, policy=_policy())
    plan = service.preview(experiment.session)
    service.prepare(plan, profile, experiment, recipe)
    service.start()
    time.sleep(0.025)
    service.request_stop("test")
    completion = service.wait_for_terminal(5.0)

    assert completion.state is SessionCompletionState.STOPPED
    assert 0 <= completion.persisted_samples_per_channel < 25_000
    assert completion.integrity.recoverability is Recoverability.REPLAYABLE
    assert (
        sum(chunk.sample_count for chunk in store.open_replay(plan.session_path, chunk_size=37))
        == completion.persisted_samples_per_channel
    )
    service.close()


def test_acquisition_fault_finalizes_verified_faulted_prefix(tmp_path: Path) -> None:
    _, profile, experiment, recipe = _configuration(tmp_path, duration=0.3, chunk_size=1000)
    scenario = JsonSimulatorScenarioCodec().load_bundled("disconnect")
    adapter = SimulatedDaqAdapter(scenario)
    store = TdmsSessionStore()
    service = RecordingSessionService(adapter, store, store, store, policy=_policy())
    plan = service.preview(experiment.session)
    service.prepare(plan, profile, experiment, recipe)
    service.start()
    completion = service.wait_for_terminal(5.0)

    assert completion.state is SessionCompletionState.FAULTED
    assert completion.persisted_samples_per_channel > 0
    assert completion.integrity.overall is not IntegrityStatus.FAIL
    assert (
        sum(chunk.sample_count for chunk in store.open_replay(plan.session_path, chunk_size=777))
        == completion.persisted_samples_per_channel
    )
    service.close()


def test_recording_service_rejects_illegal_order_plan_and_recipe(tmp_path: Path) -> None:
    _, profile, experiment, recipe = _configuration(tmp_path)
    adapter = SimulatedDaqAdapter(JsonSimulatorScenarioCodec().load_bundled("clean-four-channel"))
    store = TdmsSessionStore()
    service = RecordingSessionService(adapter, store, store, store, policy=_policy())

    with pytest.raises(ApplicationStateError):
        service.start()
    plan = service.preview(experiment.session)
    with pytest.raises(SessionWriteError, match="preview"):
        service.prepare(replace(plan, experiment_name="changed"), profile, experiment, recipe)
    with pytest.raises(SessionWriteError, match="recipe"):
        service.prepare(plan, profile, experiment, replace(recipe, recipe_id="other"))
    service.close()


def test_closing_prepared_service_finalizes_faulted_empty_session(tmp_path: Path) -> None:
    _, profile, experiment, recipe = _configuration(tmp_path)
    adapter = SimulatedDaqAdapter(JsonSimulatorScenarioCodec().load_bundled("clean-four-channel"))
    store = TdmsSessionStore()
    service = RecordingSessionService(adapter, store, store, store, policy=_policy())
    plan = service.preview(experiment.session)
    service.prepare(plan, profile, experiment, recipe)
    service.close()
    service.close()

    summary = store.summarize_session(plan.session_path, level=VerificationLevel.FULL)
    assert summary.completion_state is SessionCompletionState.FAULTED
    assert summary.common_sample_count == 0


@pytest.mark.parametrize(
    "mutation",
    ["session", "sequence", "gap", "overlap", "channel_order", "sample_rate"],
)
def test_writer_rejects_session_and_continuity_violations(
    tmp_path: Path,
    mutation: str,
) -> None:
    session = _direct_session(tmp_path, suffix=f"-{mutation}")
    try:
        first = _chunk(session)
        if mutation == "overlap":
            session.writer.append_chunk(first)
            invalid = replace(first, sequence_number=1, start_sample_index=5)
        elif mutation == "session":
            invalid = replace(first, session_id=str(uuid4()))
        elif mutation == "sequence":
            invalid = replace(first, sequence_number=2)
        elif mutation == "gap":
            invalid = replace(first, start_sample_index=2)
        elif mutation == "channel_order":
            invalid = replace(
                first,
                ordered_channel_ids=tuple(reversed(first.ordered_channel_ids)),
            )
        else:
            invalid = replace(first, sample_rate_hz=40_000.0)
        with pytest.raises(SessionWriteError):
            session.writer.append_chunk(invalid)
    finally:
        session.close()


def test_exclusive_creation_invalid_parent_and_capacity_preflight(tmp_path: Path) -> None:
    session = _direct_session(tmp_path)
    try:
        with pytest.raises(SessionPathCollisionError):
            session.store.create_writer(session.initialization, _policy())
    finally:
        session.close()

    missing = tmp_path / "missing"
    _, profile, experiment, recipe = _configuration(missing)
    scenario = JsonSimulatorScenarioCodec().load_bundled("clean-four-channel")
    adapter = SimulatedDaqAdapter(scenario)
    store = TdmsSessionStore()
    plan = store.plan_session(
        experiment.session,
        session_id=str(uuid4()),
        created_at=datetime.now(UTC),
        policy=_policy(),
    )
    initialization = SessionInitialization(
        plan,
        profile,
        experiment,
        recipe,
        adapter.discover(),
        adapter.configure(experiment),
        "test",
    )
    with pytest.raises(InvalidSessionParentError, match="parent"):
        store.create_writer(initialization, _policy())
    assert not plan.session_path.exists()
    adapter.close()

    class NoCapacity:
        def free_bytes(self, path: Path) -> int:
            return 0

        def is_writable(self, path: Path) -> bool:
            return True

        def estimate_required_bytes(self, experiment: object, policy: object) -> int:
            return 1

    _, profile, experiment, recipe = _configuration(tmp_path)
    adapter = SimulatedDaqAdapter(scenario)
    store = TdmsSessionStore(capacity=NoCapacity())  # type: ignore[arg-type]
    plan = store.plan_session(
        experiment.session,
        session_id=str(uuid4()),
        created_at=datetime.now(UTC),
        policy=_policy(),
    )
    initialization = SessionInitialization(
        plan,
        profile,
        experiment,
        recipe,
        adapter.discover(),
        adapter.configure(experiment),
        "test",
    )
    with pytest.raises(SessionWriteError, match="insufficient"):
        store.create_writer(initialization, _policy())
    assert not plan.session_path.exists()
    adapter.close()


def test_initialization_failure_retains_fault_manifest_and_stale_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, profile, experiment, recipe = _configuration(tmp_path)
    scenario = JsonSimulatorScenarioCodec().load_bundled("clean-four-channel")
    adapter = SimulatedDaqAdapter(scenario)
    store = TdmsSessionStore()
    plan = store.plan_session(
        experiment.session,
        session_id=str(uuid4()),
        created_at=datetime.now(UTC),
        policy=_policy(),
    )
    initialization = SessionInitialization(
        plan,
        profile,
        experiment,
        recipe,
        adapter.discover(),
        adapter.configure(experiment),
        "test",
    )

    def fail_metadata(self: object, path: Path, initialization: SessionInitialization) -> None:
        raise OSError(5, "injected after manifest")

    monkeypatch.setattr(tdms_session_module._Hdf5RawWriter, "__init__", fail_metadata)
    with pytest.raises(SessionWriteError) as captured:
        store.create_writer(initialization, _policy())
    adapter.close()

    assert captured.value.code == "DAQ.STORAGE.SESSION_INITIALIZATION_FAILED"
    retained = json.loads((plan.session_path / "manifest.json").read_text(encoding="utf-8"))
    assert retained["session"]["completion_state"] == "faulted"
    assert retained["fault"]["code"] == "DAQ.STORAGE.SESSION_INITIALIZATION_FAILED"
    assert (plan.session_path / ".recording.lock").exists()


def test_receipts_and_repeated_create_finalize_reopen_cycles(tmp_path: Path) -> None:
    created: set[Path] = set()
    for cycle in range(3):
        session = _direct_session(tmp_path)
        receipt = session.writer.append_chunk(_chunk(session, count=cycle + 1))
        completion = session.writer.finalize(SessionFinalization(_completion(session, cycle + 1)))
        session.adapter.close()

        assert receipt.first_sample_index == 0
        assert receipt.last_sample_index == cycle
        assert receipt.accepted_sample_count == cycle + 1
        assert receipt.total_persisted_sample_count == cycle + 1
        assert receipt.checkpoint_sequence >= 1
        assert completion.integrity.overall is IntegrityStatus.PASS
        assert completion.session_path not in created
        created.add(completion.session_path)


def test_active_writer_then_explicit_recovery_preserves_raw_prefix(tmp_path: Path) -> None:
    session = _direct_session(tmp_path)
    chunk = _chunk(session, count=25)
    session.writer.append_chunk(chunk)
    active = session.store.inspect_session(session.initialization.plan.session_path)
    assert active.recoverability is Recoverability.ACTIVE_WRITER
    with pytest.raises(SessionOwnedError):
        session.store.recover_session(session.initialization.plan.session_path)
    session.writer.close()
    session.adapter.close()

    reopened = TdmsSessionStore(process_is_alive=lambda host, process_id: False)
    candidate = reopened.inspect_session(session.initialization.plan.session_path)
    assert candidate.recoverability is Recoverability.RECOVERY_CANDIDATE
    recovered = reopened.recover_session(session.initialization.plan.session_path)

    assert recovered.effective_state is SessionCompletionState.INTERRUPTED
    assert recovered.recoverability is Recoverability.REPLAYABLE_PREFIX
    replay = list(reopened.open_replay(session.initialization.plan.session_path, chunk_size=7))
    assert np.array_equal(np.concatenate([item.samples for item in replay], axis=1), chunk.samples)
    manifest = json.loads(
        (session.initialization.plan.session_path / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["recovery"]["raw_samples_modified"] is False
    processed = RecordedSessionProcessingService(
        reopened,
        JsonConfigurationAdapter(),
        BaselineScientificProcessing(),
    ).process(session.initialization.plan.session_path, chunk_size=7)
    assert processed.provenance.source_completion_state == "interrupted"
    assert processed.provenance.source_range.stop_sample_index == 25


def test_recovery_ignores_only_truncated_final_event_line(tmp_path: Path) -> None:
    session = _direct_session(tmp_path)
    session.writer.append_chunk(_chunk(session, count=8))
    session.writer.append_event(_event(session))
    session.writer.close()
    session.adapter.close()
    with (session.initialization.plan.session_path / "events.jsonl").open("ab") as events:
        events.write(b'{"truncated"')
    reopened = TdmsSessionStore(process_is_alive=lambda host, process_id: False)
    report = reopened.recover_session(session.initialization.plan.session_path)

    assert report.overall is IntegrityStatus.WARNING
    assert any(issue.code == "DAQ.STORAGE.EVENT_FINAL_LINE_TRUNCATED" for issue in report.issues)
    assert (
        sum(
            chunk.sample_count
            for chunk in reopened.open_replay(
                session.initialization.plan.session_path, chunk_size=3
            )
        )
        == 8
    )


def test_malformed_intermediate_event_sequence_is_corruption(tmp_path: Path) -> None:
    session = _direct_session(tmp_path)
    session.writer.append_chunk(_chunk(session, count=4))
    session.writer.append_event(_event(session, sequence=0))
    session.writer.append_event(_event(session, sequence=1))
    session.writer.finalize(SessionFinalization(_completion(session, 4)))
    session.adapter.close()
    path = session.initialization.plan.session_path
    events_path = path / "events.jsonl"
    lines = events_path.read_text(encoding="utf-8").splitlines()
    second = json.loads(lines[1])
    second["event_sequence"] = 5
    lines[1] = json.dumps(second, sort_keys=True, separators=(",", ":"))
    events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    from labdaq.storage.integrity import file_sha256

    manifest["integrity"]["file_checksums"]["events.jsonl"] = file_sha256(events_path)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = session.store.inspect_session(path, level=VerificationLevel.FULL)
    assert report.effective_state is SessionCompletionState.CORRUPT
    assert any(issue.code == "DAQ.STORAGE.EVENT_SEQUENCE_INVALID" for issue in report.issues)


def test_corrupt_counts_checksum_and_unsupported_schema_fail_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    session = _direct_session(tmp_path)
    session.writer.append_chunk(_chunk(session))
    result = session.writer.finalize(SessionFinalization(_completion(session, 10)))
    assert result.integrity.overall is IntegrityStatus.PASS
    session.adapter.close()
    path = session.initialization.plan.session_path
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["counts"]["persisted_samples_per_channel"] = 11
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = session.store.inspect_session(path, level=VerificationLevel.FULL)
    assert report.overall is IntegrityStatus.FAIL
    with pytest.raises(SessionNotReplayableError):
        session.store.open_replay(path, chunk_size=4)
    assert main(["session", "summary", str(path), "--verify", "--json"]) == EXIT_SESSION_FAILURE
    assert json.loads(capsys.readouterr().out)["integrity"]["overall"] == "fail"

    manifest["schema_version"] = 3
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(SessionSchemaError):
        session.store.inspect_session(path)


def test_replay_is_single_use_cancellable_and_validates_chunk_size(tmp_path: Path) -> None:
    session = _direct_session(tmp_path)
    session.writer.append_chunk(_chunk(session, count=10))
    session.writer.finalize(SessionFinalization(_completion(session, 10)))
    session.adapter.close()
    path = session.initialization.plan.session_path

    with pytest.raises(ValueError):
        session.store.open_replay(path, chunk_size=0)
    replay = session.store.open_replay(path, chunk_size=4)
    iterator = iter(replay)
    assert next(iterator).sample_count == 4
    replay.cancel()
    assert list(iterator) == []
    assert replay.state is ReplayState.CANCELLED
    with pytest.raises(RuntimeError):
        list(replay)
    replay.close()
    assert replay.state is ReplayState.CLOSED


def test_runtime_capacity_event_and_hard_stop_are_explicit(tmp_path: Path) -> None:
    class AdvancingClock:
        def __init__(self) -> None:
            self.value = 0.0

        def __call__(self) -> float:
            self.value += 1.0
            return self.value

    class Capacity:
        def __init__(self) -> None:
            self.calls = 0

        def free_bytes(self, path: Path) -> int:
            self.calls += 1
            return 10_000 if self.calls == 1 else 0

        def is_writable(self, path: Path) -> bool:
            return True

        def estimate_required_bytes(self, experiment: object, policy: object) -> int:
            return 1

    clock = AdvancingClock()
    store = TdmsSessionStore(capacity=Capacity(), monotonic=clock)  # type: ignore[arg-type]
    session = _direct_session(tmp_path, store=store, policy=_policy())
    try:
        with pytest.raises(SessionWriteError) as captured:
            session.writer.append_chunk(_chunk(session))
        assert captured.value.code == "DAQ.STORAGE.RUNTIME_SPACE_THRESHOLD"
        assert (session.initialization.plan.session_path / ".metadata-reserve").exists()
        session.writer.abort(
            SessionFaultSummary(captured.value.code, captured.value.message, captured.value.context)
        )
    finally:
        session.adapter.close()


def test_runtime_capacity_warning_is_persisted_without_stopping(tmp_path: Path) -> None:
    class AdvancingClock:
        def __init__(self) -> None:
            self.value = 0.0

        def __call__(self) -> float:
            self.value += 1.0
            return self.value

    class Capacity:
        def __init__(self) -> None:
            self.calls = 0

        def free_bytes(self, path: Path) -> int:
            self.calls += 1
            return 10_000 if self.calls == 1 else 1500

        def is_writable(self, path: Path) -> bool:
            return True

        def estimate_required_bytes(self, experiment: object, policy: object) -> int:
            return 1

    store = TdmsSessionStore(
        capacity=Capacity(),  # type: ignore[arg-type]
        monotonic=AdvancingClock(),
    )
    session = _direct_session(tmp_path, store=store, policy=_policy())
    receipt = session.writer.append_chunk(_chunk(session, count=5))
    completion = session.writer.finalize(SessionFinalization(_completion(session, 5)))
    session.adapter.close()

    assert receipt.total_persisted_sample_count == 5
    assert completion.state is SessionCompletionState.COMPLETED
    events = (completion.session_path / "events.jsonl").read_text(encoding="utf-8")
    assert "DAQ.STORAGE.SPACE_LOW" in events


def test_injected_hdf5_event_and_manifest_failures_do_not_acknowledge_samples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _direct_session(tmp_path, suffix="-hdf5")

    def fail_append(chunk: AcquisitionChunk) -> None:
        raise OSError(28, "disk full")

    assert session.writer._raw_writer is not None
    monkeypatch.setattr(session.writer._raw_writer, "append_chunk", fail_append)
    with pytest.raises(SessionWriteError) as hdf5_error:
        session.writer.append_chunk(_chunk(session))
    assert hdf5_error.value.code == "DAQ.STORAGE.DISK_FULL"
    assert session.writer.persisted_samples_per_channel == 0
    assert not (session.initialization.plan.session_path / ".metadata-reserve").exists()
    session.writer.abort(
        SessionFaultSummary(
            hdf5_error.value.code, hdf5_error.value.message, hdf5_error.value.context
        )
    )
    session.adapter.close()

    session = _direct_session(tmp_path, suffix="-event")
    assert session.writer._event_stream is not None
    session.writer._event_stream.close()
    with pytest.raises(SessionWriteError) as event_error:
        session.writer.append_event(_event(session))
    assert event_error.value.code == "DAQ.STORAGE.EVENT_APPEND_FAILED"
    session.writer.close()
    session.adapter.close()

    session = _direct_session(tmp_path, suffix="-manifest")
    original = tdms_session_module._atomic_write_json

    def fail_atomic(path: Path, payload: object, *, schema_name: str) -> None:
        raise OSError(5, "injected")

    monkeypatch.setattr(tdms_session_module, "_atomic_write_json", fail_atomic)
    with pytest.raises(SessionWriteError) as manifest_error:
        session.writer._checkpoint_manifest(force=True)
    assert manifest_error.value.code == "DAQ.STORAGE.MANIFEST_UPDATE_FAILED"
    monkeypatch.setattr(tdms_session_module, "_atomic_write_json", original)
    session.writer.close()
    session.adapter.close()

    session = _direct_session(tmp_path, suffix="-finalization")
    session.writer.append_chunk(_chunk(session))

    def fail_terminal(path: Path, payload: object, *, schema_name: str) -> None:
        if (
            isinstance(payload, dict)
            and isinstance(payload.get("session"), dict)
            and payload["session"].get("completion_state") == "completed"
        ):
            raise OSError(5, "injected terminal manifest failure")
        original(path, payload, schema_name=schema_name)  # type: ignore[arg-type]

    monkeypatch.setattr(tdms_session_module, "_atomic_write_json", fail_terminal)
    with pytest.raises(SessionWriteError) as final_error:
        session.writer.finalize(SessionFinalization(_completion(session, 10)))
    assert final_error.value.code == "DAQ.STORAGE.MANIFEST_UPDATE_FAILED"
    assert final_error.value.context["phase"] == "finalization"
    monkeypatch.setattr(tdms_session_module, "_atomic_write_json", original)
    aborted = session.writer.abort(
        SessionFaultSummary(
            final_error.value.code,
            final_error.value.message,
            final_error.value.context,
        )
    )
    assert aborted.state is SessionCompletionState.FAULTED
    assert aborted.persisted_samples_per_channel == 10
    session.adapter.close()


@pytest.mark.parametrize(
    ("phase", "physical_samples", "committed_samples"),
    [
        ("before_data_write", 0, 0),
        ("after_data_flush_before_commit", 10, 0),
        ("after_commit_before_manifest_checkpoint", 10, 10),
    ],
)
def test_hdf5_append_phase_faults_expose_only_the_committed_prefix(
    tmp_path: Path,
    phase: str,
    physical_samples: int,
    committed_samples: int,
) -> None:
    class FaultInjector:
        armed = False

        def __call__(self, current_phase: str) -> None:
            if self.armed and current_phase == phase:
                raise OSError(5, f"injected {phase}")

    injector = FaultInjector()
    store = TdmsSessionStore(hdf5_fault_injector=injector)
    session = _direct_session(tmp_path, store=store)
    injector.armed = True
    with pytest.raises(SessionWriteError) as captured:
        session.writer.append_chunk(_chunk(session))
    assert captured.value.code == "DAQ.STORAGE.HDF5_APPEND_FAILED"
    assert session.writer.persisted_samples_per_channel == 0
    session.writer.close()
    session.adapter.close()

    path = session.initialization.plan.session_path
    with h5py.File(path / "raw.h5", "r", swmr=True) as hdf5:
        assert hdf5["/raw/samples"].shape[1] == physical_samples
        commits = hdf5["/journal/commits"]
        actual_committed = int(commits[-1]["stop_sample_index"]) if len(commits) else 0
        assert actual_committed == committed_samples

    reopened = TdmsSessionStore(process_is_alive=lambda host, process_id: False)
    report = reopened.inspect_session(path)
    assert report.readable_common_sample_count == committed_samples
    if committed_samples:
        recovered = reopened.recover_session(path)
        assert recovered.recoverability is Recoverability.REPLAYABLE_PREFIX
        assert sum(chunk.sample_count for chunk in reopened.open_replay(path, chunk_size=4)) == 10
    else:
        with pytest.raises(SessionIntegrityError):
            reopened.recover_session(path)


def test_hdf5_os_sync_and_finalization_faults_are_typed(
    tmp_path: Path,
) -> None:
    class FaultInjector:
        phase: str | None = None

        def __call__(self, current_phase: str) -> None:
            if current_phase == self.phase:
                raise OSError(5, f"injected {current_phase}")

    injector = FaultInjector()
    session = _direct_session(
        tmp_path,
        store=TdmsSessionStore(hdf5_fault_injector=injector),
        suffix="-sync",
    )
    session.writer.append_chunk(_chunk(session))
    injector.phase = "during_os_sync"
    with pytest.raises(SessionWriteError) as sync_error:
        session.writer._checkpoint_manifest(force=True)
    assert sync_error.value.code == "DAQ.STORAGE.HDF5_FLUSH_FAILED"
    injector.phase = None
    session.writer.close()
    session.adapter.close()

    session = _direct_session(
        tmp_path,
        store=TdmsSessionStore(hdf5_fault_injector=injector),
        suffix="-finalization",
    )
    session.writer.append_chunk(_chunk(session))
    injector.phase = "during_finalization"
    with pytest.raises(SessionWriteError) as finalization_error:
        session.writer.finalize(SessionFinalization(_completion(session, 10)))
    assert finalization_error.value.code == "DAQ.STORAGE.HDF5_FINALIZATION_FAILED"
    injector.phase = None
    session.writer.close()
    session.adapter.close()


def test_empty_abandoned_session_is_not_recoverable(tmp_path: Path) -> None:
    session = _direct_session(tmp_path)
    session.writer.close()
    session.adapter.close()
    reopened = TdmsSessionStore(process_is_alive=lambda host, process_id: False)
    with pytest.raises(SessionIntegrityError, match="No complete"):
        reopened.recover_session(session.initialization.plan.session_path)


def test_moved_session_is_warning_not_corruption(tmp_path: Path) -> None:
    session = _direct_session(tmp_path)
    session.writer.append_chunk(_chunk(session))
    session.writer.finalize(SessionFinalization(_completion(session, 10)))
    session.adapter.close()
    path = session.initialization.plan.session_path
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["session"]["created_path"] = str(tmp_path / "old-location")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = session.store.inspect_session(path)
    assert report.overall is IntegrityStatus.WARNING
    assert report.recoverability is Recoverability.REPLAYABLE
    assert any(issue.code == "DAQ.STORAGE.SESSION_RELOCATED" for issue in report.issues)


def test_recording_service_turns_first_hdf5_failure_into_faulted_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, profile, experiment, recipe = _configuration(tmp_path, duration=0.2)
    adapter = SimulatedDaqAdapter(JsonSimulatorScenarioCodec().load_bundled("clean-four-channel"))
    store = TdmsSessionStore()
    service = RecordingSessionService(adapter, store, store, store, policy=_policy())
    plan = service.preview(experiment.session)
    service.prepare(plan, profile, experiment, recipe)

    writer = service._writer
    assert isinstance(writer, TdmsSessionWriter)
    assert writer._raw_writer is not None

    def fail_append(chunk: AcquisitionChunk) -> None:
        raise OSError(28, "disk full")

    monkeypatch.setattr(writer._raw_writer, "append_chunk", fail_append)
    service.start()
    completion = service.wait_for_terminal(5.0)

    assert completion.state is SessionCompletionState.FAULTED
    assert completion.reason.value == "disk_space_exhausted"
    assert completion.persisted_samples_per_channel == 0
    assert completion.fault is not None
    assert completion.fault.code == "DAQ.STORAGE.DISK_FULL"
    manifest = json.loads((plan.session_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"]["acquired_samples_per_channel"] == (
        completion.acquired_samples_per_channel
    )
    assert manifest["counts"]["persisted_samples_per_channel"] == 0
    assert not (plan.session_path / ".recording.lock").exists()
    service.close()


def test_abrupt_subprocess_restart_recovers_synced_prefix_under_unicode_parent(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "operator sessions Ω"
    parent.mkdir()
    marker = parent / "created-session.txt"
    script = f"""
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from labdaq.adapters.configuration_json import JsonConfigurationAdapter
from labdaq.adapters.simulated.adapter import SimulatedDaqAdapter
from labdaq.adapters.simulated.generators import generate_samples
from labdaq.adapters.simulated.scenario import JsonSimulatorScenarioCodec
from labdaq.adapters.tdms.session import TdmsSessionStore
from labdaq.ports.acquisition import AcquisitionChunk
from labdaq.ports.session import RecordingPolicy, SessionInitialization

parent = Path({str(parent)!r})
marker = Path({str(marker)!r})
documents = JsonConfigurationAdapter()
experiment = documents.load_experiment_config(Path({str(FIXTURE.resolve())!r}))
experiment = replace(
    experiment,
    session=replace(experiment.session, parent_output_directory=parent),
)
profile = documents.load_bundled_hardware_profile(
    experiment.hardware_profile_id,
    experiment.compatible_profile_revision,
)
recipe = documents.load_bundled_processing_recipe("baseline-v1-draft")
adapter = SimulatedDaqAdapter(
    JsonSimulatorScenarioCodec().load_bundled("clean-four-channel")
)
discovery = adapter.discover()
configuration = adapter.configure(experiment)
policy = RecordingPolicy(
    metadata_margin_bytes=1024,
    metadata_reserve_bytes=128,
    warning_free_bytes=2048,
    hard_stop_free_bytes=1024,
)
store = TdmsSessionStore()
plan = store.plan_session(
    experiment.session,
    session_id=str(uuid4()),
    created_at=datetime.now(UTC),
    policy=policy,
)
writer = store.create_writer(
    SessionInitialization(
        plan, profile, experiment, recipe, discovery, configuration, "subprocess-test"
    ),
    policy,
)
samples = generate_samples(
    JsonSimulatorScenarioCodec().load_bundled("clean-four-channel"),
    configuration.ordered_logical_channels,
    start_sample=0,
    sample_count=64,
    sample_rate_hz=configuration.effective_sample_rate_hz,
)
samples.flags.writeable = False
writer.append_chunk(
    AcquisitionChunk(
        plan.session_id,
        0,
        0,
        64,
        configuration.effective_sample_rate_hz,
        configuration.ordered_logical_channels,
        ("V", "V", "V", "V"),
        samples,
        1.0,
    )
)
writer._checkpoint_manifest(force=True)
marker.write_text(str(plan.session_path), encoding="utf-8")
os._exit(0)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    assert result.returncode == 0, result.stderr
    session_path = Path(marker.read_text(encoding="utf-8"))
    assert (session_path / ".recording.lock").exists()

    restarted = TdmsSessionStore(process_is_alive=lambda host, process_id: False)
    recovered = restarted.recover_session(session_path)
    chunks = list(restarted.open_replay(session_path, chunk_size=11))
    assert recovered.recoverability is Recoverability.REPLAYABLE_PREFIX
    assert sum(chunk.sample_count for chunk in chunks) == 64
    assert [chunk.sample_count for chunk in chunks] == [11, 11, 11, 11, 11, 9]
    assert not (session_path / ".recording.lock").exists()


def test_full_verification_detects_shorter_manifest_count_and_invalid_commit_journal(
    tmp_path: Path,
) -> None:
    session = _direct_session(tmp_path)
    session.writer.append_chunk(_chunk(session, count=10))
    session.writer.finalize(SessionFinalization(_completion(session, 10)))
    session.adapter.close()
    path = session.initialization.plan.session_path
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["counts"]["persisted_samples_per_channel"] = 9
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    smaller = session.store.inspect_session(path, level=VerificationLevel.FULL)
    assert smaller.overall is IntegrityStatus.FAIL
    assert any(issue.code == "DAQ.STORAGE.MANIFEST_COUNT_MISMATCH" for issue in smaller.issues)

    with h5py.File(path / "raw.h5", "r+") as hdf5:
        commits = hdf5["/journal/commits"]
        last = commits[-1]
        last["stop_sample_index"] = 9
        commits[-1] = last
    divergent = session.store.inspect_session(path, level=VerificationLevel.FULL)
    assert divergent.effective_state is SessionCompletionState.CORRUPT
    assert any(issue.code == "DAQ.STORAGE.HDF5_COMMIT_INVALID" for issue in divergent.issues)


def test_terminal_hdf5_uncommitted_tail_is_detected_and_not_replayable(tmp_path: Path) -> None:
    session = _direct_session(tmp_path)
    session.writer.append_chunk(_chunk(session, count=10))
    session.writer.append_chunk(_chunk(session, start=10, count=10, sequence=1))
    session.writer.finalize(SessionFinalization(_completion(session, 20)))
    session.adapter.close()
    path = session.initialization.plan.session_path
    raw_path = path / "raw.h5"
    with h5py.File(raw_path, "r+") as hdf5:
        samples = hdf5["/raw/samples"]
        samples.resize(21, axis=1)
        samples[:, 20] = 0.0

    report = session.store.inspect_session(path, level=VerificationLevel.FULL)
    assert report.effective_state is SessionCompletionState.CORRUPT
    assert report.recoverability is Recoverability.NOT_REPLAYABLE
    issue_codes = {issue.code for issue in report.issues}
    assert "DAQ.STORAGE.HDF5_UNCOMMITTED_TAIL" in issue_codes
    with pytest.raises(SessionNotReplayableError):
        session.store.open_replay(path, chunk_size=5)


def test_terminal_hdf5_finalization_record_disagreement_is_corruption(tmp_path: Path) -> None:
    session = _direct_session(tmp_path)
    session.writer.append_chunk(_chunk(session, count=10))
    session.writer.finalize(SessionFinalization(_completion(session, 10)))
    session.adapter.close()
    path = session.initialization.plan.session_path
    with h5py.File(path / "raw.h5", "r+") as hdf5:
        finalization = hdf5["/journal/finalization"]
        record = finalization[0]
        record["terminal_state"] = b"stopped"
        finalization[0] = record

    report = session.store.inspect_session(path, level=VerificationLevel.FULL)
    assert report.effective_state is SessionCompletionState.CORRUPT
    assert any(issue.code == "DAQ.STORAGE.HDF5_FINALIZATION_INVALID" for issue in report.issues)


def test_hdf5_channel_metadata_disagreement_is_corruption(tmp_path: Path) -> None:
    session = _direct_session(tmp_path)
    session.writer.append_chunk(_chunk(session, count=10))
    session.writer.finalize(SessionFinalization(_completion(session, 10)))
    session.adapter.close()
    path = session.initialization.plan.session_path
    with h5py.File(path / "raw.h5", "r+") as hdf5:
        hdf5["/channels/000"].attrs["sensor_model"] = "tampered-model"

    report = session.store.inspect_session(path, level=VerificationLevel.FULL)
    assert report.effective_state is SessionCompletionState.CORRUPT
    mismatch = next(
        issue for issue in report.issues if issue.code == "DAQ.STORAGE.METADATA_MISMATCH"
    )
    assert "accel-x" in mismatch.context["channels"]


def test_hdf5_layout_and_required_metadata_fail_closed(tmp_path: Path) -> None:
    session = _direct_session(tmp_path, suffix="-missing-dataset")
    session.writer.append_chunk(_chunk(session, count=10))
    session.writer.finalize(SessionFinalization(_completion(session, 10)))
    session.adapter.close()
    path = session.initialization.plan.session_path
    with h5py.File(path / "raw.h5", "r+") as hdf5:
        del hdf5["/raw/channel_units"]
    report = session.store.inspect_session(path)
    assert any(issue.code == "DAQ.STORAGE.HDF5_LAYOUT_UNSUPPORTED" for issue in report.issues)

    session = _direct_session(tmp_path, suffix="-missing-channel")
    session.writer.append_chunk(_chunk(session, count=10))
    session.writer.finalize(SessionFinalization(_completion(session, 10)))
    session.adapter.close()
    path = session.initialization.plan.session_path
    with h5py.File(path / "raw.h5", "r+") as hdf5:
        del hdf5["/channels/003"]
    report = session.store.inspect_session(path)
    assert any(issue.code == "DAQ.STORAGE.METADATA_MISMATCH" for issue in report.issues)

    session = _direct_session(tmp_path, suffix="-invalid-axes")
    session.writer.append_chunk(_chunk(session, count=10))
    session.writer.finalize(SessionFinalization(_completion(session, 10)))
    session.adapter.close()
    path = session.initialization.plan.session_path
    with h5py.File(path / "raw.h5", "r+") as hdf5:
        hdf5["/raw/samples"].dims[1].label = "wrong-axis"
    report = session.store.inspect_session(path)
    assert any(issue.code == "DAQ.STORAGE.HDF5_LAYOUT_UNSUPPORTED" for issue in report.issues)

    session = _direct_session(tmp_path, suffix="-missing-finalization")
    session.writer.append_chunk(_chunk(session, count=10))
    session.writer.finalize(SessionFinalization(_completion(session, 10)))
    session.adapter.close()
    path = session.initialization.plan.session_path
    with h5py.File(path / "raw.h5", "r+") as hdf5:
        hdf5["/journal/finalization"].resize(0, axis=0)
    report = session.store.inspect_session(path)
    assert any(issue.code == "DAQ.STORAGE.HDF5_FINALIZATION_INVALID" for issue in report.issues)


def test_record_and_replay_are_bounded_across_many_chunks(tmp_path: Path) -> None:
    parent = tmp_path / "space and unicode 測試"
    parent.mkdir()
    session = _direct_session(parent)
    total = 50_000
    acquisition_chunk = 4096
    sequence = 0
    for start in range(0, total, acquisition_chunk):
        count = min(acquisition_chunk, total - start)
        session.writer.append_chunk(_chunk(session, start=start, count=count, sequence=sequence))
        sequence += 1
    completion = session.writer.finalize(SessionFinalization(_completion(session, total)))
    session.adapter.close()

    replay_chunks = list(session.store.open_replay(completion.session_path, chunk_size=257))
    assert max(chunk.sample_count for chunk in replay_chunks) == 257
    assert len(replay_chunks) > sequence
    assert np.array_equal(
        np.concatenate([chunk.samples for chunk in replay_chunks], axis=1),
        generate_samples(
            session.scenario,
            session.initialization.daq_configuration.ordered_logical_channels,
            start_sample=0,
            sample_count=total,
            sample_rate_hz=session.initialization.daq_configuration.effective_sample_rate_hz,
        ),
    )
