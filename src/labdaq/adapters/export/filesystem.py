"""Collision-safe filesystem finalization for derived artifacts."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from uuid import uuid4

from labdaq.errors import ExportError
from labdaq.storage.integrity import file_sha256

if TYPE_CHECKING:
    from pathlib import Path

    from labdaq.ports.plotting import FileReceipt


class FileSystemArtifactStore:
    def validate_destination(self, directory: Path) -> None:
        if not directory.is_dir() or not os.access(directory, os.W_OK):
            raise ExportError(
                "The export destination does not exist or is not writable.",
                code="DAQ.EXPORT.DESTINATION_INVALID",
                context={"destination": str(directory)},
                remediation="Select an existing writable directory.",
            )

    def reject_existing(self, paths: tuple[Path, ...]) -> None:
        existing = [str(path) for path in paths if path.exists()]
        if existing:
            raise ExportError(
                "An export destination path already exists.",
                code="DAQ.EXPORT.PATH_COLLISION",
                context={"paths": existing},
                remediation="Choose another base name; M07 never overwrites exports.",
            )

    def temporary_path(self, final_path: Path) -> Path:
        return final_path.with_name(f".{final_path.name}.{uuid4().hex}.tmp")

    def finalize_exclusive(self, temporary_path: Path, final_path: Path) -> FileReceipt:
        from labdaq.ports.plotting import FileReceipt

        if temporary_path.parent != final_path.parent:
            raise ValueError("temporary and final artifacts must share a filesystem directory")
        linked = False
        try:
            with temporary_path.open("r+b") as stream:
                os.fsync(stream.fileno())
            receipt = FileReceipt(temporary_path.stat().st_size, file_sha256(temporary_path))
            os.link(temporary_path, final_path)
            linked = True
            temporary_path.unlink()
        except FileExistsError as cause:
            raise ExportError(
                "The export path was created by another process.",
                code="DAQ.EXPORT.PATH_COLLISION",
                context={"path": str(final_path)},
                remediation="Retry with another base name.",
                cause=cause,
            ) from cause
        except OSError as cause:
            if linked:
                self.remove(final_path)
            raise ExportError(
                "The completed export could not be installed atomically.",
                code="DAQ.EXPORT.WRITE_FAILED",
                context={"temporary_path": str(temporary_path), "path": str(final_path)},
                remediation="Use a writable local destination that supports hard links.",
                cause=cause,
            ) from cause
        else:
            return receipt

    def remove(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def checksum(self, path: Path) -> FileReceipt:
        from labdaq.ports.plotting import FileReceipt

        try:
            return FileReceipt(path.stat().st_size, file_sha256(path))
        except OSError as cause:
            raise ExportError(
                "An export checksum could not be calculated.",
                code="DAQ.EXPORT.CHECKSUM_FAILED",
                context={"path": str(path)},
                remediation="Verify that the artifact remains readable.",
                cause=cause,
            ) from cause
