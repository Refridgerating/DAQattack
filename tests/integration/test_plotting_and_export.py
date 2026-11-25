from __future__ import annotations

import csv
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
from jsonschema import Draft202012Validator, FormatChecker

from labdaq.adapters.configuration_json import JsonConfigurationAdapter
from labdaq.adapters.export import (
    CsvArtifactWriter,
    FileSystemArtifactStore,
    JsonArtifactWriter,
    MatplotlibStaticRenderer,
)
from labdaq.adapters.plotting_json import JsonPlotExportCodec
from labdaq.adapters.simulated.adapter import SimulatedDaqAdapter
from labdaq.adapters.simulated.generators import generate_samples
from labdaq.adapters.simulated.scenario import JsonSimulatorScenarioCodec
from labdaq.adapters.tdms.session import TdmsSessionStore
from labdaq.application.export_service import ExportCoordinator, ExportService
from labdaq.application.plotting_service import PlotDataService
from labdaq.application.processing_service import CalibrationResolutionService
from labdaq.cli.main import EXIT_SUCCESS, main
from labdaq.domain.enums import SignalStage
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
from labdaq.errors import ExportError, ProcessingError
from labdaq.ports.acquisition import (
    AcquisitionChunk,
    AcquisitionCompletion,
    AcquisitionState,
    CleanupSummary,
    CompletionReason,
)
from labdaq.ports.session import (
    RecordingPolicy,
    SessionFinalization,
    SessionInitialization,
)
from labdaq.processing import BaselineScientificProcessing

pytestmark = pytest.mark.integration

FIXTURE = Path("tests/fixtures/configuration/valid-four-channel-synthetic-v1.json")


@dataclass(frozen=True)
class RecordedFixture:
    path: Path
    store: TdmsSessionStore
    expected: np.ndarray
    channel_ids: tuple[str, ...]
    sample_rate_hz: float


@pytest.fixture
def recorded(tmp_path: Path) -> RecordedFixture:
    documents = JsonConfigurationAdapter()
    experiment = documents.load_experiment_config(FIXTURE)
    experiment = replace(
        experiment,
        session=replace(experiment.session, parent_output_directory=tmp_path),
        acquisition=replace(experiment.acquisition, requested_duration_seconds=0.1),
    )
    profile = documents.load_bundled_hardware_profile(
        experiment.hardware_profile_id,
        experiment.compatible_profile_revision,
    )
    recipe = documents.load_bundled_processing_recipe("baseline-v1")
    scenario = JsonSimulatorScenarioCodec().load_bundled("clean-four-channel")
    adapter = SimulatedDaqAdapter(scenario)
    discovery = adapter.discover()
    configuration = adapter.configure(experiment)
    store = TdmsSessionStore()
    policy = RecordingPolicy(
        metadata_margin_bytes=1024,
        metadata_reserve_bytes=128,
        warning_free_bytes=2048,
        hard_stop_free_bytes=1024,
    )
    plan = store.plan_session(
        experiment.session,
        session_id=str(uuid4()),
        created_at=datetime.now(UTC),
        policy=policy,
    )
    writer = store.create_writer(
        SessionInitialization(
            plan,
            profile,
            experiment,
            recipe,
            discovery,
            configuration,
            "m07-test",
        ),
        policy,
    )
    expected = generate_samples(
        scenario,
        configuration.ordered_logical_channels,
        start_sample=0,
        sample_count=5000,
        sample_rate_hz=configuration.effective_sample_rate_hz,
    )
    for sequence, start in enumerate(range(0, 5000, 333)):
        values = np.ascontiguousarray(expected[:, start : min(5000, start + 333)])
        values.flags.writeable = False
        writer.append_chunk(
            AcquisitionChunk(
                plan.session_id,
                sequence,
                start,
                values.shape[1],
                configuration.effective_sample_rate_hz,
                configuration.ordered_logical_channels,
                ("V", "V", "V", "V"),
                values,
                float(sequence + 1),
            )
        )
    completion = AcquisitionCompletion(
        AcquisitionState.COMPLETED,
        CompletionReason.DURATION_REACHED,
        plan.session_id,
        datetime.now(UTC),
        datetime.now(UTC),
        5000,
        5000,
        15,
        16,
        0,
        (),
        None,
        CleanupSummary(True, True, False, True),
    )
    result = writer.finalize(SessionFinalization(completion))
    writer.close()
    adapter.close()
    assert result.persisted_samples_per_channel == 5000
    return RecordedFixture(
        plan.session_path,
        store,
        expected,
        configuration.ordered_logical_channels,
        configuration.effective_sample_rate_hz,
    )


def _plot_service(recorded: RecordedFixture) -> PlotDataService:
    return PlotDataService(
        recorded.store,
        JsonConfigurationAdapter(),
        BaselineScientificProcessing(),
    )


def _range(recorded: RecordedFixture, start: int = 100, stop: int = 900):
    return resolve_sample_range(
        sample_rate_hz=recorded.sample_rate_hz,
        readable_sample_count=5000,
        start_index=start,
        stop_index=stop,
    )


def test_plot_data_raw_calibrated_filtered_and_spectra(recorded: RecordedFixture) -> None:
    service = _plot_service(recorded)
    selected = ChannelSelection((recorded.channel_ids[2], recorded.channel_ids[0]))
    source_range = _range(recorded)

    raw_request = PlotRequest(
        recorded.path,
        PlotRepresentation.RAW_TIME,
        selected,
        source_range,
        PlotViewport(1000),
    )
    raw = service.build_plot_data(raw_request, chunk_size=127)
    np.testing.assert_array_equal(raw.series[0].y_values, recorded.expected[2, 100:900])
    np.testing.assert_array_equal(raw.series[0].source_sample_indices, np.arange(100, 900))
    assert raw.series[0].physical_channel.endswith("ai2")
    assert raw.source_manifest_sha256 == service.source_metadata(recorded.path).manifest_sha256

    calibrated_request = replace(raw_request, representation=PlotRepresentation.CALIBRATED_TIME)
    calibrated = service.build_plot_data(calibrated_request, chunk_size=211)
    metadata = service.source_metadata(recorded.path)
    calibrations, _ = CalibrationResolutionService().resolve_source(
        metadata,
        selected.channel_ids,
    )
    raw_selected = np.ascontiguousarray(recorded.expected[[2, 0], 100:900])
    expected_calibrated = BaselineScientificProcessing().calibrate_samples(
        raw_selected,
        calibrations,
    )
    np.testing.assert_array_equal(calibrated.series[0].y_values, expected_calibrated[0])
    assert calibrated.series[0].y_unit == "g"

    filtered = service.build_plot_data(
        replace(
            raw_request,
            representation=PlotRepresentation.FILTERED_TIME,
            recipe_id="baseline-v1",
            viewport=PlotViewport(31),
        ),
        chunk_size=113,
    )
    assert filtered.recipe_id == "baseline"
    assert filtered.series[0].decimated is True
    assert len(filtered.series[0].y_values) <= 31

    for representation in (PlotRepresentation.FFT_AMPLITUDE, PlotRepresentation.PSD):
        for stage in (SignalStage.RAW, SignalStage.CALIBRATED, SignalStage.CONDITIONED):
            bundle = service.build_plot_data(
                PlotRequest(
                    recorded.path,
                    representation,
                    selected,
                    source_range,
                    PlotViewport(40),
                    "baseline-v1",
                    stage,
                ),
                chunk_size=157,
            )
            assert bundle.spectral_details["input_stage"] == stage.value
            assert all(series.x_unit == "Hz" for series in bundle.series)
            assert all(len(series.x_values) <= 40 for series in bundle.series)


def _coordinator(recorded: RecordedFixture, renderer=None, csv_writer=None) -> ExportCoordinator:
    return ExportCoordinator(
        ExportService(
            _plot_service(recorded),
            FileSystemArtifactStore(),
            csv_writer or CsvArtifactWriter(),
            JsonArtifactWriter(),
            renderer or MatplotlibStaticRenderer(),
        )
    )


def test_csv_export_is_exact_traceable_atomic_and_collision_safe(
    recorded: RecordedFixture,
) -> None:
    source_before = {
        name: (recorded.path / name).read_bytes()
        for name in ("manifest.json", "raw.h5", "events.jsonl", "processing-recipe.json")
    }
    request = ExportRequest(
        recorded.path,
        ExportProduct.RAW_TIME_DATA,
        ExportFormat.CSV,
        ChannelSelection((recorded.channel_ids[1], recorded.channel_ids[3])),
        _range(recorded, 123, 456),
        PlotRepresentation.RAW_TIME,
        base_name="raw-selection",
        chunk_size=47,
    )

    class CountingCsvWriter(CsvArtifactWriter):
        chunk_count = 0

        def write(self, destination, *, headers, chunks, cancelled, progressed):
            def counted():
                for chunk in chunks:
                    self.chunk_count += 1
                    yield chunk

            return super().write(
                destination,
                headers=headers,
                chunks=counted(),
                cancelled=cancelled,
                progressed=progressed,
            )

    writer = CountingCsvWriter()
    progress = []
    manifest = _coordinator(recorded, csv_writer=writer).run_export_job(
        (request,),
        progress=progress.append,
    )

    assert manifest.final_state is ExportJobState.COMPLETED
    assert progress[-1].state is ExportJobState.COMPLETED
    assert writer.chunk_count > 1
    artifact = manifest.artifacts[0]
    csv_path = recorded.path / "exports" / artifact.relative_path
    sidecar_path = recorded.path / "exports" / artifact.sidecar_relative_path
    with csv_path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream))
    assert rows[0] == [
        "sample_index",
        "relative_time_s",
        recorded.channel_ids[1],
        recorded.channel_ids[3],
    ]
    indices = np.asarray([int(row[0]) for row in rows[1:]], dtype=np.int64)
    values = np.asarray([[float(value) for value in row[2:]] for row in rows[1:]])
    np.testing.assert_array_equal(indices, np.arange(123, 456))
    np.testing.assert_array_equal(values[:, 0], recorded.expected[1, 123:456])
    np.testing.assert_array_equal(values[:, 1], recorded.expected[3, 123:456])
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    persisted_manifest = json.loads(manifest.manifest_path.read_text(encoding="utf-8"))  # type: ignore[union-attr]
    _validate_schema("export-sidecar.schema.json", sidecar)
    legacy_sidecar = json.loads(json.dumps(sidecar))
    for channel in legacy_sidecar["channels"]:
        channel.pop("spectral_input_stage")
    _validate_schema("export-sidecar.schema.json", legacy_sidecar)
    _validate_schema("export-manifest.schema.json", persisted_manifest)
    codec = JsonPlotExportCodec()
    assert (
        codec.loads_sidecar(sidecar_path.read_text(encoding="utf-8"))["artifact_id"]
        == artifact.artifact_id
    )
    assert codec.loads_manifest(manifest.manifest_path.read_text(encoding="utf-8")) == manifest  # type: ignore[union-attr]
    assert sidecar["output_sha256"] == artifact.sha256
    assert {name: (recorded.path / name).read_bytes() for name in source_before} == source_before

    with pytest.raises(ExportError) as collision:
        _coordinator(recorded).run_export_job((request,))
    assert collision.value.code == "DAQ.EXPORT.PATH_COLLISION"


def test_oversized_fft_is_rejected_before_replay_allocation(
    recorded: RecordedFixture,
) -> None:
    class GuardedSource:
        replay_opened = False

        def processing_metadata(self, path):
            return replace(
                recorded.store.processing_metadata(path),
                readable_sample_count=1_048_577,
            )

        def open_replay(self, path, *, chunk_size):
            self.replay_opened = True
            raise AssertionError("oversized FFT must be rejected before replay")

    source = GuardedSource()
    service = PlotDataService(
        source,  # type: ignore[arg-type]
        JsonConfigurationAdapter(),
        BaselineScientificProcessing(),
        CalibrationResolutionService(),
    )
    request = PlotRequest(
        recorded.path,
        PlotRepresentation.FFT_AMPLITUDE,
        ChannelSelection((recorded.channel_ids[0],)),
        resolve_sample_range(
            sample_rate_hz=recorded.sample_rate_hz,
            readable_sample_count=1_048_577,
        ),
        recipe_id="baseline-v1",
        spectral_input_stage=SignalStage.RAW,
    )

    with pytest.raises(ProcessingError, match="memory bound"):
        service.build_plot_data(request, chunk_size=17)
    assert source.replay_opened is False


def test_filtered_csv_and_headless_png_svg_figures(recorded: RecordedFixture) -> None:
    selected = ChannelSelection(recorded.channel_ids)
    source_range = _range(recorded, 0, 1000)
    filtered_csv = ExportRequest(
        recorded.path,
        ExportProduct.FILTERED_TIME_DATA,
        ExportFormat.CSV,
        selected,
        source_range,
        PlotRepresentation.FILTERED_TIME,
        recipe_id="baseline-v1",
        base_name="filtered-values",
        chunk_size=101,
    )
    overlay = ExportRequest(
        recorded.path,
        ExportProduct.OVERLAY_FIGURE,
        ExportFormat.PNG,
        selected,
        source_range,
        PlotRepresentation.FILTERED_TIME,
        recipe_id="baseline-v1",
        destination_directory=recorded.path / "exports",
        base_name="filtered-overlay",
        max_points=80,
    )
    psd = ExportRequest(
        recorded.path,
        ExportProduct.PSD_FIGURE,
        ExportFormat.SVG,
        selected,
        source_range,
        PlotRepresentation.PSD,
        recipe_id="baseline-v1",
        spectral_input_stage=SignalStage.CONDITIONED,
        destination_directory=recorded.path / "exports",
        base_name="conditioned-psd",
        max_points=80,
    )
    manifest = _coordinator(recorded).run_export_job((filtered_csv, overlay, psd))

    assert manifest.final_state is ExportJobState.COMPLETED
    assert len(manifest.artifacts) == 3
    png = recorded.path / "exports" / manifest.artifacts[1].relative_path
    svg = recorded.path / "exports" / manifest.artifacts[2].relative_path
    assert png.read_bytes().startswith(b"\x89PNG")
    assert "<svg" in svg.read_text(encoding="utf-8")
    for artifact in manifest.artifacts:
        sidecar = json.loads(
            (recorded.path / "exports" / artifact.sidecar_relative_path).read_text(encoding="utf-8")
        )
        assert sidecar["recipe_id"] == "baseline"
        assert all(channel["calibration_hash"] for channel in sidecar["channels"])


def test_every_figure_kind_renders_png_and_svg(recorded: RecordedFixture) -> None:
    requests = []
    for product, representation in (
        (ExportProduct.TIME_HISTORY_FIGURE, PlotRepresentation.RAW_TIME),
        (ExportProduct.OVERLAY_FIGURE, PlotRepresentation.RAW_TIME),
        (ExportProduct.FFT_FIGURE, PlotRepresentation.FFT_AMPLITUDE),
        (ExportProduct.PSD_FIGURE, PlotRepresentation.PSD),
    ):
        for output_format in (ExportFormat.PNG, ExportFormat.SVG):
            spectral = representation in {
                PlotRepresentation.FFT_AMPLITUDE,
                PlotRepresentation.PSD,
            }
            channels = (
                (recorded.channel_ids[0],)
                if product is ExportProduct.TIME_HISTORY_FIGURE
                else recorded.channel_ids[:2]
            )
            requests.append(
                ExportRequest(
                    recorded.path,
                    product,
                    output_format,
                    ChannelSelection(channels),
                    _range(recorded, 0, 1000),
                    representation,
                    recipe_id="baseline-v1" if spectral else None,
                    spectral_input_stage=SignalStage.RAW if spectral else None,
                    destination_directory=recorded.path / "exports",
                    base_name=f"{product.value}-{output_format.value}",
                    max_points=40,
                )
            )

    manifest = _coordinator(recorded).run_export_job(tuple(requests))

    assert manifest.final_state is ExportJobState.COMPLETED
    assert len(manifest.artifacts) == 8
    for artifact in manifest.artifacts:
        path = recorded.path / "exports" / artifact.relative_path
        if artifact.output_format is ExportFormat.PNG:
            assert path.read_bytes().startswith(b"\x89PNG")
        else:
            assert "<svg" in path.read_text(encoding="utf-8")


def test_incompatible_spectral_grids_split_without_interpolation(
    recorded: RecordedFixture,
) -> None:
    delegate = _plot_service(recorded)

    class SplitGridPlots:
        def source_metadata(self, path):
            return delegate.source_metadata(path)

        def build_plot_data(self, request, *, chunk_size=8192):
            bundle = delegate.build_plot_data(request, chunk_size=chunk_size)
            shifted = np.array(bundle.series[1].x_values + 0.125, dtype=np.float64)
            shifted.flags.writeable = False
            return replace(
                bundle,
                series=(bundle.series[0], replace(bundle.series[1], x_values=shifted)),
            )

    coordinator = ExportCoordinator(
        ExportService(
            SplitGridPlots(),  # type: ignore[arg-type]
            FileSystemArtifactStore(),
            CsvArtifactWriter(),
            JsonArtifactWriter(),
            MatplotlibStaticRenderer(),
        )
    )
    request = ExportRequest(
        recorded.path,
        ExportProduct.PSD_DATA,
        ExportFormat.CSV,
        ChannelSelection(recorded.channel_ids[:2]),
        _range(recorded, 0, 1000),
        PlotRepresentation.PSD,
        recipe_id="baseline-v1",
        spectral_input_stage=SignalStage.RAW,
        base_name="split-spectrum",
    )
    manifest = coordinator.run_export_job((request,))

    assert manifest.final_state is ExportJobState.COMPLETED
    assert manifest.total_artifacts == 2
    assert [artifact.relative_path for artifact in manifest.artifacts] == [
        "split-spectrum.csv",
        "split-spectrum-grid-2.csv",
    ]
    for artifact, channel_id in zip(
        manifest.artifacts,
        recorded.channel_ids[:2],
        strict=True,
    ):
        with (recorded.path / "exports" / artifact.relative_path).open(
            encoding="utf-8",
            newline="",
        ) as stream:
            assert next(csv.reader(stream)) == ["frequency_hz", channel_id]
        sidecar = json.loads(
            (recorded.path / "exports" / artifact.sidecar_relative_path).read_text(encoding="utf-8")
        )
        assert sidecar["spectral_details"]["input_stage"] == "raw"
        assert sidecar["channels"][0]["calibration_hash"] is None


def test_cancellation_and_partial_failure_write_truthful_manifests(
    recorded: RecordedFixture,
) -> None:
    request = ExportRequest(
        recorded.path,
        ExportProduct.RAW_TIME_DATA,
        ExportFormat.CSV,
        ChannelSelection((recorded.channel_ids[0],)),
        _range(recorded),
        PlotRepresentation.RAW_TIME,
        base_name="cancelled-data",
    )
    different_destination = ExportRequest(
        recorded.path,
        ExportProduct.TIME_HISTORY_FIGURE,
        ExportFormat.PNG,
        ChannelSelection((recorded.channel_ids[0],)),
        _range(recorded),
        PlotRepresentation.RAW_TIME,
        base_name="different-destination",
    )
    with pytest.raises(ExportError, match="share a destination"):
        _coordinator(recorded).run_export_job((request, different_destination))
    with pytest.raises(ExportError, match="same output path"):
        _coordinator(recorded).run_export_job((request, request))

    cancelled = _coordinator(recorded).run_export_job((request,), cancelled=lambda: True)
    assert cancelled.final_state is ExportJobState.CANCELLED
    assert not cancelled.artifacts
    assert cancelled.faults[0].code == "DAQ.EXPORT.CANCELLED"
    assert not (recorded.path / "exports" / "cancelled-data.csv").exists()

    class FailingRenderer:
        def render(self, *args, **kwargs):
            raise ExportError("injected renderer failure", code="DAQ.EXPORT.RENDER_FAILED")

    first = replace(request, base_name="completed-before-failure")
    second = ExportRequest(
        recorded.path,
        ExportProduct.TIME_HISTORY_FIGURE,
        ExportFormat.PNG,
        ChannelSelection((recorded.channel_ids[0],)),
        _range(recorded),
        PlotRepresentation.RAW_TIME,
        destination_directory=recorded.path / "exports",
        base_name="failed-figure",
    )
    partial = _coordinator(recorded, FailingRenderer()).run_export_job((first, second))
    assert partial.final_state is ExportJobState.FAULTED
    assert len(partial.artifacts) == 1
    assert (recorded.path / "exports" / "completed-before-failure.csv").exists()
    assert not (recorded.path / "exports" / "failed-figure.png").exists()


def test_plot_and_export_cli_are_headless(recorded: RecordedFixture, capsys) -> None:
    assert (
        main(
            [
                "plot-data",
                str(recorded.path),
                "--view",
                "raw",
                "--channels",
                recorded.channel_ids[0],
                "--start-sample",
                "10",
                "--stop-sample",
                "30",
                "--max-points",
                "10",
                "--json",
            ]
        )
        == EXIT_SUCCESS
    )
    plot = json.loads(capsys.readouterr().out)
    assert plot["series"][0]["output_point_count"] <= 10
    assert (
        main(
            [
                "export",
                "csv",
                str(recorded.path),
                "--view",
                "raw",
                "--channels",
                recorded.channel_ids[0],
                "--start-sample",
                "10",
                "--stop-sample",
                "30",
                "--name",
                "cli-raw",
                "--json",
            ]
        )
        == EXIT_SUCCESS
    )
    exported = json.loads(capsys.readouterr().out)
    assert exported["final_state"] == "completed"
    assert (
        main(
            [
                "export",
                "figure",
                str(recorded.path),
                "--kind",
                "overlay",
                "--view",
                "filtered",
                "--recipe",
                "baseline-v1",
                "--format",
                "png",
                "--name",
                "cli-filtered-overlay",
                "--json",
            ]
        )
        == EXIT_SUCCESS
    )
    overlay = json.loads(capsys.readouterr().out)
    assert overlay["final_state"] == "completed"
    assert (recorded.path / "plots" / "cli-filtered-overlay.png").exists()
    assert (
        main(
            [
                "export",
                "figure",
                str(recorded.path),
                "--kind",
                "psd",
                "--view",
                "psd",
                "--spectral-input",
                "conditioned",
                "--recipe",
                "baseline-v1",
                "--format",
                "svg",
                "--name",
                "cli-conditioned-psd",
                "--json",
            ]
        )
        == EXIT_SUCCESS
    )
    psd = json.loads(capsys.readouterr().out)
    assert psd["final_state"] == "completed"
    assert (recorded.path / "plots" / "cli-conditioned-psd.svg").exists()
    assert main(["plot-data", str(recorded.path), "--view", "filtered", "--json"]) == EXIT_SUCCESS
    automatic = json.loads(capsys.readouterr().out)
    assert automatic["recipe_id"] == "baseline"


def _validate_schema(name: str, payload: object) -> None:
    path = files("labdaq.resources").joinpath("schemas", "v1", name)
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(payload)
