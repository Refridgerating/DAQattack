"""Application orchestration for calibration and reproducible processing."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import ceil
from typing import TYPE_CHECKING, Never

from labdaq.domain.enums import (
    CalibrationQuality,
    CalibrationSourceKind,
    Polarity,
    ProcessingDisposition,
    ProcessingMode,
    SignalStage,
    Unit,
)
from labdaq.domain.models import (
    ProcessingNotice,
    ProcessingProvenance,
    ProcessingRange,
    ResolvedChannelCalibration,
)
from labdaq.domain.serialization import canonical_sha256
from labdaq.errors import ErrorSeverity, ProcessingError
from labdaq.version import __version__

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping
    from pathlib import Path

    import numpy as np

    from labdaq.domain.models import (
        CalibrationConfig,
        ChannelConfig,
        ExperimentConfig,
        HardwareProfile,
        NominalCalibrationFallbackPolicy,
        ProcessingRecipeEnvelope,
        SensorConfig,
    )
    from labdaq.ports.acquisition import AcquisitionChunk
    from labdaq.ports.processing import (
        ChannelExtremaResult,
        LocatedExtremum,
        ProcessedSessionSummary,
        ProcessingSourceMetadata,
        RecipeRepositoryPort,
        RecordedSignalChunk,
        ScientificProcessingPort,
        SessionChannelMetadata,
        SessionProcessingSourcePort,
        StreamingProcessorPort,
        TransientDetectionResult,
        TransientDetectionSettings,
    )


class CalibrationResolutionService:
    """Resolve channel-linked configuration into hash-addressed calibrations."""

    def resolve_experiment(
        self,
        profile: HardwareProfile,
        experiment: ExperimentConfig,
        ordered_channel_ids: tuple[str, ...],
    ) -> tuple[tuple[ResolvedChannelCalibration, ...], tuple[ProcessingNotice, ...]]:
        channels = {channel.channel_id: channel for channel in experiment.channels}
        sensors = {sensor.sensor_id: sensor for sensor in experiment.sensors}
        calibrations = {
            calibration.calibration_id: calibration for calibration in experiment.calibrations
        }
        resolved = []
        notices: list[ProcessingNotice] = []
        failures: list[dict[str, object]] = []
        for channel_id in ordered_channel_ids:
            channel = channels.get(channel_id)
            if channel is None:
                failures.append({"channel_id": channel_id, "reason": "channel is missing"})
                continue
            if channel.processing_disposition is ProcessingDisposition.RAW_ONLY_DIAGNOSTIC:
                continue
            sensor = sensors.get(channel.sensor_id) if channel.sensor_id else None
            calibration = (
                calibrations.get(channel.calibration_id)
                if channel.calibration_id is not None
                else None
            )
            if sensor is None or calibration is None:
                failures.append(
                    {"channel_id": channel_id, "reason": "sensor or calibration is missing"}
                )
                continue
            try:
                item, item_notices = self._resolve_one(
                    channel,
                    sensor,
                    calibration,
                    profile.nominal_calibration_fallback,
                )
            except ProcessingError as error:
                failures.append(
                    {"channel_id": channel_id, "code": error.code, "reason": error.message}
                )
                continue
            resolved.append(item)
            notices.extend(item_notices)
        if failures:
            raise ProcessingError(
                "One or more channel calibrations could not be resolved.",
                code="DAQ.PROCESSING.CALIBRATION_INVALID",
                context={"channel_failures": failures},
                remediation="Supply explicit offsets, spans, sensitivity, polarity, and provenance for every channel.",
            )
        return tuple(resolved), tuple(notices)

    def resolve_source(
        self,
        metadata: ProcessingSourceMetadata,
        ordered_channel_ids: tuple[str, ...] | None = None,
    ) -> tuple[tuple[ResolvedChannelCalibration, ...], tuple[ProcessingNotice, ...]]:
        resolved = []
        notices: list[ProcessingNotice] = []
        failures: list[dict[str, object]] = []
        by_id = {channel.channel_id: channel for channel in metadata.channels}
        selected_ids = (
            tuple(
                channel.channel_id
                for channel in metadata.channels
                if channel.processing_disposition is ProcessingDisposition.CALIBRATED
            )
            if ordered_channel_ids is None
            else ordered_channel_ids
        )
        unknown = [channel_id for channel_id in selected_ids if channel_id not in by_id]
        if unknown:
            raise ProcessingError(
                "One or more selected channels do not exist in the recorded session.",
                code="DAQ.PROCESSING.CALIBRATION_INVALID",
                context={"session_id": metadata.session_id, "channel_ids": unknown},
                remediation="Select logical channel identifiers present in the source session.",
            )
        for channel_id in selected_ids:
            channel = by_id[channel_id]
            if channel.processing_disposition is ProcessingDisposition.RAW_ONLY_DIAGNOSTIC:
                failures.append(
                    {
                        "channel_id": channel.channel_id,
                        "code": "DAQ.PROCESSING.RAW_ONLY_DIAGNOSTIC",
                        "reason": "raw-only diagnostic channels cannot enter derived processing",
                    }
                )
                continue
            try:
                item, item_notices = self._resolve_source_channel(
                    channel,
                    metadata.nominal_calibration_fallback,
                )
            except ProcessingError as error:
                failures.append(
                    {
                        "channel_id": channel.channel_id,
                        "code": error.code,
                        "reason": error.message,
                    }
                )
                continue
            resolved.append(item)
            notices.extend(item_notices)
        if failures:
            raise ProcessingError(
                "Recorded-session calibrations are incomplete or invalid.",
                code="DAQ.PROCESSING.CALIBRATION_INVALID",
                context={"session_id": metadata.session_id, "channel_failures": failures},
                remediation="Use a session recorded with complete M06 calibration metadata.",
            )
        return tuple(resolved), tuple(notices)

    def _resolve_source_channel(
        self,
        channel: SessionChannelMetadata,
        fallback: NominalCalibrationFallbackPolicy | None,
    ) -> tuple[ResolvedChannelCalibration, tuple[ProcessingNotice, ...]]:
        if channel.calibration is None:
            raise ProcessingError(
                "A calibrated channel has no calibration record.",
                code="DAQ.PROCESSING.CALIBRATION_INVALID",
                context={"channel_id": channel.channel_id},
            )

        class _Channel:
            channel_id = channel.channel_id
            channel_role = channel.channel_role
            engineering_unit = channel.engineering_unit

        class _Sensor:
            sensor_id = channel.sensor_id
            model = channel.sensor_model
            serial_number = channel.sensor_serial_number

        return self._resolve_one(_Channel, _Sensor, channel.calibration, fallback)  # type: ignore[arg-type]

    def _resolve_one(
        self,
        channel: ChannelConfig,
        sensor: SensorConfig,
        calibration: CalibrationConfig,
        fallback: NominalCalibrationFallbackPolicy | None,
    ) -> tuple[ResolvedChannelCalibration, tuple[ProcessingNotice, ...]]:
        if (
            calibration.input_unit is not Unit.VOLT
            or calibration.output_unit is not channel.engineering_unit
        ):
            self._calibration_fault(
                "Calibration units do not match the channel.",
                "CALIBRATION_CHANNEL_MISMATCH",
                channel.channel_id,
            )
        if calibration.input_offset is None:
            self._calibration_fault(
                "Calibration input offset is missing.", "CALIBRATION_INVALID", channel.channel_id
            )
        quality = _quality(calibration.source_kind)
        notices: tuple[ProcessingNotice, ...] = ()
        if quality is CalibrationQuality.NOMINAL:
            if (
                fallback is None
                or sensor.model != fallback.sensor_model
                or calibration.sensitivity_value != fallback.sensitivity_value
                or calibration.sensitivity_unit is not fallback.sensitivity_unit
                or channel.channel_role not in fallback.permitted_channel_roles
            ):
                self._calibration_fault(
                    "Nominal calibration fallback is not explicitly permitted by the hardware profile.",
                    "CALIBRATION_INVALID",
                    channel.channel_id,
                )
            notices = (
                ProcessingNotice(
                    code="DAQ.PROCESSING.NOMINAL_CALIBRATION_FALLBACK",
                    severity=ErrorSeverity.WARNING.value,
                    message="Manufacturer nominal sensitivity is being used instead of certificate data.",
                    channel_id=channel.channel_id,
                ),
            )
        multiplier = 0.001 if calibration.sensitivity_unit.value.startswith("mV/") else 1.0
        sensitivity = calibration.sensitivity_value * multiplier
        content = {
            "schema_version": 1,
            "calibration_id": calibration.calibration_id,
            "logical_channel_id": channel.channel_id,
            "channel_role": channel.channel_role.value,
            "sensor_id": sensor.sensor_id,
            "sensor_model": sensor.model,
            "sensor_serial_number": sensor.serial_number,
            "input_unit": calibration.input_unit.value,
            "output_unit": calibration.output_unit.value,
            "sensitivity_v_per_output_unit": sensitivity,
            "input_offset_v": calibration.input_offset,
            "polarity_multiplier": 1 if calibration.polarity is Polarity.POSITIVE else -1,
            "valid_input_min_v": calibration.valid_input_min,
            "valid_input_max_v": calibration.valid_input_max,
            "valid_output_min": calibration.valid_output_min,
            "valid_output_max": calibration.valid_output_max,
            "validated_frequency_min_hz": calibration.validated_frequency_min_hz,
            "validated_frequency_max_hz": calibration.validated_frequency_max_hz,
            "extended_frequency_min_hz": calibration.extended_frequency_min_hz,
            "extended_frequency_max_hz": calibration.extended_frequency_max_hz,
            "uncertainty": calibration.uncertainty,
            "notes": calibration.notes,
            "quality": quality.value,
            "source_kind": calibration.source_kind.value,
            "source_reference": calibration.source_reference,
            "effective_date": calibration.effective_date.isoformat()
            if calibration.effective_date is not None
            else None,
            "expiration_date": calibration.expiration_date.isoformat()
            if calibration.expiration_date
            else None,
            "certificate_identifier": calibration.certificate_identifier,
            "conditions": calibration.conditions,
        }
        resolved = ResolvedChannelCalibration(
            schema_version=1,
            calibration_id=calibration.calibration_id,
            calibration_hash=canonical_sha256(content),
            logical_channel_id=channel.channel_id,
            channel_role=channel.channel_role,
            sensor_id=sensor.sensor_id,
            sensor_model=sensor.model,
            sensor_serial_number=sensor.serial_number,
            input_unit=calibration.input_unit,
            output_unit=calibration.output_unit,
            sensitivity_v_per_output_unit=sensitivity,
            input_offset_v=calibration.input_offset,
            polarity_multiplier=1 if calibration.polarity is Polarity.POSITIVE else -1,
            valid_input_min_v=calibration.valid_input_min,
            valid_input_max_v=calibration.valid_input_max,
            valid_output_min=calibration.valid_output_min,
            valid_output_max=calibration.valid_output_max,
            validated_frequency_min_hz=calibration.validated_frequency_min_hz,
            validated_frequency_max_hz=calibration.validated_frequency_max_hz,
            extended_frequency_min_hz=calibration.extended_frequency_min_hz,
            extended_frequency_max_hz=calibration.extended_frequency_max_hz,
            uncertainty=calibration.uncertainty,
            notes=calibration.notes,
            quality=quality,
            source_kind=calibration.source_kind,
            source_reference=calibration.source_reference,
            effective_date=calibration.effective_date,
            expiration_date=calibration.expiration_date,
            certificate_identifier=calibration.certificate_identifier,
            conditions=calibration.conditions,
        )
        return resolved, notices

    @staticmethod
    def _calibration_fault(message: str, suffix: str, channel_id: str) -> Never:
        raise ProcessingError(
            message,
            code=f"DAQ.PROCESSING.{suffix}",
            context={"channel_id": channel_id},
            remediation="Correct the channel-linked calibration record.",
        )


class StreamingProcessingService:
    def __init__(
        self,
        numerical: ScientificProcessingPort,
        resolver: CalibrationResolutionService | None = None,
    ) -> None:
        self._numerical = numerical
        self._resolver = resolver or CalibrationResolutionService()

    def create(
        self,
        profile: HardwareProfile,
        experiment: ExperimentConfig,
        recipe: ProcessingRecipeEnvelope,
        *,
        ordered_channel_ids: tuple[str, ...],
        sample_rate_hz: float,
        mode: ProcessingMode = ProcessingMode.LIVE,
        expected_recipe_hash: str | None = None,
        expected_calibration_hashes: tuple[str, ...] | None = None,
    ) -> tuple[StreamingProcessorPort, tuple[ProcessingNotice, ...]]:
        calibrations, notices = self._resolver.resolve_experiment(
            profile,
            experiment,
            ordered_channel_ids,
        )
        recipe_hash = canonical_sha256(recipe)
        if expected_recipe_hash is not None and expected_recipe_hash != recipe_hash:
            raise ProcessingError(
                "The selected recipe does not match the expected processing context.",
                code="DAQ.PROCESSING.RECIPE_HASH_MISMATCH",
                context={
                    "expected_recipe_hash": expected_recipe_hash,
                    "received_recipe_hash": recipe_hash,
                },
                remediation="Create a new processor with the intended immutable recipe.",
            )
        calibration_hashes = tuple(item.calibration_hash for item in calibrations)
        if (
            expected_calibration_hashes is not None
            and expected_calibration_hashes != calibration_hashes
        ):
            raise ProcessingError(
                "Resolved calibrations do not match the expected processing context.",
                code="DAQ.PROCESSING.CALIBRATION_INVALID",
                context={
                    "expected_calibration_hashes": expected_calibration_hashes,
                    "received_calibration_hashes": calibration_hashes,
                },
                remediation="Create a new processor after resolving the intended calibrations.",
            )
        return (
            self._numerical.create_stream(
                recipe,
                calibrations,
                sample_rate_hz=sample_rate_hz,
                mode=mode,
            ),
            notices,
        )


@dataclass(frozen=True, slots=True)
class RecordedSignalContext:
    """Resolved, immutable context for a stage-specific recorded signal stream."""

    metadata: ProcessingSourceMetadata
    channels: tuple[SessionChannelMetadata, ...]
    signal_stage: SignalStage
    calibrations: tuple[ResolvedChannelCalibration, ...]
    output_units: tuple[Unit, ...]
    recipe: ProcessingRecipeEnvelope | None
    recipe_hash: str | None
    notices: tuple[ProcessingNotice, ...]


class RecordedSignalStreamService:
    """Yield selected replay spans without duplicating M06 stage semantics."""

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

    def source_metadata(self, path: Path) -> ProcessingSourceMetadata:
        return self._source.processing_metadata(path)

    def prepare(
        self,
        path: Path,
        *,
        ordered_channel_ids: tuple[str, ...],
        signal_stage: SignalStage,
        recipe_id: str | None = None,
    ) -> RecordedSignalContext:
        metadata = self.source_metadata(path)
        if metadata.session_type == "raw-diagnostic" and signal_stage is not SignalStage.RAW:
            raise ProcessingError(
                "Raw diagnostic sessions do not authorize derived processing.",
                code="DAQ.PROCESSING.RAW_DIAGNOSTIC_SESSION",
                context={"session_id": metadata.session_id},
                remediation="Use immutable raw-voltage replay only.",
            )
        by_id = {channel.channel_id: channel for channel in metadata.channels}
        unknown = [channel_id for channel_id in ordered_channel_ids if channel_id not in by_id]
        if not ordered_channel_ids or unknown:
            raise ProcessingError(
                "The recorded-signal channel selection is invalid.",
                code="DAQ.PROCESSING.CHANNEL_NOT_FOUND",
                context={"session_id": metadata.session_id, "channel_ids": unknown},
                remediation="Select logical channel identifiers present in the source session.",
            )
        channels = tuple(by_id[channel_id] for channel_id in ordered_channel_ids)
        calibrations: tuple[ResolvedChannelCalibration, ...] = ()
        notices: tuple[ProcessingNotice, ...] = ()
        if signal_stage is not SignalStage.RAW:
            calibrations, notices = self._resolver.resolve_source(metadata, ordered_channel_ids)
        calibration_by_id = {item.logical_channel_id: item for item in calibrations}
        output_units = tuple(
            channel.raw_unit
            if signal_stage is SignalStage.RAW
            else calibration_by_id[channel.channel_id].output_unit
            for channel in channels
        )
        recipe = None
        recipe_hash = None
        if signal_stage is SignalStage.CONDITIONED:
            if recipe_id is None:
                raise ProcessingError(
                    "Conditioned replay requires an explicit processing recipe.",
                    code="DAQ.PROCESSING.RECIPE_REQUIRED",
                    remediation="Select the qualified baseline recipe explicitly.",
                )
            recipe = self._recipes.load_bundled_processing_recipe(recipe_id)
            recipe_hash = canonical_sha256(recipe)
        return RecordedSignalContext(
            metadata=metadata,
            channels=channels,
            signal_stage=signal_stage,
            calibrations=calibrations,
            output_units=output_units,
            recipe=recipe,
            recipe_hash=recipe_hash,
            notices=notices,
        )

    def iter_chunks(
        self,
        path: Path,
        context: RecordedSignalContext,
        *,
        sample_range: ProcessingRange,
        chunk_size: int = 8192,
        cancelled: Callable[[], bool] | None = None,
    ) -> Iterator[RecordedSignalChunk]:
        from labdaq.ports.processing import RecordedSignalChunk

        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if sample_range.stop_sample_index > context.metadata.readable_sample_count:
            raise ProcessingError(
                "The recorded-signal range exceeds the verified source prefix.",
                code="DAQ.PROCESSING.PROCESSING_RANGE_INVALID",
                context={
                    "stop_sample": sample_range.stop_sample_index,
                    "readable_sample_count": context.metadata.readable_sample_count,
                },
            )
        cancellation = cancelled or (lambda: False)
        positions = tuple(
            context.metadata.ordered_channel_ids.index(channel.channel_id)
            for channel in context.channels
        )
        processor = None
        if context.signal_stage is SignalStage.CONDITIONED:
            assert context.recipe is not None
            processor = self._numerical.create_stream(
                context.recipe,
                context.calibrations,
                sample_rate_hz=context.metadata.sample_rate_hz,
                mode=ProcessingMode.REPLAY,
            )
        replay = self._source.open_replay(path, chunk_size=chunk_size)
        try:
            for source_chunk in replay:
                if cancellation():
                    replay.cancel()
                    raise ProcessingError(
                        "Recorded-signal streaming was cancelled.",
                        code="DAQ.PROCESSING.CANCELLED",
                        context={"session_id": context.metadata.session_id},
                    )
                selected = _select_recorded_chunk(
                    source_chunk,
                    positions,
                    tuple(channel.channel_id for channel in context.channels),
                )
                processed = processor.process(selected) if processor else None
                chunk_stop = source_chunk.start_sample_index + source_chunk.sample_count
                overlap_start = max(
                    sample_range.start_sample_index,
                    source_chunk.start_sample_index,
                )
                overlap_stop = min(sample_range.stop_sample_index, chunk_stop)
                if overlap_stop > overlap_start:
                    left = overlap_start - source_chunk.start_sample_index
                    right = overlap_stop - source_chunk.start_sample_index
                    raw = _readonly_matrix(selected.samples[:, left:right])
                    calibrated = None
                    conditioned = None
                    notices: tuple[ProcessingNotice, ...] = ()
                    if context.signal_stage is SignalStage.CALIBRATED:
                        calibrated = _readonly_matrix(
                            self._numerical.calibrate_samples(raw, context.calibrations)
                        )
                    elif context.signal_stage is SignalStage.CONDITIONED:
                        assert processed is not None
                        calibrated = _readonly_matrix(processed.calibrated_samples[:, left:right])
                        conditioned = _readonly_matrix(processed.conditioned_samples[:, left:right])
                        notices = processed.notices
                    yield RecordedSignalChunk(
                        session_id=context.metadata.session_id,
                        sequence_number=source_chunk.sequence_number,
                        start_sample_index=overlap_start,
                        sample_count=overlap_stop - overlap_start,
                        sample_rate_hz=context.metadata.sample_rate_hz,
                        ordered_channel_ids=tuple(
                            channel.channel_id for channel in context.channels
                        ),
                        signal_stage=context.signal_stage,
                        output_units=context.output_units,
                        raw_samples=raw,
                        calibrated_samples=calibrated,
                        conditioned_samples=conditioned,
                        notices=notices,
                    )
                if chunk_stop >= sample_range.stop_sample_index:
                    replay.cancel()
                    break
        finally:
            replay.close()


@dataclass(slots=True)
class _DetectedSpan:
    start: int
    stop: int
    peak_sample_index: int
    peak_deviation: float


class RecordedSessionAnalysisService:
    """Bounded, non-mutating review analysis over a verified session."""

    _BASELINE_SAMPLE_LIMIT = 262_144

    def __init__(
        self,
        source: SessionProcessingSourcePort,
        recipes: RecipeRepositoryPort,
        numerical: ScientificProcessingPort,
        resolver: CalibrationResolutionService | None = None,
    ) -> None:
        self._signals = RecordedSignalStreamService(source, recipes, numerical, resolver)

    def detect_transients(
        self,
        path: Path,
        *,
        ordered_channel_ids: tuple[str, ...],
        signal_stage: SignalStage,
        settings: TransientDetectionSettings,
        recipe_id: str = "baseline-v1",
        chunk_size: int = 8192,
        cancelled: Callable[[], bool] | None = None,
    ) -> TransientDetectionResult:
        import numpy as np

        from labdaq.ports.processing import (
            DetectedTransientEvent,
            TransientChannelThreshold,
            TransientDetectionResult,
        )

        context = self._signals.prepare(
            path,
            ordered_channel_ids=ordered_channel_ids,
            signal_stage=signal_stage,
            recipe_id=recipe_id if signal_stage is SignalStage.CONDITIONED else None,
        )
        source_range = ProcessingRange(0, context.metadata.readable_sample_count)
        sample_indices = _baseline_sample_indices(
            source_range.sample_count,
            self._BASELINE_SAMPLE_LIMIT,
        )
        sampled: list[list[np.ndarray]] = [[] for _ in context.channels]
        notices = list(context.notices)
        for chunk in self._signals.iter_chunks(
            path,
            context,
            sample_range=source_range,
            chunk_size=chunk_size,
            cancelled=cancelled,
        ):
            notices.extend(chunk.notices)
            left = int(np.searchsorted(sample_indices, chunk.start_sample_index, side="left"))
            stop = chunk.start_sample_index + chunk.sample_count
            right = int(np.searchsorted(sample_indices, stop, side="left"))
            if right <= left:
                continue
            offsets = sample_indices[left:right] - chunk.start_sample_index
            for position, values in enumerate(chunk.samples):
                sampled[position].append(np.asarray(values[offsets], dtype=np.float64))

        baselines: list[float | None] = []
        scales: list[float | None] = []
        threshold_results = []
        for channel, unit, parts in zip(
            context.channels,
            context.output_units,
            sampled,
            strict=True,
        ):
            values = np.concatenate(parts) if parts else np.empty(0, dtype=np.float64)
            if not len(values) or not np.all(np.isfinite(values)):
                reason = "signal contains no finite baseline sample"
                baselines.append(None)
                scales.append(None)
                threshold_results.append(
                    TransientChannelThreshold(channel.channel_id, unit, None, None, None, reason)
                )
                continue
            baseline = float(np.median(values))
            scale = float(1.4826 * np.median(np.abs(values - baseline)))
            baselines.append(baseline)
            scales.append(scale)
            threshold_results.append(
                TransientChannelThreshold(
                    channel.channel_id,
                    unit,
                    baseline,
                    scale,
                    settings.mad_multiplier * scale,
                )
            )

        gap_samples = ceil(settings.merge_gap_seconds * context.metadata.sample_rate_hz)
        spans: list[list[_DetectedSpan]] = [[] for _ in context.channels]
        invalid_channels: set[int] = set()
        for chunk in self._signals.iter_chunks(
            path,
            context,
            sample_range=source_range,
            chunk_size=chunk_size,
            cancelled=cancelled,
        ):
            notices.extend(chunk.notices)
            for position, values in enumerate(chunk.samples):
                baseline_value = baselines[position]
                scale_value = scales[position]
                if baseline_value is None or scale_value is None or position in invalid_channels:
                    continue
                if not np.all(np.isfinite(values)):
                    invalid_channels.add(position)
                    spans[position].clear()
                    continue
                deviations = np.abs(values - baseline_value)
                crossings = (
                    deviations > 0
                    if scale_value == 0
                    else deviations >= settings.mad_multiplier * scale_value
                )
                _append_crossing_runs(
                    spans[position],
                    crossings,
                    deviations,
                    chunk.start_sample_index,
                    gap_samples,
                )

        for position in invalid_channels:
            channel = context.channels[position]
            threshold_results[position] = TransientChannelThreshold(
                channel.channel_id,
                context.output_units[position],
                None,
                None,
                None,
                "signal contains non-finite samples",
            )

        pre_samples = ceil(settings.pre_padding_seconds * context.metadata.sample_rate_hz)
        post_samples = ceil(settings.post_padding_seconds * context.metadata.sample_rate_hz)
        events = []
        for position, channel in enumerate(context.channels):
            baseline_value = baselines[position]
            scale_value = scales[position]
            if baseline_value is None or scale_value is None or position in invalid_channels:
                continue
            padded = _pad_and_merge_spans(
                spans[position],
                pre_samples,
                post_samples,
                source_range.stop_sample_index,
            )
            for span, analysis_start, analysis_stop in padded:
                score = span.peak_deviation / scale_value if scale_value > 0 else float("inf")
                events.append(
                    DetectedTransientEvent(
                        channel_id=channel.channel_id,
                        unit=context.output_units[position],
                        crossing_range=ProcessingRange(span.start, span.stop),
                        analysis_range=ProcessingRange(analysis_start, analysis_stop),
                        peak_sample_index=span.peak_sample_index,
                        peak_time_seconds=(
                            span.peak_sample_index / context.metadata.sample_rate_hz
                        ),
                        peak_deviation=span.peak_deviation,
                        normalized_peak_score=score,
                    )
                )
        channel_order = {channel_id: index for index, channel_id in enumerate(ordered_channel_ids)}
        events.sort(
            key=lambda item: (
                item.analysis_range.start_sample_index,
                channel_order[item.channel_id],
            )
        )
        return TransientDetectionResult(
            signal_stage=signal_stage,
            sample_range=source_range,
            sample_rate_hz=context.metadata.sample_rate_hz,
            settings=settings,
            thresholds=tuple(threshold_results),
            events=tuple(events),
            notices=_unique_notices(notices),
        )

    def compute_extrema(
        self,
        path: Path,
        *,
        ordered_channel_ids: tuple[str, ...],
        signal_stage: SignalStage,
        sample_range: ProcessingRange,
        recipe_id: str = "baseline-v1",
        chunk_size: int = 8192,
        cancelled: Callable[[], bool] | None = None,
    ) -> tuple[ChannelExtremaResult, ...]:
        import numpy as np

        from labdaq.domain.enums import MetricAvailability
        from labdaq.ports.processing import ChannelExtremaResult, LocatedExtremum

        context = self._signals.prepare(
            path,
            ordered_channel_ids=ordered_channel_ids,
            signal_stage=signal_stage,
            recipe_id=recipe_id if signal_stage is SignalStage.CONDITIONED else None,
        )
        maxima: list[tuple[float, int] | None] = [None] * len(context.channels)
        minima: list[tuple[float, int] | None] = [None] * len(context.channels)
        absolute: list[tuple[float, int] | None] = [None] * len(context.channels)
        invalid = [False] * len(context.channels)
        notices = list(context.notices)
        for chunk in self._signals.iter_chunks(
            path,
            context,
            sample_range=sample_range,
            chunk_size=chunk_size,
            cancelled=cancelled,
        ):
            notices.extend(chunk.notices)
            for position, values in enumerate(chunk.samples):
                if invalid[position]:
                    continue
                if not np.all(np.isfinite(values)):
                    invalid[position] = True
                    continue
                maximum_offset = int(np.argmax(values))
                minimum_offset = int(np.argmin(values))
                absolute_offset = int(np.argmax(np.abs(values)))
                maximum = (float(values[maximum_offset]), chunk.start_sample_index + maximum_offset)
                minimum = (float(values[minimum_offset]), chunk.start_sample_index + minimum_offset)
                peak = (float(values[absolute_offset]), chunk.start_sample_index + absolute_offset)
                previous_maximum = maxima[position]
                previous_minimum = minima[position]
                previous_absolute = absolute[position]
                if previous_maximum is None or maximum[0] > previous_maximum[0]:
                    maxima[position] = maximum
                if previous_minimum is None or minimum[0] < previous_minimum[0]:
                    minima[position] = minimum
                if previous_absolute is None or abs(peak[0]) > abs(previous_absolute[0]):
                    absolute[position] = peak

        common_notices = _unique_notices(notices)
        results = []
        for position, (channel, unit) in enumerate(
            zip(context.channels, context.output_units, strict=True)
        ):
            if invalid[position] or maxima[position] is None:
                reason = (
                    "signal contains non-finite samples"
                    if invalid[position]
                    else "signal contains no samples"
                )
                unavailable = LocatedExtremum(
                    MetricAvailability.UNAVAILABLE,
                    None,
                    None,
                    None,
                    None,
                    reason,
                )
                maximum_result = minimum_result = absolute_result = unavailable
            else:
                maximum_value = maxima[position]
                minimum_value = minima[position]
                absolute_value = absolute[position]
                assert (
                    maximum_value is not None
                    and minimum_value is not None
                    and absolute_value is not None
                )
                maximum_result = _located_extremum(
                    maximum_value,
                    context.metadata.sample_rate_hz,
                )
                minimum_result = _located_extremum(
                    minimum_value,
                    context.metadata.sample_rate_hz,
                )
                absolute_result = _located_extremum(
                    absolute_value,
                    context.metadata.sample_rate_hz,
                )
            results.append(
                ChannelExtremaResult(
                    channel_id=channel.channel_id,
                    unit=unit,
                    signal_stage=signal_stage,
                    sample_range=sample_range,
                    sample_rate_hz=context.metadata.sample_rate_hz,
                    maximum=maximum_result,
                    minimum=minimum_result,
                    absolute_peak=absolute_result,
                    notices=common_notices,
                )
            )
        return tuple(results)


def _baseline_sample_indices(sample_count: int, limit: int) -> np.ndarray:
    import numpy as np

    count = min(sample_count, limit)
    if count <= 0:
        return np.empty(0, dtype=np.int64)
    if count == 1:
        return np.array([0], dtype=np.int64)
    return (np.arange(count, dtype=np.int64) * (sample_count - 1)) // (count - 1)


def _append_crossing_runs(
    spans: list[_DetectedSpan],
    crossings: np.ndarray,
    deviations: np.ndarray,
    start_sample_index: int,
    gap_samples: int,
) -> None:
    import numpy as np

    positions = np.flatnonzero(crossings)
    if not len(positions):
        return
    boundaries = np.flatnonzero(np.diff(positions) > 1) + 1
    for run in np.split(positions, boundaries):
        run_start = int(run[0])
        run_stop = int(run[-1]) + 1
        peak_offset = int(run[int(np.argmax(deviations[run]))])
        incoming = _DetectedSpan(
            start_sample_index + run_start,
            start_sample_index + run_stop,
            start_sample_index + peak_offset,
            float(deviations[peak_offset]),
        )
        if spans and incoming.start - spans[-1].stop <= gap_samples:
            current = spans[-1]
            current.stop = incoming.stop
            if incoming.peak_deviation > current.peak_deviation:
                current.peak_sample_index = incoming.peak_sample_index
                current.peak_deviation = incoming.peak_deviation
        else:
            spans.append(incoming)


def _pad_and_merge_spans(
    spans: list[_DetectedSpan],
    pre_samples: int,
    post_samples: int,
    sample_count: int,
) -> list[tuple[_DetectedSpan, int, int]]:
    merged: list[tuple[_DetectedSpan, int, int]] = []
    for span in spans:
        start = max(0, span.start - pre_samples)
        stop = min(sample_count, span.stop + post_samples)
        if merged and start <= merged[-1][2]:
            current, current_start, _current_stop = merged[-1]
            current.stop = span.stop
            if span.peak_deviation > current.peak_deviation:
                current.peak_sample_index = span.peak_sample_index
                current.peak_deviation = span.peak_deviation
            merged[-1] = (current, current_start, stop)
        else:
            merged.append((span, start, stop))
    return merged


def _located_extremum(
    value_and_index: tuple[float, int],
    sample_rate_hz: float,
) -> LocatedExtremum:
    from labdaq.domain.enums import MetricAvailability
    from labdaq.ports.processing import LocatedExtremum

    value, sample_index = value_and_index
    return LocatedExtremum(
        MetricAvailability.AVAILABLE,
        value,
        abs(value),
        sample_index,
        sample_index / sample_rate_hz,
    )


def _unique_notices(notices: list[ProcessingNotice]) -> tuple[ProcessingNotice, ...]:
    unique: dict[tuple[object, ...], ProcessingNotice] = {}
    for notice in notices:
        key = (
            notice.code,
            notice.channel_id,
            notice.start_sample_index,
            notice.stop_sample_index,
        )
        unique.setdefault(key, notice)
    return tuple(unique.values())


class RecordedSessionProcessingService:
    """Reprocess a verified M05 session without mutating its source record."""

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

    def recipe(self, recipe_id: str = "baseline-v1") -> ProcessingRecipeEnvelope:
        return self._recipes.load_bundled_processing_recipe(recipe_id)

    def process(
        self,
        path: Path,
        *,
        recipe_id: str = "baseline-v1",
        start_sample: int = 0,
        stop_sample: int | None = None,
        chunk_size: int = 8192,
        expected_recipe_hash: str | None = None,
    ) -> ProcessedSessionSummary:
        import numpy as np

        from labdaq.ports.processing import ProcessedSessionSummary

        metadata = self._source.processing_metadata(path)
        if metadata.session_type == "raw-diagnostic":
            raise ProcessingError(
                "Raw diagnostic sessions do not authorize calibrated or spectral processing.",
                code="DAQ.PROCESSING.RAW_DIAGNOSTIC_SESSION",
                context={"session_id": metadata.session_id},
                remediation="Use immutable raw-voltage replay or raw-voltage export only.",
            )
        stop = metadata.readable_sample_count if stop_sample is None else stop_sample
        if start_sample < 0 or stop <= start_sample or stop > metadata.readable_sample_count:
            raise ProcessingError(
                "The requested processing range is outside the verified session prefix.",
                code="DAQ.PROCESSING.PROCESSING_RANGE_INVALID",
                context={
                    "session_id": metadata.session_id,
                    "start_sample": start_sample,
                    "stop_sample": stop,
                    "readable_sample_count": metadata.readable_sample_count,
                },
                remediation="Select a non-empty half-open range inside the verified prefix.",
            )
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        selected_range = ProcessingRange(start_sample, stop)
        recipe = self.recipe(recipe_id)
        recipe_hash = canonical_sha256(recipe)
        if expected_recipe_hash is not None and expected_recipe_hash != recipe_hash:
            raise ProcessingError(
                "The selected recipe does not match the requested deterministic identity.",
                code="DAQ.PROCESSING.RECIPE_HASH_MISMATCH",
                context={
                    "session_id": metadata.session_id,
                    "expected_recipe_hash": expected_recipe_hash,
                    "received_recipe_hash": recipe_hash,
                },
                remediation="Use the exact expected recipe or omit the stale expected hash.",
            )
        assert recipe.metrics is not None and recipe.fft is not None and recipe.psd is not None
        if selected_range.sample_count > recipe.fft.maximum_samples_per_channel:
            raise ProcessingError(
                "The selected session range exceeds the qualified FFT memory bound.",
                code="DAQ.PROCESSING.SPECTRAL_SELECTION_TOO_LARGE",
                context={
                    "session_id": metadata.session_id,
                    "sample_count": selected_range.sample_count,
                    "maximum_samples": recipe.fft.maximum_samples_per_channel,
                },
                remediation="Select at most 1,048,576 samples for the all-products summary.",
            )
        calibrations, calibration_notices = self._resolver.resolve_source(metadata)
        calibrated_ids = tuple(item.logical_channel_id for item in calibrations)
        calibrated_positions = tuple(
            metadata.ordered_channel_ids.index(channel_id) for channel_id in calibrated_ids
        )
        processor = self._numerical.create_stream(
            recipe,
            calibrations,
            sample_rate_hz=metadata.sample_rate_hz,
            mode=ProcessingMode.REPLAY,
        )
        psd_accumulators = tuple(
            self._numerical.create_welch_accumulator(
                channel_id=calibration.logical_channel_id,
                unit=calibration.output_unit,
                sample_range=selected_range,
                sample_rate_hz=metadata.sample_rate_hz,
                recipe_hash=processor.recipe_hash,
                calibration_hash=calibration.calibration_hash,
                segment_duration_seconds=recipe.psd.segment_duration_seconds,
                overlap_fraction=recipe.psd.overlap_fraction,
            )
            for calibration in calibrations
        )
        raw_parts: list[list[np.ndarray]] = [[] for _ in calibrations]
        conditioned_parts: list[list[np.ndarray]] = [[] for _ in calibrations]
        notices = list(calibration_notices)
        replay = self._source.open_replay(path, chunk_size=chunk_size)
        try:
            for chunk in replay:
                selected_chunk = _select_recorded_chunk(chunk, calibrated_positions, calibrated_ids)
                processed = processor.process(selected_chunk)
                notices.extend(processed.notices)
                chunk_stop = chunk.start_sample_index + chunk.sample_count
                overlap_start = max(start_sample, chunk.start_sample_index)
                overlap_stop = min(stop, chunk_stop)
                if overlap_stop > overlap_start:
                    left = overlap_start - chunk.start_sample_index
                    right = overlap_stop - chunk.start_sample_index
                    for index in range(len(calibrations)):
                        raw_parts[index].append(
                            np.array(selected_chunk.samples[index, left:right], copy=True)
                        )
                        conditioned_parts[index].append(
                            np.array(processed.conditioned_samples[index, left:right], copy=True)
                        )
                        psd_accumulators[index].append(
                            processed.conditioned_samples[index, left:right]
                        )
                if chunk_stop >= stop:
                    replay.cancel()
                    break
        finally:
            replay.close()
        raw = tuple(np.concatenate(parts) for parts in raw_parts)
        conditioned = tuple(np.concatenate(parts) for parts in conditioned_parts)
        metrics = tuple(
            self._numerical.metrics(
                raw[index],
                conditioned[index],
                channel_id=calibration.logical_channel_id,
                unit=calibration.output_unit,
                sample_range=selected_range,
                sample_rate_hz=metadata.sample_rate_hz,
                recipe_hash=processor.recipe_hash,
                calibration=calibration,
                clipping_margin_v=recipe.metrics.clipping_margin_v,
            )
            for index, calibration in enumerate(calibrations)
        )
        fft = tuple(
            self._numerical.fft_amplitude(
                conditioned[index],
                channel_id=calibration.logical_channel_id,
                unit=calibration.output_unit,
                sample_range=selected_range,
                sample_rate_hz=metadata.sample_rate_hz,
                recipe_hash=processor.recipe_hash,
                calibration_hash=calibration.calibration_hash,
                maximum_samples=recipe.fft.maximum_samples_per_channel,
            )
            for index, calibration in enumerate(calibrations)
        )
        psd = tuple(accumulator.finalize() for accumulator in psd_accumulators)
        provenance = _provenance(
            metadata=metadata,
            selected_range=selected_range,
            calibrations=calibrations,
            recipe=recipe,
            recipe_hash=processor.recipe_hash,
            numpy_version=processor.numpy_version,
            scipy_version=processor.scipy_version,
            realized_algorithm_details=processor.algorithm_details,
            notices=tuple(notices),
        )
        return ProcessedSessionSummary(provenance=provenance, metrics=metrics, fft=fft, psd=psd)


def _quality(source: CalibrationSourceKind) -> CalibrationQuality:
    return {
        CalibrationSourceKind.CERTIFICATE: CalibrationQuality.TRACEABLE,
        CalibrationSourceKind.MANUFACTURER_NOMINAL: CalibrationQuality.NOMINAL,
        CalibrationSourceKind.EXTERNAL_VALIDATION: CalibrationQuality.EXTERNALLY_VALIDATED,
        CalibrationSourceKind.TEST_FIXTURE: CalibrationQuality.SYNTHETIC,
        CalibrationSourceKind.OPERATOR_TRANSCRIBED: CalibrationQuality.UNVERIFIED,
    }[source]


def _select_recorded_chunk(
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
    )


def _readonly_matrix(values: np.ndarray) -> np.ndarray:
    import numpy as np

    copied = np.ascontiguousarray(values, dtype=np.float64)
    copied.flags.writeable = False
    return copied


def _provenance(
    *,
    metadata: ProcessingSourceMetadata,
    selected_range: ProcessingRange,
    calibrations: tuple[ResolvedChannelCalibration, ...],
    recipe: ProcessingRecipeEnvelope,
    recipe_hash: str,
    numpy_version: str,
    scipy_version: str,
    realized_algorithm_details: Mapping[str, object],
    notices: tuple[ProcessingNotice, ...],
) -> ProcessingProvenance:
    algorithm_details = {
        **realized_algorithm_details,
        "metrics": asdict(recipe.metrics) if recipe.metrics else None,
        "fft": asdict(recipe.fft) if recipe.fft else None,
        "psd": asdict(recipe.psd) if recipe.psd else None,
    }
    content = {
        "schema_version": 1,
        "source_session_id": metadata.session_id,
        "source_completion_state": metadata.completion_state,
        "source_raw_digest": metadata.raw_data_digest,
        "source_range": asdict(selected_range),
        "ordered_channel_ids": [cal.logical_channel_id for cal in calibrations],
        "sample_rate_hz": metadata.sample_rate_hz,
        "calibration_ids": [cal.calibration_id for cal in calibrations],
        "calibration_hashes": [cal.calibration_hash for cal in calibrations],
        "calibration_qualities": [cal.quality.value for cal in calibrations],
        "recipe_id": recipe.recipe_id,
        "recipe_version": recipe.recipe_version,
        "recipe_hash": recipe_hash,
        "processing_mode": ProcessingMode.REPLAY.value,
        "application_version": __version__,
        "numpy_version": numpy_version,
        "scipy_version": scipy_version,
        "output_units": [cal.output_unit.value for cal in calibrations],
        "notices": [asdict(notice) for notice in notices],
        "algorithm_details": algorithm_details,
    }
    return ProcessingProvenance(
        schema_version=1,
        provenance_hash=canonical_sha256(content),
        source_session_id=metadata.session_id,
        source_completion_state=metadata.completion_state,
        source_raw_digest=metadata.raw_data_digest,
        source_range=selected_range,
        ordered_channel_ids=tuple(cal.logical_channel_id for cal in calibrations),
        sample_rate_hz=metadata.sample_rate_hz,
        calibration_ids=tuple(cal.calibration_id for cal in calibrations),
        calibration_hashes=tuple(cal.calibration_hash for cal in calibrations),
        calibration_qualities=tuple(cal.quality for cal in calibrations),
        recipe_id=recipe.recipe_id,
        recipe_version=recipe.recipe_version,
        recipe_hash=recipe_hash,
        processing_mode=ProcessingMode.REPLAY,
        application_version=__version__,
        numpy_version=numpy_version,
        scipy_version=scipy_version,
        output_units=tuple(cal.output_unit for cal in calibrations),
        notices=notices,
        algorithm_details=algorithm_details,
    )
