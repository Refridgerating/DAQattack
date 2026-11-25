"""Atomic DAQ-M07 CSV, figure, sidecar, and export-job orchestration."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Never
from uuid import uuid4

from labdaq.domain.plotting import (
    ExportArtifact,
    ExportFault,
    ExportFormat,
    ExportJob,
    ExportJobState,
    ExportManifest,
    ExportProduct,
    ExportProgress,
    FigureKind,
    PlotRepresentation,
    PlotRequest,
    PlotViewport,
)
from labdaq.errors import ExportError, LabDAQError, PlotError
from labdaq.version import __version__

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from pathlib import Path

    from labdaq.application.plotting_service import PlotDataService, TimeDataContext
    from labdaq.domain.plotting import ExportRequest
    from labdaq.ports.plotting import (
        ArtifactStorePort,
        CsvArtifactWriterPort,
        JsonArtifactWriterPort,
        PlotBundle,
        PlotSeries,
        StaticFigureRendererPort,
        TabularChunk,
    )

EXPORT_SERVICE_VERSION = "m07-v1"


class ExportService:
    def __init__(
        self,
        plots: PlotDataService,
        store: ArtifactStorePort,
        csv_writer: CsvArtifactWriterPort,
        json_writer: JsonArtifactWriterPort,
        renderer: StaticFigureRendererPort,
    ) -> None:
        self._plots = plots
        self._store = store
        self._csv = csv_writer
        self._json = json_writer
        self._renderer = renderer

    @property
    def plots(self) -> PlotDataService:
        return self._plots

    @property
    def store(self) -> ArtifactStorePort:
        return self._store

    @property
    def json_writer(self) -> JsonArtifactWriterPort:
        return self._json

    def export_csv(
        self,
        request: ExportRequest,
        *,
        export_job_id: str,
        artifact_id: str,
        destination: Path,
        base_name: str,
        cancelled: Callable[[], bool],
        progressed: Callable[[int], None],
    ) -> tuple[ExportArtifact, ...]:
        _validate_product(request)
        if request.representation in {
            PlotRepresentation.FFT_AMPLITUDE,
            PlotRepresentation.PSD,
        }:
            return self._export_spectral_csv(
                request,
                export_job_id=export_job_id,
                artifact_id=artifact_id,
                destination=destination,
                base_name=base_name,
                cancelled=cancelled,
                progressed=progressed,
            )
        final_path = destination / f"{base_name}.csv"
        sidecar_path = destination / f"{base_name}.csv.metadata.json"
        temporary = self._store.temporary_path(final_path)
        sidecar_temporary = self._store.temporary_path(sidecar_path)
        primary_installed = False
        sidecar_installed = False
        try:
            plot_request = _plot_request(request)
            context = self._plots.prepare_time_data(plot_request)
            headers = (
                "sample_index",
                "relative_time_s",
                *request.channels.channel_ids,
            )
            chunks = _time_csv_chunks(
                self._plots,
                plot_request,
                context,
                chunk_size=request.chunk_size,
                cancelled=cancelled,
            )
            self._csv.write(
                temporary,
                headers=headers,
                chunks=chunks,
                cancelled=cancelled,
                progressed=progressed,
            )
            bundle = self._plots.build_plot_data(
                plot_request,
                chunk_size=request.chunk_size,
            )
            primary_receipt = self._store.finalize_exclusive(temporary, final_path)
            primary_installed = True
            created_at = datetime.now(UTC)
            sidecar_payload = _sidecar_payload(
                request=request,
                bundle=bundle,
                export_job_id=export_job_id,
                artifact_id=artifact_id,
                output_path=final_path,
                primary_byte_count=primary_receipt.byte_count,
                primary_sha256=primary_receipt.sha256,
                created_at=created_at,
                output_point_counts=(request.sample_range.sample_count,) * len(bundle.series),
            )
            self._json.write(sidecar_temporary, sidecar_payload)
            sidecar_receipt = self._store.finalize_exclusive(sidecar_temporary, sidecar_path)
            sidecar_installed = True
            return (
                _artifact(
                    request,
                    bundle,
                    export_job_id,
                    artifact_id,
                    final_path,
                    primary_receipt.byte_count,
                    primary_receipt.sha256,
                    sidecar_path,
                    sidecar_receipt.sha256,
                    created_at,
                ),
            )
        except Exception:
            self._store.remove(temporary)
            self._store.remove(sidecar_temporary)
            if primary_installed:
                self._store.remove(final_path)
            if sidecar_installed:
                self._store.remove(sidecar_path)
            raise

    def _export_spectral_csv(
        self,
        request: ExportRequest,
        *,
        export_job_id: str,
        artifact_id: str,
        destination: Path,
        base_name: str,
        cancelled: Callable[[], bool],
        progressed: Callable[[int], None],
    ) -> tuple[ExportArtifact, ...]:
        from labdaq.domain.plotting import ChannelSelection

        complete_request = replace(
            _plot_request(request),
            viewport=PlotViewport(max(4, request.sample_range.sample_count + 2)),
        )
        bundle = self._plots.build_plot_data(
            complete_request,
            chunk_size=request.chunk_size,
        )
        artifacts: list[ExportArtifact] = []
        installed: list[Path] = []
        try:
            for group_index, group in enumerate(_spectral_groups(bundle), start=1):
                group_base = base_name if group_index == 1 else f"{base_name}-grid-{group_index}"
                group_id = artifact_id if group_index == 1 else str(uuid4())
                group_details = dict(bundle.spectral_details or {})
                stages = {series.spectral_input_stage for series in group}
                if None not in stages:
                    first_stage = group[0].spectral_input_stage
                    assert first_stage is not None
                    group_details["input_stage"] = "mixed" if len(stages) > 1 else first_stage.value
                group_bundle = replace(bundle, series=group, spectral_details=group_details)
                group_request = replace(
                    request,
                    channels=ChannelSelection(tuple(series.channel_id for series in group)),
                )
                final_path = destination / f"{group_base}.csv"
                sidecar_path = destination / f"{group_base}.csv.metadata.json"
                temporary = self._store.temporary_path(final_path)
                sidecar_temporary = self._store.temporary_path(sidecar_path)
                try:
                    self._csv.write(
                        temporary,
                        headers=("frequency_hz", *(series.channel_id for series in group)),
                        chunks=_spectral_csv_chunks(group_bundle, request.chunk_size),
                        cancelled=cancelled,
                        progressed=progressed,
                    )
                    primary = self._store.finalize_exclusive(temporary, final_path)
                    installed.append(final_path)
                    created_at = datetime.now(UTC)
                    sidecar = _sidecar_payload(
                        request=group_request,
                        bundle=group_bundle,
                        export_job_id=export_job_id,
                        artifact_id=group_id,
                        output_path=final_path,
                        primary_byte_count=primary.byte_count,
                        primary_sha256=primary.sha256,
                        created_at=created_at,
                        output_point_counts=tuple(series.output_point_count for series in group),
                    )
                    self._json.write(sidecar_temporary, sidecar)
                    sidecar_receipt = self._store.finalize_exclusive(
                        sidecar_temporary,
                        sidecar_path,
                    )
                    installed.append(sidecar_path)
                    artifacts.append(
                        _artifact(
                            group_request,
                            group_bundle,
                            export_job_id,
                            group_id,
                            final_path,
                            primary.byte_count,
                            primary.sha256,
                            sidecar_path,
                            sidecar_receipt.sha256,
                            created_at,
                        )
                    )
                except Exception:
                    self._store.remove(temporary)
                    self._store.remove(sidecar_temporary)
                    raise
        except Exception:
            for path in installed:
                self._store.remove(path)
            raise
        return tuple(artifacts)

    def export_figure(
        self,
        request: ExportRequest,
        *,
        export_job_id: str,
        artifact_id: str,
        destination: Path,
        base_name: str,
        cancelled: Callable[[], bool],
    ) -> ExportArtifact:
        _validate_product(request)
        suffix = request.output_format.value
        final_path = destination / f"{base_name}.{suffix}"
        sidecar_path = destination / f"{base_name}.{suffix}.metadata.json"
        temporary = self._store.temporary_path(final_path)
        sidecar_temporary = self._store.temporary_path(sidecar_path)
        primary_installed = False
        sidecar_installed = False
        if cancelled():
            _raise_cancelled("Figure export was cancelled.")
        try:
            bundle = self._plots.build_plot_data(
                _plot_request(request),
                chunk_size=request.chunk_size,
            )
            kind = _figure_kind(request.product)
            receipt = self._renderer.render(
                bundle,
                kind=kind,
                options=request.figure_options,
                output_format=request.output_format,
                destination=temporary,
            )
            primary_receipt = self._store.finalize_exclusive(temporary, final_path)
            primary_installed = True
            created_at = datetime.now(UTC)
            sidecar = _sidecar_payload(
                request=request,
                bundle=bundle,
                export_job_id=export_job_id,
                artifact_id=artifact_id,
                output_path=final_path,
                primary_byte_count=primary_receipt.byte_count,
                primary_sha256=primary_receipt.sha256,
                created_at=created_at,
                output_point_counts=tuple(series.output_point_count for series in bundle.series),
            )
            sidecar["figure"] = {
                "kind": receipt.figure_kind.value,
                "format": receipt.output_format.value,
                "axis_labels": [list(value) for value in receipt.axis_labels],
                "legend_labels": list(receipt.legend_labels),
                "series_count": receipt.series_count,
            }
            self._json.write(sidecar_temporary, sidecar)
            sidecar_receipt = self._store.finalize_exclusive(sidecar_temporary, sidecar_path)
            sidecar_installed = True
            return _artifact(
                request,
                bundle,
                export_job_id,
                artifact_id,
                final_path,
                primary_receipt.byte_count,
                primary_receipt.sha256,
                sidecar_path,
                sidecar_receipt.sha256,
                created_at,
            )
        except Exception:
            self._store.remove(temporary)
            self._store.remove(sidecar_temporary)
            if primary_installed:
                self._store.remove(final_path)
            if sidecar_installed:
                self._store.remove(sidecar_path)
            raise


class ExportCoordinator:
    def __init__(self, service: ExportService) -> None:
        self._service = service

    def run_export_job(
        self,
        requests: Sequence[ExportRequest],
        *,
        cancelled: Callable[[], bool] | None = None,
        progress: Callable[[ExportProgress], None] | None = None,
    ) -> ExportManifest:
        if not requests:
            raise ExportError(
                "An export job requires at least one request.",
                code="DAQ.EXPORT.REQUEST_INVALID",
            )
        cancellation = cancelled or (lambda: False)
        job_id = str(uuid4())
        created_at = datetime.now(UTC)
        metadata = self._service.plots.source_metadata(requests[0].source_session)
        if any(
            request.source_session.absolute() != requests[0].source_session.absolute()
            for request in requests
        ):
            raise ExportError(
                "All requests in one export job must use the same source session.",
                code="DAQ.EXPORT.REQUEST_INVALID",
            )
        resolved = tuple(_resolve_output(request) for request in requests)
        destinations = {destination.absolute() for destination, _ in resolved}
        if len(destinations) != 1:
            raise ExportError(
                "All requests in one export job must share a destination directory.",
                code="DAQ.EXPORT.REQUEST_INVALID",
                remediation="Run separate jobs or select one common destination.",
            )
        destination = resolved[0][0]
        job = ExportJob(job_id, tuple(requests), destination, created_at)
        requests = job.requests
        self._service.store.validate_destination(destination)
        manifest_path = destination / f"export-manifest-{job_id}.json"
        output_paths: list[Path] = [manifest_path]
        for request, (_, base_name) in zip(requests, resolved, strict=True):
            suffix = request.output_format.value
            primary = destination / f"{base_name}.{suffix}"
            output_paths.extend((primary, destination / f"{base_name}.{suffix}.metadata.json"))
            if request.output_format is ExportFormat.CSV and request.representation in {
                PlotRepresentation.FFT_AMPLITUDE,
                PlotRepresentation.PSD,
            }:
                for group_index in range(2, len(request.channels.channel_ids) + 1):
                    grouped = destination / f"{base_name}-grid-{group_index}.csv"
                    output_paths.extend((grouped, destination / f"{grouped.name}.metadata.json"))
            _validate_product(request)
        if len(set(output_paths)) != len(output_paths):
            raise ExportError(
                "Two export requests resolve to the same output path.",
                code="DAQ.EXPORT.PATH_COLLISION",
            )
        self._service.store.reject_existing(tuple(output_paths))
        artifacts: list[ExportArtifact] = []
        faults: list[ExportFault] = []
        warnings: list[str] = []
        processed_samples_total = 0
        final_state = ExportJobState.RUNNING
        _emit(progress, job_id, final_state, 0, requests, 0, "starting")
        for index, (request, (_, base_name)) in enumerate(zip(requests, resolved, strict=True)):
            artifact_id = str(uuid4())
            processed = 0
            request_progress_start = processed_samples_total

            def progressed(count: int, current_artifact_id: str = artifact_id) -> None:
                nonlocal processed, processed_samples_total
                processed += count
                processed_samples_total += count
                _emit(
                    progress,
                    job_id,
                    ExportJobState.RUNNING,
                    processed_samples_total,
                    requests,
                    len(artifacts),
                    "writing",
                    current_artifact_id,
                )

            try:
                if cancellation():
                    _raise_cancelled("Export job was cancelled.")
                if request.output_format is ExportFormat.CSV:
                    completed = self._service.export_csv(
                        request,
                        export_job_id=job_id,
                        artifact_id=artifact_id,
                        destination=destination,
                        base_name=base_name,
                        cancelled=cancellation,
                        progressed=progressed,
                    )
                else:
                    completed = (
                        self._service.export_figure(
                            request,
                            export_job_id=job_id,
                            artifact_id=artifact_id,
                            destination=destination,
                            base_name=base_name,
                            cancelled=cancellation,
                        ),
                    )
                artifacts.extend(completed)
                for artifact in completed:
                    warnings.extend(artifact.warnings)
                processed_samples_total = request_progress_start + request.sample_range.sample_count
                _emit(
                    progress,
                    job_id,
                    ExportJobState.RUNNING,
                    processed_samples_total,
                    requests,
                    len(artifacts),
                    "artifact-completed",
                    artifact_id,
                )
            except Exception as cause:
                error = _as_export_error(cause, request)
                faults.append(ExportFault(error.code, error.message, error.context))
                final_state = (
                    ExportJobState.CANCELLED
                    if error.code == "DAQ.EXPORT.CANCELLED"
                    or (isinstance(cause, PlotError) and cause.code == "DAQ.PLOT.CANCELLED")
                    else ExportJobState.FAULTED
                )
                break
            if index == len(requests) - 1:
                final_state = ExportJobState.COMPLETED
        completed_at = datetime.now(UTC)
        manifest = ExportManifest(
            schema_version=1,
            export_job_id=job_id,
            source_session_id=metadata.session_id,
            source_manifest_sha256=metadata.manifest_sha256,
            application_version=__version__,
            export_service_version=EXPORT_SERVICE_VERSION,
            requests=tuple(request.to_dict() for request in requests),
            artifacts=tuple(artifacts),
            warnings=tuple(dict.fromkeys(warnings)),
            faults=tuple(faults),
            created_at=created_at,
            completed_at=completed_at,
            final_state=final_state,
            processed_samples=processed_samples_total,
            total_samples=sum(request.sample_range.sample_count for request in requests),
            completed_artifacts=len(artifacts),
            total_artifacts=max(len(requests), len(artifacts)),
            manifest_path=manifest_path,
        )
        temporary_manifest = self._service.store.temporary_path(manifest_path)
        try:
            self._service.json_writer.write(temporary_manifest, manifest.to_dict())
            self._service.store.finalize_exclusive(temporary_manifest, manifest_path)
        except Exception:
            self._service.store.remove(temporary_manifest)
            raise
        _emit(
            progress,
            job_id,
            final_state,
            processed_samples_total,
            requests,
            len(artifacts),
            "finished",
        )
        return manifest


def _plot_request(request: ExportRequest) -> PlotRequest:
    return PlotRequest(
        source_session=request.source_session,
        representation=request.representation,
        channels=request.channels,
        sample_range=request.sample_range,
        viewport=PlotViewport(request.max_points),
        recipe_id=request.recipe_id,
        spectral_input_stage=request.spectral_input_stage,
    )


def _time_csv_chunks(
    service: PlotDataService,
    request: PlotRequest,
    context: TimeDataContext,
    *,
    chunk_size: int,
    cancelled: Callable[[], bool],
) -> Iterator[TabularChunk]:
    import numpy as np

    from labdaq.ports.plotting import TabularChunk

    for chunk in service.iter_time_data(
        request,
        context,
        chunk_size=chunk_size,
        cancelled=cancelled,
    ):
        count = chunk.values.shape[1]
        indices = np.arange(
            chunk.start_sample_index,
            chunk.start_sample_index + count,
            dtype=np.int64,
        )
        times = np.asarray(indices / context.metadata.sample_rate_hz, dtype=np.float64)
        yield TabularChunk(
            (indices, times, *(chunk.values[index] for index in range(len(chunk.values))))
        )


def _spectral_csv_chunks(bundle: PlotBundle, chunk_size: int) -> Iterator[TabularChunk]:
    from labdaq.ports.plotting import TabularChunk

    total = len(bundle.series[0].x_values)
    for start in range(0, total, chunk_size):
        stop = min(total, start + chunk_size)
        yield TabularChunk(
            (
                bundle.series[0].x_values[start:stop],
                *(series.y_values[start:stop] for series in bundle.series),
            )
        )


def _spectral_groups(bundle: PlotBundle) -> tuple[tuple[PlotSeries, ...], ...]:
    import numpy as np

    groups: list[list[PlotSeries]] = []
    for series in bundle.series:
        for group in groups:
            reference = group[0]
            if np.array_equal(reference.x_values, series.x_values):
                group.append(series)
                break
        else:
            groups.append([series])
    return tuple(tuple(group) for group in groups)


def _sidecar_payload(
    *,
    request: ExportRequest,
    bundle: PlotBundle,
    export_job_id: str,
    artifact_id: str,
    output_path: Path,
    primary_byte_count: int,
    primary_sha256: str,
    created_at: datetime,
    output_point_counts: tuple[int, ...],
) -> dict[str, object]:
    metadata = {
        "schema_version": 1,
        "artifact_id": artifact_id,
        "export_job_id": export_job_id,
        "source_session_id": bundle.source_session_id,
        "source_session_path": str(bundle.source_session_path),
        "source_manifest_path": str(bundle.source_manifest_path),
        "source_manifest_sha256": bundle.source_manifest_sha256,
        "source_raw_digest": bundle.source_raw_digest,
        "source_schema_version": bundle.source_schema_version,
        "source_application_version": bundle.source_application_version,
        "application_version": __version__,
        "export_service_version": EXPORT_SERVICE_VERSION,
        "representation": request.representation.value,
        "product": request.product.value,
        "request": request.to_dict(),
        "selected_channels": [series.channel_id for series in bundle.series],
        "channels": [
            {
                "logical_channel_id": series.channel_id,
                "physical_channel": series.physical_channel,
                "sensor_id": series.sensor_id,
                "sensor_model": series.sensor_model,
                "sensor_serial_number": series.sensor_serial_number,
                "label": series.label,
                "raw_unit": series.raw_unit,
                "x_unit": series.x_unit,
                "y_unit": series.y_unit,
                "calibration_id": (
                    series.calibration.calibration_id if series.calibration else None
                ),
                "calibration_hash": (
                    series.calibration.calibration_hash if series.calibration else None
                ),
                "calibration_source": (
                    series.calibration.source_reference if series.calibration else None
                ),
                "calibration_quality": (
                    series.calibration.quality.value if series.calibration else None
                ),
                "calibration_sensitivity_v_per_output_unit": (
                    series.calibration.sensitivity_v_per_output_unit if series.calibration else None
                ),
                "calibration_input_offset_v": (
                    series.calibration.input_offset_v if series.calibration else None
                ),
                "calibration_polarity_multiplier": (
                    series.calibration.polarity_multiplier if series.calibration else None
                ),
                "calibration_effective_date": (
                    series.calibration.effective_date.isoformat()
                    if series.calibration is not None
                    and series.calibration.effective_date is not None
                    else None
                ),
                "processing_disposition": series.processing_disposition,
                "engineering_interpretation": series.engineering_interpretation,
                "warning": series.warning,
                "spectral_input_stage": (
                    series.spectral_input_stage.value if series.spectral_input_stage else None
                ),
            }
            for series in bundle.series
        ],
        "sample_range": request.sample_range.to_dict(),
        "source_sample_count": request.sample_range.sample_count,
        "recipe_id": bundle.recipe_id,
        "recipe_hash": bundle.recipe_hash,
        "recipe": dict(bundle.recipe_details or {}),
        "spectral_details": dict(bundle.spectral_details or {}),
        "decimation": {
            "applied": any(series.decimated for series in bundle.series),
            "algorithm": next(
                (series.decimation_algorithm for series in bundle.series if series.decimated),
                None,
            ),
        },
        "output_filename": output_path.name,
        "output_point_counts": list(output_point_counts),
        "output_byte_count": primary_byte_count,
        "output_sha256": primary_sha256,
        "created_at": created_at.isoformat(),
        "timezone": "UTC",
        "warnings": list(bundle.warnings),
    }
    return metadata


def _artifact(
    request: ExportRequest,
    bundle: PlotBundle,
    job_id: str,
    artifact_id: str,
    output_path: Path,
    byte_count: int,
    sha256: str,
    sidecar_path: Path,
    sidecar_sha256: str,
    created_at: datetime,
) -> ExportArtifact:
    return ExportArtifact(
        artifact_id=artifact_id,
        export_job_id=job_id,
        source_session_id=bundle.source_session_id,
        product=request.product,
        output_format=request.output_format,
        relative_path=output_path.name,
        byte_count=byte_count,
        sha256=sha256,
        sidecar_relative_path=sidecar_path.name,
        sidecar_sha256=sidecar_sha256,
        channel_ids=tuple(series.channel_id for series in bundle.series),
        sample_range=request.sample_range,
        representation=request.representation,
        units=tuple(series.y_unit for series in bundle.series),
        recipe_id=bundle.recipe_id,
        recipe_hash=bundle.recipe_hash,
        calibration_hashes=tuple(
            series.calibration.calibration_hash if series.calibration else None
            for series in bundle.series
        ),
        warnings=bundle.warnings,
        created_at=created_at,
    )


def _resolve_output(request: ExportRequest) -> tuple[Path, str]:
    figure = request.output_format in {ExportFormat.PNG, ExportFormat.SVG}
    destination = request.destination_directory or request.source_session / (
        "plots" if figure else "exports"
    )
    return destination.absolute(), request.base_name or request.product.value


def _figure_kind(product: ExportProduct) -> FigureKind:
    mapping = {
        ExportProduct.TIME_HISTORY_FIGURE: FigureKind.TIME_HISTORY,
        ExportProduct.OVERLAY_FIGURE: FigureKind.OVERLAY,
        ExportProduct.FFT_FIGURE: FigureKind.FFT,
        ExportProduct.PSD_FIGURE: FigureKind.PSD,
    }
    try:
        return mapping[product]
    except KeyError as cause:
        raise ExportError(
            "The export product is not a figure.",
            code="DAQ.EXPORT.REQUEST_INVALID",
            context={"product": product.value},
            cause=cause,
        ) from cause


def _validate_product(request: ExportRequest) -> None:
    expected = {
        ExportProduct.RAW_TIME_DATA: PlotRepresentation.RAW_TIME,
        ExportProduct.CALIBRATED_TIME_DATA: PlotRepresentation.CALIBRATED_TIME,
        ExportProduct.FILTERED_TIME_DATA: PlotRepresentation.FILTERED_TIME,
        ExportProduct.FFT_AMPLITUDE_DATA: PlotRepresentation.FFT_AMPLITUDE,
        ExportProduct.PSD_DATA: PlotRepresentation.PSD,
        ExportProduct.FFT_FIGURE: PlotRepresentation.FFT_AMPLITUDE,
        ExportProduct.PSD_FIGURE: PlotRepresentation.PSD,
    }.get(request.product)
    if expected is not None and request.representation is not expected:
        raise ExportError(
            "The export product and plot representation are incompatible.",
            code="DAQ.EXPORT.REQUEST_INVALID",
            context={
                "product": request.product.value,
                "representation": request.representation.value,
            },
        )
    if request.product in {
        ExportProduct.TIME_HISTORY_FIGURE,
        ExportProduct.OVERLAY_FIGURE,
    } and request.representation not in {
        PlotRepresentation.RAW_TIME,
        PlotRepresentation.CALIBRATED_TIME,
        PlotRepresentation.FILTERED_TIME,
    }:
        raise ExportError(
            "Time-history figures require a time-domain representation.",
            code="DAQ.EXPORT.REQUEST_INVALID",
        )
    if (
        request.product is ExportProduct.TIME_HISTORY_FIGURE
        and len(request.channels.channel_ids) != 1
    ):
        raise ExportError(
            "A time-history figure requires one selected channel.",
            code="DAQ.EXPORT.REQUEST_INVALID",
        )


def _as_export_error(cause: Exception, request: ExportRequest) -> ExportError:
    if isinstance(cause, ExportError):
        return cause
    if isinstance(cause, LabDAQError):
        if cause.code in {"DAQ.PLOT.CANCELLED", "DAQ.PROCESSING.CANCELLED"}:
            return ExportError(
                cause.message,
                code="DAQ.EXPORT.CANCELLED",
                context={"source_code": cause.code, "product": request.product.value},
                cause=cause,
            )
        return ExportError(
            cause.message,
            code="DAQ.EXPORT.PARTIAL_FAILURE",
            context={
                "source_code": cause.code,
                "product": request.product.value,
                "source_session": str(request.source_session),
            },
            remediation=cause.remediation,
            cause=cause,
        )
    return ExportError(
        "An unexpected export operation failed.",
        code="DAQ.EXPORT.PARTIAL_FAILURE",
        context={"exception_type": type(cause).__name__, "product": request.product.value},
        cause=cause,
    )


def _emit(
    callback: Callable[[ExportProgress], None] | None,
    job_id: str,
    state: ExportJobState,
    processed_samples: int,
    requests: Sequence[ExportRequest],
    completed_artifacts: int,
    operation: str,
    artifact_id: str | None = None,
) -> None:
    if callback is None:
        return
    callback(
        ExportProgress(
            export_job_id=job_id,
            artifact_id=artifact_id,
            state=state,
            processed_samples=processed_samples,
            total_samples=sum(request.sample_range.sample_count for request in requests),
            completed_artifacts=completed_artifacts,
            total_artifacts=len(requests),
            operation=operation,
            timestamp=datetime.now(UTC),
        )
    )


def _raise_cancelled(message: str) -> Never:
    raise ExportError(message, code="DAQ.EXPORT.CANCELLED")
