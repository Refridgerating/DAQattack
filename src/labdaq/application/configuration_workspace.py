"""Field-addressable, storage-neutral configuration workspace for DAQ-M09."""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

from labdaq._context import freeze_mapping
from labdaq.application.processing_service import CalibrationResolutionService
from labdaq.domain.naming import make_slug
from labdaq.domain.timing import rates_equivalent, samples_for_interval
from labdaq.domain.validation import validate_experiment_config
from labdaq.errors import ConfigurationValidationError, ErrorSeverity, ProcessingError

if TYPE_CHECKING:
    from pathlib import Path

    from labdaq.domain.models import ExperimentConfig, HardwareProfile
    from labdaq.ports.configuration import ConfigurationDocumentPort

_PATH_PART = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\])?")
_ALIASES = {
    "experiment.name": "session.experiment_name",
    "experiment.description": "session.description",
    "experiment.operator": "session.operator",
    "experiment.sample_id": "session.sample_id",
    "experiment.notes": "session.notes",
    "storage.parent_directory": "session.parent_output_directory",
    "acquisition.duration_s": "acquisition.requested_duration_seconds",
    "acquisition.chunk_size": "acquisition.chunk_config.samples_per_channel",
}
_ALLOWED_ROOTS = {
    "session",
    "acquisition",
    "channels",
    "sensors",
    "calibrations",
    "conditioning_chains",
}

_DAQ_CHANNEL_FIELDS = {
    "enabled",
    "channel_order",
    "channel_id",
    "physical_channel",
    "requested_input_min",
    "requested_input_max",
    "raw_quantity",
    "raw_unit",
}

_BASELINE_RECIPE = ("baseline", "v1")
_LOW_RATE_RECIPE = ("baseline-low-rate", "v1")


def recipe_for_sample_rate(sample_rate_hz: float) -> tuple[str, str]:
    """Return the bundled causal recipe compatible with an NI-9239 rate."""
    return _BASELINE_RECIPE if sample_rate_hz >= 25_000.0 else _LOW_RATE_RECIPE


def field_affects_hardware_validation(field_path: str) -> bool:
    """Return whether an editable field can change the configured DAQ task."""
    wire_path = _ALIASES.get(field_path, field_path)
    if wire_path.startswith("acquisition."):
        return True
    if not wire_path.startswith("channels["):
        return False
    suffix = wire_path.split("].", 1)[1] if "]." in wire_path else ""
    return suffix.split(".", 1)[0] in _DAQ_CHANNEL_FIELDS


@dataclass(frozen=True, slots=True)
class FieldIssue:
    code: str
    field_path: str
    severity: ErrorSeverity
    message: str
    remediation: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "field_path": self.field_path,
            "severity": self.severity.value,
            "message": self.message,
            "remediation": self.remediation,
        }


@dataclass(frozen=True, slots=True)
class ConfigurationNormalization:
    field_path: str
    normalized_value: object
    explanation: str


@dataclass(frozen=True, slots=True)
class ChannelCalibrationStatus:
    channel_id: str
    calibration_id: str
    status: Literal[
        "traceable",
        "nominal",
        "externally_validated",
        "synthetic",
        "unverified",
        "unresolved",
    ]
    source_reference: str | None


@dataclass(frozen=True, slots=True)
class ConfigurationDraftSnapshot:
    revision: int
    template_id: str | None
    raw_fields: Mapping[str, object]
    normalized: ExperimentConfig | None
    issues: tuple[FieldIssue, ...]
    normalizations: tuple[ConfigurationNormalization, ...]
    calibration_statuses: tuple[ChannelCalibrationStatus, ...]
    active_recipe_id: str
    active_recipe_version: str

    @property
    def valid(self) -> bool:
        return self.normalized is not None and not any(
            issue.severity in {ErrorSeverity.ERROR, ErrorSeverity.CRITICAL} for issue in self.issues
        )


class ConfigurationWorkspaceService:
    """Own an in-memory v1 draft while adapters retain JSON/schema details."""

    def __init__(
        self,
        documents: ConfigurationDocumentPort,
        *,
        active_recipe_id: str,
        active_recipe_version: str,
    ) -> None:
        self._documents = documents
        self._active_recipe_id = active_recipe_id
        self._active_recipe_version = active_recipe_version
        self._profile: HardwareProfile | None = None
        self._raw: dict[str, object] = {}
        self._template_id: str | None = None
        self._revision = 0
        self._normalizations: tuple[ConfigurationNormalization, ...] = ()

    @property
    def profile(self) -> HardwareProfile:
        if self._profile is None:
            raise RuntimeError("A hardware profile has not been selected")
        return self._profile

    def create_from_template(
        self,
        profile: HardwareProfile,
        template_id: str,
    ) -> ConfigurationDraftSnapshot:
        self._profile = profile
        self._template_id = template_id
        self._raw = deepcopy(dict(self._documents.load_bundled_experiment_template(template_id)))
        self._select_recipe_for_raw_rate()
        self._revision += 1
        self._normalizations = ()
        return self.snapshot()

    def load(self, profile: HardwareProfile, path: Path) -> ConfigurationDraftSnapshot:
        self._profile = profile
        self._template_id = None
        self._raw = deepcopy(dict(self._documents.load_experiment_draft(path)))
        self._select_recipe_for_raw_rate()
        self._revision += 1
        self._normalizations = ()
        return self.snapshot()

    def update_field(self, field_path: str, value: object) -> ConfigurationDraftSnapshot:
        if not self._raw:
            raise RuntimeError("A configuration draft has not been created")
        wire_path = _ALIASES.get(field_path, field_path)
        if wire_path == "acquisition.sample_rate_hz":
            if not isinstance(value, bool) and isinstance(value, (int, float)):
                return self.update_sample_rate(float(value))
        root = wire_path.split(".", 1)[0].split("[", 1)[0]
        if root not in _ALLOWED_ROOTS:
            raise ValueError(f"Unsupported configuration field path: {field_path}")
        _assign_path(self._raw, wire_path, value)
        normalizations: list[ConfigurationNormalization] = []
        if field_path == "experiment.name" and isinstance(value, str) and value.strip():
            try:
                slug = make_slug(value)
            except ValueError:
                pass
            else:
                _assign_path(self._raw, "session.experiment_slug", slug)
                normalizations.append(
                    ConfigurationNormalization(
                        "session.experiment_slug",
                        slug,
                        "The session slug was explicitly regenerated from the experiment name.",
                    )
                )
        if wire_path.startswith("channels[") and (
            wire_path.endswith(".enabled") or wire_path.endswith(".channel_order")
        ):
            channel_ids = _enabled_channel_ids(self._raw)
            _assign_path(self._raw, "acquisition.channel_ids", list(channel_ids))
            normalizations.append(
                ConfigurationNormalization(
                    "acquisition.channel_ids",
                    channel_ids,
                    "Enabled channels were normalized into authoritative channel order.",
                )
            )
        self._revision += 1
        self._normalizations = tuple(normalizations)
        return self.snapshot()

    def update_sample_rate(self, sample_rate_hz: float) -> ConfigurationDraftSnapshot:
        """Atomically select rate, the default 100 ms batch, and its recipe."""
        if not self._raw:
            raise RuntimeError("A configuration draft has not been created")
        selected_rate = next(
            (
                allowed
                for allowed in self.profile.application_supported_sample_rates_hz
                if rates_equivalent(sample_rate_hz, allowed)
            ),
            None,
        )
        if selected_rate is None:
            raise ValueError(
                f"{sample_rate_hz:g} Hz is not an allowed rate for profile revision "
                f"{self.profile.profile_revision}"
            )
        samples_per_channel = samples_for_interval(selected_rate)
        recipe_id, recipe_version = recipe_for_sample_rate(selected_rate)
        _assign_path(self._raw, "acquisition.sample_rate_hz", selected_rate)
        _assign_path(
            self._raw,
            "acquisition.chunk_config.samples_per_channel",
            samples_per_channel,
        )
        _assign_path(self._raw, "draft_processing_recipe_id", recipe_id)
        self._active_recipe_id = recipe_id
        self._active_recipe_version = recipe_version
        self._revision += 1
        self._normalizations = (
            ConfigurationNormalization(
                "acquisition.chunk_size",
                samples_per_channel,
                "Samples/read/channel was reset to the 100 ms default for the selected rate.",
            ),
            ConfigurationNormalization(
                "processing.recipe",
                f"{recipe_id}@{recipe_version}",
                "The compatible bundled processing recipe was selected for this rate.",
            ),
        )
        return self.snapshot()

    def _select_recipe_for_raw_rate(self) -> None:
        acquisition = self._raw.get("acquisition")
        if not isinstance(acquisition, Mapping):
            return
        rate = acquisition.get("sample_rate_hz")
        if isinstance(rate, bool) or not isinstance(rate, (int, float)):
            return
        self._active_recipe_id, self._active_recipe_version = recipe_for_sample_rate(float(rate))

    def snapshot(self) -> ConfigurationDraftSnapshot:
        issues: list[FieldIssue] = []
        normalized: ExperimentConfig | None = None
        calibration_statuses = _raw_calibration_statuses(self._raw)
        if self._profile is None or not self._raw:
            issues.append(
                FieldIssue(
                    "DAQ.CONFIG.WORKSPACE_EMPTY",
                    "$",
                    ErrorSeverity.ERROR,
                    "No experiment configuration is loaded.",
                    "Select a hardware profile and experiment template.",
                )
            )
        else:
            try:
                candidate = self._documents.decode_experiment_draft(self._raw)
            except ConfigurationValidationError as error:
                issues.extend(_configuration_error_issues(error))
            else:
                semantic = validate_experiment_config(candidate, self._profile)
                issues.extend(
                    FieldIssue(
                        "DAQ.CONFIG.SEMANTIC_INVALID",
                        _display_path(issue.field_path),
                        ErrorSeverity.ERROR,
                        issue.rule,
                        issue.correction,
                    )
                    for issue in semantic
                )
                required_recipe_id, required_recipe_version = recipe_for_sample_rate(
                    candidate.acquisition.sample_rate_hz
                )
                if candidate.draft_processing_recipe_id not in {
                    required_recipe_id,
                    f"{required_recipe_id}-{required_recipe_version}",
                }:
                    issues.append(
                        FieldIssue(
                            "DAQ.CONFIG.BASELINE_RECIPE_REQUIRED",
                            "processing.recipe",
                            ErrorSeverity.ERROR,
                            "The configuration does not select the recipe compatible with its rate.",
                            f"Use {required_recipe_id}@{required_recipe_version}.",
                        )
                    )
                if not semantic:
                    try:
                        resolved, notices = CalibrationResolutionService().resolve_experiment(
                            self._profile,
                            candidate,
                            candidate.acquisition.channel_ids,
                        )
                    except ProcessingError as error:
                        issues.extend(_calibration_error_issues(error, candidate))
                    else:
                        calibration_statuses = tuple(
                            ChannelCalibrationStatus(
                                item.logical_channel_id,
                                item.calibration_id,
                                item.quality.value,
                                item.source_reference,
                            )
                            for item in resolved
                        )
                        issues.extend(
                            FieldIssue(
                                notice.code,
                                _channel_path(candidate, notice.channel_id, "calibration"),
                                ErrorSeverity(notice.severity),
                                notice.message,
                                "Review the calibration provenance before qualification.",
                            )
                            for notice in notices
                        )
                if not any(
                    issue.severity in {ErrorSeverity.ERROR, ErrorSeverity.CRITICAL}
                    for issue in issues
                ):
                    normalized = candidate
        return ConfigurationDraftSnapshot(
            revision=self._revision,
            template_id=self._template_id,
            raw_fields=freeze_mapping(deepcopy(self._raw)),
            normalized=normalized,
            issues=tuple(issues),
            normalizations=self._normalizations,
            calibration_statuses=calibration_statuses,
            active_recipe_id=self._active_recipe_id,
            active_recipe_version=self._active_recipe_version,
        )


def field_value(raw: Mapping[str, object], field_path: str) -> object | None:
    """Read one display or wire field path from a frozen draft snapshot."""
    wire_path = _ALIASES.get(field_path, field_path)
    current: object = raw
    for name, index in _parts(wire_path):
        if not isinstance(current, Mapping) or name not in current:
            return None
        current = current[name]
        if index is not None:
            if not isinstance(current, (list, tuple)) or index >= len(current):
                return None
            current = current[index]
    return current


def _assign_path(raw: dict[str, object], field_path: str, value: object) -> None:
    parts = _parts(field_path)
    if not parts:
        raise ValueError(f"Invalid field path: {field_path}")
    current: object = raw
    for name, index in parts[:-1]:
        if not isinstance(current, dict) or name not in current:
            raise ValueError(f"Unknown configuration field path: {field_path}")
        current = current[name]
        if index is not None:
            if not isinstance(current, list) or index >= len(current):
                raise ValueError(f"Unknown configuration field path: {field_path}")
            current = current[index]
    name, index = parts[-1]
    if not isinstance(current, dict) or name not in current:
        raise ValueError(f"Unknown configuration field path: {field_path}")
    if index is None:
        current[name] = value
        return
    collection = current[name]
    if not isinstance(collection, list) or index >= len(collection):
        raise ValueError(f"Unknown configuration field path: {field_path}")
    collection[index] = value


def _parts(field_path: str) -> list[tuple[str, int | None]]:
    result: list[tuple[str, int | None]] = []
    for raw_part in field_path.split("."):
        match = _PATH_PART.fullmatch(raw_part)
        if match is None:
            raise ValueError(f"Invalid field path: {field_path}")
        result.append((match.group(1), int(match.group(2)) if match.group(2) else None))
    return result


def _enabled_channel_ids(raw: Mapping[str, object]) -> tuple[str, ...]:
    values = raw.get("channels")
    if not isinstance(values, (list, tuple)):
        return ()
    channels = [
        item for item in values if isinstance(item, Mapping) and item.get("enabled") is True
    ]
    channels.sort(key=lambda item: int(cast("int", item.get("channel_order", 0))))
    return tuple(str(item.get("channel_id", "")) for item in channels)


def _configuration_error_issues(error: ConfigurationValidationError) -> tuple[FieldIssue, ...]:
    raw_issues = error.context.get("issues", ())
    result: list[FieldIssue] = []
    if isinstance(raw_issues, (list, tuple)):
        for raw in raw_issues:
            if not isinstance(raw, Mapping):
                continue
            result.append(
                FieldIssue(
                    "DAQ.CONFIG.FIELD_INVALID",
                    _display_path(str(raw.get("field_path", "$"))),
                    ErrorSeverity.ERROR,
                    str(raw.get("rule", error.message)),
                    str(raw.get("correction", error.remediation or "Correct the field.")),
                )
            )
    return tuple(result) or (
        FieldIssue(
            error.code,
            "$",
            error.severity,
            error.message,
            error.remediation or "Correct the configuration.",
        ),
    )


def _calibration_error_issues(
    error: ProcessingError,
    experiment: ExperimentConfig,
) -> tuple[FieldIssue, ...]:
    raw_failures = error.context.get("channel_failures", ())
    issues: list[FieldIssue] = []
    if isinstance(raw_failures, (list, tuple)):
        for raw in raw_failures:
            if isinstance(raw, Mapping):
                channel_id = str(raw.get("channel_id", ""))
                issues.append(
                    FieldIssue(
                        str(raw.get("code", error.code)),
                        _channel_path(experiment, channel_id, "calibration"),
                        ErrorSeverity.ERROR,
                        str(raw.get("reason", error.message)),
                        error.remediation or "Complete the channel calibration.",
                    )
                )
    return tuple(issues) or (
        FieldIssue(
            error.code,
            "channels",
            ErrorSeverity.ERROR,
            error.message,
            error.remediation or "Complete every channel calibration.",
        ),
    )


def _raw_calibration_statuses(raw: Mapping[str, object]) -> tuple[ChannelCalibrationStatus, ...]:
    raw_channels = raw.get("channels")
    raw_calibrations = raw.get("calibrations")
    if not isinstance(raw_channels, (list, tuple)) or not isinstance(
        raw_calibrations, (list, tuple)
    ):
        return ()
    calibrations = {
        str(item.get("calibration_id")): item
        for item in raw_calibrations
        if isinstance(item, Mapping)
    }
    quality = {
        "certificate": "traceable",
        "manufacturer_nominal": "nominal",
        "external_validation": "externally_validated",
        "test_fixture": "synthetic",
        "operator_transcribed": "unverified",
    }
    result: list[ChannelCalibrationStatus] = []
    for channel in raw_channels:
        if not isinstance(channel, Mapping):
            continue
        calibration_id = str(channel.get("calibration_id", ""))
        calibration = calibrations.get(calibration_id, {})
        sensitivity = calibration.get("sensitivity_value")
        reference = calibration.get("source_reference")
        source_kind = str(calibration.get("source_kind", ""))
        status = quality.get(source_kind, "unresolved")
        if sensitivity is None or not isinstance(reference, str) or not reference.strip():
            status = "unresolved"
        result.append(
            ChannelCalibrationStatus(
                str(channel.get("channel_id", "")),
                calibration_id,
                cast(
                    "Literal['traceable', 'nominal', 'externally_validated', 'synthetic', 'unverified', 'unresolved']",
                    status,
                ),
                reference if isinstance(reference, str) and reference else None,
            )
        )
    return tuple(result)


def _display_path(path: str) -> str:
    normalized = path.removeprefix("$").removeprefix(".")
    reverse = {value: key for key, value in _ALIASES.items()}
    return reverse.get(normalized, normalized or "$")


def _channel_path(experiment: ExperimentConfig, channel_id: str | None, suffix: str) -> str:
    for index, channel in enumerate(experiment.channels):
        if channel.channel_id == channel_id:
            return f"channels[{index}].{suffix}"
    return "channels"
