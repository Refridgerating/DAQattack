"""Resilient, bounded-metadata session catalog for DAQ-M09."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from labdaq.errors import LabDAQError
from labdaq.ports.session import VerificationLevel

if TYPE_CHECKING:
    from pathlib import Path

    from labdaq.ports.session import SessionCatalogPort, SessionReaderPort, SessionSummary


@dataclass(frozen=True, slots=True)
class SessionCatalogEntry:
    session_path: Path
    valid: bool
    summary: SessionSummary | None
    error_code: str | None = None
    error_message: str | None = None
    remediation: str | None = None


@dataclass(frozen=True, slots=True)
class SessionCatalog:
    parent_directory: Path
    entries: tuple[SessionCatalogEntry, ...]
    scanned_at: datetime


class SessionCatalogService:
    def __init__(self, catalog: SessionCatalogPort, reader: SessionReaderPort) -> None:
        self._catalog = catalog
        self._reader = reader

    def scan(self, parent: Path) -> SessionCatalog:
        entries: list[SessionCatalogEntry] = []
        for path in self._catalog.discover_session_paths(parent):
            try:
                summary = self._reader.summarize_session(path, level=VerificationLevel.FAST)
                entries.append(SessionCatalogEntry(path, True, summary))
            except LabDAQError as error:
                entries.append(
                    SessionCatalogEntry(
                        path,
                        False,
                        None,
                        error.code,
                        error.message,
                        error.remediation,
                    )
                )
            except Exception:
                entries.append(
                    SessionCatalogEntry(
                        path,
                        False,
                        None,
                        "DAQ.SESSION.CATALOG_ENTRY_INVALID",
                        "The session metadata could not be summarized.",
                        "Preserve the directory and inspect its manifest and diagnostics.",
                    )
                )
        valid = sorted(
            (entry for entry in entries if entry.valid and entry.summary is not None),
            key=lambda entry: (
                (
                    entry.summary.started_at
                    if entry.summary is not None
                    else datetime.min.replace(tzinfo=UTC)
                ),
                entry.session_path.name.casefold(),
            ),
            reverse=True,
        )
        invalid = sorted(
            (entry for entry in entries if not entry.valid),
            key=lambda entry: entry.session_path.name.casefold(),
        )
        return SessionCatalog(parent.absolute(), tuple((*valid, *invalid)), datetime.now(UTC))
