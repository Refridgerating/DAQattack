"""Headless DAQ-M07 plot-data orchestration over M05 replay and M06 processing."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast

from labdaq.application.decimation import StreamingMinMaxDecimator, decimate_xy
from labdaq.application.processing_service import (
    CalibrationResolutionService,
    RecordedSignalContext,
    RecordedSignalStreamService,
)
from labdaq.domain.enums import ProcessingDisposition, SignalStage
from labdaq.domain.models import ProcessingRange
from labdaq.domain.plotting import (
    ChannelSelection,
    PlotRepresentation,
    PlotRequest,
    resolve_sample_range,
)
from labdaq.domain.serialization import canonical_sha256, to_canonical_primitive
from labdaq.errors import PlotError, ProcessingError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    import numpy as np

    from labdaq.domain.plotting import SampleRange
    from labdaq.ports.plotting import PlotBundle, TimeDataChunk
    from labdaq.ports.processing import (
        FftAmplitudeResult,
        ProcessingSourceMetadata,
        PsdResult,
        RecipeRepositoryPort,
        ScientificProcessingPort,
        SessionChannelMetadata,
        SessionProcessingSourcePort,
    )


TimeDataContext = RecordedSignalContext


class PlotDataService:
    """Build bounded, renderer-neutral plot bundles from verified sessions."""

    def __init__(
        self,
        source: SessionProcessingSourcePort,
        recipes: RecipeRepositoryPort,
        numerical: ScientificProcessingPort,
        resolver: CalibrationResolutionService | None = None,
    ) -> None:
        self._source = source
        self._recipes = recipes
        self._numerical = numerical
        self._resolver = resolver or CalibrationResolutionService()
        self._signals = RecordedSignalStreamService(
            source,
            recipes,
            numerical,
            self._resolver,
        )

    def source_metadata(self, path: Path) -> ProcessingSourceMetadata:
        return self._signals.source_metadata(path)

    def resolve_range(
        self,
        path: Path,
        *,
        start_index: int | None = None,
        stop_index: int | None = None,
        start_seconds: float | None = None,
        stop_seconds: float | None = None,
    ) -> SampleRange:
        metadata = self.source_metadata(path)
        try:
            return resolve_sample_range(
                sample_rate_hz=metadata.sample_rate_hz,
                readable_sample_count=metadata.readable_sample_count,
                start_index=start_index,
                stop_index=stop_index,
                start_seconds=start_seconds,
                stop_seconds=stop_seconds,
            )
        except ValueError as cause:
            raise PlotError(
                "The requested plot range is invalid.",
                code="DAQ.PLOT.RANGE_INVALID",
                context={"session_id": metadata.session_id, "reason": str(cause)},
                remediation="Select one non-empty range inside the verified session extent.",
                cause=cause,
            ) from cause

    def build_plot_data(self, request: PlotRequest, *, chunk_size: int = 8192) -> PlotBundle:
        if chunk_size <= 0:
            raise PlotError(
                "Plot replay chunk size must be positive.",
                code="DAQ.PLOT.REQUEST_INVALID",
                remediation="Use a positive bounded chunk size.",
            )
        metadata = self.source_metadata(request.source_session)
        channels = self._validate_request(request, metadata)
        if request.representation in {
            PlotRepresentation.RAW_TIME,
            PlotRepresentation.CALIBRATED_TIME,
            PlotRepresentation.FILTERED_TIME,
        }:
            return self._time_bundle(request, metadata, channels, chunk_size)
        return self._spectral_bundle(request, metadata, channels, chunk_size)

    def prepare_time_data(self, request: PlotRequest) -> TimeDataContext:
        if request.representation not in {
            PlotRepresentation.RAW_TIME,
            PlotRepresentation.CALIBRATED_TIME,
            PlotRepresentation.FILTERED_TIME,
        }:
            raise PlotError(
                "A time-domain representation is required.",
                code="DAQ.PLOT.REQUEST_INVALID",
                remediation="Use raw, calibrated, or filtered time data.",
            )
        metadata = self.source_metadata(request.source_session)
        channels = self._validate_request(request, metadata)
        stage = {
            PlotRepresentation.RAW_TIME: SignalStage.RAW,
            PlotRepresentation.CALIBRATED_TIME: SignalStage.CALIBRATED,
            PlotRepresentation.FILTERED_TIME: SignalStage.CONDITIONED,
        }[request.representation]
        return self._signals.prepare(
            request.source_session,
            ordered_channel_ids=tuple(channel.channel_id for channel in channels),
            signal_stage=stage,
            recipe_id=request.recipe_id if stage is SignalStage.CONDITIONED else None,
        )

    def iter_time_data(
        self,
        request: PlotRequest,
        context: TimeDataContext,
        *,
        chunk_size: int = 8192,
        cancelled: Callable[[], bool] | None = None,
    ) -> Iterator[TimeDataChunk]:
        from labdaq.ports.plotting import TimeDataChunk

        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        try:
            for chunk in self._signals.iter_chunks(
                request.source_session,
                context,
                sample_range=ProcessingRange(
                    request.sample_range.start_index,
                    request.sample_range.stop_index,
                ),
                chunk_size=chunk_size,
                cancelled=cancelled,
            ):
                yield TimeDataChunk(
                    chunk.start_sample_index,
                    chunk.samples,
                    tuple(notice.code for notice in chunk.notices),
                )
        except ProcessingError as cause:
            if cause.code != "DAQ.PROCESSING.CANCELLED":
                raise
            raise PlotError(
                "Plot-data streaming was cancelled.",
                code="DAQ.PLOT.CANCELLED",
                context={"session_id": context.metadata.session_id},
                cause=cause,
            ) from cause

    def _validate_request(
        self,
        request: PlotRequest,
        metadata: ProcessingSourceMetadata,
    ) -> tuple[SessionChannelMetadata, ...]:
        selected_range = request.sample_range
        if (
            selected_range.sample_rate_hz != metadata.sample_rate_hz
            or selected_range.stop_index > metadata.readable_sample_count
        ):
            raise PlotError(
                "The plot range does not match the verified source extent.",
                code="DAQ.PLOT.RANGE_OUT_OF_BOUNDS",
                context={
                    "session_id": metadata.session_id,
                    "stop_index": selected_range.stop_index,
                    "readable_sample_count": metadata.readable_sample_count,
                },
                remediation="Resolve the range against the current verified session metadata.",
            )
        by_id = {channel.channel_id: channel for channel in metadata.channels}
        unknown = [
            channel_id for channel_id in request.channels.channel_ids if channel_id not in by_id
        ]
        if unknown:
            raise PlotError(
                "One or more selected plot channels do not exist.",
                code="DAQ.PLOT.CHANNEL_NOT_FOUND",
                context={"session_id": metadata.session_id, "channel_ids": unknown},
                remediation="Select logical channel identifiers present in the session.",
            )
        selected = tuple(by_id[channel_id] for channel_id in request.channels.channel_ids)
        spectral = request.representation in {
            PlotRepresentation.FFT_AMPLITUDE,
            PlotRepresentation.PSD,
        }
        if (
            metadata.session_type == "raw-diagnostic"
            and request.representation is not PlotRepresentation.RAW_TIME
            and not (spectral and request.spectral_input_stage is SignalStage.RAW)
        ):
            raise PlotError(
                "Raw diagnostic sessions require raw-voltage time or spectral products.",
                code="DAQ.PLOT.RAW_DIAGNOSTIC_SESSION",
                context={
                    "session_id": metadata.session_id,
                    "channel_ids": request.channels.channel_ids,
                },
                remediation="Select raw voltage for time history, FFT, or PSD.",
            )
        raw_only = tuple(
            channel.channel_id
            for channel in selected
            if channel.processing_disposition is ProcessingDisposition.RAW_ONLY_DIAGNOSTIC
        )
        if request.representation is not PlotRepresentation.RAW_TIME and not spectral and raw_only:
            raise PlotError(
                "Raw-only diagnostic channels cannot enter calibrated or conditioned time products.",
                code="DAQ.PLOT.RAW_ONLY_DIAGNOSTIC",
                context={"session_id": metadata.session_id, "channel_ids": raw_only},
                remediation="Use raw-voltage time history or a voltage FFT/PSD.",
            )
        return selected

    def _time_bundle(
        self,
        request: PlotRequest,
        metadata: ProcessingSourceMetadata,
        channels: tuple[SessionChannelMetadata, ...],
        chunk_size: int,
    ) -> PlotBundle:
        import numpy as np

        from labdaq.ports.plotting import PlotBundle, PlotSeries

        ids = request.channels.channel_ids
        context = self.prepare_time_data(request)
        collector = StreamingMinMaxDecimator(
            channel_count=len(ids),
            start_index=request.sample_range.start_index,
            stop_index=request.sample_range.stop_index,
            target_points=request.viewport.max_points,
        )
        warnings = [notice.code for notice in context.notices]
        for chunk in self.iter_time_data(request, context, chunk_size=chunk_size):
            warnings.extend(chunk.notices)
            collector.append(chunk.start_sample_index, chunk.values)
        reduced = collector.finalize()
        calibration_by_id = {item.logical_channel_id: item for item in context.calibrations}
        recipe = context.recipe
        recipe_hash = context.recipe_hash
        labels = request.channels.display_names
        series = []
        for position, (channel, (sample_indices, values)) in enumerate(
            zip(channels, reduced, strict=True)
        ):
            calibration = calibration_by_id.get(channel.channel_id)
            unit = channel.raw_unit if calibration is None else calibration.output_unit
            x = np.asarray(sample_indices / metadata.sample_rate_hz, dtype=np.float64)
            x.flags.writeable = False
            series.append(
                PlotSeries(
                    channel_id=channel.channel_id,
                    physical_channel=channel.physical_channel,
                    sensor_id=channel.sensor_id or None,
                    sensor_model=channel.sensor_model,
                    sensor_serial_number=channel.sensor_serial_number,
                    label=labels[position] if labels else channel.display_name,
                    x_values=x,
                    y_values=values,
                    source_sample_indices=sample_indices,
                    x_unit="s",
                    y_unit=unit.value,
                    raw_unit=channel.raw_unit.value,
                    representation=request.representation,
                    source_range=request.sample_range,
                    output_point_count=len(values),
                    decimated=len(values) != request.sample_range.sample_count,
                    decimation_algorithm=(
                        request.viewport.algorithm.value
                        if len(values) != request.sample_range.sample_count
                        else None
                    ),
                    calibration=calibration,
                    recipe_id=recipe.recipe_id if recipe else None,
                    recipe_hash=recipe_hash,
                    processing_disposition=channel.processing_disposition.value,
                    engineering_interpretation=(
                        None
                        if channel.processing_disposition
                        is ProcessingDisposition.RAW_ONLY_DIAGNOSTIC
                        else channel.engineering_unit.value
                    ),
                    warning=(
                        (
                            "RAW VOLTAGE / SENSOR UNKNOWN"
                            if channel.sensor_id is None
                            else "UNPOWERED / UNQUALIFIED / NOT CHARGE"
                        )
                        if channel.processing_disposition
                        is ProcessingDisposition.RAW_ONLY_DIAGNOSTIC
                        else None
                    ),
                )
            )
        return PlotBundle(
            source_session_id=metadata.session_id,
            source_session_path=metadata.session_path,
            source_manifest_path=metadata.manifest_path,
            source_manifest_sha256=metadata.manifest_sha256,
            source_raw_digest=metadata.raw_data_digest,
            source_schema_version=metadata.source_schema_version,
            source_application_version=metadata.source_application_version,
            representation=request.representation,
            series=tuple(series),
            request_summary=request.to_dict(),
            warnings=tuple(dict.fromkeys(warnings)),
            recipe_id=recipe.recipe_id if recipe else None,
            recipe_hash=recipe_hash,
            recipe_details=(
                cast("dict[str, object]", to_canonical_primitive(recipe)) if recipe else None
            ),
        )

    def _spectral_bundle(
        self,
        request: PlotRequest,
        metadata: ProcessingSourceMetadata,
        channels: tuple[SessionChannelMetadata, ...],
        chunk_size: int,
    ) -> PlotBundle:
        assert request.spectral_input_stage is not None
        groups: dict[SignalStage, list[int]] = {}
        for index, channel in enumerate(channels):
            stage = (
                SignalStage.RAW
                if channel.processing_disposition is ProcessingDisposition.RAW_ONLY_DIAGNOSTIC
                else request.spectral_input_stage
            )
            groups.setdefault(stage, []).append(index)
        bundles = []
        for stage, indices in groups.items():
            group_channels = tuple(channels[index] for index in indices)
            labels = request.channels.display_names
            group_request = replace(
                request,
                channels=ChannelSelection(
                    tuple(channel.channel_id for channel in group_channels),
                    tuple(labels[index] for index in indices) if labels else None,
                ),
                spectral_input_stage=stage,
            )
            bundles.append(
                self._spectral_stage_bundle(group_request, metadata, group_channels, chunk_size)
            )
        by_id = {series.channel_id: series for bundle in bundles for series in bundle.series}
        details = dict(bundles[0].spectral_details or {})
        details["input_stage"] = "mixed" if len(groups) > 1 else next(iter(groups)).value
        return replace(
            bundles[0],
            series=tuple(by_id[channel.channel_id] for channel in channels),
            request_summary=request.to_dict(),
            spectral_details=details,
            warnings=tuple(
                dict.fromkeys(warning for bundle in bundles for warning in bundle.warnings)
            ),
        )

    def _spectral_stage_bundle(
        self,
        request: PlotRequest,
        metadata: ProcessingSourceMetadata,
        channels: tuple[SessionChannelMetadata, ...],
        chunk_size: int,
    ) -> PlotBundle:
        import numpy as np

        from labdaq.ports.plotting import PlotBundle, PlotSeries

        assert request.recipe_id is not None and request.spectral_input_stage is not None
        recipe = self._recipes.load_bundled_processing_recipe(request.recipe_id)
        recipe_hash = canonical_sha256(recipe)
        assert recipe.fft is not None and recipe.psd is not None
        if (
            request.representation is PlotRepresentation.FFT_AMPLITUDE
            and request.sample_range.sample_count > recipe.fft.maximum_samples_per_channel
        ):
            raise ProcessingError(
                "The selected FFT range exceeds the qualified memory bound.",
                code="DAQ.PROCESSING.SPECTRAL_SELECTION_TOO_LARGE",
                context={
                    "sample_count": request.sample_range.sample_count,
                    "maximum_samples": recipe.fft.maximum_samples_per_channel,
                },
                remediation="Select a shorter FFT range.",
            )
        ids = request.channels.channel_ids
        signal_context = self._signals.prepare(
            request.source_session,
            ordered_channel_ids=ids,
            signal_stage=request.spectral_input_stage,
            recipe_id=(
                request.recipe_id
                if request.spectral_input_stage is SignalStage.CONDITIONED
                else None
            ),
        )
        calibrations = signal_context.calibrations
        calibration_by_id = {item.logical_channel_id: item for item in calibrations}
        units = signal_context.output_units
        processing_range = ProcessingRange(
            request.sample_range.start_index,
            request.sample_range.stop_index,
        )
        accumulators = (
            tuple(
                self._numerical.create_welch_accumulator(
                    channel_id=channel.channel_id,
                    unit=unit,
                    sample_range=processing_range,
                    sample_rate_hz=metadata.sample_rate_hz,
                    recipe_hash=recipe_hash,
                    calibration_hash=(
                        calibration_by_id[channel.channel_id].calibration_hash
                        if request.spectral_input_stage is not SignalStage.RAW
                        else None
                    ),
                    segment_duration_seconds=recipe.psd.segment_duration_seconds,
                    overlap_fraction=recipe.psd.overlap_fraction,
                    signal_stage=request.spectral_input_stage,
                )
                for channel, unit in zip(channels, units, strict=True)
            )
            if request.representation is PlotRepresentation.PSD
            else ()
        )
        parts: list[list[np.ndarray]] = [[] for _ in channels]
        warnings = [notice.code for notice in signal_context.notices]
        for chunk in self._signals.iter_chunks(
            request.source_session,
            signal_context,
            sample_range=processing_range,
            chunk_size=chunk_size,
        ):
            warnings.extend(notice.code for notice in chunk.notices)
            for index, vector in enumerate(chunk.samples):
                if accumulators:
                    accumulators[index].append(vector)
                else:
                    parts[index].append(np.array(vector, copy=True))
        products: tuple[FftAmplitudeResult | PsdResult, ...]
        if accumulators:
            products = tuple(accumulator.finalize() for accumulator in accumulators)
        else:
            products = tuple(
                self._numerical.fft_amplitude(
                    np.concatenate(parts[index]),
                    channel_id=channel.channel_id,
                    unit=units[index],
                    sample_range=processing_range,
                    sample_rate_hz=metadata.sample_rate_hz,
                    recipe_hash=recipe_hash,
                    calibration_hash=(
                        calibration_by_id[channel.channel_id].calibration_hash
                        if request.spectral_input_stage is not SignalStage.RAW
                        else None
                    ),
                    maximum_samples=recipe.fft.maximum_samples_per_channel,
                    signal_stage=request.spectral_input_stage,
                )
                for index, channel in enumerate(channels)
            )
        labels = request.channels.display_names
        series = []
        from labdaq.ports.processing import PsdResult

        for position, (channel, product) in enumerate(zip(channels, products, strict=True)):
            source_x = product.frequencies_hz
            source_y = product.density if isinstance(product, PsdResult) else product.amplitudes
            x, y, decimated = decimate_xy(
                source_x,
                source_y,
                target_points=request.viewport.max_points,
            )
            unit = product.density_unit if isinstance(product, PsdResult) else product.unit.value
            series.append(
                PlotSeries(
                    channel_id=channel.channel_id,
                    physical_channel=channel.physical_channel,
                    sensor_id=channel.sensor_id or None,
                    sensor_model=channel.sensor_model,
                    sensor_serial_number=channel.sensor_serial_number,
                    label=(labels[position] if labels else channel.display_name)
                    + (
                        " — Raw voltage"
                        if channel.processing_disposition
                        is ProcessingDisposition.RAW_ONLY_DIAGNOSTIC
                        else ""
                    ),
                    x_values=x,
                    y_values=y,
                    x_unit="Hz",
                    y_unit=unit,
                    raw_unit=channel.raw_unit.value,
                    representation=request.representation,
                    source_range=request.sample_range,
                    output_point_count=len(y),
                    decimated=decimated,
                    decimation_algorithm=request.viewport.algorithm.value if decimated else None,
                    calibration=calibration_by_id.get(channel.channel_id),
                    recipe_id=recipe.recipe_id,
                    recipe_hash=recipe_hash,
                    spectral_input_stage=request.spectral_input_stage,
                    processing_disposition=channel.processing_disposition.value,
                    engineering_interpretation=(
                        None
                        if channel.processing_disposition
                        is ProcessingDisposition.RAW_ONLY_DIAGNOSTIC
                        or metadata.session_type == "raw-diagnostic"
                        else channel.engineering_unit.value
                    ),
                    warning=(
                        (
                            "RAW VOLTAGE / SENSOR UNKNOWN"
                            if channel.sensor_id is None
                            else "UNPOWERED / UNQUALIFIED / NOT CHARGE"
                        )
                        if channel.processing_disposition
                        is ProcessingDisposition.RAW_ONLY_DIAGNOSTIC
                        else "RAW DIAGNOSTIC / UNQUALIFIED"
                        if metadata.session_type == "raw-diagnostic"
                        else None
                    ),
                )
            )
        first = products[0]
        details: dict[str, object] = {
            "input_stage": request.spectral_input_stage.value,
            "window": first.window,
            "detrend": first.detrend,
        }
        if not isinstance(first, PsdResult):
            details["normalization"] = first.normalization
        else:
            details.update(
                {
                    "scaling": first.scaling,
                    "segment_sample_count": first.segment_sample_count,
                    "overlap_sample_count": first.overlap_sample_count,
                }
            )
        return PlotBundle(
            source_session_id=metadata.session_id,
            source_session_path=metadata.session_path,
            source_manifest_path=metadata.manifest_path,
            source_manifest_sha256=metadata.manifest_sha256,
            source_raw_digest=metadata.raw_data_digest,
            source_schema_version=metadata.source_schema_version,
            source_application_version=metadata.source_application_version,
            representation=request.representation,
            series=tuple(series),
            request_summary=request.to_dict(),
            warnings=tuple(dict.fromkeys(warnings)),
            recipe_id=recipe.recipe_id,
            recipe_hash=recipe_hash,
            recipe_details=cast("dict[str, object]", to_canonical_primitive(recipe)),
            spectral_details=details,
        )
