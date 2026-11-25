"""Application-facing configuration document contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from labdaq.domain.models import (
        ExperimentConfig,
        HardwareProfile,
        ProcessingRecipeEnvelope,
    )


@dataclass(frozen=True, slots=True)
class HardwareProfileDescriptor:
    hardware_profile_id: str
    profile_revision: int
    display_name: str


@dataclass(frozen=True, slots=True)
class ExperimentTemplateDescriptor:
    template_id: str
    display_name: str
    backend_id: str
    hardware_profile_id: str
    profile_revision: int


class ConfigurationDocumentPort(Protocol):
    """Load typed configuration documents without exposing their storage format."""

    def load_hardware_profile(self, path: Path) -> HardwareProfile: ...

    def load_experiment_config(self, path: Path) -> ExperimentConfig: ...

    def load_processing_recipe(self, path: Path) -> ProcessingRecipeEnvelope: ...

    def load_bundled_hardware_profile(
        self,
        hardware_profile_id: str,
        profile_revision: int,
    ) -> HardwareProfile: ...

    def load_bundled_processing_recipe(
        self,
        recipe_id: str,
    ) -> ProcessingRecipeEnvelope: ...

    def list_bundled_hardware_profiles(self) -> tuple[HardwareProfileDescriptor, ...]: ...

    def list_bundled_experiment_templates(
        self,
    ) -> tuple[ExperimentTemplateDescriptor, ...]: ...

    def load_bundled_experiment_template(
        self,
        template_id: str,
    ) -> Mapping[str, object]: ...

    def load_experiment_draft(self, path: Path) -> Mapping[str, object]: ...

    def decode_experiment_draft(
        self,
        raw: Mapping[str, object],
        *,
        document: str | None = None,
    ) -> ExperimentConfig: ...


class BundledRecipeResolverPort(Protocol):
    """Resolve a named bundled recipe without exposing its resource adapter."""

    def load_bundled_processing_recipe(
        self,
        recipe_id: str,
    ) -> ProcessingRecipeEnvelope: ...
