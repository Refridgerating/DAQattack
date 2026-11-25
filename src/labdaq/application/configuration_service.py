"""Headless use cases for validating configuration documents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from labdaq.domain.validation import (
    raise_for_issues,
    validate_experiment_config,
    validate_hardware_profile,
    validate_processing_recipe,
)

if TYPE_CHECKING:
    from pathlib import Path

    from labdaq.ports.configuration import ConfigurationDocumentPort


@dataclass(frozen=True, slots=True)
class ValidationSummary:
    document_type: Literal["hardware_profile", "experiment_config", "processing_recipe"]
    document_id: str
    schema_version: int
    revision: int | str
    hardware_profile_id: str | None
    channel_count: int | None
    sample_rate_hz: float | None
    overall: Literal["ok"] = "ok"

    def to_dict(self) -> dict[str, object]:
        return {
            "document_type": self.document_type,
            "document_id": self.document_id,
            "schema_version": self.schema_version,
            "revision": self.revision,
            "hardware_profile_id": self.hardware_profile_id,
            "channel_count": self.channel_count,
            "sample_rate_hz": self.sample_rate_hz,
            "overall": self.overall,
        }


class ConfigurationService:
    """Coordinate document loading, profile resolution, and semantic validation."""

    def __init__(self, documents: ConfigurationDocumentPort) -> None:
        self._documents = documents

    def validate_profile_file(self, path: Path) -> ValidationSummary:
        profile = self._documents.load_hardware_profile(path)
        raise_for_issues(
            validate_hardware_profile(profile),
            document_type="hardware_profile",
            document=str(path),
        )
        return ValidationSummary(
            document_type="hardware_profile",
            document_id=profile.hardware_profile_id,
            schema_version=profile.schema_version,
            revision=profile.profile_revision,
            hardware_profile_id=profile.hardware_profile_id,
            channel_count=len(profile.supported_physical_channels),
            sample_rate_hz=None,
        )

    def validate_experiment_file(self, path: Path) -> ValidationSummary:
        config = self._documents.load_experiment_config(path)
        profile = self._documents.load_bundled_hardware_profile(
            config.hardware_profile_id,
            config.compatible_profile_revision,
        )
        raise_for_issues(
            validate_experiment_config(config, profile),
            document_type="experiment_config",
            document=str(path),
        )
        return ValidationSummary(
            document_type="experiment_config",
            document_id=config.experiment_config_id,
            schema_version=config.schema_version,
            revision=config.config_revision,
            hardware_profile_id=config.hardware_profile_id,
            channel_count=len(config.acquisition.channel_ids),
            sample_rate_hz=config.acquisition.sample_rate_hz,
        )

    def validate_recipe_file(self, path: Path) -> ValidationSummary:
        recipe = self._documents.load_processing_recipe(path)
        raise_for_issues(
            validate_processing_recipe(recipe),
            document_type="processing_recipe",
            document=str(path),
        )
        return ValidationSummary(
            document_type="processing_recipe",
            document_id=recipe.recipe_id,
            schema_version=recipe.schema_version,
            revision=recipe.recipe_version,
            hardware_profile_id=None,
            channel_count=None,
            sample_rate_hz=None,
        )
