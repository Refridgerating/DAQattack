"""Persistence-neutral desktop preference records and port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class UserPreferences:
    schema_version: int = 1
    window_geometry: bytes | None = None
    recent_session_parents: tuple[Path, ...] = ()
    recent_browser_roots: tuple[Path, ...] = ()
    production_profile_path: Path | None = None
    production_configuration_path: Path | None = None
    production_qualification_plan_path: Path | None = None
    commissioning_profile_path: Path | None = None
    commissioning_configuration_path: Path | None = None
    commissioning_qualification_plan_path: Path | None = None

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("Only user-preference schema version 1 is supported")
        if len(self.recent_session_parents) > 8 or len(self.recent_browser_roots) > 8:
            raise ValueError("At most eight recent paths are retained per category")


class UserPreferencePort(Protocol):
    def load(self) -> UserPreferences: ...

    def save(self, preferences: UserPreferences) -> None: ...

    def sync(self) -> None: ...
