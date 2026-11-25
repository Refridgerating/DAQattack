"""Storage-neutral contracts for raw-container interoperability exports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

    from labdaq.ports.acquisition import AcquisitionChunk
    from labdaq.ports.processing import ProcessingSourceMetadata
    from labdaq.ports.session import SessionSummary


@dataclass(frozen=True, slots=True)
class RawContainerExportReceipt:
    source_session_id: str
    source_format: str
    output_path: Path
    sidecar_path: Path
    sample_count: int
    channel_ids: tuple[str, ...]
    byte_count: int
    sha256: str
    sidecar_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "source_session_id": self.source_session_id,
            "source_format": self.source_format,
            "output_path": str(self.output_path),
            "sidecar_path": str(self.sidecar_path),
            "sample_count": self.sample_count,
            "channel_ids": list(self.channel_ids),
            "byte_count": self.byte_count,
            "sha256": self.sha256,
            "sidecar_sha256": self.sidecar_sha256,
            "state": "completed",
        }


class TdmsArtifactWriterPort(Protocol):
    def write(
        self,
        destination: Path,
        *,
        chunks: Iterable[AcquisitionChunk],
        metadata: ProcessingSourceMetadata,
        summary: SessionSummary,
        cancelled: Callable[[], bool],
        progressed: Callable[[int], None],
    ) -> None: ...
