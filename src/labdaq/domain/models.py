"""Immutable M01 configuration records."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime  # noqa: TC003 - runtime validation rejects malformed dates
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, TypeVar

from labdaq.domain.enums import (
    CalibrationModel,
    CalibrationQuality,
    CalibrationSourceKind,
    ChannelRole,
    ExecutionClass,
    ExperimentMode,
    FilterInitializationPolicy,
    PhysicalQuantity,
    Polarity,
    ProcessingDisposition,
    ProcessingMode,
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
from labdaq.domain.naming import validate_identifier, validate_slug
from labdaq.domain.timing import rounded_sample_count

_T = TypeVar("_T")

if TYPE_CHECKING:
    from collections.abc import Mapping


def _require_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must not be empty")


def _require_tuple(value: tuple[_T, ...], field: str) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")


def _require_finite(value: float, field: str, *, positive: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    if positive and value <= 0:
        raise ValueError(f"{field} must be positive")


def _require_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")


@dataclass(frozen=True, slots=True)
class DeviceConfig:
    device_id: str
    vendor: str
    model: str
    role: str
    required: bool
    expected_serial_number: str | None = None
    alias: str | None = None

    def __post_init__(self) -> None:
        validate_identifier(self.device_id, field="device_id")
        for field, value in (("vendor", self.vendor), ("model", self.model), ("role", self.role)):
            _require_text(value, field)


@dataclass(frozen=True, slots=True)
class ModuleConfig:
    module_id: str
    device_id: str
    vendor: str
    model: str
    logical_slot: int
    required: bool
    channel_count: int
    expected_serial_number: str | None = None

    def __post_init__(self) -> None:
        validate_identifier(self.module_id, field="module_id")
        validate_identifier(self.device_id, field="device_id")
        _require_text(self.vendor, "vendor")
        _require_text(self.model, "model")
        if isinstance(self.logical_slot, bool) or self.logical_slot < 0:
            raise ValueError("logical_slot must be a non-negative integer")
        if isinstance(self.channel_count, bool) or self.channel_count <= 0:
            raise ValueError("channel_count must be a positive integer")


@dataclass(frozen=True, slots=True)
class PhysicalChannelRef:
    module_id: str
    channel_index: int
    terminal_configuration: TerminalConfiguration

    def __post_init__(self) -> None:
        validate_identifier(self.module_id, field="module_id")
        if isinstance(self.channel_index, bool) or self.channel_index < 0:
            raise ValueError("channel_index must be a non-negative integer")


DirectionCosineMatrix = tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
]


@dataclass(frozen=True, slots=True)
class CoordinateFrameConfig:
    coordinate_frame_id: str
    name: str
    reference_description: str
    direction_cosine_matrix: DirectionCosineMatrix | None = None

    def __post_init__(self) -> None:
        validate_identifier(self.coordinate_frame_id, field="coordinate_frame_id")
        _require_text(self.name, "coordinate frame name")
        _require_text(self.reference_description, "reference_description")
        matrix = self.direction_cosine_matrix
        if matrix is None:
            return
        if not isinstance(matrix, tuple) or len(matrix) != 3:
            raise ValueError("direction_cosine_matrix must contain three rows")
        for row in matrix:
            if not isinstance(row, tuple) or len(row) != 3:
                raise ValueError("direction_cosine_matrix rows must contain three values")
            for value in row:
                _require_finite(value, "direction_cosine_matrix value")


@dataclass(frozen=True, slots=True)
class SensorPowerEvidence:
    excitation_source: str
    excitation_voltage_v: float | None
    excitation_current_ma: float | None
    source_reference: str
    externally_verified: bool

    def __post_init__(self) -> None:
        _require_text(self.excitation_source, "excitation_source")
        _require_text(self.source_reference, "source_reference")
        for field in ("excitation_voltage_v", "excitation_current_ma"):
            value = getattr(self, field)
            if value is not None:
                _require_finite(value, field, positive=True)


@dataclass(frozen=True, slots=True)
class SensorConfig:
    sensor_id: str
    manufacturer: str
    model: str
    serial_number: str
    sensor_type: SensorType
    output_quantity: PhysicalQuantity
    output_count: int
    coordinate_frame_id: str
    asset_tag: str | None = None
    mounting_description: str | None = None
    certificate_reference: str | None = None
    sensor_power: SensorPowerEvidence | None = None

    def __post_init__(self) -> None:
        validate_identifier(self.sensor_id, field="sensor_id")
        validate_identifier(self.coordinate_frame_id, field="coordinate_frame_id")
        for field, value in (
            ("manufacturer", self.manufacturer),
            ("model", self.model),
            ("serial_number", self.serial_number),
        ):
            _require_text(value, field)
        if isinstance(self.output_count, bool) or self.output_count <= 0:
            raise ValueError("output_count must be a positive integer")


@dataclass(frozen=True, slots=True)
class ChargeConverterEvidence:
    manufacturer: str
    model: str
    serial_number: str | None
    nominal_charge_sensitivity: float
    sensitivity_unit: SensitivityUnit
    output_polarity: Polarity
    excitation_source: str
    excitation_current_ma: float
    source_reference: str

    def __post_init__(self) -> None:
        _require_text(self.manufacturer, "charge converter manufacturer")
        _require_text(self.model, "charge converter model")
        _require_text(self.excitation_source, "charge converter excitation_source")
        _require_text(self.source_reference, "charge converter source_reference")
        _require_finite(
            self.nominal_charge_sensitivity,
            "nominal_charge_sensitivity",
            positive=True,
        )
        _require_finite(self.excitation_current_ma, "excitation_current_ma", positive=True)


@dataclass(frozen=True, slots=True)
class NiTerminalConditioningEvidence:
    stage_description: str
    bias_removal_documented: bool
    expected_bias_v: float
    bias_tolerance_v: float
    dynamic_min_v: float
    dynamic_max_v: float
    source_reference: str

    def __post_init__(self) -> None:
        _require_text(self.stage_description, "NI-terminal conditioning stage_description")
        _require_text(self.source_reference, "NI-terminal conditioning source_reference")
        for field in (
            "expected_bias_v",
            "bias_tolerance_v",
            "dynamic_min_v",
            "dynamic_max_v",
        ):
            _require_finite(getattr(self, field), field)
        if self.bias_tolerance_v < 0:
            raise ValueError("bias_tolerance_v must be non-negative")
        if self.dynamic_min_v >= self.dynamic_max_v:
            raise ValueError("dynamic_min_v must be less than dynamic_max_v")


@dataclass(frozen=True, slots=True)
class ConditioningChainConfig:
    conditioning_chain_id: str
    description: str
    components: tuple[str, ...]
    input_quantity: PhysicalQuantity
    input_unit: Unit
    output_quantity: PhysicalQuantity
    output_unit: Unit
    supply_description: str | None
    calibration_reference: str
    polarity: Polarity
    charge_converter: ChargeConverterEvidence | None = None
    ni_terminal_conditioning: NiTerminalConditioningEvidence | None = None

    def __post_init__(self) -> None:
        validate_identifier(self.conditioning_chain_id, field="conditioning_chain_id")
        _require_text(self.description, "conditioning chain description")
        _require_tuple(self.components, "components")
        if not self.components or any(not item.strip() for item in self.components):
            raise ValueError("components must contain non-empty ordered descriptions")
        _require_text(self.calibration_reference, "calibration_reference")


@dataclass(frozen=True, slots=True)
class CalibrationConfig:
    calibration_id: str
    calibration_model: CalibrationModel
    input_quantity: PhysicalQuantity
    input_unit: Unit
    output_quantity: PhysicalQuantity
    output_unit: Unit
    sensitivity_value: float
    sensitivity_unit: SensitivityUnit
    polarity: Polarity
    source_kind: CalibrationSourceKind
    source_reference: str
    effective_date: date | None
    expiration_date: date | None = None
    certificate_identifier: str | None = None
    conditions: str | None = None
    notes: str | None = None
    input_offset: float | None = None
    valid_input_min: float | None = None
    valid_input_max: float | None = None
    valid_output_min: float | None = None
    valid_output_max: float | None = None
    validated_frequency_min_hz: float | None = None
    validated_frequency_max_hz: float | None = None
    extended_frequency_min_hz: float | None = None
    extended_frequency_max_hz: float | None = None
    uncertainty: str | None = None
    transcription_timestamp: datetime | None = None
    missing_provenance_fields: tuple[str, ...] = ()
    characterization_session_ids: tuple[str, ...] = ()
    owner_approval: str | None = None

    def __post_init__(self) -> None:
        validate_identifier(self.calibration_id, field="calibration_id")
        _require_finite(self.sensitivity_value, "sensitivity_value", positive=True)
        _require_text(self.source_reference, "source_reference")
        if self.effective_date is None:
            if self.source_kind is not CalibrationSourceKind.OPERATOR_TRANSCRIBED:
                raise ValueError("effective_date is required for verified calibration sources")
        elif not isinstance(self.effective_date, date):
            raise TypeError("effective_date must be a datetime.date or None")
        if self.source_kind is CalibrationSourceKind.OPERATOR_TRANSCRIBED:
            if self.transcription_timestamp is None:
                raise ValueError(
                    "operator-transcribed calibration requires transcription_timestamp"
                )
        elif self.transcription_timestamp is not None:
            raise ValueError(
                "transcription_timestamp is only valid for operator-transcribed calibration"
            )
        if self.source_kind is not CalibrationSourceKind.OPERATOR_TRANSCRIBED and (
            self.missing_provenance_fields
            or self.characterization_session_ids
            or self.owner_approval is not None
        ):
            raise ValueError(
                "incomplete provenance fields are only valid for operator-transcribed calibration"
            )
        for field_name, values in (
            ("missing_provenance_fields", self.missing_provenance_fields),
            ("characterization_session_ids", self.characterization_session_ids),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"{field_name} must not contain duplicates")
            for value in values:
                _require_text(value, field_name)
        if self.owner_approval is not None:
            _require_text(self.owner_approval, "owner_approval")
        if self.expiration_date is not None and not isinstance(self.expiration_date, date):
            raise TypeError("expiration_date must be a datetime.date or None")
        if self.expiration_date is not None and self.effective_date is None:
            raise ValueError("expiration_date requires an effective_date")
        if (
            self.expiration_date is not None
            and self.effective_date is not None
            and self.expiration_date < self.effective_date
        ):
            raise ValueError("expiration_date must not precede effective_date")
        for field in (
            "input_offset",
            "valid_input_min",
            "valid_input_max",
            "valid_output_min",
            "valid_output_max",
            "validated_frequency_min_hz",
            "validated_frequency_max_hz",
            "extended_frequency_min_hz",
            "extended_frequency_max_hz",
        ):
            value = getattr(self, field)
            if value is not None:
                _require_finite(value, field, positive=field.endswith("_hz"))
        for prefix in ("valid_input", "valid_output", "validated_frequency", "extended_frequency"):
            minimum = getattr(self, f"{prefix}_min" + ("_hz" if "frequency" in prefix else ""))
            maximum = getattr(self, f"{prefix}_max" + ("_hz" if "frequency" in prefix else ""))
            if (minimum is None) != (maximum is None):
                raise ValueError(f"{prefix} minimum and maximum must be supplied together")
            if minimum is not None and maximum is not None and minimum >= maximum:
                raise ValueError(f"{prefix} minimum must be less than maximum")


@dataclass(frozen=True, slots=True)
class ChannelConfig:
    channel_id: str
    logical_name: str
    channel_role: ChannelRole
    physical_channel: PhysicalChannelRef
    channel_order: int
    raw_quantity: PhysicalQuantity
    raw_unit: Unit
    engineering_quantity: PhysicalQuantity
    engineering_unit: Unit
    sensor_id: str | None
    sensor_output_axis: SensorAxis | None
    experiment_axis_label: str | None
    mounting_notes: str | None
    calibration_id: str | None
    conditioning_chain_id: str | None
    requested_input_min: float
    requested_input_max: float
    enabled: bool
    processing_disposition: ProcessingDisposition = ProcessingDisposition.CALIBRATED

    def __post_init__(self) -> None:
        validate_identifier(self.channel_id, field="channel_id")
        if self.sensor_id is not None:
            validate_identifier(self.sensor_id, field="sensor_id")
        if self.calibration_id is not None:
            validate_identifier(self.calibration_id, field="calibration_id")
        if self.conditioning_chain_id is not None:
            validate_identifier(self.conditioning_chain_id, field="conditioning_chain_id")
        _require_text(self.logical_name, "logical_name")
        if isinstance(self.channel_order, bool) or self.channel_order < 0:
            raise ValueError("channel_order must be a non-negative integer")
        _require_finite(self.requested_input_min, "requested_input_min")
        _require_finite(self.requested_input_max, "requested_input_max")
        if self.requested_input_min >= self.requested_input_max:
            raise ValueError("requested_input_min must be less than requested_input_max")


@dataclass(frozen=True, slots=True)
class TriggerConfig:
    mode: TriggerMode
    source: str | None = None
    edge: TriggerEdge | None = None
    pretrigger_samples: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.pretrigger_samples, bool) or self.pretrigger_samples < 0:
            raise ValueError("pretrigger_samples must be a non-negative integer")
        if self.mode is TriggerMode.IMMEDIATE:
            if self.source is not None or self.edge is not None or self.pretrigger_samples != 0:
                raise ValueError("immediate trigger forbids source, edge, and pretrigger samples")
        elif not self.source or self.edge is None:
            raise ValueError("digital_edge trigger requires source and edge")


@dataclass(frozen=True, slots=True)
class DataChunkConfig:
    samples_per_channel: int
    read_timeout_seconds: float
    stop_timeout_seconds: float = 5.0
    recording_queue_capacity_chunks: int = 32
    recording_backpressure_timeout_seconds: float = 2.0
    display_queue_capacity_chunks: int = 2
    progress_event_interval_seconds: float = 0.5

    def __post_init__(self) -> None:
        if isinstance(self.samples_per_channel, bool) or self.samples_per_channel <= 0:
            raise ValueError("samples_per_channel must be a positive integer")
        _require_finite(self.read_timeout_seconds, "read_timeout_seconds", positive=True)
        _require_finite(self.stop_timeout_seconds, "stop_timeout_seconds", positive=True)
        for field, value in (
            ("recording_queue_capacity_chunks", self.recording_queue_capacity_chunks),
            ("display_queue_capacity_chunks", self.display_queue_capacity_chunks),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field} must be a positive integer")
        _require_finite(
            self.recording_backpressure_timeout_seconds,
            "recording_backpressure_timeout_seconds",
            positive=True,
        )
        _require_finite(
            self.progress_event_interval_seconds,
            "progress_event_interval_seconds",
            positive=True,
        )


@dataclass(frozen=True, slots=True)
class AcquisitionConfig:
    sample_rate_hz: float
    sampling_mode: SamplingMode
    requested_duration_seconds: float | None
    channel_ids: tuple[str, ...]
    trigger: TriggerConfig
    chunk_config: DataChunkConfig

    def __post_init__(self) -> None:
        _require_finite(self.sample_rate_hz, "sample_rate_hz", positive=True)
        _require_tuple(self.channel_ids, "channel_ids")
        for channel_id in self.channel_ids:
            validate_identifier(channel_id, field="channel_id")
        if self.sampling_mode is SamplingMode.FINITE:
            if self.requested_duration_seconds is None:
                raise ValueError("finite acquisition requires requested_duration_seconds")
            _require_finite(
                self.requested_duration_seconds,
                "requested_duration_seconds",
                positive=True,
            )
            rounded_sample_count(self.sample_rate_hz, self.requested_duration_seconds)
        elif self.requested_duration_seconds is not None:
            raise ValueError("continuous acquisition forbids requested_duration_seconds")


@dataclass(frozen=True, slots=True)
class SessionConfig:
    experiment_name: str
    experiment_slug: str
    description: str
    operator: str
    sample_id: str | None
    notes: str | None
    parent_output_directory: Path

    def __post_init__(self) -> None:
        _require_text(self.experiment_name, "experiment_name")
        validate_slug(self.experiment_slug)
        _require_text(self.description, "description")
        _require_text(self.operator, "operator")
        if not isinstance(self.parent_output_directory, Path):
            raise TypeError("parent_output_directory must be a pathlib.Path")


@dataclass(frozen=True, slots=True)
class HardwareProfile:
    schema_version: int
    hardware_profile_id: str
    profile_revision: int
    devices: tuple[DeviceConfig, ...]
    modules: tuple[ModuleConfig, ...]
    supported_physical_channels: tuple[PhysicalChannelRef, ...]
    required_terminal_configuration: TerminalConfiguration
    input_min_v: float
    input_max_v: float
    application_supported_sample_rates_hz: tuple[float, ...]
    supported_trigger_modes: tuple[TriggerMode, ...]
    minimum_channel_count: int
    maximum_channel_count: int
    notes: str | None = None
    nominal_calibration_fallback: NominalCalibrationFallbackPolicy | None = None

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("schema_version must be 1")
        validate_identifier(self.hardware_profile_id, field="hardware_profile_id")
        if isinstance(self.profile_revision, bool) or self.profile_revision <= 0:
            raise ValueError("profile_revision must be a positive integer")
        for field, value in (
            ("devices", self.devices),
            ("modules", self.modules),
            ("supported_physical_channels", self.supported_physical_channels),
            ("application_supported_sample_rates_hz", self.application_supported_sample_rates_hz),
            ("supported_trigger_modes", self.supported_trigger_modes),
        ):
            _require_tuple(value, field)
        _require_finite(self.input_min_v, "input_min_v")
        _require_finite(self.input_max_v, "input_max_v")
        if self.input_min_v >= self.input_max_v:
            raise ValueError("input_min_v must be less than input_max_v")
        for rate in self.application_supported_sample_rates_hz:
            _require_finite(rate, "application_supported_sample_rate", positive=True)
        if len(self.application_supported_sample_rates_hz) != len(
            set(self.application_supported_sample_rates_hz)
        ):
            raise ValueError("application-supported sample rates must be unique")
        if (
            isinstance(self.minimum_channel_count, bool)
            or self.minimum_channel_count <= 0
            or self.maximum_channel_count < self.minimum_channel_count
        ):
            raise ValueError("channel-count constraints are invalid")


@dataclass(frozen=True, slots=True)
class NominalCalibrationFallbackPolicy:
    sensor_model: str
    sensitivity_value: float
    sensitivity_unit: SensitivityUnit
    permitted_channel_roles: tuple[ChannelRole, ...]

    def __post_init__(self) -> None:
        _require_text(self.sensor_model, "sensor_model")
        _require_finite(self.sensitivity_value, "sensitivity_value", positive=True)
        _require_tuple(self.permitted_channel_roles, "permitted_channel_roles")
        if not self.permitted_channel_roles:
            raise ValueError("permitted_channel_roles must not be empty")


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    schema_version: int
    experiment_config_id: str
    config_revision: int
    hardware_profile_id: str
    compatible_profile_revision: int
    session: SessionConfig
    coordinate_frames: tuple[CoordinateFrameConfig, ...]
    sensors: tuple[SensorConfig, ...]
    conditioning_chains: tuple[ConditioningChainConfig, ...]
    calibrations: tuple[CalibrationConfig, ...]
    channels: tuple[ChannelConfig, ...]
    acquisition: AcquisitionConfig
    draft_processing_recipe_id: str | None = None
    experiment_mode: ExperimentMode = ExperimentMode.PRODUCTION

    def __post_init__(self) -> None:
        if self.schema_version not in {1, 2}:
            raise ValueError("schema_version must be 1 or 2")
        if self.schema_version == 1 and self.experiment_mode is not ExperimentMode.PRODUCTION:
            raise ValueError("schema version 1 configurations are production configurations")
        validate_identifier(self.experiment_config_id, field="experiment_config_id")
        validate_identifier(self.hardware_profile_id, field="hardware_profile_id")
        if self.draft_processing_recipe_id is not None:
            validate_identifier(self.draft_processing_recipe_id, field="draft_processing_recipe_id")
        if isinstance(self.config_revision, bool) or self.config_revision <= 0:
            raise ValueError("config_revision must be a positive integer")
        if (
            isinstance(self.compatible_profile_revision, bool)
            or self.compatible_profile_revision <= 0
        ):
            raise ValueError("compatible_profile_revision must be a positive integer")
        for field, value in (
            ("coordinate_frames", self.coordinate_frames),
            ("sensors", self.sensors),
            ("conditioning_chains", self.conditioning_chains),
            ("calibrations", self.calibrations),
            ("channels", self.channels),
        ):
            _require_tuple(value, field)


@dataclass(frozen=True, slots=True)
class ProcessingStageDefinition:
    stage_id: str
    kind: ProcessingStageKind
    applies_to: tuple[ChannelRole, ...]
    causal: bool
    order: int | None = None
    cutoff_hz: float | None = None

    def __post_init__(self) -> None:
        validate_identifier(self.stage_id, field="stage_id")
        _require_tuple(self.applies_to, "applies_to")
        if not self.applies_to:
            raise ValueError("applies_to must not be empty")
        if self.kind is ProcessingStageKind.AFFINE_CALIBRATION:
            if self.order is not None or self.cutoff_hz is not None:
                raise ValueError("affine calibration does not accept filter parameters")
        elif self.order is None or self.order <= 0 or self.cutoff_hz is None:
            raise ValueError("Butterworth stages require positive order and cutoff_hz")
        elif not math.isfinite(self.cutoff_hz) or self.cutoff_hz <= 0:
            raise ValueError("cutoff_hz must be positive and finite")


@dataclass(frozen=True, slots=True)
class MetricConfiguration:
    source_stage: str
    clipping_margin_v: float

    def __post_init__(self) -> None:
        _require_text(self.source_stage, "source_stage")
        _require_finite(self.clipping_margin_v, "clipping_margin_v")
        if self.clipping_margin_v < 0:
            raise ValueError("clipping_margin_v must be non-negative")


@dataclass(frozen=True, slots=True)
class FftConfiguration:
    source_stage: str
    detrend: str
    window: str
    normalization: str
    maximum_samples_per_channel: int

    def __post_init__(self) -> None:
        for field in ("source_stage", "detrend", "window", "normalization"):
            _require_text(str(getattr(self, field)), field)
        if self.maximum_samples_per_channel <= 1:
            raise ValueError("maximum_samples_per_channel must exceed one")


@dataclass(frozen=True, slots=True)
class PsdConfiguration:
    source_stage: str
    segment_duration_seconds: float
    overlap_fraction: float
    detrend: str
    window: str
    scaling: str

    def __post_init__(self) -> None:
        _require_finite(self.segment_duration_seconds, "segment_duration_seconds", positive=True)
        _require_finite(self.overlap_fraction, "overlap_fraction")
        if not 0 <= self.overlap_fraction < 1:
            raise ValueError("overlap_fraction must be in [0, 1)")


@dataclass(frozen=True, slots=True)
class ProcessingRecipeEnvelope:
    schema_version: int
    recipe_id: str
    recipe_version: str
    name: str
    status: RecipeStatus
    execution_class: ExecutionClass
    stages: tuple[ProcessingStageDefinition, ...]
    initialization_policy: FilterInitializationPolicy | None = None
    metrics: MetricConfiguration | None = None
    fft: FftConfiguration | None = None
    psd: PsdConfiguration | None = None

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("schema_version must be 1")
        validate_identifier(self.recipe_id, field="recipe_id")
        validate_identifier(self.recipe_version, field="recipe_version")
        _require_text(self.name, "recipe name")
        _require_tuple(self.stages, "stages")
        stage_ids = [stage.stage_id for stage in self.stages]
        if len(stage_ids) != len(set(stage_ids)):
            raise ValueError("processing stage IDs must be unique")
        if self.status is RecipeStatus.ACTIVE:
            if self.execution_class is not ExecutionClass.CAUSAL:
                raise ValueError("active baseline recipe must be causal")
            if not self.stages or self.initialization_policy is None:
                raise ValueError("active recipe requires stages and initialization policy")
            if self.metrics is None or self.fft is None or self.psd is None:
                raise ValueError("active recipe requires metric, FFT, and PSD configuration")


@dataclass(frozen=True, slots=True)
class ResolvedChannelCalibration:
    schema_version: int
    calibration_id: str
    calibration_hash: str
    logical_channel_id: str
    channel_role: ChannelRole
    sensor_id: str
    sensor_model: str
    sensor_serial_number: str
    input_unit: Unit
    output_unit: Unit
    sensitivity_v_per_output_unit: float
    input_offset_v: float
    polarity_multiplier: int
    quality: CalibrationQuality
    source_kind: CalibrationSourceKind
    source_reference: str
    effective_date: date | None
    valid_input_min_v: float | None = None
    valid_input_max_v: float | None = None
    valid_output_min: float | None = None
    valid_output_max: float | None = None
    validated_frequency_min_hz: float | None = None
    validated_frequency_max_hz: float | None = None
    extended_frequency_min_hz: float | None = None
    extended_frequency_max_hz: float | None = None
    uncertainty: str | None = None
    notes: str | None = None
    expiration_date: date | None = None
    certificate_identifier: str | None = None
    conditions: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("resolved calibration schema_version must be 1")
        _require_sha256(self.calibration_hash, "calibration_hash")
        validate_identifier(self.calibration_id, field="calibration_id")
        validate_identifier(self.logical_channel_id, field="logical_channel_id")
        _require_finite(
            self.sensitivity_v_per_output_unit,
            "sensitivity_v_per_output_unit",
            positive=True,
        )
        _require_finite(self.input_offset_v, "input_offset_v")
        if (
            self.effective_date is None
            and self.source_kind is not CalibrationSourceKind.OPERATOR_TRANSCRIBED
        ):
            raise ValueError("effective_date is required for verified calibration sources")
        if self.polarity_multiplier not in {-1, 1}:
            raise ValueError("polarity_multiplier must be -1 or 1")
        for field in (
            "valid_input_min_v",
            "valid_input_max_v",
            "valid_output_min",
            "valid_output_max",
            "validated_frequency_min_hz",
            "validated_frequency_max_hz",
            "extended_frequency_min_hz",
            "extended_frequency_max_hz",
        ):
            value = getattr(self, field)
            if value is not None:
                _require_finite(value, field, positive=field.endswith("_hz"))
        for minimum_name, maximum_name in (
            ("valid_input_min_v", "valid_input_max_v"),
            ("valid_output_min", "valid_output_max"),
            ("validated_frequency_min_hz", "validated_frequency_max_hz"),
            ("extended_frequency_min_hz", "extended_frequency_max_hz"),
        ):
            minimum = getattr(self, minimum_name)
            maximum = getattr(self, maximum_name)
            if (minimum is None) != (maximum is None):
                raise ValueError(f"{minimum_name} and {maximum_name} must be supplied together")
            if minimum is not None and maximum is not None and minimum >= maximum:
                raise ValueError(f"{minimum_name} must be less than {maximum_name}")


@dataclass(frozen=True, slots=True)
class ProcessingRange:
    start_sample_index: int
    stop_sample_index: int

    def __post_init__(self) -> None:
        if self.start_sample_index < 0 or self.stop_sample_index < self.start_sample_index:
            raise ValueError("processing range must be a valid half-open interval")

    @property
    def sample_count(self) -> int:
        return self.stop_sample_index - self.start_sample_index


@dataclass(frozen=True, slots=True)
class ProcessingNotice:
    code: str
    severity: str
    message: str
    channel_id: str | None = None
    start_sample_index: int | None = None
    stop_sample_index: int | None = None

    def __post_init__(self) -> None:
        if not self.code.startswith("DAQ.PROCESSING."):
            raise ValueError("processing notice code must use DAQ.PROCESSING")
        _require_text(self.message, "message")


@dataclass(frozen=True, slots=True)
class ProcessingProvenance:
    schema_version: int
    provenance_hash: str
    source_session_id: str
    source_completion_state: str
    source_raw_digest: str
    source_range: ProcessingRange
    ordered_channel_ids: tuple[str, ...]
    sample_rate_hz: float
    calibration_ids: tuple[str, ...]
    calibration_hashes: tuple[str, ...]
    calibration_qualities: tuple[CalibrationQuality, ...]
    recipe_id: str
    recipe_version: str
    recipe_hash: str
    processing_mode: ProcessingMode
    application_version: str
    numpy_version: str
    scipy_version: str
    output_units: tuple[Unit, ...]
    notices: tuple[ProcessingNotice, ...]
    algorithm_details: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("processing provenance schema_version must be 1")
        _require_sha256(self.provenance_hash, "provenance_hash")
        _require_sha256(self.source_raw_digest, "source_raw_digest")
        _require_sha256(self.recipe_hash, "recipe_hash")
        for field in (
            "ordered_channel_ids",
            "calibration_ids",
            "calibration_hashes",
            "calibration_qualities",
            "output_units",
            "notices",
        ):
            _require_tuple(getattr(self, field), field)
        count = len(self.ordered_channel_ids)
        if any(
            len(values) != count
            for values in (
                self.calibration_ids,
                self.calibration_hashes,
                self.calibration_qualities,
                self.output_units,
            )
        ):
            raise ValueError("processing provenance channel metadata must align")
        for calibration_hash in self.calibration_hashes:
            _require_sha256(calibration_hash, "calibration_hash")
        _require_finite(self.sample_rate_hz, "sample_rate_hz", positive=True)
        object.__setattr__(
            self, "algorithm_details", MappingProxyType(dict(self.algorithm_details))
        )
