from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from labdaq.adapters.configuration_json import JsonConfigurationAdapter
from labdaq.adapters.export import FileSystemExportHistoryAdapter
from labdaq.adapters.simulated.adapter import SimulatedDaqAdapter
from labdaq.adapters.simulated.scenario import JsonSimulatorScenarioCodec
from labdaq.adapters.tdms.session import TdmsSessionStore
from labdaq.application.live_service import LiveAcquisitionCoordinator
from labdaq.application.plotting_service import PlotDataService
from labdaq.application.processing_service import (
    CalibrationResolutionService,
    RecordedSessionAnalysisService,
    RecordedSessionProcessingService,
)
from labdaq.application.readiness_service import (
    AcquisitionLaunchIntent,
    FunctionalPreviewLaunchIntent,
    RawDiagnosticAuthorization,
    RawDiagnosticConfirmation,
    RawDiagnosticLaunchIntent,
)
from labdaq.application.review_service import SessionReviewService
from labdaq.application.session_service import RecordingSessionService, SessionInspectionService
from labdaq.bootstrap import create_export_coordinator
from labdaq.cli.main import EXIT_SUCCESS, main
from labdaq.domain.enums import SignalStage
from labdaq.domain.plotting import (
    ChannelSelection,
    ExportFormat,
    ExportProduct,
    PlotRepresentation,
    PlotRequest,
    PlotViewport,
)
from labdaq.errors import PlotError, ProcessingError
from labdaq.ports.acquisition import AcquisitionState
from labdaq.ports.operator import LiveDisplayConfiguration
from labdaq.ports.processing import TransientDetectionSettings
from labdaq.ports.session import QualificationAttachment, RecordingPolicy, SessionType
from labdaq.processing import BaselineScientificProcessing

pytestmark = pytest.mark.integration

FIXTURE = Path("tests/fixtures/configuration/valid-four-channel-synthetic-v1.json")


def test_simulated_live_record_review_process_and_export_agree(tmp_path: Path) -> None:
    documents = JsonConfigurationAdapter()
    experiment = documents.load_experiment_config(FIXTURE)
    experiment = replace(
        experiment,
        session=replace(experiment.session, parent_output_directory=tmp_path),
        acquisition=replace(
            experiment.acquisition,
            requested_duration_seconds=0.04,
            chunk_config=replace(
                experiment.acquisition.chunk_config,
                samples_per_channel=5000,
            ),
        ),
    )
    profile = documents.load_bundled_hardware_profile(
        experiment.hardware_profile_id,
        experiment.compatible_profile_revision,
    )
    recipe = documents.load_bundled_processing_recipe("baseline-v1")
    scenario = replace(
        JsonSimulatorScenarioCodec().load_bundled("clean-four-channel"),
        pacing_mode="real_time",
    )
    store = TdmsSessionStore()
    policy = RecordingPolicy()
    plan = store.plan_session(
        experiment.session,
        session_id=str(uuid4()),
        created_at=datetime.now(UTC),
        policy=policy,
    )
    report_bytes = b'{"qualification":"simulator-test"}'
    attachment = QualificationAttachment(
        report_id="m10-simulator-qualification",
        report_sha256=hashlib.sha256(report_bytes).hexdigest(),
        qualification_fingerprint="a" * 64,
        overall_status="warning",
        evaluated_at=datetime.now(UTC),
        age_seconds=0.0,
        eligible=True,
        report_json=report_bytes,
        override_reason_code="DAQ.QUALIFICATION.SIMULATOR_METADATA_EXEMPTION",
    )
    intent = AcquisitionLaunchIntent(
        profile=profile,
        experiment=experiment,
        session_plan=plan,
        qualification_report_path=tmp_path / "qualification-source.json",
        qualification_report_id=attachment.report_id,
        qualification_fingerprint=attachment.qualification_fingerprint,
        eligibility_evaluated_at=attachment.evaluated_at,
        qualification_attachment=attachment,
    )
    numerical = BaselineScientificProcessing()

    def recording_factory() -> RecordingSessionService:
        return RecordingSessionService(
            SimulatedDaqAdapter(scenario),
            store,
            store,
            store,
            policy=policy,
        )

    coordinator = LiveAcquisitionCoordinator(
        recording_factory,
        SessionInspectionService(store),
        numerical,
        recipe,
        display=LiveDisplayConfiguration(
            visible_window_seconds=0.04,
            maximum_history_seconds=1.0,
            maximum_points_per_channel=4000,
            metric_update_interval_seconds=0.5,
            metric_window_seconds=0.02,
        ),
    )
    try:
        coordinator.start(intent)
        deadline = time.monotonic() + 5.0
        while coordinator.metrics() is None and time.monotonic() < deadline:
            time.sleep(0.005)
        summary = coordinator.wait_for_terminal(10.0)

        status = coordinator.status()
        assert status.state is AcquisitionState.COMPLETED
        assert status.acquired_samples_per_channel == 2000
        assert status.persisted_samples_per_channel == 2000
        assert status.persistence_loss_samples == 0
        assert status.acquisition_gap_count == 0
        assert summary.recorded.acquisition_completion is not None
        assert summary.recorded.acquisition_completion.total_acquired_samples_per_channel == 2000
        assert coordinator.request_stop() == status

        raw_frame = coordinator.build_plot_frame(SignalStage.RAW)
        conditioned_frame = coordinator.build_plot_frame(SignalStage.CONDITIONED)
        assert raw_frame is not None and conditioned_frame is not None
        assert raw_frame.source_sample_count == 2000
        assert conditioned_frame.source_sample_count == 2000
        assert coordinator.metrics() is not None

        plots = PlotDataService(store, documents, numerical)
        selected_range = plots.resolve_range(plan.session_path)
        channels = ChannelSelection(experiment.acquisition.channel_ids)
        raw_replay = plots.build_plot_data(
            PlotRequest(
                plan.session_path,
                PlotRepresentation.RAW_TIME,
                channels,
                selected_range,
                PlotViewport(4000),
            )
        )
        conditioned_replay = plots.build_plot_data(
            PlotRequest(
                plan.session_path,
                PlotRepresentation.FILTERED_TIME,
                channels,
                selected_range,
                PlotViewport(4000),
                recipe_id="baseline",
            )
        )
        for live, replay in zip(raw_frame.series, raw_replay.series, strict=True):
            np.testing.assert_array_equal(live.source_sample_indices, replay.source_sample_indices)
            np.testing.assert_allclose(live.values, replay.y_values, rtol=0, atol=0)
        for live, replay in zip(conditioned_frame.series, conditioned_replay.series, strict=True):
            np.testing.assert_array_equal(live.source_sample_indices, replay.source_sample_indices)
            np.testing.assert_allclose(live.values, replay.y_values, rtol=0, atol=1e-12)

        review = SessionReviewService(
            SessionInspectionService(store),
            store,
            FileSystemExportHistoryAdapter(),
            plots,
            create_export_coordinator(),
            analysis=RecordedSessionAnalysisService(store, documents, numerical),
        )
        loaded = review.load(plan.session_path)
        assert loaded.summary.persisted_samples_per_channel == 2000
        assert loaded.summary.qualification_report_available
        assert loaded.events[-1].code == "DAQ.ACQUISITION.COMPLETED"
        source_digest = store.processing_metadata(plan.session_path).raw_data_digest
        detections = review.detect_transients(
            plan.session_path,
            ("accel-x", "accel-y"),
            SignalStage.RAW,
            TransientDetectionSettings(
                mad_multiplier=0.5,
                pre_padding_seconds=0.0,
                post_padding_seconds=0.0,
                merge_gap_seconds=0.0,
            ),
        )
        assert detections.events
        extrema = review.compute_extrema(
            plan.session_path,
            ("accel-x", "accel-y"),
            SignalStage.RAW,
            selected_range,
        )
        assert len(extrema) == 2
        assert all(item.maximum.sample_index is not None for item in extrema)
        fft = review.build_plot(
            plan.session_path,
            PlotRepresentation.FFT_AMPLITUDE,
            ("accel-x", "accel-y"),
            selected_range,
            spectral_input_stage=SignalStage.RAW,
        )
        assert fft.representation is PlotRepresentation.FFT_AMPLITUDE
        assert store.processing_metadata(plan.session_path).raw_data_digest == source_digest
        request = review.build_export_request(
            plan.session_path,
            ExportProduct.FILTERED_TIME_DATA,
            ExportFormat.CSV,
            PlotRepresentation.FILTERED_TIME,
            ("accel-x", "accel-y"),
            selected_range,
        )
        manifest = review.run_export((request,))
        assert manifest.final_state.value == "completed"
        refreshed = review.load(plan.session_path)
        assert refreshed.export_history[0].manifest == manifest
    finally:
        coordinator.close()


def test_functional_preview_records_four_raw_rows_but_processes_only_xyz(
    tmp_path: Path,
) -> None:
    documents = JsonConfigurationAdapter()
    experiment = documents.decode_experiment_config(
        documents.load_bundled_experiment_template("accelerometer-commissioning-v2")
    )
    experiment = replace(
        experiment,
        session=replace(experiment.session, parent_output_directory=tmp_path),
        acquisition=replace(
            experiment.acquisition,
            requested_duration_seconds=0.1,
            chunk_config=replace(experiment.acquisition.chunk_config, samples_per_channel=1000),
        ),
    )
    profile = documents.load_bundled_hardware_profile(
        experiment.hardware_profile_id, experiment.compatible_profile_revision
    )
    recipe = documents.load_bundled_processing_recipe("baseline-v1")
    scenario = replace(
        JsonSimulatorScenarioCodec().load_bundled("clean-three-axis"),
        pacing_mode="real_time",
    )
    store = TdmsSessionStore()
    policy = RecordingPolicy()
    plan = store.plan_session(
        experiment.session,
        session_id=str(uuid4()),
        created_at=datetime.now(UTC),
        policy=policy,
    )
    intent = FunctionalPreviewLaunchIntent(
        profile=profile,
        experiment=experiment,
        session_plan=plan,
        eligibility_evaluated_at=datetime.now(UTC),
    )
    numerical = BaselineScientificProcessing()

    def recording_factory() -> RecordingSessionService:
        return RecordingSessionService(
            SimulatedDaqAdapter(scenario), store, store, store, policy=policy
        )

    coordinator = LiveAcquisitionCoordinator(
        recording_factory,
        SessionInspectionService(store),
        numerical,
        recipe,
        display=LiveDisplayConfiguration(
            visible_window_seconds=0.02,
            maximum_history_seconds=1.0,
            maximum_points_per_channel=2000,
            metric_update_interval_seconds=0.01,
            metric_window_seconds=0.01,
        ),
    )
    try:
        coordinator.start(intent)
        coordinator.wait_for_terminal(10.0)
        raw = coordinator.build_plot_frame(SignalStage.RAW)
        conditioned = coordinator.build_plot_frame(SignalStage.CONDITIONED)
        assert raw is not None and len(raw.series) == 4
        assert raw.series[-1].display_name.endswith("NOT CHARGE")
        assert conditioned is not None and len(conditioned.series) == 3
        assert tuple(series.channel_id for series in conditioned.series) == (
            "accel-x",
            "accel-y",
            "accel-z",
        )

        metadata = store.processing_metadata(plan.session_path)
        assert metadata.session_type == "experiment"
        assert len(metadata.channels) == 4
        assert metadata.channels[-1].calibration is None
        assert metadata.channels[-1].processing_disposition.value == "raw_only_diagnostic"
        with pytest.raises(ProcessingError, match="do not exist"):
            CalibrationResolutionService().resolve_source(metadata, ("missing-ai3",))
        with pytest.raises(ProcessingError, match="incomplete or invalid") as rejected:
            CalibrationResolutionService().resolve_source(metadata, ("pcb-422e03-raw-diagnostic",))
        assert rejected.value.context["channel_failures"][0]["code"] == (
            "DAQ.PROCESSING.RAW_ONLY_DIAGNOSTIC"
        )
        broken_channel = replace(metadata.channels[0], calibration=None)
        with pytest.raises(ProcessingError, match="incomplete or invalid"):
            CalibrationResolutionService().resolve_source(
                replace(metadata, channels=(broken_channel, *metadata.channels[1:])),
                ("accel-x",),
            )
        replay = store.open_replay(plan.session_path, chunk_size=1000)
        try:
            assert next(iter(replay)).samples.shape == (4, 1000)
        finally:
            replay.close()
        processed = RecordedSessionProcessingService(store, documents, numerical).process(
            plan.session_path
        )
        assert processed.provenance.ordered_channel_ids == ("accel-x", "accel-y", "accel-z")
        assert len(processed.fft) == len(processed.psd) == len(processed.metrics) == 3

        plots = PlotDataService(store, documents, numerical)
        selected_range = plots.resolve_range(plan.session_path, start_index=1250, stop_index=3750)
        diagnostic = ChannelSelection(("pcb-422e03-raw-diagnostic",))
        raw_request = PlotRequest(
            plan.session_path,
            PlotRepresentation.RAW_TIME,
            diagnostic,
            selected_range,
            PlotViewport(2000),
        )
        raw_bundle = plots.build_plot_data(raw_request)
        assert raw_bundle.series[0].processing_disposition == "raw_only_diagnostic"
        assert raw_bundle.series[0].engineering_interpretation is None
        assert raw_bundle.series[0].warning == "UNPOWERED / UNQUALIFIED / NOT CHARGE"
        source_before = {
            name: (plan.session_path / name).read_bytes()
            for name in ("raw.h5", "manifest.json", "processing-recipe.json")
        }
        ordered_ids = (diagnostic.channel_ids[0], "accel-z", "accel-x", "accel-y")
        references = {}
        for representation in (PlotRepresentation.FFT_AMPLITUDE, PlotRepresentation.PSD):
            for stage in SignalStage:
                request = replace(
                    raw_request,
                    channels=ChannelSelection(ordered_ids, ("ai3", "Z", "X", "Y")),
                    representation=representation,
                    recipe_id="baseline-v1",
                    spectral_input_stage=stage,
                    viewport=PlotViewport(10000),
                )
                bundle = plots.build_plot_data(request, chunk_size=333)
                assert tuple(series.channel_id for series in bundle.series) == ordered_ids
                assert bundle.spectral_details["input_stage"] == (
                    "raw" if stage is SignalStage.RAW else "mixed"
                )
                ai3 = bundle.series[0]
                assert ai3.label == "ai3 — Raw voltage"
                assert ai3.spectral_input_stage is SignalStage.RAW
                assert ai3.to_dict(include_values=False)["spectral_input_stage"] == "raw"
                assert ai3.calibration is None
                assert ai3.engineering_interpretation is None
                assert ai3.processing_disposition == "raw_only_diagnostic"
                assert ai3.warning == "UNPOWERED / UNQUALIFIED / NOT CHARGE"
                assert ai3.y_unit == (
                    "V" if representation is PlotRepresentation.FFT_AMPLITUDE else "V^2/Hz"
                )
                assert all(series.spectral_input_stage is stage for series in bundle.series[1:])
                if stage is SignalStage.RAW:
                    references[representation] = ai3
                else:
                    np.testing.assert_array_equal(ai3.y_values, references[representation].y_values)
                single = plots.build_plot_data(
                    replace(request, channels=diagnostic), chunk_size=777
                ).series[0]
                np.testing.assert_allclose(single.y_values, ai3.y_values, rtol=1e-12, atol=1e-20)
                assert single.source_range == selected_range
                peak = np.argmax(ai3.y_values)
                assert ai3.x_values[peak] == pytest.approx(80.0)
                if representation is PlotRepresentation.FFT_AMPLITUDE:
                    assert ai3.y_values[peak] == pytest.approx(0.001, rel=1e-10)
                else:
                    df = ai3.x_values[1] - ai3.x_values[0]
                    assert np.sum(ai3.y_values) * df == pytest.approx(0.001**2 / 2, rel=1e-10)
        with pytest.raises(PlotError, match="Raw-only diagnostic"):
            plots.build_plot_data(
                replace(raw_request, representation=PlotRepresentation.CALIBRATED_TIME)
            )

        review = SessionReviewService(
            SessionInspectionService(store),
            store,
            FileSystemExportHistoryAdapter(),
            plots,
            create_export_coordinator(),
        )
        spectral_export_request = review.build_export_request(
            plan.session_path,
            ExportProduct.PSD_DATA,
            ExportFormat.CSV,
            PlotRepresentation.PSD,
            ordered_ids,
            selected_range,
        )
        for product, output_format, representation in (
            (ExportProduct.PSD_DATA, ExportFormat.CSV, PlotRepresentation.PSD),
            (ExportProduct.PSD_FIGURE, ExportFormat.PNG, PlotRepresentation.PSD),
            (ExportProduct.PSD_FIGURE, ExportFormat.SVG, PlotRepresentation.PSD),
            (ExportProduct.FFT_AMPLITUDE_DATA, ExportFormat.CSV, PlotRepresentation.FFT_AMPLITUDE),
            (ExportProduct.FFT_FIGURE, ExportFormat.PNG, PlotRepresentation.FFT_AMPLITUDE),
            (ExportProduct.FFT_FIGURE, ExportFormat.SVG, PlotRepresentation.FFT_AMPLITUDE),
        ):
            spectral_export = review.run_export(
                (
                    replace(
                        spectral_export_request,
                        product=product,
                        output_format=output_format,
                        representation=representation,
                    ),
                )
            )
            assert spectral_export.final_state.value == "completed"
            artifact = spectral_export.artifacts[0]
            destination = plan.session_path / (
                "exports" if output_format is ExportFormat.CSV else "plots"
            )
            sidecar = json.loads(
                (destination / artifact.sidecar_relative_path).read_text(encoding="utf-8")
            )
            assert sidecar["selected_channels"] == list(ordered_ids)
            assert sidecar["spectral_details"]["input_stage"] == "mixed"
            assert sidecar["channels"][0]["spectral_input_stage"] == "raw"
            assert sidecar["channels"][0]["calibration_hash"] is None
            assert sidecar["channels"][1]["spectral_input_stage"] == "conditioned"
            if output_format is ExportFormat.CSV:
                values = np.loadtxt(
                    plan.session_path / "exports" / artifact.relative_path,
                    delimiter=",",
                    skiprows=1,
                )
                np.testing.assert_allclose(
                    values[:, 1],
                    references[representation].y_values,
                    rtol=1e-12,
                    atol=1e-20,
                )
        export_request = review.build_export_request(
            plan.session_path,
            ExportProduct.RAW_TIME_DATA,
            ExportFormat.CSV,
            PlotRepresentation.RAW_TIME,
            diagnostic.channel_ids,
            selected_range,
        )
        export = review.run_export((export_request,))
        assert export.final_state.value == "completed"
        sidecar_path = plan.session_path / "exports" / export.artifacts[0].sidecar_relative_path
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        assert sidecar["channels"][0]["processing_disposition"] == "raw_only_diagnostic"
        assert sidecar["channels"][0]["engineering_interpretation"] is None
        assert sidecar["channels"][0]["warning"] == "UNPOWERED / UNQUALIFIED / NOT CHARGE"
        for name, contents in source_before.items():
            assert (plan.session_path / name).read_bytes() == contents
    finally:
        coordinator.close()

    manifest = json.loads((plan.session_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["session"]["session_type"] == "experiment"
    assert manifest["session"]["experiment_mode"] == "accelerometer_commissioning"
    assert manifest["qualification"] is None


def test_raw_diagnostic_allows_voltage_spectra_but_rejects_calibration(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documents = JsonConfigurationAdapter()
    experiment = documents.decode_experiment_config(
        documents.load_bundled_experiment_template("accelerometer-commissioning-v2")
    )
    experiment = replace(
        experiment,
        session=replace(experiment.session, parent_output_directory=tmp_path),
        acquisition=replace(
            experiment.acquisition,
            requested_duration_seconds=0.02,
            chunk_config=replace(experiment.acquisition.chunk_config, samples_per_channel=1000),
        ),
    )
    profile = documents.load_bundled_hardware_profile(
        experiment.hardware_profile_id,
        experiment.compatible_profile_revision,
    )
    recipe = documents.load_bundled_processing_recipe("baseline-v1")
    scenario = replace(
        JsonSimulatorScenarioCodec().load_bundled("clean-three-axis"),
        pacing_mode="real_time",
    )
    store = TdmsSessionStore()
    policy = RecordingPolicy()
    session_plan = store.plan_session(
        experiment.session,
        session_id=str(uuid4()),
        created_at=datetime.now(UTC),
        policy=policy,
    )
    confirmation = RawDiagnosticConfirmation(
        operator="diagnostic-operator",
        confirmed_at=datetime.now(UTC),
        measured_supply_voltage_v=5.0,
        polarity_confirmed=True,
        ai3_connection="disconnected",
    )
    authorization = RawDiagnosticAuthorization(
        confirmation=confirmation,
        hardware_validation_sha256="c" * 64,
        selected_device_id="SimDev1",
        selected_module_id="SimDev1Mod1",
        ordered_physical_channels=("ai0", "ai1", "ai2", "ai3"),
    )
    intent = RawDiagnosticLaunchIntent(
        profile=profile,
        experiment=experiment,
        session_plan=session_plan,
        authorization=authorization,
        eligibility_evaluated_at=datetime.now(UTC),
    )

    def recording_factory() -> RecordingSessionService:
        return RecordingSessionService(
            SimulatedDaqAdapter(scenario),
            store,
            store,
            store,
            policy=policy,
        )

    coordinator = LiveAcquisitionCoordinator(
        recording_factory,
        SessionInspectionService(store),
        BaselineScientificProcessing(),
        recipe,
        display=LiveDisplayConfiguration(
            visible_window_seconds=0.02,
            maximum_history_seconds=1.0,
            maximum_points_per_channel=2000,
            metric_update_interval_seconds=0.01,
            metric_window_seconds=0.01,
        ),
    )
    try:
        coordinator.start(intent)
        summary = coordinator.wait_for_terminal(10.0)
        raw = coordinator.build_plot_frame(SignalStage.RAW)

        assert summary.session.session_type is SessionType.RAW_DIAGNOSTIC
        assert summary.recorded.persisted_samples_per_channel == 1000
        assert raw is not None and len(raw.series) == 4
        assert coordinator.build_plot_frame(SignalStage.CONDITIONED) is None
        assert coordinator.metrics() is None
    finally:
        coordinator.close()

    manifest = json.loads((session_plan.session_path / "manifest.json").read_text())
    assert manifest["session"]["session_type"] == "raw-diagnostic"
    assert manifest["qualification"] is None
    assert manifest["diagnostic_authorization"]["authorization_scope"] == (
        "raw-voltage-diagnostic-only"
    )
    assert manifest["processing_recipe"]["derived_processing_allowed"] is False

    metadata = store.processing_metadata(session_plan.session_path)
    assert metadata.session_type == "raw-diagnostic"
    plots = PlotDataService(store, documents, BaselineScientificProcessing())
    selected_range = plots.resolve_range(
        session_plan.session_path,
        start_index=0,
        stop_index=1000,
    )
    raw_request = PlotRequest(
        source_session=session_plan.session_path,
        representation=PlotRepresentation.RAW_TIME,
        channels=ChannelSelection(experiment.acquisition.channel_ids),
        sample_range=selected_range,
        viewport=PlotViewport(2000),
    )
    assert len(plots.build_plot_data(raw_request).series) == 4
    for representation in (PlotRepresentation.FFT_AMPLITUDE, PlotRepresentation.PSD):
        spectral_request = replace(
            raw_request,
            representation=representation,
            recipe_id="baseline-v1",
            spectral_input_stage=SignalStage.RAW,
        )
        bundle = plots.build_plot_data(spectral_request)
        assert len(bundle.series) == 4
        assert all(series.spectral_input_stage is SignalStage.RAW for series in bundle.series)
        assert all(series.calibration is None for series in bundle.series)
        assert all(series.engineering_interpretation is None for series in bundle.series)
        for stage in (SignalStage.CALIBRATED, SignalStage.CONDITIONED):
            with pytest.raises(PlotError) as error:
                plots.build_plot_data(replace(spectral_request, spectral_input_stage=stage))
            assert error.value.code == "DAQ.PLOT.RAW_DIAGNOSTIC_SESSION"
    derived_request = replace(
        raw_request,
        representation=PlotRepresentation.CALIBRATED_TIME,
    )
    with pytest.raises(PlotError) as plot_error:
        plots.build_plot_data(derived_request)
    assert plot_error.value.code == "DAQ.PLOT.RAW_DIAGNOSTIC_SESSION"
    with pytest.raises(ProcessingError) as processing_error:
        RecordedSessionProcessingService(
            store,
            documents,
            BaselineScientificProcessing(),
        ).process(session_plan.session_path)
    assert processing_error.value.code == "DAQ.PROCESSING.RAW_DIAGNOSTIC_SESSION"

    review = SessionReviewService(
        SessionInspectionService(store),
        store,
        FileSystemExportHistoryAdapter(),
        plots,
        create_export_coordinator(),
    )
    raw_export = review.build_export_request(
        session_plan.session_path,
        ExportProduct.RAW_TIME_DATA,
        ExportFormat.CSV,
        PlotRepresentation.RAW_TIME,
        experiment.acquisition.channel_ids,
        selected_range,
    )
    export_manifest = review.run_export((raw_export,))
    assert export_manifest.final_state.value == "completed"
    derived_export = review.build_export_request(
        session_plan.session_path,
        ExportProduct.CALIBRATED_TIME_DATA,
        ExportFormat.CSV,
        PlotRepresentation.CALIBRATED_TIME,
        ("accel-x",),
        selected_range,
    )
    rejected_export = review.run_export((derived_export,))
    assert rejected_export.final_state.value == "faulted"
    assert rejected_export.faults[0].context["source_code"] == ("DAQ.PLOT.RAW_DIAGNOSTIC_SESSION")

    assert (
        main(
            [
                "plot-data",
                str(session_plan.session_path),
                "--view",
                "fft",
                "--spectral-input",
                "raw",
                "--recipe",
                "baseline-v1",
                "--json",
            ]
        )
        == EXIT_SUCCESS
    )
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["series"]) == 4
    assert all(series["spectral_input_stage"] == "raw" for series in payload["series"])
    assert payload["series"][-1]["calibration_hash"] is None
    spectral_export = review.build_export_request(
        session_plan.session_path,
        ExportProduct.FFT_AMPLITUDE_DATA,
        ExportFormat.CSV,
        PlotRepresentation.FFT_AMPLITUDE,
        experiment.acquisition.channel_ids,
        selected_range,
        spectral_input_stage=SignalStage.RAW,
    )
    assert review.run_export((spectral_export,)).final_state.value == "completed"

    summary_without_recipe = replace(review.load(session_plan.session_path).summary, recipe=None)
    monkeypatch.setattr(
        review._inspection, "summary", lambda *args, **kwargs: summary_without_recipe
    )
    with pytest.raises(ValueError, match="does not contain processing recipe provenance"):
        review.build_plot(
            session_plan.session_path,
            PlotRepresentation.FFT_AMPLITUDE,
            experiment.acquisition.channel_ids,
            selected_range,
            spectral_input_stage=SignalStage.RAW,
        )
