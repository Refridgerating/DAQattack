"""Incremental CSV and deterministic JSON artifact writers."""

from __future__ import annotations

import csv
import json
import math
import os
from typing import TYPE_CHECKING, Any, cast

from labdaq.adapters.plotting_json import JsonPlotExportCodec
from labdaq.errors import ExportError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping
    from pathlib import Path

    from labdaq.ports.plotting import TabularChunk


class CsvArtifactWriter:
    def write(
        self,
        destination: Path,
        *,
        headers: tuple[str, ...],
        chunks: Iterable[TabularChunk],
        cancelled: Callable[[], bool],
        progressed: Callable[[int], None],
    ) -> None:
        try:
            with destination.open("x", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream, lineterminator="\n")
                writer.writerow(headers)
                for chunk in chunks:
                    if cancelled():
                        _raise_cancelled()
                    rows = zip(*chunk.columns, strict=True)
                    written = 0
                    for row in rows:
                        writer.writerow(tuple(_format_cell(value) for value in row))
                        written += 1
                    progressed(written)
                stream.flush()
                os.fsync(stream.fileno())
        except ExportError:
            raise
        except (OSError, csv.Error, ValueError) as cause:
            raise ExportError(
                "CSV data could not be written.",
                code="DAQ.EXPORT.WRITE_FAILED",
                context={"path": str(destination)},
                remediation="Check destination capacity and permissions.",
                cause=cause,
            ) from cause


class JsonArtifactWriter:
    def __init__(self, codec: JsonPlotExportCodec | None = None) -> None:
        self._codec = codec or JsonPlotExportCodec()

    def write(self, destination: Path, payload: Mapping[str, object]) -> None:
        try:
            encoded = self._codec.dumps_artifact_document(payload)
            with destination.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        except ExportError:
            raise
        except (OSError, TypeError, ValueError) as cause:
            raise ExportError(
                "Export metadata could not be written.",
                code="DAQ.EXPORT.WRITE_FAILED",
                context={"path": str(destination)},
                remediation="Check metadata values, destination capacity, and permissions.",
                cause=cause,
            ) from cause


class PlainJsonArtifactWriter:
    """Durable JSON writer for artifact schemas owned outside DAQ-M07."""

    def write(self, destination: Path, payload: Mapping[str, object]) -> None:
        try:
            encoded = (
                json.dumps(
                    payload,
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                    allow_nan=False,
                )
                + "\n"
            )
            with destination.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        except (OSError, TypeError, ValueError) as cause:
            raise ExportError(
                "Raw-container export metadata could not be written.",
                code="DAQ.EXPORT.WRITE_FAILED",
                context={"path": str(destination)},
                remediation="Check metadata values, destination capacity, and permissions.",
                cause=cause,
            ) from cause


def _format_cell(value: object) -> str:
    if isinstance(value, bool):
        raise TypeError("boolean values are not valid numeric CSV cells")
    if hasattr(value, "item"):
        value = cast("Any", value).item()
    if isinstance(value, int):
        return str(value)
    number = float(value)  # type: ignore[arg-type]
    if math.isnan(number):
        return "nan"
    if not math.isfinite(number):
        raise ValueError("infinite values are not valid CSV cells")
    return format(number, ".17g")


def _raise_cancelled() -> None:
    raise ExportError(
        "CSV export was cancelled.",
        code="DAQ.EXPORT.CANCELLED",
    )
