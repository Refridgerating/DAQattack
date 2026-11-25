"""Controlled vocabulary for M01 configuration documents."""

from enum import StrEnum


class Unit(StrEnum):
    VOLT = "V"
    MILLIVOLT = "mV"
    STANDARD_GRAVITY = "g"
    METRES_PER_SECOND_SQUARED = "m/s^2"
    PICOCOULOMB = "pC"
    NANOCOULOMB = "nC"


class SensitivityUnit(StrEnum):
    VOLT_PER_G = "V/g"
    MILLIVOLT_PER_G = "mV/g"
    VOLT_PER_METRES_PER_SECOND_SQUARED = "V/(m/s^2)"
    MILLIVOLT_PER_METRES_PER_SECOND_SQUARED = "mV/(m/s^2)"
    VOLT_PER_PICOCOULOMB = "V/pC"
    MILLIVOLT_PER_PICOCOULOMB = "mV/pC"
    VOLT_PER_NANOCOULOMB = "V/nC"
    MILLIVOLT_PER_NANOCOULOMB = "mV/nC"


class PhysicalQuantity(StrEnum):
    VOLTAGE = "voltage"
    ACCELERATION = "acceleration"
    CHARGE = "charge"


class Polarity(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"


class SensorAxis(StrEnum):
    X = "x"
    Y = "y"
    Z = "z"


class TerminalConfiguration(StrEnum):
    DIFFERENTIAL = "differential"


class SensorType(StrEnum):
    TRIAXIAL_ACCELEROMETER = "triaxial_accelerometer"
    CHARGE_TRANSDUCER = "charge_transducer"


class ChannelRole(StrEnum):
    ACCELEROMETER_X = "accelerometer_x"
    ACCELEROMETER_Y = "accelerometer_y"
    ACCELEROMETER_Z = "accelerometer_z"
    CONDITIONED_CHARGE = "conditioned_charge"
    DIAGNOSTIC_RAW_VOLTAGE = "diagnostic_raw_voltage"


class ProcessingDisposition(StrEnum):
    """Whether a recorded channel may enter scientific processing."""

    CALIBRATED = "calibrated"
    RAW_ONLY_DIAGNOSTIC = "raw_only_diagnostic"


class ExperimentMode(StrEnum):
    """The acceptance scope of an experiment configuration."""

    PRODUCTION = "production"
    ACCELEROMETER_COMMISSIONING = "accelerometer_commissioning"


class SamplingMode(StrEnum):
    FINITE = "finite"
    CONTINUOUS = "continuous"


class TriggerMode(StrEnum):
    IMMEDIATE = "immediate"
    DIGITAL_EDGE = "digital_edge"


class TriggerEdge(StrEnum):
    RISING = "rising"
    FALLING = "falling"


class CalibrationSourceKind(StrEnum):
    CERTIFICATE = "certificate"
    MANUFACTURER_NOMINAL = "manufacturer_nominal"
    EXTERNAL_VALIDATION = "external_validation"
    TEST_FIXTURE = "test_fixture"
    OPERATOR_TRANSCRIBED = "operator_transcribed"


class CalibrationModel(StrEnum):
    LINEAR_SENSITIVITY = "linear_sensitivity"


class RecipeStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"


class ExecutionClass(StrEnum):
    UNQUALIFIED = "unqualified"
    CAUSAL = "causal"


class ProcessingStageKind(StrEnum):
    AFFINE_CALIBRATION = "affine_calibration"
    BUTTERWORTH_HIGH_PASS = "butterworth_high_pass"
    BUTTERWORTH_LOW_PASS = "butterworth_low_pass"


class FilterInitializationPolicy(StrEnum):
    FIRST_SAMPLE_STEADY_STATE = "first_sample_steady_state"


class CalibrationQuality(StrEnum):
    TRACEABLE = "traceable"
    NOMINAL = "nominal"
    EXTERNALLY_VALIDATED = "externally_validated"
    SYNTHETIC = "synthetic"
    UNVERIFIED = "unverified"


class SignalStage(StrEnum):
    RAW = "raw"
    CALIBRATED = "calibrated"
    CONDITIONED = "conditioned"


class ProcessingMode(StrEnum):
    LIVE = "live"
    REPLAY = "replay"


class ContinuityStatus(StrEnum):
    NEW_SEGMENT = "new_segment"
    CONTIGUOUS = "contiguous"
    RESET = "reset"


class MetricAvailability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    NOT_EVALUATED = "not_evaluated"
