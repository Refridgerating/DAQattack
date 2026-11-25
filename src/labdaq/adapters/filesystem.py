"""Filesystem capacity adapter used by durable session storage."""

from __future__ import annotations

import os
import re
import shutil
from typing import TYPE_CHECKING

from labdaq.errors import InvalidSessionParentError
from labdaq.storage.policy import estimate_required_bytes

_SESSION_DIRECTORY_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}_.+_[0-9a-fA-F]{8}$")
_SESSION_MARKERS = ("manifest.json", "raw.h5", "raw.tdms", "events.jsonl")

if TYPE_CHECKING:
    from pathlib import Path

    from labdaq.domain.models import ExperimentConfig
    from labdaq.ports.session import RecordingPolicy


class FileSystemCapacityAdapter:
    def free_bytes(self, path: Path) -> int:
        return int(shutil.disk_usage(path).free)

    def is_writable(self, path: Path) -> bool:
        return path.is_dir() and os.access(path, os.W_OK)

    def exists(self, path: Path) -> bool:
        return path.exists()

    def is_directory(self, path: Path) -> bool:
        return path.is_dir()

    def estimate_required_bytes(
        self,
        experiment: ExperimentConfig,
        policy: RecordingPolicy,
    ) -> int:
        return estimate_required_bytes(experiment, policy)


class FileSystemSessionCatalogAdapter:
    """Discover direct child candidates without parsing session documents."""

    def discover_session_paths(self, parent: Path) -> tuple[Path, ...]:
        if not parent.exists() or not parent.is_dir():
            raise InvalidSessionParentError(
                "The selected session catalog parent is not an existing directory.",
                code="DAQ.STORAGE.INVALID_SESSION_PARENT",
                context={"parent_directory": str(parent)},
                remediation="Choose an existing session parent directory.",
            )
        candidates = []
        try:
            children = tuple(parent.iterdir())
        except OSError as cause:
            raise InvalidSessionParentError(
                "The selected session catalog parent could not be read.",
                code="DAQ.STORAGE.INVALID_SESSION_PARENT",
                context={"parent_directory": str(parent)},
                remediation="Check directory permissions and try again.",
                cause=cause,
            ) from cause
        for child in children:
            if not child.is_dir():
                continue
            if _SESSION_DIRECTORY_PATTERN.fullmatch(child.name) or any(
                (child / marker).exists() for marker in _SESSION_MARKERS
            ):
                candidates.append(child.absolute())
        return tuple(sorted(candidates, key=lambda item: item.name.casefold()))
