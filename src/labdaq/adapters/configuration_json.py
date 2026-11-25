"""Strict JSON Schema and codec adapter for M01 configuration documents."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from enum import Enum
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from jsonschema import Draft202012Validator, FormatChecker

from labdaq.domain.enums import (
    CalibrationModel,
    CalibrationSourceKind,
    ChannelRole,
    ExecutionClass,
    ExperimentMode,
    FilterInitializationPolicy,
    PhysicalQuantity,
    Polarity,
    ProcessingDisposition,
    ProcessingStageKind,
    RecipeStatus,
    SamplingMode,
    SensitivityUnit,
    SensorAxis,
    SensorType,
    TerminalConfiguration,
    TriggerEdge,
    TriggerMode,
    Unit,
)
from labdaq.domain.models import (
    AcquisitionConfig,
    CalibrationConfig,
    ChannelConfig,
    ChargeConverterEvidence,
    ConditioningChainConfig,
    CoordinateFrameConfig,
    DataChunkConfig,
    DeviceConfig,
    ExperimentConfig,
    FftConfiguration,
    HardwareProfile,
    MetricConfiguration,
    ModuleConfig,
    NiTerminalConditioningEvidence,
    NominalCalibrationFallbackPolicy,
    PhysicalChannelRef,
    ProcessingRecipeEnvelope,
    ProcessingStageDefinition,
    PsdConfiguration,
    SensorConfig,
    SensorPowerEvidence,
    SessionConfig,
    TriggerConfig,
)
from labdaq.domain.validation import (
    raise_for_issues,
    validate_hardware_profile,
    validate_processing_recipe,
)
from labdaq.errors import ConfigurationValidationError, UnsupportedSchemaVersionError
from labdaq.ports.configuration import ExperimentTemplateDescriptor, HardwareProfileDescriptor

if TYPE_CHECKING:
    from collections.abc import Mapping
    from importlib.resources.abc import Traversable

DocumentType = Literal["hardware_profile", "experiment_config", "processing_recipe"]
DomainDocument = HardwareProfile | ExperimentConfig | ProcessingRecipeEnvelope

_SCHEMA_FILES: dict[DocumentType, str] = {
    "hardware_profile": "hardware-profile.schema.json",
    "experiment_config": "experiment-config.schema.json",
    "processing_recipe": "processing-recipe.schema.json",
}
_BUNDLED_PROFILES = {
    ("cdaq-9171-ni-9239-four-channel", 1): (
        "config/hardware-profiles/cdaq-9171-ni-9239-four-channel-v1.json"
    ),
    ("cdaq-9171-ni-9239-four-channel", 2): (
        "config/hardware-profiles/cdaq-9171-ni-9239-four-channel-v2.json"
    ),
}
_BUNDLED_RECIPES = {
    "baseline": "config/processing-recipes/baseline-v1.json",
    "baseline-v1": "config/processing-recipes/baseline-v1.json",
    "baseline-v1-draft": "config/processing-recipes/baseline-v1-draft.json",
    "baseline-low-rate": "config/processing-recipes/baseline-low-rate-v1.json",
    "baseline-low-rate-v1": "config/processing-recipes/baseline-low-rate-v1.json",
}
_BUNDLED_EXPERIMENT_TEMPLATES = {
    "raw-voltage-v1": "config/experiments/raw-voltage-v1.json",
    "raw-voltage-v2": "config/experiments/raw-voltage-v1.json",
    "four-channel-vibration-physical-v1": (
        "config/experiments/four-channel-vibration-template-v1.json"
    ),
    "four-channel-vibration-synthetic-v1": (
        "config/experiments/four-channel-vibration-synthetic-v1.json"
    ),
    "four-channel-vibration-physical-v2": (
        "config/experiments/four-channel-vibration-template-v1.json"
    ),
    "four-channel-vibration-synthetic-v2": (
        "config/experiments/four-channel-vibration-synthetic-v1.json"
    ),
    "accelerometer-commissioning-v2": (
        "config/experiments/four-channel-vibration-synthetic-v1.json"
    ),
    "accelerometer-commissioning-v3": (
        "config/experiments/four-channel-vibration-synthetic-v1.json"
    ),
}


class JsonConfigurationAdapter:
    """Decode, encode, and load all supported version-1 JSON documents."""

    def load_hardware_profile(self, path: Path) -> HardwareProfile:
        profile = self.decode_hardware_profile(
            self._read_path(path, "hardware_profile"),
            document=str(path),
        )
        raise_for_issues(
            validate_hardware_profile(profile),
            document_type="hardware_profile",
            document=str(path),
        )
        return profile

    def load_experiment_config(self, path: Path) -> ExperimentConfig:
        return self.decode_experiment_config(
            self._read_path(path, "experiment_config"),
            document=str(path),
        )

    def load_processing_recipe(self, path: Path) -> ProcessingRecipeEnvelope:
        recipe = self.decode_processing_recipe(
            self._read_path(path, "processing_recipe"),
            document=str(path),
        )
        raise_for_issues(
            validate_processing_recipe(recipe),
            document_type="processing_recipe",
            document=str(path),
        )
        return recipe

    def load_bundled_hardware_profile(
        self,
        hardware_profile_id: str,
        profile_revision: int,
    ) -> HardwareProfile:
        relative_path = _BUNDLED_PROFILES.get((hardware_profile_id, profile_revision))
        if relative_path is None:
            raise ConfigurationValidationError(
                "The referenced bundled hardware profile was not found.",
                document_type="hardware_profile",
                issues=[
                    {
                        "field_path": "hardware_profile_id",
                        "rule": "profile ID and revision must resolve exactly",
                        "invalid_value": f"{hardware_profile_id}@{profile_revision}",
                        "correction": "Use bundled cDAQ-9171/NI-9239 profile revision 1 or 2.",
                    }
                ],
            )
        resource = self._resource(relative_path)
        return self.decode_hardware_profile(
            self._read_resource(resource, "hardware_profile"),
            document=str(resource),
        )

    def load_bundled_processing_recipe(self, recipe_id: str) -> ProcessingRecipeEnvelope:
        relative_path = _BUNDLED_RECIPES.get(recipe_id)
        if relative_path is None:
            raise ConfigurationValidationError(
                "The referenced bundled processing recipe was not found.",
                document_type="processing_recipe",
                issues=[
                    {
                        "field_path": "recipe_id",
                        "rule": "recipe lookup name must resolve to a bundled recipe",
                        "invalid_value": recipe_id,
                        "correction": "Use the bundled rate-compatible baseline recipe.",
                    }
                ],
            )
        resource = self._resource(relative_path)
        recipe = self.decode_processing_recipe(
            self._read_resource(resource, "processing_recipe"),
            document=str(resource),
        )
        raise_for_issues(
            validate_processing_recipe(recipe),
            document_type="processing_recipe",
            document=str(resource),
        )
        return recipe

    def list_bundled_hardware_profiles(self) -> tuple[HardwareProfileDescriptor, ...]:
        return (
            HardwareProfileDescriptor(
                hardware_profile_id="cdaq-9171-ni-9239-four-channel",
                profile_revision=1,
                display_name="NI cDAQ-9171 with NI-9239 (legacy 50 kHz)",
            ),
            HardwareProfileDescriptor(
                hardware_profile_id="cdaq-9171-ni-9239-four-channel",
                profile_revision=2,
                display_name="NI cDAQ-9171 with NI-9239 (variable rate)",
            ),
        )

    def list_bundled_experiment_templates(self) -> tuple[ExperimentTemplateDescriptor, ...]:
        return (
            ExperimentTemplateDescriptor(
                template_id="four-channel-vibration-physical-v2",
                display_name="Four-channel vibration variable-rate (physical, incomplete)",
                backend_id="ni",
                hardware_profile_id="cdaq-9171-ni-9239-four-channel",
                profile_revision=2,
            ),
            ExperimentTemplateDescriptor(
                template_id="four-channel-vibration-synthetic-v2",
                display_name="Four-channel vibration variable-rate (synthetic)",
                backend_id="simulated",
                hardware_profile_id="cdaq-9171-ni-9239-four-channel",
                profile_revision=2,
            ),
            ExperimentTemplateDescriptor(
                template_id="four-channel-vibration-physical-v1",
                display_name="Four-channel vibration (physical, incomplete)",
                backend_id="ni",
                hardware_profile_id="cdaq-9171-ni-9239-four-channel",
                profile_revision=1,
            ),
            ExperimentTemplateDescriptor(
                template_id="four-channel-vibration-synthetic-v1",
                display_name="Four-channel vibration (synthetic test fixture)",
                backend_id="simulated",
                hardware_profile_id="cdaq-9171-ni-9239-four-channel",
                profile_revision=1,
            ),
            ExperimentTemplateDescriptor(
                template_id="accelerometer-commissioning-v2",
                display_name="830M1 commissioning (X/Y/Z plus raw-voltage ai3)",
                backend_id="ni",
                hardware_profile_id="cdaq-9171-ni-9239-four-channel",
                profile_revision=1,
            ),
            ExperimentTemplateDescriptor(
                template_id="accelerometer-commissioning-v3",
                display_name="830M1 variable-rate commissioning (X/Y/Z plus raw-voltage ai3)",
                backend_id="ni",
                hardware_profile_id="cdaq-9171-ni-9239-four-channel",
                profile_revision=2,
            ),
        )

    def load_bundled_experiment_template(
        self,
        template_id: str,
    ) -> Mapping[str, object]:
        relative_path = _BUNDLED_EXPERIMENT_TEMPLATES.get(template_id)
        if relative_path is None:
            raise ConfigurationValidationError(
                "The requested bundled experiment template was not found.",
                document_type="experiment_config",
                issues=[
                    {
                        "field_path": "template_id",
                        "rule": "template ID must resolve exactly",
                        "invalid_value": template_id,
                        "correction": "Select a bundled LabDAQ experiment template.",
                    }
                ],
            )
        raw = deepcopy(self._read_unvalidated_resource(relative_path, "experiment_config"))
        if template_id in {
            "raw-voltage-v2",
            "four-channel-vibration-physical-v2",
            "four-channel-vibration-synthetic-v2",
        }:
            return _variable_rate_template(raw)
        if template_id in {"accelerometer-commissioning-v2", "accelerometer-commissioning-v3"}:
            if template_id == "accelerometer-commissioning-v3":
                raw = _variable_rate_template(raw)
            return _commissioning_template(raw)
        return raw

    def load_experiment_draft(self, path: Path) -> Mapping[str, object]:
        try:
            payload = path.read_text(encoding="utf-8")
        except OSError as cause:
            raise ConfigurationValidationError(
                "The experiment draft could not be read.",
                document_type="experiment_config",
                document=str(path),
                issues=[
                    {
                        "field_path": "$",
                        "rule": "document must be readable UTF-8 text",
                        "correction": "Check the path and file permissions.",
                    }
                ],
                cause=cause,
            ) from cause
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError as cause:
            raise ConfigurationValidationError(
                "The experiment draft is not valid JSON.",
                document_type="experiment_config",
                document=str(path),
                issues=[
                    {
                        "field_path": f"line {cause.lineno}, column {cause.colno}",
                        "rule": "document must be valid JSON",
                        "correction": "Correct the JSON syntax.",
                    }
                ],
                cause=cause,
            ) from cause
        if not isinstance(raw, dict):
            raise ConfigurationValidationError(
                "The experiment draft must be a JSON object.",
                document_type="experiment_config",
                document=str(path),
                issues=[
                    {
                        "field_path": "$",
                        "rule": "top-level value must be an object",
                        "correction": "Select a LabDAQ experiment configuration.",
                    }
                ],
            )
        return cast("dict[str, object]", raw)

    def decode_experiment_draft(
        self,
        raw: Mapping[str, object],
        *,
        document: str | None = None,
    ) -> ExperimentConfig:
        return self.decode_experiment_config(raw, document=document)

    def decode_hardware_profile(
        self,
        raw: Mapping[str, object],
        *,
        document: str | None = None,
    ) -> HardwareProfile:
        self._validate_raw(raw, "hardware_profile", document=document)
        try:
            return HardwareProfile(
                schema_version=_integer(raw, "schema_version"),
                hardware_profile_id=_string(raw, "hardware_profile_id"),
                profile_revision=_integer(raw, "profile_revision"),
                devices=tuple(_decode_device(item) for item in _objects(raw, "devices")),
                modules=tuple(_decode_module(item) for item in _objects(raw, "modules")),
                supported_physical_channels=tuple(
                    _decode_physical_channel(item)
                    for item in _objects(raw, "supported_physical_channels")
                ),
                required_terminal_configuration=TerminalConfiguration(
                    _string(raw, "required_terminal_configuration")
                ),
                input_min_v=_number(raw, "input_min_v"),
                input_max_v=_number(raw, "input_max_v"),
                application_supported_sample_rates_hz=tuple(
                    _number_value(item)
                    for item in _array(raw, "application_supported_sample_rates_hz")
                ),
                supported_trigger_modes=tuple(
                    TriggerMode(_string_value(item))
                    for item in _array(raw, "supported_trigger_modes")
                ),
                minimum_channel_count=_integer(raw, "minimum_channel_count"),
                maximum_channel_count=_integer(raw, "maximum_channel_count"),
                notes=_optional_string(raw, "notes"),
                nominal_calibration_fallback=_decode_nominal_fallback(
                    _optional_object(raw, "nominal_calibration_fallback")
                ),
            )
        except (KeyError, TypeError, ValueError) as cause:
            raise self._construction_error("hardware_profile", document, cause) from cause

    def decode_experiment_config(
        self,
        raw: Mapping[str, object],
        *,
        document: str | None = None,
    ) -> ExperimentConfig:
        self._validate_raw(raw, "experiment_config", document=document)
        try:
            return ExperimentConfig(
                schema_version=_integer(raw, "schema_version"),
                experiment_config_id=_string(raw, "experiment_config_id"),
                config_revision=_integer(raw, "config_revision"),
                hardware_profile_id=_string(raw, "hardware_profile_id"),
                compatible_profile_revision=_integer(raw, "compatible_profile_revision"),
                session=_decode_session(_object(raw, "session")),
                coordinate_frames=tuple(
                    _decode_coordinate_frame(item) for item in _objects(raw, "coordinate_frames")
                ),
                sensors=tuple(_decode_sensor(item) for item in _objects(raw, "sensors")),
                conditioning_chains=tuple(
                    _decode_conditioning_chain(item)
                    for item in _objects(raw, "conditioning_chains")
                ),
                calibrations=tuple(
                    _decode_calibration(item) for item in _objects(raw, "calibrations")
                ),
                channels=tuple(_decode_channel(item) for item in _objects(raw, "channels")),
                acquisition=_decode_acquisition(_object(raw, "acquisition")),
                draft_processing_recipe_id=_optional_string(raw, "draft_processing_recipe_id"),
                experiment_mode=ExperimentMode(
                    _string_value(raw.get("experiment_mode", ExperimentMode.PRODUCTION.value))
                ),
            )
        except (KeyError, TypeError, ValueError) as cause:
            raise self._construction_error("experiment_config", document, cause) from cause

    def decode_processing_recipe(
        self,
        raw: Mapping[str, object],
        *,
        document: str | None = None,
    ) -> ProcessingRecipeEnvelope:
        self._validate_raw(raw, "processing_recipe", document=document)
        try:
            return ProcessingRecipeEnvelope(
                schema_version=_integer(raw, "schema_version"),
                recipe_id=_string(raw, "recipe_id"),
                recipe_version=_string(raw, "recipe_version"),
                name=_string(raw, "name"),
                status=RecipeStatus(_string(raw, "status")),
                execution_class=ExecutionClass(_string(raw, "execution_class")),
                stages=tuple(_decode_processing_stage(item) for item in _objects(raw, "stages")),
                initialization_policy=(
                    FilterInitializationPolicy(_string(raw, "initialization_policy"))
                    if raw.get("initialization_policy") is not None
                    else None
                ),
                metrics=_decode_metric_configuration(_optional_object(raw, "metrics")),
                fft=_decode_fft_configuration(_optional_object(raw, "fft")),
                psd=_decode_psd_configuration(_optional_object(raw, "psd")),
            )
        except (KeyError, TypeError, ValueError) as cause:
            raise self._construction_error("processing_recipe", document, cause) from cause

    def encode(self, document: DomainDocument) -> dict[str, object]:
        encoded = _to_primitive(document)
        if not isinstance(encoded, dict):
            raise TypeError("Configuration document did not encode to an object")
        return cast("dict[str, object]", encoded)

    def dumps(self, document: DomainDocument) -> str:
        return (
            json.dumps(
                self.encode(document),
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        )

    def loads(self, payload: str, document_type: DocumentType) -> DomainDocument:
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError as cause:
            raise ConfigurationValidationError(
                "The configuration document is not valid JSON.",
                document_type=document_type,
                issues=[
                    {
                        "field_path": f"line {cause.lineno}, column {cause.colno}",
                        "rule": "document must be valid JSON",
                        "correction": "Correct the JSON syntax.",
                    }
                ],
                cause=cause,
            ) from cause
        if not isinstance(raw, dict):
            raise ConfigurationValidationError(
                "The configuration document must be a JSON object.",
                document_type=document_type,
                issues=[
                    {
                        "field_path": "$",
                        "rule": "top-level value must be an object",
                        "correction": "Wrap the document fields in a JSON object.",
                    }
                ],
            )
        typed_raw = cast("dict[str, object]", raw)
        if document_type == "hardware_profile":
            return self.decode_hardware_profile(typed_raw)
        if document_type == "experiment_config":
            return self.decode_experiment_config(typed_raw)
        return self.decode_processing_recipe(typed_raw)

    def _read_path(
        self,
        path: Path,
        document_type: DocumentType,
    ) -> dict[str, object]:
        try:
            payload = path.read_text(encoding="utf-8")
        except OSError as cause:
            raise ConfigurationValidationError(
                "The configuration document could not be read.",
                document_type=document_type,
                document=str(path),
                issues=[
                    {
                        "field_path": "$",
                        "rule": "document must be readable UTF-8 text",
                        "correction": "Check the path and file permissions.",
                    }
                ],
                cause=cause,
            ) from cause
        result = self.loads(payload, document_type)
        return self.encode(result)

    def _read_resource(
        self,
        resource: Traversable,
        document_type: DocumentType,
    ) -> dict[str, object]:
        try:
            payload = resource.read_text(encoding="utf-8")
        except OSError as cause:
            raise ConfigurationValidationError(
                "A bundled configuration resource could not be read.",
                document_type=document_type,
                document=str(resource),
                issues=[
                    {
                        "field_path": "$",
                        "rule": "bundled resource must be readable",
                        "correction": "Reinstall LabDAQ.",
                    }
                ],
                cause=cause,
            ) from cause
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError as cause:
            raise ConfigurationValidationError(
                "A bundled configuration resource is invalid JSON.",
                document_type=document_type,
                document=str(resource),
                issues=[
                    {
                        "field_path": "$",
                        "rule": "bundled resource must be valid JSON",
                        "correction": "Reinstall or report the damaged package.",
                    }
                ],
                cause=cause,
            ) from cause
        if not isinstance(raw, dict):
            raise TypeError("Bundled configuration resource must be an object")
        return cast("dict[str, object]", raw)

    def _validate_raw(
        self,
        raw: Mapping[str, object],
        document_type: DocumentType,
        *,
        document: str | None,
    ) -> None:
        received_version = raw.get("schema_version")
        if (
            isinstance(received_version, bool)
            or received_version not in {1, 2}
            or (
                received_version == 2
                and document_type == "experiment_config"
                and "experiment_mode" not in raw
            )
        ):
            raise UnsupportedSchemaVersionError(
                document_type=document_type,
                received_version=received_version,
                document=document,
            )
        if received_version == 2 and document_type == "experiment_config":
            mode = raw.get("experiment_mode")
            if mode not in {item.value for item in ExperimentMode}:
                raise ConfigurationValidationError(
                    "Version-2 experiment configurations must declare experiment_mode.",
                    document_type=document_type,
                    document=document,
                    issues=[
                        {
                            "field_path": "experiment_mode",
                            "rule": "version-2 configuration requires a supported experiment mode",
                            "correction": "Use production or accelerometer_commissioning.",
                        }
                    ],
                )
            channels = raw.get("channels")
            if isinstance(channels, list):
                missing_dispositions = [
                    index
                    for index, channel in enumerate(channels)
                    if isinstance(channel, dict) and "processing_disposition" not in channel
                ]
                if missing_dispositions:
                    raise ConfigurationValidationError(
                        "Version-2 channels must declare processing_disposition.",
                        document_type=document_type,
                        document=document,
                        issues=[
                            {
                                "field_path": f"channels[{index}].processing_disposition",
                                "rule": "version-2 channels require an explicit processing disposition",
                                "correction": "Use calibrated or raw_only_diagnostic.",
                            }
                            for index in missing_dispositions
                        ],
                    )
        schema_resource = self._resource(f"schemas/v1/{_SCHEMA_FILES[document_type]}")
        schema_raw = json.loads(schema_resource.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema_raw, format_checker=FormatChecker())
        errors = sorted(validator.iter_errors(raw), key=lambda error: list(error.absolute_path))
        if not errors:
            return
        issues: list[dict[str, object]] = []
        for error in errors[:32]:
            field_path = "$"
            if error.absolute_path:
                field_path += "".join(
                    f"[{part}]" if isinstance(part, int) else f".{part}"
                    for part in error.absolute_path
                )
            issues.append(
                {
                    "field_path": field_path,
                    "rule": error.message,
                    "correction": "Supply a value accepted by the version-1 schema.",
                }
            )
        raise ConfigurationValidationError(
            "The configuration document does not match its JSON Schema.",
            document_type=document_type,
            document=document,
            issues=issues,
        )

    @staticmethod
    def _resource(relative_path: str) -> Traversable:
        resource: Traversable = files("labdaq.resources")
        for component in relative_path.split("/"):
            resource = resource.joinpath(component)
        return resource

    def _read_unvalidated_resource(
        self,
        relative_path: str,
        document_type: DocumentType,
    ) -> dict[str, object]:
        return self._read_resource(self._resource(relative_path), document_type)

    @staticmethod
    def _construction_error(
        document_type: DocumentType,
        document: str | None,
        cause: BaseException,
    ) -> ConfigurationValidationError:
        return ConfigurationValidationError(
            "The configuration contains an invalid domain value.",
            document_type=document_type,
            document=document,
            issues=[
                {
                    "field_path": "$",
                    "rule": str(cause),
                    "correction": "Correct the reported value without relying on implicit defaults.",
                }
            ],
            cause=cause,
        )


def _variable_rate_template(raw: dict[str, object]) -> dict[str, object]:
    """Promote a legacy bundled draft to the revision-2 variable-rate profile."""
    raw["config_revision"] = 2
    raw["compatible_profile_revision"] = 2
    raw["draft_processing_recipe_id"] = "baseline"
    return raw


def _commissioning_template(raw: dict[str, object]) -> dict[str, object]:
    """Derive a four-row commissioning draft with one raw-only diagnostic."""
    channels = raw["channels"]
    calibrations = raw["calibrations"]
    sensors = raw["sensors"]
    if (
        not isinstance(channels, list)
        or not isinstance(calibrations, list)
        or not isinstance(sensors, list)
    ):
        raise TypeError("bundled commissioning source template is malformed")
    raw["schema_version"] = 2
    raw["experiment_config_id"] = "accelerometer-commissioning"
    raw["experiment_mode"] = ExperimentMode.ACCELEROMETER_COMMISSIONING.value
    raw["session"] = {
        **cast("dict[str, object]", raw["session"]),
        "experiment_name": "830M1 accelerometer commissioning",
        "experiment_slug": "830m1-accelerometer-commissioning",
        "description": "Calibrated X/Y/Z commissioning plus unpowered raw ai3 diagnostic voltage.",
        "notes": "ai3 is UNPOWERED / UNQUALIFIED / NOT CHARGE and is excluded from derived processing.",
    }
    diagnostic_sensor = cast("dict[str, object]", sensors[1])
    diagnostic_sensor.update(
        {
            "manufacturer": "PCB Piezotronics",
            "model": "422E03",
            "serial_number": "UNKNOWN-UNVERIFIED",
            "asset_tag": None,
            "mounting_description": "Unpowered output observed only as raw diagnostic voltage.",
            "certificate_reference": None,
            "sensor_power": {
                "excitation_source": "none - unpowered diagnostic",
                "excitation_voltage_v": None,
                "excitation_current_ma": None,
                "source_reference": "PCB 422E03 manufacturer datasheet; required excitation is not installed",
                "externally_verified": False,
            },
        }
    )
    raw["sensors"] = [sensors[0], diagnostic_sensor]
    raw["conditioning_chains"] = []
    sensitivities = (6.914, 6.791, 7.398)
    selected_calibrations: list[object] = []
    for calibration, sensitivity in zip(calibrations[:3], sensitivities, strict=True):
        item = cast("dict[str, object]", calibration)
        item["sensitivity_value"] = sensitivity
        item["source_kind"] = CalibrationSourceKind.OPERATOR_TRANSCRIBED.value
        item["source_reference"] = (
            "Operator transcription from unit-specific calibration record; identifier unavailable"
        )
        item["effective_date"] = None
        item["transcription_timestamp"] = datetime.now(UTC).isoformat()
        item["notes"] = (
            "Axis sensitivity supplied by operator; serial, certificate date, uncertainty, and zero output remain missing."
        )
        item["missing_provenance_fields"] = [
            "sensor_serial_number",
            "certificate_identifier",
            "calibration_effective_date",
            "uncertainty",
            "zero_output_characterization",
        ]
        item["characterization_session_ids"] = []
        item["owner_approval"] = None
        item["input_offset"] = 0.0
        selected_calibrations.append(item)
    raw["calibrations"] = selected_calibrations
    selected_channels = channels[:3]
    for channel in selected_channels:
        cast("dict[str, object]", channel)["processing_disposition"] = (
            ProcessingDisposition.CALIBRATED.value
        )
    diagnostic = cast("dict[str, object]", channels[3])
    diagnostic.update(
        {
            "channel_id": "pcb-422e03-raw-diagnostic",
            "logical_name": "ai3 UNPOWERED / UNQUALIFIED / NOT CHARGE",
            "channel_role": ChannelRole.DIAGNOSTIC_RAW_VOLTAGE.value,
            "engineering_quantity": PhysicalQuantity.VOLTAGE.value,
            "engineering_unit": Unit.VOLT.value,
            "experiment_axis_label": None,
            "mounting_notes": "Raw voltage only; never interpret as charge, force, or production evidence.",
            "calibration_id": None,
            "conditioning_chain_id": None,
            "processing_disposition": ProcessingDisposition.RAW_ONLY_DIAGNOSTIC.value,
        }
    )
    raw["channels"] = [*selected_channels, diagnostic]
    acquisition = cast("dict[str, object]", raw["acquisition"])
    acquisition["channel_ids"] = [
        "accel-x",
        "accel-y",
        "accel-z",
        "pcb-422e03-raw-diagnostic",
    ]
    return raw


def _decode_device(raw: Mapping[str, object]) -> DeviceConfig:
    return DeviceConfig(
        device_id=_string(raw, "device_id"),
        vendor=_string(raw, "vendor"),
        model=_string(raw, "model"),
        role=_string(raw, "role"),
        required=_boolean(raw, "required"),
        expected_serial_number=_optional_string(raw, "expected_serial_number"),
        alias=_optional_string(raw, "alias"),
    )


def _require_empty_stages(stages: list[object]) -> None:
    if stages:
        raise ValueError("M01 processing recipe stages must be empty")


def _decode_module(raw: Mapping[str, object]) -> ModuleConfig:
    return ModuleConfig(
        module_id=_string(raw, "module_id"),
        device_id=_string(raw, "device_id"),
        vendor=_string(raw, "vendor"),
        model=_string(raw, "model"),
        logical_slot=_integer(raw, "logical_slot"),
        required=_boolean(raw, "required"),
        channel_count=_integer(raw, "channel_count"),
        expected_serial_number=_optional_string(raw, "expected_serial_number"),
    )


def _decode_physical_channel(raw: Mapping[str, object]) -> PhysicalChannelRef:
    return PhysicalChannelRef(
        module_id=_string(raw, "module_id"),
        channel_index=_integer(raw, "channel_index"),
        terminal_configuration=TerminalConfiguration(_string(raw, "terminal_configuration")),
    )


def _decode_coordinate_frame(raw: Mapping[str, object]) -> CoordinateFrameConfig:
    matrix_raw = raw.get("direction_cosine_matrix")
    matrix = None
    if matrix_raw is not None:
        if not isinstance(matrix_raw, list):
            raise TypeError("direction_cosine_matrix must be an array")
        matrix = cast(
            "tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]",
            tuple(tuple(_number_value(value) for value in row) for row in matrix_raw),
        )
    return CoordinateFrameConfig(
        coordinate_frame_id=_string(raw, "coordinate_frame_id"),
        name=_string(raw, "name"),
        reference_description=_string(raw, "reference_description"),
        direction_cosine_matrix=matrix,
    )


def _decode_sensor(raw: Mapping[str, object]) -> SensorConfig:
    return SensorConfig(
        sensor_id=_string(raw, "sensor_id"),
        manufacturer=_string(raw, "manufacturer"),
        model=_string(raw, "model"),
        serial_number=_string(raw, "serial_number"),
        sensor_type=SensorType(_string(raw, "sensor_type")),
        output_quantity=PhysicalQuantity(_string(raw, "output_quantity")),
        output_count=_integer(raw, "output_count"),
        coordinate_frame_id=_string(raw, "coordinate_frame_id"),
        asset_tag=_optional_string(raw, "asset_tag"),
        mounting_description=_optional_string(raw, "mounting_description"),
        certificate_reference=_optional_string(raw, "certificate_reference"),
        sensor_power=_decode_sensor_power(_optional_object(raw, "sensor_power")),
    )


def _decode_sensor_power(raw: Mapping[str, object] | None) -> SensorPowerEvidence | None:
    if raw is None:
        return None
    return SensorPowerEvidence(
        excitation_source=_string(raw, "excitation_source"),
        excitation_voltage_v=_optional_nullable_number(raw, "excitation_voltage_v"),
        excitation_current_ma=_optional_nullable_number(raw, "excitation_current_ma"),
        source_reference=_string(raw, "source_reference"),
        externally_verified=_boolean(raw, "externally_verified"),
    )


def _decode_conditioning_chain(raw: Mapping[str, object]) -> ConditioningChainConfig:
    return ConditioningChainConfig(
        conditioning_chain_id=_string(raw, "conditioning_chain_id"),
        description=_string(raw, "description"),
        components=tuple(_string_value(item) for item in _array(raw, "components")),
        input_quantity=PhysicalQuantity(_string(raw, "input_quantity")),
        input_unit=Unit(_string(raw, "input_unit")),
        output_quantity=PhysicalQuantity(_string(raw, "output_quantity")),
        output_unit=Unit(_string(raw, "output_unit")),
        supply_description=_optional_string(raw, "supply_description"),
        calibration_reference=_string(raw, "calibration_reference"),
        polarity=Polarity(_string(raw, "polarity")),
        charge_converter=_decode_charge_converter(_optional_object(raw, "charge_converter")),
        ni_terminal_conditioning=_decode_ni_terminal_conditioning(
            _optional_object(raw, "ni_terminal_conditioning")
        ),
    )


def _decode_charge_converter(
    raw: Mapping[str, object] | None,
) -> ChargeConverterEvidence | None:
    if raw is None:
        return None
    return ChargeConverterEvidence(
        manufacturer=_string(raw, "manufacturer"),
        model=_string(raw, "model"),
        serial_number=_optional_string(raw, "serial_number"),
        nominal_charge_sensitivity=_number(raw, "nominal_charge_sensitivity"),
        sensitivity_unit=SensitivityUnit(_string(raw, "sensitivity_unit")),
        output_polarity=Polarity(_string(raw, "output_polarity")),
        excitation_source=_string(raw, "excitation_source"),
        excitation_current_ma=_number(raw, "excitation_current_ma"),
        source_reference=_string(raw, "source_reference"),
    )


def _decode_ni_terminal_conditioning(
    raw: Mapping[str, object] | None,
) -> NiTerminalConditioningEvidence | None:
    if raw is None:
        return None
    return NiTerminalConditioningEvidence(
        stage_description=_string(raw, "stage_description"),
        bias_removal_documented=_boolean(raw, "bias_removal_documented"),
        expected_bias_v=_number(raw, "expected_bias_v"),
        bias_tolerance_v=_number(raw, "bias_tolerance_v"),
        dynamic_min_v=_number(raw, "dynamic_min_v"),
        dynamic_max_v=_number(raw, "dynamic_max_v"),
        source_reference=_string(raw, "source_reference"),
    )


def _decode_calibration(raw: Mapping[str, object]) -> CalibrationConfig:
    return CalibrationConfig(
        calibration_id=_string(raw, "calibration_id"),
        calibration_model=CalibrationModel(_string(raw, "calibration_model")),
        input_quantity=PhysicalQuantity(_string(raw, "input_quantity")),
        input_unit=Unit(_string(raw, "input_unit")),
        output_quantity=PhysicalQuantity(_string(raw, "output_quantity")),
        output_unit=Unit(_string(raw, "output_unit")),
        sensitivity_value=_number(raw, "sensitivity_value"),
        sensitivity_unit=SensitivityUnit(_string(raw, "sensitivity_unit")),
        polarity=Polarity(_string(raw, "polarity")),
        source_kind=CalibrationSourceKind(_string(raw, "source_kind")),
        source_reference=_string(raw, "source_reference"),
        effective_date=(
            date.fromisoformat(_string(raw, "effective_date"))
            if raw.get("effective_date") is not None
            else None
        ),
        expiration_date=_optional_date(raw, "expiration_date"),
        certificate_identifier=_optional_string(raw, "certificate_identifier"),
        conditions=_optional_string(raw, "conditions"),
        notes=_optional_string(raw, "notes"),
        input_offset=_optional_nullable_number(raw, "input_offset"),
        valid_input_min=_optional_nullable_number(raw, "valid_input_min"),
        valid_input_max=_optional_nullable_number(raw, "valid_input_max"),
        valid_output_min=_optional_nullable_number(raw, "valid_output_min"),
        valid_output_max=_optional_nullable_number(raw, "valid_output_max"),
        validated_frequency_min_hz=_optional_nullable_number(raw, "validated_frequency_min_hz"),
        validated_frequency_max_hz=_optional_nullable_number(raw, "validated_frequency_max_hz"),
        extended_frequency_min_hz=_optional_nullable_number(raw, "extended_frequency_min_hz"),
        extended_frequency_max_hz=_optional_nullable_number(raw, "extended_frequency_max_hz"),
        uncertainty=_optional_string(raw, "uncertainty"),
        transcription_timestamp=(
            datetime.fromisoformat(_string(raw, "transcription_timestamp"))
            if raw.get("transcription_timestamp") is not None
            else None
        ),
        missing_provenance_fields=tuple(
            _string_tuple(raw, "missing_provenance_fields")
            if raw.get("missing_provenance_fields") is not None
            else ()
        ),
        characterization_session_ids=tuple(
            _string_tuple(raw, "characterization_session_ids")
            if raw.get("characterization_session_ids") is not None
            else ()
        ),
        owner_approval=_optional_string(raw, "owner_approval"),
    )


def _decode_nominal_fallback(
    raw: Mapping[str, object] | None,
) -> NominalCalibrationFallbackPolicy | None:
    if raw is None:
        return None
    return NominalCalibrationFallbackPolicy(
        sensor_model=_string(raw, "sensor_model"),
        sensitivity_value=_number(raw, "sensitivity_value"),
        sensitivity_unit=SensitivityUnit(_string(raw, "sensitivity_unit")),
        permitted_channel_roles=tuple(
            ChannelRole(_string_value(item)) for item in _array(raw, "permitted_channel_roles")
        ),
    )


def _decode_processing_stage(raw: Mapping[str, object]) -> ProcessingStageDefinition:
    return ProcessingStageDefinition(
        stage_id=_string(raw, "stage_id"),
        kind=ProcessingStageKind(_string(raw, "kind")),
        applies_to=tuple(ChannelRole(_string_value(item)) for item in _array(raw, "applies_to")),
        causal=_boolean(raw, "causal"),
        order=_optional_nullable_integer(raw, "order"),
        cutoff_hz=_optional_nullable_number(raw, "cutoff_hz"),
    )


def _decode_metric_configuration(raw: Mapping[str, object] | None) -> MetricConfiguration | None:
    if raw is None:
        return None
    return MetricConfiguration(
        source_stage=_string(raw, "source_stage"),
        clipping_margin_v=_number(raw, "clipping_margin_v"),
    )


def _decode_fft_configuration(raw: Mapping[str, object] | None) -> FftConfiguration | None:
    if raw is None:
        return None
    return FftConfiguration(
        source_stage=_string(raw, "source_stage"),
        detrend=_string(raw, "detrend"),
        window=_string(raw, "window"),
        normalization=_string(raw, "normalization"),
        maximum_samples_per_channel=_integer(raw, "maximum_samples_per_channel"),
    )


def _decode_psd_configuration(raw: Mapping[str, object] | None) -> PsdConfiguration | None:
    if raw is None:
        return None
    return PsdConfiguration(
        source_stage=_string(raw, "source_stage"),
        segment_duration_seconds=_number(raw, "segment_duration_seconds"),
        overlap_fraction=_number(raw, "overlap_fraction"),
        detrend=_string(raw, "detrend"),
        window=_string(raw, "window"),
        scaling=_string(raw, "scaling"),
    )


def _decode_channel(raw: Mapping[str, object]) -> ChannelConfig:
    axis = _optional_string(raw, "sensor_output_axis")
    return ChannelConfig(
        channel_id=_string(raw, "channel_id"),
        logical_name=_string(raw, "logical_name"),
        channel_role=ChannelRole(_string(raw, "channel_role")),
        physical_channel=_decode_physical_channel(_object(raw, "physical_channel")),
        channel_order=_integer(raw, "channel_order"),
        raw_quantity=PhysicalQuantity(_string(raw, "raw_quantity")),
        raw_unit=Unit(_string(raw, "raw_unit")),
        engineering_quantity=PhysicalQuantity(_string(raw, "engineering_quantity")),
        engineering_unit=Unit(_string(raw, "engineering_unit")),
        sensor_id=_optional_string(raw, "sensor_id"),
        sensor_output_axis=SensorAxis(axis) if axis is not None else None,
        experiment_axis_label=_optional_string(raw, "experiment_axis_label"),
        mounting_notes=_optional_string(raw, "mounting_notes"),
        calibration_id=_optional_string(raw, "calibration_id"),
        conditioning_chain_id=_optional_string(raw, "conditioning_chain_id"),
        requested_input_min=_number(raw, "requested_input_min"),
        requested_input_max=_number(raw, "requested_input_max"),
        enabled=_boolean(raw, "enabled"),
        processing_disposition=ProcessingDisposition(
            str(raw.get("processing_disposition", ProcessingDisposition.CALIBRATED.value))
        ),
    )


def _decode_trigger(raw: Mapping[str, object]) -> TriggerConfig:
    edge = _optional_string(raw, "edge")
    return TriggerConfig(
        mode=TriggerMode(_string(raw, "mode")),
        source=_optional_string(raw, "source"),
        edge=TriggerEdge(edge) if edge is not None else None,
        pretrigger_samples=_integer(raw, "pretrigger_samples"),
    )


def _decode_chunk(raw: Mapping[str, object]) -> DataChunkConfig:
    return DataChunkConfig(
        samples_per_channel=_integer(raw, "samples_per_channel"),
        read_timeout_seconds=_number(raw, "read_timeout_seconds"),
        stop_timeout_seconds=_optional_number(raw, "stop_timeout_seconds", 5.0),
        recording_queue_capacity_chunks=_optional_integer(
            raw,
            "recording_queue_capacity_chunks",
            32,
        ),
        recording_backpressure_timeout_seconds=_optional_number(
            raw,
            "recording_backpressure_timeout_seconds",
            2.0,
        ),
        display_queue_capacity_chunks=_optional_integer(
            raw,
            "display_queue_capacity_chunks",
            2,
        ),
        progress_event_interval_seconds=_optional_number(
            raw,
            "progress_event_interval_seconds",
            0.5,
        ),
    )


def _decode_acquisition(raw: Mapping[str, object]) -> AcquisitionConfig:
    duration = raw.get("requested_duration_seconds")
    return AcquisitionConfig(
        sample_rate_hz=_number(raw, "sample_rate_hz"),
        sampling_mode=SamplingMode(_string(raw, "sampling_mode")),
        requested_duration_seconds=None if duration is None else _number_value(duration),
        channel_ids=tuple(_string_value(item) for item in _array(raw, "channel_ids")),
        trigger=_decode_trigger(_object(raw, "trigger")),
        chunk_config=_decode_chunk(_object(raw, "chunk_config")),
    )


def _decode_session(raw: Mapping[str, object]) -> SessionConfig:
    return SessionConfig(
        experiment_name=_string(raw, "experiment_name"),
        experiment_slug=_string(raw, "experiment_slug"),
        description=_string(raw, "description"),
        operator=_string(raw, "operator"),
        sample_id=_optional_string(raw, "sample_id"),
        notes=_optional_string(raw, "notes"),
        parent_output_directory=Path(_string(raw, "parent_output_directory")),
    )


def _to_primitive(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return _to_primitive(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _to_primitive(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_to_primitive(item) for item in value]
    return value


def _object(raw: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = raw[key]
    if not isinstance(value, dict):
        raise TypeError(f"{key} must be an object")
    return cast("dict[str, object]", value)


def _objects(raw: Mapping[str, object], key: str) -> list[Mapping[str, object]]:
    result: list[Mapping[str, object]] = []
    for value in _array(raw, key):
        if not isinstance(value, dict):
            raise TypeError(f"{key} entries must be objects")
        result.append(cast("dict[str, object]", value))
    return result


def _array(raw: Mapping[str, object], key: str) -> list[object]:
    value = raw[key]
    if not isinstance(value, list):
        raise TypeError(f"{key} must be an array")
    return value


def _string_tuple(raw: Mapping[str, object], key: str) -> tuple[str, ...]:
    values = _array(raw, key)
    if not all(isinstance(value, str) and value for value in values):
        raise ValueError(f"{key} must contain non-empty strings")
    return tuple(cast("str", value) for value in values)


def _string(raw: Mapping[str, object], key: str) -> str:
    return _string_value(raw[key])


def _optional_string(raw: Mapping[str, object], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    return _string_value(value)


def _string_value(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("value must be a string")
    return value


def _integer(raw: Mapping[str, object], key: str) -> int:
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an integer")
    return value


def _optional_integer(raw: Mapping[str, object], key: str, default: int) -> int:
    if key not in raw:
        return default
    return _integer(raw, key)


def _number(raw: Mapping[str, object], key: str) -> float:
    return _number_value(raw[key])


def _optional_number(raw: Mapping[str, object], key: str, default: float) -> float:
    if key not in raw:
        return default
    return _number(raw, key)


def _number_value(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("value must be a number")
    return float(value)


def _boolean(raw: Mapping[str, object], key: str) -> bool:
    value = raw[key]
    if not isinstance(value, bool):
        raise TypeError(f"{key} must be a boolean")
    return value


def _optional_date(raw: Mapping[str, object], key: str) -> date | None:
    value = _optional_string(raw, key)
    return date.fromisoformat(value) if value is not None else None


def _optional_nullable_number(raw: Mapping[str, object], key: str) -> float | None:
    value = raw.get(key)
    if value is None:
        return None
    return _number_value(value)


def _optional_nullable_integer(raw: Mapping[str, object], key: str) -> int | None:
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an integer or null")
    return value


def _optional_object(raw: Mapping[str, object], key: str) -> Mapping[str, object] | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError(f"{key} must be an object or null")
    return cast("Mapping[str, object]", value)
