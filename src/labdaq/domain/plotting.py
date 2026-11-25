"""Infrastructure-neutral DAQ-M07 plotting and export records."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from labdaq._context import freeze_mapping, sanitize_context, thaw_mapping
from labdaq.domain.naming import validate_slug

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from labdaq.domain.enums import SignalStage


class PlotRepresentation(StrEnum):
    RAW_TIME = "raw"
    CALIBRATED_TIME = "calibrated"
    FILTERED_TIME = "filtered"
    FFT_AMPLITUDE = "fft"
    PSD = "psd"


class DecimationAlgorithm(StrEnum):
    MIN_MAX_V1 = "min-max-v1"


class FigureKind(StrEnum):
    TIME_HISTORY = "time-history"
    OVERLAY = "overlay"
    FFT = "fft"
    PSD = "psd"


class ExportFormat(StrEnum):
    CSV = "csv"
    PNG = "png"
    SVG = "svg"


class ExportProduct(StrEnum):
    RAW_TIME_DATA = "raw-time-data"
    CALIBRATED_TIME_DATA = "calibrated-time-data"
    FILTERED_TIME_DATA = "filtered-time-data"
    FFT_AMPLITUDE_DATA = "fft-amplitude-data"
    PSD_DATA = "psd-data"
    TIME_HISTORY_FIGURE = "time-history-figure"
    OVERLAY_FIGURE = "overlay-figure"
    FFT_FIGURE = "fft-figure"
    PSD_FIGURE = "psd-figure"


class ExportJobState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAULTED = "faulted"


class CollisionPolicy(StrEnum):
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class SampleRange:
    start_index: int
    stop_index: int
    sample_rate_hz: float
    requested_start_seconds: float | None = None
    requested_stop_seconds: float | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.start_index, bool)
            or not isinstance(self.start_index, int)
            or self.start_index < 0
        ):
            raise ValueError("start_index must be a non-negative integer")
        if (
            isinstance(self.stop_index, bool)
            or not isinstance(self.stop_index, int)
            or self.stop_index <= self.start_index
        ):
            raise ValueError("stop_index must exceed start_index")
        if not math.isfinite(self.sample_rate_hz) or self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive and finite")
        for name in ("requested_start_seconds", "requested_stop_seconds"):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError(f"{name} must be non-negative and finite")
        if (
            self.requested_start_seconds is not None
            and self.requested_stop_seconds is not None
            and self.requested_stop_seconds <= self.requested_start_seconds
        ):
            raise ValueError("requested seconds must describe a non-empty range")

    @property
    def sample_count(self) -> int:
        return self.stop_index - self.start_index

    def to_dict(self) -> dict[str, object]:
        return {
            "start_index": self.start_index,
            "stop_index": self.stop_index,
            "sample_count": self.sample_count,
            "sample_rate_hz": self.sample_rate_hz,
            "requested_start_seconds": self.requested_start_seconds,
            "requested_stop_seconds": self.requested_stop_seconds,
        }


def resolve_sample_range(
    *,
    sample_rate_hz: float,
    readable_sample_count: int,
    start_index: int | None = None,
    stop_index: int | None = None,
    start_seconds: float | None = None,
    stop_seconds: float | None = None,
) -> SampleRange:
    """Resolve one half-open source range without repeated float accumulation."""
    if (
        isinstance(readable_sample_count, bool)
        or not isinstance(readable_sample_count, int)
        or readable_sample_count <= 0
    ):
        raise ValueError("readable_sample_count must be positive")
    using_indices = start_index is not None or stop_index is not None
    using_seconds = start_seconds is not None or stop_seconds is not None
    if using_indices and using_seconds:
        raise ValueError("sample-index and second-based ranges are mutually exclusive")
    if not math.isfinite(sample_rate_hz) or sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be positive and finite")
    if using_seconds:
        start_value = 0.0 if start_seconds is None else start_seconds
        stop_value = stop_seconds
        if (
            not math.isfinite(start_value)
            or (stop_value is not None and not math.isfinite(stop_value))
            or start_value < 0
            or (stop_value is not None and stop_value <= start_value)
        ):
            raise ValueError("second-based range must be finite, non-negative, and non-empty")
        try:
            rate = Decimal(str(sample_rate_hz))
            resolved_start = (
                0
                if start_seconds is None
                else int(
                    (Decimal(str(start_value)) * rate).to_integral_value(rounding=ROUND_CEILING)
                )
            )
            resolved_stop = (
                readable_sample_count
                if stop_value is None
                else int(
                    (Decimal(str(stop_value)) * rate).to_integral_value(rounding=ROUND_CEILING)
                )
            )
        except InvalidOperation as cause:
            raise ValueError("second-based range cannot be resolved") from cause
        requested_start = start_value if start_seconds is not None else None
        requested_stop = stop_value
    else:
        resolved_start = 0 if start_index is None else start_index
        resolved_stop = readable_sample_count if stop_index is None else stop_index
        requested_start = requested_stop = None
    if (
        isinstance(resolved_start, bool)
        or isinstance(resolved_stop, bool)
        or not isinstance(resolved_start, int)
        or not isinstance(resolved_stop, int)
        or resolved_start < 0
        or resolved_stop <= resolved_start
        or resolved_stop > readable_sample_count
    ):
        raise ValueError("resolved range must be non-empty and inside the verified source")
    return SampleRange(
        resolved_start,
        resolved_stop,
        sample_rate_hz,
        requested_start,
        requested_stop,
    )


@dataclass(frozen=True, slots=True)
class ChannelSelection:
    channel_ids: tuple[str, ...]
    display_names: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not self.channel_ids or any(not value for value in self.channel_ids):
            raise ValueError("channel selection must not be empty")
        if len(set(self.channel_ids)) != len(self.channel_ids):
            raise ValueError("channel selection must not contain duplicates")
        if self.display_names is not None and len(self.display_names) != len(self.channel_ids):
            raise ValueError("display names must align with selected channels")

    def to_dict(self) -> dict[str, object]:
        return {
            "channel_ids": list(self.channel_ids),
            "display_names": list(self.display_names) if self.display_names is not None else None,
        }


@dataclass(frozen=True, slots=True)
class PlotViewport:
    max_points: int = 2000
    algorithm: DecimationAlgorithm = DecimationAlgorithm.MIN_MAX_V1

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_points, bool)
            or not isinstance(self.max_points, int)
            or self.max_points <= 0
        ):
            raise ValueError("max_points must be a positive integer")


@dataclass(frozen=True, slots=True)
class PlotRequest:
    source_session: Path
    representation: PlotRepresentation
    channels: ChannelSelection
    sample_range: SampleRange
    viewport: PlotViewport = field(default_factory=PlotViewport)
    recipe_id: str | None = None
    spectral_input_stage: SignalStage | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("only plot request schema version 1 is supported")
        if not isinstance(self.source_session, Path):
            raise TypeError("source_session must be a pathlib.Path")
        spectral = self.representation in {
            PlotRepresentation.FFT_AMPLITUDE,
            PlotRepresentation.PSD,
        }
        if (
            self.representation is PlotRepresentation.FILTERED_TIME or spectral
        ) and not self.recipe_id:
            raise ValueError("filtered and spectral requests require a recipe identifier")
        if spectral and self.spectral_input_stage is None:
            raise ValueError("spectral requests require an input signal stage")
        if not spectral and self.spectral_input_stage is not None:
            raise ValueError("spectral_input_stage is valid only for FFT or PSD")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_session": str(self.source_session),
            "representation": self.representation.value,
            "channels": self.channels.to_dict(),
            "sample_range": self.sample_range.to_dict(),
            "viewport": {
                "max_points": self.viewport.max_points,
                "algorithm": self.viewport.algorithm.value,
            },
            "recipe_id": self.recipe_id,
            "spectral_input_stage": (
                self.spectral_input_stage.value if self.spectral_input_stage else None
            ),
        }


@dataclass(frozen=True, slots=True)
class FigureOptionsV1:
    schema_version: int = 1
    width_inches: float = 10.0
    height_inches: float = 6.0
    dpi: int = 120
    title: str | None = None
    logarithmic_y: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("only figure options schema version 1 is supported")
        if (
            not math.isfinite(self.width_inches)
            or not math.isfinite(self.height_inches)
            or self.width_inches <= 0
            or self.height_inches <= 0
            or isinstance(self.dpi, bool)
            or not isinstance(self.dpi, int)
            or self.dpi <= 0
        ):
            raise ValueError("figure dimensions and DPI must be positive")


@dataclass(frozen=True, slots=True)
class ExportRequest:
    source_session: Path
    product: ExportProduct
    output_format: ExportFormat
    channels: ChannelSelection
    sample_range: SampleRange
    representation: PlotRepresentation
    recipe_id: str | None = None
    spectral_input_stage: SignalStage | None = None
    destination_directory: Path | None = None
    base_name: str | None = None
    figure_options: FigureOptionsV1 = field(default_factory=FigureOptionsV1)
    max_points: int = 2000
    chunk_size: int = 8192
    collision_policy: CollisionPolicy = CollisionPolicy.REJECT
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("only export request schema version 1 is supported")
        if self.base_name is not None:
            validate_slug(self.base_name)
        if (
            isinstance(self.max_points, bool)
            or not isinstance(self.max_points, int)
            or self.max_points <= 0
            or isinstance(self.chunk_size, bool)
            or not isinstance(self.chunk_size, int)
            or self.chunk_size <= 0
        ):
            raise ValueError("max_points and chunk_size must be positive")
        data_product = self.product.value.endswith("-data")
        if data_product and self.output_format is not ExportFormat.CSV:
            raise ValueError("numeric data products require CSV format")
        if not data_product and self.output_format not in {ExportFormat.PNG, ExportFormat.SVG}:
            raise ValueError("figure products require PNG or SVG format")
        expected = {
            ExportProduct.RAW_TIME_DATA: PlotRepresentation.RAW_TIME,
            ExportProduct.CALIBRATED_TIME_DATA: PlotRepresentation.CALIBRATED_TIME,
            ExportProduct.FILTERED_TIME_DATA: PlotRepresentation.FILTERED_TIME,
            ExportProduct.FFT_AMPLITUDE_DATA: PlotRepresentation.FFT_AMPLITUDE,
            ExportProduct.PSD_DATA: PlotRepresentation.PSD,
            ExportProduct.FFT_FIGURE: PlotRepresentation.FFT_AMPLITUDE,
            ExportProduct.PSD_FIGURE: PlotRepresentation.PSD,
        }.get(self.product)
        if expected is not None and self.representation is not expected:
            raise ValueError("export product and representation are incompatible")
        if self.product in {
            ExportProduct.TIME_HISTORY_FIGURE,
            ExportProduct.OVERLAY_FIGURE,
        } and self.representation not in {
            PlotRepresentation.RAW_TIME,
            PlotRepresentation.CALIBRATED_TIME,
            PlotRepresentation.FILTERED_TIME,
        }:
            raise ValueError("time-history figures require a time-domain representation")
        if (
            self.product is ExportProduct.TIME_HISTORY_FIGURE
            and len(self.channels.channel_ids) != 1
        ):
            raise ValueError("a time-history figure requires one selected channel")
        PlotRequest(
            self.source_session,
            self.representation,
            self.channels,
            self.sample_range,
            PlotViewport(self.max_points),
            self.recipe_id,
            self.spectral_input_stage,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_session": str(self.source_session),
            "product": self.product.value,
            "output_format": self.output_format.value,
            "channels": self.channels.to_dict(),
            "sample_range": self.sample_range.to_dict(),
            "representation": self.representation.value,
            "recipe_id": self.recipe_id,
            "spectral_input_stage": (
                self.spectral_input_stage.value if self.spectral_input_stage else None
            ),
            "destination_directory": (
                str(self.destination_directory) if self.destination_directory else None
            ),
            "base_name": self.base_name,
            "figure_options": {
                "schema_version": self.figure_options.schema_version,
                "width_inches": self.figure_options.width_inches,
                "height_inches": self.figure_options.height_inches,
                "dpi": self.figure_options.dpi,
                "title": self.figure_options.title,
                "logarithmic_y": self.figure_options.logarithmic_y,
            },
            "max_points": self.max_points,
            "chunk_size": self.chunk_size,
            "collision_policy": self.collision_policy.value,
        }


@dataclass(frozen=True, slots=True)
class ExportFault:
    code: str
    message: str
    context: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.code.startswith("DAQ.") or not self.message:
            raise ValueError("export faults require a DAQ code and message")
        safe = sanitize_context(self.context, reject_arrays=True, strict=True)
        object.__setattr__(self, "context", freeze_mapping(safe))

    def to_dict(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message, "context": thaw_mapping(self.context)}


@dataclass(frozen=True, slots=True)
class ExportProgress:
    export_job_id: str
    state: ExportJobState
    processed_samples: int
    total_samples: int
    completed_artifacts: int
    total_artifacts: int
    operation: str
    timestamp: datetime
    artifact_id: str | None = None

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("progress timestamps must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ExportJob:
    export_job_id: str
    requests: tuple[ExportRequest, ...]
    destination_directory: Path
    created_at: datetime
    state: ExportJobState = ExportJobState.PENDING

    def __post_init__(self) -> None:
        if not self.export_job_id or not self.requests:
            raise ValueError("export jobs require an identity and requests")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("export job timestamps must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        return {
            "export_job_id": self.export_job_id,
            "requests": [request.to_dict() for request in self.requests],
            "destination_directory": str(self.destination_directory),
            "created_at": self.created_at.isoformat(),
            "state": self.state.value,
        }


@dataclass(frozen=True, slots=True)
class ExportArtifact:
    artifact_id: str
    export_job_id: str
    source_session_id: str
    product: ExportProduct
    output_format: ExportFormat
    relative_path: str
    byte_count: int
    sha256: str
    sidecar_relative_path: str
    sidecar_sha256: str
    channel_ids: tuple[str, ...]
    sample_range: SampleRange
    representation: PlotRepresentation
    units: tuple[str, ...]
    recipe_id: str | None
    recipe_hash: str | None
    calibration_hashes: tuple[str | None, ...]
    warnings: tuple[str, ...]
    created_at: datetime
    state: ExportJobState = ExportJobState.COMPLETED

    def __post_init__(self) -> None:
        if self.byte_count < 0 or len(self.sha256) != 64 or len(self.sidecar_sha256) != 64:
            raise ValueError("completed artifacts require sizes and SHA-256 checksums")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("artifact timestamps must be timezone-aware")
        if self.state is not ExportJobState.COMPLETED:
            raise ValueError("artifact records describe completed artifacts only")

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact_id": self.artifact_id,
            "export_job_id": self.export_job_id,
            "source_session_id": self.source_session_id,
            "product": self.product.value,
            "format": self.output_format.value,
            "relative_path": self.relative_path,
            "byte_count": self.byte_count,
            "sha256": self.sha256,
            "sidecar_relative_path": self.sidecar_relative_path,
            "sidecar_sha256": self.sidecar_sha256,
            "channel_ids": list(self.channel_ids),
            "sample_range": self.sample_range.to_dict(),
            "representation": self.representation.value,
            "units": list(self.units),
            "recipe_id": self.recipe_id,
            "recipe_hash": self.recipe_hash,
            "calibration_hashes": list(self.calibration_hashes),
            "warnings": list(self.warnings),
            "created_at": self.created_at.isoformat(),
            "state": self.state.value,
        }


@dataclass(frozen=True, slots=True)
class ExportManifest:
    schema_version: int
    export_job_id: str
    source_session_id: str
    source_manifest_sha256: str
    application_version: str
    export_service_version: str
    requests: tuple[Mapping[str, object], ...]
    artifacts: tuple[ExportArtifact, ...]
    warnings: tuple[str, ...]
    faults: tuple[ExportFault, ...]
    created_at: datetime
    completed_at: datetime
    final_state: ExportJobState
    processed_samples: int
    total_samples: int
    completed_artifacts: int
    total_artifacts: int
    manifest_path: Path | None = None

    def __post_init__(self) -> None:
        if self.schema_version != 1 or len(self.source_manifest_sha256) != 64:
            raise ValueError("export manifest identity is invalid")
        if self.created_at.tzinfo is None or self.completed_at.tzinfo is None:
            raise ValueError("manifest timestamps must be timezone-aware")
        if (
            self.processed_samples < 0
            or self.total_samples < self.processed_samples
            or self.completed_artifacts < 0
            or self.total_artifacts < self.completed_artifacts
        ):
            raise ValueError("export manifest progress is invalid")
        safe_requests = tuple(
            freeze_mapping(sanitize_context(request, reject_arrays=True, strict=True))
            for request in self.requests
        )
        object.__setattr__(self, "requests", safe_requests)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "export_job_id": self.export_job_id,
            "source_session_id": self.source_session_id,
            "source_manifest_sha256": self.source_manifest_sha256,
            "application_version": self.application_version,
            "export_service_version": self.export_service_version,
            "requests": [thaw_mapping(value) for value in self.requests],
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "warnings": list(self.warnings),
            "faults": [fault.to_dict() for fault in self.faults],
            "created_at": self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "final_state": self.final_state.value,
            "progress": {
                "processed_samples": self.processed_samples,
                "total_samples": self.total_samples,
                "completed_artifacts": self.completed_artifacts,
                "total_artifacts": self.total_artifacts,
            },
            "manifest_path": str(self.manifest_path) if self.manifest_path else None,
        }
