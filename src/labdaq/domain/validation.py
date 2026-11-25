"""Pure cross-record validation for M01 configuration records."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

from labdaq.domain.enums import (
    ChannelRole,
    ExperimentMode,
    PhysicalQuantity,
    ProcessingDisposition,
    SensitivityUnit,
    SensorAxis,
    SensorType,
)
from labdaq.errors import ConfigurationValidationError

if TYPE_CHECKING:
    from collections.abc import Hashable, Iterable

    from labdaq.domain.models import (
        ExperimentConfig,
        HardwareProfile,
        ProcessingRecipeEnvelope,
    )

ORTHONORMAL_TOLERANCE = 1e-6
_HashableT = TypeVar("_HashableT", bound="Hashable")

_SENSITIVITY_DIMENSIONS = {
    SensitivityUnit.VOLT_PER_G: (PhysicalQuantity.VOLTAGE, PhysicalQuantity.ACCELERATION),
    SensitivityUnit.MILLIVOLT_PER_G: (
        PhysicalQuantity.VOLTAGE,
        PhysicalQuantity.ACCELERATION,
    ),
    SensitivityUnit.VOLT_PER_METRES_PER_SECOND_SQUARED: (
        PhysicalQuantity.VOLTAGE,
        PhysicalQuantity.ACCELERATION,
    ),
    SensitivityUnit.MILLIVOLT_PER_METRES_PER_SECOND_SQUARED: (
        PhysicalQuantity.VOLTAGE,
        PhysicalQuantity.ACCELERATION,
    ),
    SensitivityUnit.VOLT_PER_PICOCOULOMB: (
        PhysicalQuantity.VOLTAGE,
        PhysicalQuantity.CHARGE,
    ),
    SensitivityUnit.MILLIVOLT_PER_PICOCOULOMB: (
        PhysicalQuantity.VOLTAGE,
        PhysicalQuantity.CHARGE,
    ),
    SensitivityUnit.VOLT_PER_NANOCOULOMB: (
        PhysicalQuantity.VOLTAGE,
        PhysicalQuantity.CHARGE,
    ),
    SensitivityUnit.MILLIVOLT_PER_NANOCOULOMB: (
        PhysicalQuantity.VOLTAGE,
        PhysicalQuantity.CHARGE,
    ),
}


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    field_path: str
    rule: str
    correction: str
    invalid_value: object | None = None

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "field_path": self.field_path,
            "rule": self.rule,
            "correction": self.correction,
        }
        if self.invalid_value is not None:
            result["invalid_value"] = self.invalid_value
        return result


def _duplicate_values(values: Iterable[_HashableT]) -> set[_HashableT]:
    seen: set[_HashableT] = set()
    duplicates: set[_HashableT] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


def validate_hardware_profile(profile: HardwareProfile) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    device_ids = [device.device_id for device in profile.devices]
    module_ids = [module.module_id for module in profile.modules]
    for field, duplicates in (
        ("devices", _duplicate_values(device_ids)),
        ("modules", _duplicate_values(module_ids)),
    ):
        if duplicates:
            issues.append(
                ValidationIssue(
                    field,
                    "identifiers must be unique",
                    "Assign one stable identifier to each record.",
                    sorted(str(value) for value in duplicates),
                )
            )
    known_devices = set(device_ids)
    for index, module in enumerate(profile.modules):
        if module.device_id not in known_devices:
            issues.append(
                ValidationIssue(
                    f"modules[{index}].device_id",
                    "module device reference must resolve",
                    "Reference a device declared by this profile.",
                    module.device_id,
                )
            )
    known_modules = set(module_ids)
    physical_keys = [
        (channel.module_id, channel.channel_index)
        for channel in profile.supported_physical_channels
    ]
    duplicate_physical = _duplicate_values(physical_keys)
    if duplicate_physical:
        issues.append(
            ValidationIssue(
                "supported_physical_channels",
                "physical channels must be unique",
                "Remove duplicate module/channel-index pairs.",
                [f"{module}:{index}" for module, index in sorted(duplicate_physical)],
            )
        )
    for index, channel in enumerate(profile.supported_physical_channels):
        if channel.module_id not in known_modules:
            issues.append(
                ValidationIssue(
                    f"supported_physical_channels[{index}].module_id",
                    "physical-channel module reference must resolve",
                    "Reference a module declared by this profile.",
                    channel.module_id,
                )
            )
        if channel.terminal_configuration is not profile.required_terminal_configuration:
            issues.append(
                ValidationIssue(
                    f"supported_physical_channels[{index}].terminal_configuration",
                    "terminal configuration must match the profile requirement",
                    f"Use {profile.required_terminal_configuration.value}.",
                    channel.terminal_configuration.value,
                )
            )
    if not profile.application_supported_sample_rates_hz:
        issues.append(
            ValidationIssue(
                "application_supported_sample_rates_hz",
                "at least one rate is required",
                "Declare an application-qualified sample rate.",
            )
        )
    if len(profile.supported_physical_channels) < profile.minimum_channel_count:
        issues.append(
            ValidationIssue(
                "supported_physical_channels",
                "profile does not provide its minimum channel count",
                "Add supported channels or reduce the declared minimum.",
            )
        )
    return tuple(issues)


def _validate_matrix(
    field_path: str,
    matrix: tuple[tuple[float, float, float], ...],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for row_index, row in enumerate(matrix):
        norm = sum(value * value for value in row)
        if not math.isclose(norm, 1.0, abs_tol=ORTHONORMAL_TOLERANCE):
            issues.append(
                ValidationIssue(
                    f"{field_path}[{row_index}]",
                    "direction-cosine rows must have unit length",
                    f"Provide an orthonormal matrix within {ORTHONORMAL_TOLERANCE:g}.",
                    norm,
                )
            )
    for left in range(3):
        for right in range(left + 1, 3):
            dot = sum(matrix[left][index] * matrix[right][index] for index in range(3))
            if not math.isclose(dot, 0.0, abs_tol=ORTHONORMAL_TOLERANCE):
                issues.append(
                    ValidationIssue(
                        field_path,
                        "direction-cosine rows must be mutually orthogonal",
                        f"Provide an orthonormal matrix within {ORTHONORMAL_TOLERANCE:g}.",
                        dot,
                    )
                )
    return issues


def validate_experiment_config(
    config: ExperimentConfig,
    profile: HardwareProfile,
) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = list(validate_hardware_profile(profile))
    if config.hardware_profile_id != profile.hardware_profile_id:
        issues.append(
            ValidationIssue(
                "hardware_profile_id",
                "hardware profile reference must resolve",
                f"Use {profile.hardware_profile_id} or install the referenced profile.",
                config.hardware_profile_id,
            )
        )
    if config.compatible_profile_revision != profile.profile_revision:
        issues.append(
            ValidationIssue(
                "compatible_profile_revision",
                "hardware profile revision must match exactly",
                f"Use revision {profile.profile_revision}.",
                config.compatible_profile_revision,
            )
        )

    collections = (
        ("coordinate_frames", [item.coordinate_frame_id for item in config.coordinate_frames]),
        ("sensors", [item.sensor_id for item in config.sensors]),
        (
            "conditioning_chains",
            [item.conditioning_chain_id for item in config.conditioning_chains],
        ),
        ("calibrations", [item.calibration_id for item in config.calibrations]),
        ("channels", [item.channel_id for item in config.channels]),
    )
    for field, identifiers in collections:
        duplicates = _duplicate_values(identifiers)
        if duplicates:
            issues.append(
                ValidationIssue(
                    field,
                    "identifiers must be unique",
                    "Assign a unique identifier to each record.",
                    sorted(str(value) for value in duplicates),
                )
            )

    frame_by_id = {item.coordinate_frame_id: item for item in config.coordinate_frames}
    sensor_by_id = {item.sensor_id: item for item in config.sensors}
    chain_by_id = {item.conditioning_chain_id: item for item in config.conditioning_chains}
    calibration_by_id = {item.calibration_id: item for item in config.calibrations}
    channel_by_id = {item.channel_id: item for item in config.channels}

    for index, frame in enumerate(config.coordinate_frames):
        if frame.direction_cosine_matrix is not None:
            issues.extend(
                _validate_matrix(
                    f"coordinate_frames[{index}].direction_cosine_matrix",
                    frame.direction_cosine_matrix,
                )
            )
    for index, sensor in enumerate(config.sensors):
        if sensor.coordinate_frame_id not in frame_by_id:
            issues.append(
                ValidationIssue(
                    f"sensors[{index}].coordinate_frame_id",
                    "coordinate-frame reference must resolve",
                    "Reference one declared coordinate frame.",
                    sensor.coordinate_frame_id,
                )
            )

    physical_owners: dict[tuple[str, int], str] = {}
    roles: list[ChannelRole] = []
    orders: list[int] = []
    axes_by_sensor: dict[str, set[SensorAxis]] = {}
    profile_physical = {
        (channel.module_id, channel.channel_index, channel.terminal_configuration)
        for channel in profile.supported_physical_channels
    }
    for index, channel in enumerate(config.channels):
        roles.append(channel.channel_role)
        orders.append(channel.channel_order)
        physical_key = (
            channel.physical_channel.module_id,
            channel.physical_channel.channel_index,
        )
        previous_owner = physical_owners.get(physical_key)
        if previous_owner is not None:
            issues.append(
                ValidationIssue(
                    f"channels[{index}].physical_channel",
                    "physical channel must be unique",
                    "Assign each logical channel to a different physical channel.",
                    [previous_owner, channel.channel_id],
                )
            )
        else:
            physical_owners[physical_key] = channel.channel_id
        profile_key = (*physical_key, channel.physical_channel.terminal_configuration)
        if profile_key not in profile_physical:
            issues.append(
                ValidationIssue(
                    f"channels[{index}].physical_channel",
                    "physical channel must be supported by the hardware profile",
                    "Use one of the profile's declared module channels and terminal mode.",
                    f"{physical_key[0]}:{physical_key[1]}",
                )
            )
        resolved_sensor = sensor_by_id.get(channel.sensor_id) if channel.sensor_id else None
        calibration = (
            calibration_by_id.get(channel.calibration_id)
            if channel.calibration_id is not None
            else None
        )
        if resolved_sensor is None and (
            channel.sensor_id is not None
            or channel.processing_disposition is not ProcessingDisposition.RAW_ONLY_DIAGNOSTIC
        ):
            issues.append(
                ValidationIssue(
                    f"channels[{index}].sensor_id",
                    "sensor reference must resolve",
                    "Reference one declared sensor.",
                    channel.sensor_id,
                )
            )
        if (
            channel.processing_disposition is ProcessingDisposition.CALIBRATED
            and calibration is None
        ):
            issues.append(
                ValidationIssue(
                    f"channels[{index}].calibration_id",
                    "calibration reference must resolve",
                    "Reference one complete calibration.",
                    channel.calibration_id,
                )
            )
        elif calibration is not None:
            if (
                calibration.input_quantity is not channel.raw_quantity
                or calibration.input_unit is not channel.raw_unit
                or calibration.output_quantity is not channel.engineering_quantity
                or calibration.output_unit is not channel.engineering_unit
            ):
                issues.append(
                    ValidationIssue(
                        f"channels[{index}].calibration_id",
                        "calibration quantities and units must match the channel",
                        "Use a calibration whose input is the raw signal and output is the engineering signal.",
                        channel.calibration_id,
                    )
                )
            dimensions = _SENSITIVITY_DIMENSIONS[calibration.sensitivity_unit]
            if dimensions != (channel.raw_quantity, channel.engineering_quantity):
                issues.append(
                    ValidationIssue(
                        f"calibrations[{channel.calibration_id}].sensitivity_unit",
                        "sensitivity ratio dimensions must match the calibration",
                        "Use voltage/acceleration or voltage/charge as appropriate.",
                        calibration.sensitivity_unit.value,
                    )
                )
        if channel.requested_input_min < profile.input_min_v or (
            channel.requested_input_max > profile.input_max_v
        ):
            issues.append(
                ValidationIssue(
                    f"channels[{index}].requested_input_min",
                    "requested input range must be within the hardware profile range",
                    f"Use values within {profile.input_min_v:g} V to {profile.input_max_v:g} V.",
                    [channel.requested_input_min, channel.requested_input_max],
                )
            )
        is_raw_diagnostic = (
            channel.processing_disposition is ProcessingDisposition.RAW_ONLY_DIAGNOSTIC
        )
        if is_raw_diagnostic:
            if channel.channel_role is not ChannelRole.DIAGNOSTIC_RAW_VOLTAGE:
                issues.append(
                    ValidationIssue(
                        f"channels[{index}].processing_disposition",
                        "raw-only disposition is reserved for diagnostic voltage channels",
                        "Use diagnostic_raw_voltage or restore calibrated processing.",
                        channel.channel_role.value,
                    )
                )
            if channel.calibration_id is not None or channel.conditioning_chain_id is not None:
                issues.append(
                    ValidationIssue(
                        f"channels[{index}].calibration_id",
                        "raw-only diagnostic channels cannot claim calibration or conditioning",
                        "Set calibration_id and conditioning_chain_id to null.",
                    )
                )
            if (
                channel.raw_quantity is not PhysicalQuantity.VOLTAGE
                or channel.engineering_quantity is not PhysicalQuantity.VOLTAGE
                or channel.raw_unit.value != "V"
                or channel.engineering_unit.value != "V"
            ):
                issues.append(
                    ValidationIssue(
                        f"channels[{index}].engineering_unit",
                        "raw-only diagnostic channels must remain uninterpreted volts",
                        "Use voltage/V for both raw and engineering declarations.",
                    )
                )
        elif channel.channel_role is ChannelRole.DIAGNOSTIC_RAW_VOLTAGE:
            issues.append(
                ValidationIssue(
                    f"channels[{index}].processing_disposition",
                    "diagnostic voltage cannot enter calibrated processing",
                    "Use raw_only_diagnostic and remove calibration metadata.",
                )
            )
        elif channel.channel_role is ChannelRole.CONDITIONED_CHARGE:
            if channel.conditioning_chain_id is None:
                issues.append(
                    ValidationIssue(
                        f"channels[{index}].conditioning_chain_id",
                        "conditioned charge requires a complete conditioning chain",
                        "Reference a chain with validated calibration and polarity.",
                    )
                )
            else:
                chain = chain_by_id.get(channel.conditioning_chain_id)
                if chain is None:
                    issues.append(
                        ValidationIssue(
                            f"channels[{index}].conditioning_chain_id",
                            "conditioning-chain reference must resolve",
                            "Reference one declared conditioning chain.",
                            channel.conditioning_chain_id,
                        )
                    )
                elif calibration is not None and (
                    chain.output_quantity is not channel.raw_quantity
                    or chain.output_unit is not channel.raw_unit
                    or chain.input_quantity is not channel.engineering_quantity
                    or chain.input_unit is not channel.engineering_unit
                    or chain.polarity is not calibration.polarity
                ):
                    issues.append(
                        ValidationIssue(
                            f"channels[{index}].conditioning_chain_id",
                            "conditioning chain must match channel units, quantities, and polarity",
                            "Correct the complete external chain definition and calibration.",
                            channel.conditioning_chain_id,
                        )
                    )
        elif channel.conditioning_chain_id is not None:
            issues.append(
                ValidationIssue(
                    f"channels[{index}].conditioning_chain_id",
                    "accelerometer channels do not use the charge conditioning chain",
                    "Remove the conditioning-chain reference.",
                    channel.conditioning_chain_id,
                )
            )
        if channel.sensor_output_axis is not None and channel.sensor_id is not None:
            axes = axes_by_sensor.setdefault(channel.sensor_id, set())
            if channel.sensor_output_axis in axes:
                issues.append(
                    ValidationIssue(
                        f"channels[{index}].sensor_output_axis",
                        "a sensor output axis may be used only once",
                        "Assign distinct x, y, and z outputs.",
                        channel.sensor_output_axis.value,
                    )
                )
            axes.add(channel.sensor_output_axis)

    accelerometer_roles = {
        ChannelRole.ACCELEROMETER_X,
        ChannelRole.ACCELEROMETER_Y,
        ChannelRole.ACCELEROMETER_Z,
    }
    raw_voltage_config = bool(roles) and all(
        role is ChannelRole.DIAGNOSTIC_RAW_VOLTAGE for role in roles
    )
    if raw_voltage_config:
        roles_valid = True
    elif config.experiment_mode is ExperimentMode.PRODUCTION:
        expected_roles = accelerometer_roles | {ChannelRole.CONDITIONED_CHARGE}
        roles_valid = set(roles) == expected_roles and len(roles) == 4
    else:
        roles_valid = (set(roles) == accelerometer_roles and len(roles) == 3) or (
            set(roles) == accelerometer_roles | {ChannelRole.DIAGNOSTIC_RAW_VOLTAGE}
            and len(roles) == 4
        )
    if not roles_valid:
        issues.append(
            ValidationIssue(
                "channels",
                "configuration roles do not match the selected experiment mode",
                "Production requires X/Y/Z plus conditioned_charge; commissioning permits X/Y/Z plus one optional raw diagnostic.",
                [role.value for role in roles],
            )
        )
    if sorted(orders) != list(range(len(config.channels))):
        issues.append(
            ValidationIssue(
                "channels.channel_order",
                "channel order must be contiguous and start at zero",
                f"Use channel_order values 0 through {len(config.channels) - 1} exactly once.",
                orders,
            )
        )
    for sensor in config.sensors:
        if sensor.sensor_type is SensorType.TRIAXIAL_ACCELEROMETER:
            axes = axes_by_sensor.get(sensor.sensor_id, set())
            if sensor.output_count != 3 or axes != {SensorAxis.X, SensorAxis.Y, SensorAxis.Z}:
                issues.append(
                    ValidationIssue(
                        f"sensors[{sensor.sensor_id}]",
                        "triaxial sensor must expose unique x, y, and z channels",
                        "Reference the same sensor from exactly three axis channels.",
                        sorted(axis.value for axis in axes),
                    )
                )

    acquisition_ids = config.acquisition.channel_ids
    duplicate_acquisition_ids = _duplicate_values(acquisition_ids)
    if duplicate_acquisition_ids:
        issues.append(
            ValidationIssue(
                "acquisition.channel_ids",
                "acquisition channels must be unique",
                "List each enabled channel exactly once.",
                sorted(str(value) for value in duplicate_acquisition_ids),
            )
        )
    minimum_channels = 1 if raw_voltage_config else profile.minimum_channel_count
    if not minimum_channels <= len(acquisition_ids) <= profile.maximum_channel_count:
        issues.append(
            ValidationIssue(
                "acquisition.channel_ids",
                "acquisition channel count is outside the profile range",
                f"List between {minimum_channels} and {profile.maximum_channel_count} enabled channels.",
                len(acquisition_ids),
            )
        )
    for index, channel_id in enumerate(acquisition_ids):
        resolved_channel = channel_by_id.get(channel_id)
        if resolved_channel is None:
            issues.append(
                ValidationIssue(
                    f"acquisition.channel_ids[{index}]",
                    "acquisition channel reference must resolve",
                    "Reference a declared channel.",
                    channel_id,
                )
            )
        elif not resolved_channel.enabled:
            issues.append(
                ValidationIssue(
                    f"acquisition.channel_ids[{index}]",
                    "acquisition channels must be enabled",
                    "Enable the channel or remove it from acquisition.",
                    channel_id,
                )
            )
    ordered_ids = tuple(
        channel.channel_id
        for channel in sorted(config.channels, key=lambda item: item.channel_order)
        if channel.enabled or not raw_voltage_config
    )
    if acquisition_ids != ordered_ids:
        issues.append(
            ValidationIssue(
                "acquisition.channel_ids",
                "acquisition order must match explicit channel_order",
                "List channel IDs in ascending channel_order.",
                list(acquisition_ids),
            )
        )
    if config.acquisition.sample_rate_hz not in profile.application_supported_sample_rates_hz:
        issues.append(
            ValidationIssue(
                "acquisition.sample_rate_hz",
                "sample rate must be application-supported by the hardware profile",
                f"Use one of {list(profile.application_supported_sample_rates_hz)}.",
                config.acquisition.sample_rate_hz,
            )
        )
    if config.acquisition.trigger.mode not in profile.supported_trigger_modes:
        issues.append(
            ValidationIssue(
                "acquisition.trigger.mode",
                "trigger mode must be supported by the hardware profile",
                f"Use one of {[mode.value for mode in profile.supported_trigger_modes]}.",
                config.acquisition.trigger.mode.value,
            )
        )

    referenced_sensors = {channel.sensor_id for channel in config.channels if channel.sensor_id}
    referenced_calibrations = {
        channel.calibration_id for channel in config.channels if channel.calibration_id is not None
    }
    referenced_chains = {
        channel.conditioning_chain_id
        for channel in config.channels
        if channel.conditioning_chain_id is not None
    }
    referenced_frames = {sensor.coordinate_frame_id for sensor in config.sensors}
    for field, declared, referenced in (
        ("sensors", set(sensor_by_id), referenced_sensors),
        ("calibrations", set(calibration_by_id), referenced_calibrations),
        ("conditioning_chains", set(chain_by_id), referenced_chains),
        ("coordinate_frames", set(frame_by_id), referenced_frames),
    ):
        orphaned = declared - referenced
        if orphaned:
            issues.append(
                ValidationIssue(
                    field,
                    "orphaned records are not allowed",
                    "Remove unused records or reference them exactly once where required.",
                    sorted(orphaned),
                )
            )
    return tuple(issues)


def validate_processing_recipe(
    recipe: ProcessingRecipeEnvelope,
) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    if recipe.execution_class.value == "causal":
        for index, stage in enumerate(recipe.stages):
            if not stage.causal:
                issues.append(
                    ValidationIssue(
                        f"stages[{index}].causal",
                        "live causal recipes cannot contain noncausal stages",
                        "Remove the stage or mark the recipe offline-only.",
                        stage.stage_id,
                    )
                )
    calibration_indices = [
        index
        for index, stage in enumerate(recipe.stages)
        if stage.kind.value == "affine_calibration"
    ]
    if recipe.status.value == "active" and calibration_indices != [0]:
        issues.append(
            ValidationIssue(
                "stages",
                "active baseline recipe must start with exactly one affine calibration",
                "Place the affine calibration stage first.",
            )
        )
    return tuple(issues)


def raise_for_issues(
    issues: tuple[ValidationIssue, ...],
    *,
    document_type: str,
    document: str | None = None,
) -> None:
    if issues:
        raise ConfigurationValidationError(
            "Configuration validation failed.",
            document_type=document_type,
            document=document,
            issues=[issue.to_dict() for issue in issues],
        )
