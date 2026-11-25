"""Verified full-extent TDMS interoperability export orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from labdaq.domain.naming import validate_slug
from labdaq.errors import ExportError
from labdaq.ports.raw_export import RawContainerExportReceipt

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from labdaq.ports.plotting import ArtifactStorePort, JsonArtifactWriterPort
    from labdaq.ports.processing import SessionProcessingSourcePort
    from labdaq.ports.raw_export import TdmsArtifactWriterPort
    from labdaq.ports.session import ReplaySourcePort, SessionReaderPort


class RawContainerExportService:
    def __init__(
        self,
        reader: SessionReaderPort,
        replay: ReplaySourcePort,
        processing: SessionProcessingSourcePort,
        writer: TdmsArtifactWriterPort,
        artifacts: ArtifactStorePort,
        json_writer: JsonArtifactWriterPort,
    ) -> None:
        self._reader = reader
        self._replay = replay
        self._processing = processing
        self._writer = writer
        self._artifacts = artifacts
        self._json = json_writer

    def export_tdms(
        self,
        source_session: Path,
        *,
        destination: Path | None = None,
        base_name: str | None = None,
        cancelled: Callable[[], bool] | None = None,
        progress: Callable[[int, int], None] | None = None,
        chunk_size: int = 8192,
    ) -> RawContainerExportReceipt:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        source = source_session.absolute()
        summary = self._reader.summarize_session(source)
        metadata = self._processing.processing_metadata(source)
        target_directory = (destination or source / "exports").absolute()
        self._artifacts.validate_destination(target_directory)
        name = base_name or (
            f"{_slug(summary.experiment_name)}-{summary.session_id.replace('-', '')[:8]}-raw"
        )
        validate_slug(name)
        output_path = target_directory / f"{name}.tdms"
        sidecar_path = target_directory / f"{name}.tdms.metadata.json"
        self._artifacts.reject_existing((output_path, sidecar_path))
        temporary = self._artifacts.temporary_path(output_path)
        sidecar_temporary = self._artifacts.temporary_path(sidecar_path)
        output_installed = False
        sidecar_installed = False
        is_cancelled = cancelled or (lambda: False)
        progressed_samples = 0

        def progressed(count: int) -> None:
            nonlocal progressed_samples
            progressed_samples += count
            if progress is not None:
                progress(progressed_samples, metadata.readable_sample_count)

        stream = self._replay.open_replay(source, chunk_size=chunk_size)
        try:
            self._writer.write(
                temporary,
                chunks=stream,
                metadata=metadata,
                summary=summary,
                cancelled=is_cancelled,
                progressed=progressed,
            )
            output_receipt = self._artifacts.finalize_exclusive(temporary, output_path)
            output_installed = True
            created_at = datetime.now(UTC)
            sidecar = {
                "schema_version": 1,
                "artifact_type": "tdms-raw-container-export",
                "source_session_id": summary.session_id,
                "source_format": summary.raw_container.format,
                "source_manifest_path": str(metadata.manifest_path),
                "source_manifest_sha256": metadata.manifest_sha256,
                "source_raw_digest": metadata.raw_data_digest,
                "source_completion_state": metadata.completion_state,
                "source_schema_version": metadata.source_schema_version,
                "source_application_version": metadata.source_application_version,
                "exported_sample_count": metadata.readable_sample_count,
                "sample_rate_hz": metadata.sample_rate_hz,
                "ordered_channel_ids": list(metadata.ordered_channel_ids),
                "raw_units": [channel.raw_unit.value for channel in metadata.channels],
                "channels": [channel.to_dict() for channel in summary.channels],
                "output_format": "tdms",
                "output_filename": output_path.name,
                "output_byte_count": output_receipt.byte_count,
                "output_sha256": output_receipt.sha256,
                "created_at": created_at.isoformat(),
                "timezone": "UTC",
                "replayable_prefix": summary.integrity.recoverability.value == "replayable-prefix",
                "warnings": (
                    ["SOURCE_RECOVERED_PREFIX"]
                    if summary.integrity.recoverability.value == "replayable-prefix"
                    else []
                ),
            }
            self._json.write(sidecar_temporary, sidecar)
            sidecar_receipt = self._artifacts.finalize_exclusive(sidecar_temporary, sidecar_path)
            sidecar_installed = True
            return RawContainerExportReceipt(
                source_session_id=summary.session_id,
                source_format=summary.raw_container.format,
                output_path=output_path,
                sidecar_path=sidecar_path,
                sample_count=metadata.readable_sample_count,
                channel_ids=metadata.ordered_channel_ids,
                byte_count=output_receipt.byte_count,
                sha256=output_receipt.sha256,
                sidecar_sha256=sidecar_receipt.sha256,
            )
        except Exception as cause:
            self._artifacts.remove(temporary)
            self._artifacts.remove(sidecar_temporary)
            if output_installed:
                self._artifacts.remove(output_path)
            if sidecar_installed:
                self._artifacts.remove(sidecar_path)
            if isinstance(cause, ExportError):
                raise
            raise ExportError(
                "The verified TDMS interoperability export failed.",
                code="DAQ.EXPORT.TDMS_WRITE_FAILED",
                context={"source_session": str(source)},
                remediation="Preserve the source session and retry to a writable local destination.",
                cause=cause,
            ) from cause
        finally:
            stream.close()


def _slug(value: str) -> str:
    candidate = "-".join(value.casefold().replace("_", "-").split())
    return "".join(character for character in candidate if character.isalnum() or character == "-")
