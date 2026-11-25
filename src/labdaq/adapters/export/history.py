"""Filesystem discovery of durable DAQ-M07 export manifests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from labdaq.adapters.plotting_json import JsonPlotExportCodec
from labdaq.errors import LabDAQError
from labdaq.ports.operator import ExportHistoryEntry

if TYPE_CHECKING:
    from pathlib import Path


class FileSystemExportHistoryAdapter:
    """Discover export history while retaining malformed records as diagnostics."""

    def __init__(self, codec: JsonPlotExportCodec | None = None) -> None:
        self._codec = codec or JsonPlotExportCodec()

    def list_export_history(self, path: Path) -> tuple[ExportHistoryEntry, ...]:
        session_path = path.absolute()
        candidates = sorted(
            (
                candidate
                for folder_name in ("exports", "plots")
                for candidate in (session_path / folder_name).glob("export-manifest-*.json")
                if candidate.is_file()
            ),
            key=lambda candidate: candidate.name,
            reverse=True,
        )
        entries = []
        for candidate in candidates:
            try:
                manifest = self._codec.loads_manifest(candidate.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, LabDAQError, ValueError) as error:
                entries.append(
                    ExportHistoryEntry(
                        manifest_path=candidate,
                        valid=False,
                        error_code=(
                            error.code
                            if isinstance(error, LabDAQError)
                            else "DAQ.EXPORT.MANIFEST_READ_FAILED"
                        ),
                        error_message=str(error),
                    )
                )
            else:
                entries.append(
                    ExportHistoryEntry(
                        manifest_path=candidate,
                        valid=True,
                        manifest=manifest,
                    )
                )
        return tuple(entries)
