"""Versioned build identity for source and frozen LabDAQ distributions."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from functools import lru_cache
from importlib.resources import files
from typing import cast


@dataclass(frozen=True, slots=True)
class BuildInfo:
    """Trace one executable back to its release channel and source state."""

    schema_version: int
    release_channel: str
    build_id: str
    build_utc: str | None
    source_revision: str | None
    source_tree_sha256: str | None
    source_state: str
    architecture: str
    python_version: str
    runtime_dependencies: dict[str, str]
    schema_versions: dict[str, object]

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("only build-information schema version 1 is supported")
        for field_name in ("release_channel", "build_id", "source_state"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} must not be blank")
        if self.source_tree_sha256 is not None and len(self.source_tree_sha256) != 64:
            raise ValueError("source_tree_sha256 must be a SHA-256 digest or null")

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible representation with stable field names."""
        return cast("dict[str, object]", asdict(self))


@lru_cache(maxsize=1)
def get_build_info() -> BuildInfo:
    """Load the build identity bundled with the installed distribution."""
    resource = files("labdaq.resources").joinpath("build-info.json")
    payload = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("build-info.json must contain an object")
    return BuildInfo(
        schema_version=int(payload["schema_version"]),
        release_channel=str(payload["release_channel"]),
        build_id=str(payload["build_id"]),
        build_utc=_optional_text(payload.get("build_utc")),
        source_revision=_optional_text(payload.get("source_revision")),
        source_tree_sha256=_optional_text(payload.get("source_tree_sha256")),
        source_state=str(payload["source_state"]),
        architecture=str(payload["architecture"]),
        python_version=str(payload["python_version"]),
        runtime_dependencies={
            str(name): str(version)
            for name, version in _mapping(payload.get("runtime_dependencies")).items()
        },
        schema_versions={
            str(name): value for name, value in _mapping(payload.get("schema_versions")).items()
        },
    )


def _mapping(value: object) -> dict[object, object]:
    if not isinstance(value, dict):
        raise TypeError("build-information mapping field must contain an object")
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered or None
