"""Storage- and renderer-neutral DAQ-M07 plotting contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping
    from pathlib import Path

    from labdaq.domain.enums import SignalStage
    from labdaq.domain.models import ResolvedChannelCalibration
    from labdaq.domain.plotting import (
        ExportFormat,
        FigureKind,
        FigureOptionsV1,
        PlotRepresentation,
        SampleRange,
    )


def _vector(value: np.ndarray, field: str, *, integer: bool = False) -> None:
    expected = np.dtype(np.int64 if integer else np.float64)
    if not isinstance(value, np.ndarray) or value.dtype != expected or value.ndim != 1:
        raise ValueError(f"{field} must be a one-dimensional {expected} array")
    if value.flags.writeable:
        raise ValueError(f"{field} must be read-only")


@dataclass(frozen=True, slots=True)
class PlotSeries:
    channel_id: str
    physical_channel: str
    sensor_id: str | None
    sensor_model: str | None
    sensor_serial_number: str | None
    label: str
    x_values: np.ndarray
    y_values: np.ndarray
    x_unit: str
    y_unit: str
    raw_unit: str
    representation: PlotRepresentation
    source_range: SampleRange
    output_point_count: int
    decimated: bool
    decimation_algorithm: str | None
    source_sample_indices: np.ndarray | None = None
    calibration: ResolvedChannelCalibration | None = None
    recipe_id: str | None = None
    recipe_hash: str | None = None
    processing_disposition: str = "calibrated"
    engineering_interpretation: str | None = "calibrated"
    warning: str | None = None
    spectral_input_stage: SignalStage | None = None

    def __post_init__(self) -> None:
        _vector(self.x_values, "x_values")
        _vector(self.y_values, "y_values")
        if (
            len(self.x_values) != len(self.y_values)
            or len(self.x_values) != self.output_point_count
        ):
            raise ValueError("plot vectors and output point count must agree")
        if self.source_sample_indices is not None:
            _vector(self.source_sample_indices, "source_sample_indices", integer=True)
            if len(self.source_sample_indices) != self.output_point_count:
                raise ValueError("source indices must align with plot values")
        if self.decimated != (self.decimation_algorithm is not None):
            raise ValueError("decimation status and algorithm must agree")

    def to_dict(self, *, include_values: bool = True) -> dict[str, object]:
        result: dict[str, object] = {
            "channel_id": self.channel_id,
            "physical_channel": self.physical_channel,
            "sensor_id": self.sensor_id,
            "sensor_model": self.sensor_model,
            "sensor_serial_number": self.sensor_serial_number,
            "label": self.label,
            "x_unit": self.x_unit,
            "y_unit": self.y_unit,
            "raw_unit": self.raw_unit,
            "representation": self.representation.value,
            "source_range": self.source_range.to_dict(),
            "output_point_count": self.output_point_count,
            "decimated": self.decimated,
            "decimation_algorithm": self.decimation_algorithm,
            "calibration_id": self.calibration.calibration_id if self.calibration else None,
            "calibration_hash": self.calibration.calibration_hash if self.calibration else None,
            "recipe_id": self.recipe_id,
            "recipe_hash": self.recipe_hash,
            "processing_disposition": self.processing_disposition,
            "engineering_interpretation": self.engineering_interpretation,
            "warning": self.warning,
            "spectral_input_stage": (
                self.spectral_input_stage.value if self.spectral_input_stage else None
            ),
        }
        if include_values:
            result["x_values"] = self.x_values.tolist()
            result["y_values"] = self.y_values.tolist()
            result["source_sample_indices"] = (
                self.source_sample_indices.tolist()
                if self.source_sample_indices is not None
                else None
            )
        return result


@dataclass(frozen=True, slots=True)
class PlotBundle:
    source_session_id: str
    source_session_path: Path
    source_manifest_path: Path
    source_manifest_sha256: str
    source_raw_digest: str
    source_schema_version: int
    source_application_version: str
    representation: PlotRepresentation
    series: tuple[PlotSeries, ...]
    request_summary: Mapping[str, object]
    warnings: tuple[str, ...] = ()
    recipe_id: str | None = None
    recipe_hash: str | None = None
    recipe_details: Mapping[str, object] | None = None
    spectral_details: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if not self.series or len(self.source_manifest_sha256) != 64:
            raise ValueError("plot bundles require series and source integrity identity")

    def to_dict(self, *, include_values: bool = True) -> dict[str, object]:
        return {
            "source_session_id": self.source_session_id,
            "source_session_path": str(self.source_session_path),
            "source_manifest_path": str(self.source_manifest_path),
            "source_manifest_sha256": self.source_manifest_sha256,
            "source_raw_digest": self.source_raw_digest,
            "source_schema_version": self.source_schema_version,
            "source_application_version": self.source_application_version,
            "representation": self.representation.value,
            "series": [item.to_dict(include_values=include_values) for item in self.series],
            "request": dict(self.request_summary),
            "warnings": list(self.warnings),
            "recipe_id": self.recipe_id,
            "recipe_hash": self.recipe_hash,
            "recipe_details": dict(self.recipe_details or {}),
            "spectral_details": dict(self.spectral_details or {}),
        }


@dataclass(frozen=True, slots=True)
class TabularChunk:
    columns: tuple[np.ndarray, ...]

    def __post_init__(self) -> None:
        if not self.columns:
            raise ValueError("tabular chunks require columns")
        length = len(self.columns[0])
        for column in self.columns:
            if not isinstance(column, np.ndarray) or column.ndim != 1 or len(column) != length:
                raise ValueError("tabular columns must be aligned one-dimensional arrays")


@dataclass(frozen=True, slots=True)
class TimeDataChunk:
    start_sample_index: int
    values: np.ndarray
    notices: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.start_sample_index < 0:
            raise ValueError("time-data chunk index must be non-negative")
        if (
            not isinstance(self.values, np.ndarray)
            or self.values.dtype != np.float64
            or self.values.ndim != 2
            or self.values.shape[1] == 0
            or self.values.flags.writeable
        ):
            raise ValueError("time-data values must be a read-only float64 matrix")


@dataclass(frozen=True, slots=True)
class FileReceipt:
    byte_count: int
    sha256: str


@dataclass(frozen=True, slots=True)
class FigureRenderReceipt:
    figure_kind: FigureKind
    output_format: ExportFormat
    axis_labels: tuple[tuple[str, str], ...]
    legend_labels: tuple[str, ...]
    series_count: int


class StaticFigureRendererPort(Protocol):
    def render(
        self,
        bundle: PlotBundle,
        *,
        kind: FigureKind,
        options: FigureOptionsV1,
        output_format: ExportFormat,
        destination: Path,
    ) -> FigureRenderReceipt: ...


class CsvArtifactWriterPort(Protocol):
    def write(
        self,
        destination: Path,
        *,
        headers: tuple[str, ...],
        chunks: Iterable[TabularChunk],
        cancelled: Callable[[], bool],
        progressed: Callable[[int], None],
    ) -> None: ...


class JsonArtifactWriterPort(Protocol):
    def write(self, destination: Path, payload: Mapping[str, object]) -> None: ...


class ArtifactStorePort(Protocol):
    def validate_destination(self, directory: Path) -> None: ...

    def reject_existing(self, paths: tuple[Path, ...]) -> None: ...

    def temporary_path(self, final_path: Path) -> Path: ...

    def finalize_exclusive(self, temporary_path: Path, final_path: Path) -> FileReceipt: ...

    def remove(self, path: Path) -> None: ...

    def checksum(self, path: Path) -> FileReceipt: ...
