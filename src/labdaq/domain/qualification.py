"""Immutable hardware-qualification records and deterministic decisions."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING

from labdaq.domain.serialization import canonical_sha256, canonical_sha256_excluding

if TYPE_CHECKING:
    from datetime import datetime


class QualificationStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"
    NOT_TESTED = "not-tested"


class QualificationReasonCode(StrEnum):
    STARTED = "DAQ.QUALIFICATION.QUALIFICATION_STARTED"
    COMPLETED = "DAQ.QUALIFICATION.QUALIFICATION_COMPLETED"
    CANCELLED = "DAQ.QUALIFICATION.QUALIFICATION_CANCELLED"
    FAILED = "DAQ.QUALIFICATION.QUALIFICATION_FAILED"
    HARDWARE_IDENTITY_MISMATCH = "DAQ.QUALIFICATION.HARDWARE_IDENTITY_MISMATCH"
    HARDWARE_CAPABILITY_MISMATCH = "DAQ.QUALIFICATION.HARDWARE_CAPABILITY_MISMATCH"
    HARDWARE_SELF_TEST_PASSED = "DAQ.QUALIFICATION.HARDWARE_SELF_TEST_PASSED"
    HARDWARE_SELF_TEST_FAILED = "DAQ.QUALIFICATION.HARDWARE_SELF_TEST_FAILED"
    HARDWARE_SELF_TEST_NOT_SUPPORTED = "DAQ.QUALIFICATION.HARDWARE_SELF_TEST_NOT_SUPPORTED"
    CHANNEL_BIAS_OUT_OF_RANGE = "DAQ.QUALIFICATION.CHANNEL_BIAS_OUT_OF_RANGE"
    CHANNEL_NOISE_ABOVE_LIMIT = "DAQ.QUALIFICATION.CHANNEL_NOISE_ABOVE_LIMIT"
    CHANNEL_NEAR_CLIPPING = "DAQ.QUALIFICATION.CHANNEL_NEAR_CLIPPING"
    CHANNEL_CLIPPING_DETECTED = "DAQ.QUALIFICATION.CHANNEL_CLIPPING_DETECTED"
    CHANNEL_FROZEN = "DAQ.QUALIFICATION.CHANNEL_FROZEN"
    CHANNEL_DROPOUT_DETECTED = "DAQ.QUALIFICATION.CHANNEL_DROPOUT_DETECTED"
    CHANNEL_NONFINITE_SAMPLE = "DAQ.QUALIFICATION.CHANNEL_NONFINITE_SAMPLE"
    SENSOR_RESPONSE_MISSING = "DAQ.QUALIFICATION.SENSOR_RESPONSE_MISSING"
    SENSOR_AXIS_MAPPING_AMBIGUOUS = "DAQ.QUALIFICATION.SENSOR_AXIS_MAPPING_AMBIGUOUS"
    CHARGE_CONDITIONING_INCOMPATIBLE = "DAQ.QUALIFICATION.CHARGE_CONDITIONING_INCOMPATIBLE"
    CROSS_CHANNEL_MISALIGNMENT = "DAQ.QUALIFICATION.CROSS_CHANNEL_MISALIGNMENT"
    CROSSTALK_ABOVE_LIMIT = "DAQ.QUALIFICATION.CROSSTALK_ABOVE_LIMIT"
    THROUGHPUT_GAP_DETECTED = "DAQ.QUALIFICATION.THROUGHPUT_GAP_DETECTED"
    THROUGHPUT_CHANNEL_LENGTH_MISMATCH = "DAQ.QUALIFICATION.THROUGHPUT_CHANNEL_LENGTH_MISMATCH"
    THROUGHPUT_STORAGE_BACKPRESSURE = "DAQ.QUALIFICATION.THROUGHPUT_STORAGE_BACKPRESSURE"
    REPORT_STALE = "DAQ.QUALIFICATION.REPORT_STALE"
    REQUIRED_TEST_NOT_RUN = "DAQ.QUALIFICATION.REQUIRED_TEST_NOT_RUN"
    BIAS_LIMIT_UNRESOLVED = "DAQ.QUALIFICATION.BIAS_LIMIT_UNRESOLVED"
    CROSSTALK_LIMIT_UNRESOLVED = "DAQ.QUALIFICATION.CROSSTALK_LIMIT_UNRESOLVED"
    RENDERING_UNAVAILABLE = "DAQ.QUALIFICATION.RENDERING_UNAVAILABLE"
    PLAN_INVALID = "DAQ.QUALIFICATION.PLAN_INVALID"
    PLAN_MISMATCH = "DAQ.QUALIFICATION.PLAN_MISMATCH"
    REPORT_INVALID = "DAQ.QUALIFICATION.REPORT_INVALID"
    REPORT_READ_FAILED = "DAQ.QUALIFICATION.REPORT_READ_FAILED"
    REPORT_WRITE_FAILED = "DAQ.QUALIFICATION.REPORT_WRITE_FAILED"


class QualificationLifecycle(StrEnum):
    IN_PROGRESS = "in-progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAULTED = "faulted"
    RECOVERED = "recovered"


class QualificationExecutionMode(StrEnum):
    AUTOMATED = "automated"
    OPERATOR_ASSISTED = "operator-assisted"
    EXTERNAL_FIXTURE = "external-fixture"


class QualificationClassification(StrEnum):
    BLOCKING = "blocking"
    ADVISORY = "advisory"


class LimitComparison(StrEnum):
    LESS_THAN = "lt"
    LESS_THAN_OR_EQUAL = "lte"
    GREATER_THAN = "gt"
    GREATER_THAN_OR_EQUAL = "gte"
    EQUAL = "eq"
    NOT_EQUAL = "neq"
    BETWEEN_INCLUSIVE = "between-inclusive"


class LimitSourceCategory(StrEnum):
    MANUFACTURER_SPECIFICATION = "manufacturer-specification"
    CALIBRATION_CERTIFICATE = "calibration-certificate"
    LABORATORY_BASELINE = "laboratory-baseline"
    ENGINEERING_POLICY = "engineering-policy"
    EXTERNAL_VALIDATION = "external-validation"
    UNRESOLVED = "unresolved"


class SignalStage(StrEnum):
    RAW = "raw"
    CALIBRATED = "calibrated"
    CONDITIONED = "conditioned"
    ACQUISITION = "acquisition"


class MetricEvaluation(StrEnum):
    WITHIN_LIMIT = "within-limit"
    OUTSIDE_WARNING_LIMIT = "outside-warning-limit"
    OUTSIDE_FAILURE_LIMIT = "outside-failure-limit"
    UNEVALUATED = "unevaluated"


class MetricValueKind(StrEnum):
    NUMBER = "number"
    BOOLEAN = "boolean"
    TEXT = "text"


class EvidenceRetention(StrEnum):
    RETAIN_RAW = "retain-raw"


class OverrideDisposition(StrEnum):
    ACKNOWLEDGED = "acknowledged"


@dataclass(frozen=True, slots=True)
class QualificationLimit:
    limit_id: str
    comparison: LimitComparison
    value: float | bool | str | tuple[float, float] | None
    unit: str
    signal_stage: SignalStage
    source_category: LimitSourceCategory
    source_reference: str
    rationale: str
    consequence: QualificationStatus
    metric_name: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.limit_id, "limit_id")
        _require_text(self.unit, "unit")
        _require_text(self.source_reference, "source_reference")
        _require_text(self.rationale, "rationale")
        if self.metric_name is not None:
            _require_text(self.metric_name, "metric_name")
        if self.source_category is LimitSourceCategory.UNRESOLVED:
            if self.value is not None:
                raise ValueError("unresolved limits must not fabricate a value")
            if self.consequence not in {
                QualificationStatus.WARNING,
                QualificationStatus.NOT_TESTED,
            }:
                raise ValueError("unresolved limits may only warn or remain not-tested")
        elif self.value is None:
            raise ValueError("resolved limits require a value")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("limit value must be finite")
        if isinstance(self.value, tuple):
            if len(self.value) != 2 or not all(math.isfinite(item) for item in self.value):
                raise ValueError("range limits require two finite values")
            if self.value[0] > self.value[1]:
                raise ValueError("range limit minimum must not exceed maximum")
            if self.comparison is not LimitComparison.BETWEEN_INCLUSIVE:
                raise ValueError("tuple limit values require between-inclusive comparison")


@dataclass(frozen=True, slots=True)
class QualificationTestDefinition:
    test_id: str
    version: int
    display_name: str
    purpose: str
    required: bool
    classification: QualificationClassification
    execution_mode: QualificationExecutionMode
    required_inputs: tuple[str, ...]
    prerequisites: tuple[str, ...]
    declared_metrics: tuple[str, ...]
    limits: tuple[QualificationLimit, ...]
    required_confirmation_ids: tuple[str, ...]
    advisory_reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text(self.test_id, "test_id")
        if self.version < 1:
            raise ValueError("test version must be positive")
        _require_text(self.display_name, "display_name")
        _require_text(self.purpose, "purpose")
        _require_unique(self.required_confirmation_ids, "required_confirmation_ids")
        _require_unique(tuple(item.limit_id for item in self.limits), "limit IDs")
        for code in self.advisory_reason_codes:
            _require_reason_code(code)

    @property
    def blocking(self) -> bool:
        return self.classification is QualificationClassification.BLOCKING


@dataclass(frozen=True, slots=True)
class QualificationPlan:
    schema_version: int
    plan_id: str
    plan_revision: int
    hardware_profile_id: str | None
    hardware_profile_revision: int | None
    experiment_config_hash: str | None
    tests: tuple[QualificationTestDefinition, ...]
    acquisition_rate_hz: float
    powered_idle_duration_seconds: float
    assisted_window_duration_seconds: float
    throughput_duration_seconds: float
    maximum_report_age_seconds: float | None
    evidence_retention: EvidenceRetention = EvidenceRetention.RETAIN_RAW
    assisted_settling_duration_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("qualification plan schema_version must be 1")
        _require_text(self.plan_id, "plan_id")
        if self.plan_revision < 1:
            raise ValueError("plan_revision must be positive")
        _require_unique(tuple(item.test_id for item in self.tests), "test IDs")
        for name in (
            "acquisition_rate_hz",
            "powered_idle_duration_seconds",
            "assisted_window_duration_seconds",
            "throughput_duration_seconds",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.maximum_report_age_seconds is not None and (
            not math.isfinite(self.maximum_report_age_seconds)
            or self.maximum_report_age_seconds <= 0
        ):
            raise ValueError("maximum_report_age_seconds must be finite and positive")
        if (
            not math.isfinite(self.assisted_settling_duration_seconds)
            or self.assisted_settling_duration_seconds < 0
        ):
            raise ValueError("assisted_settling_duration_seconds must be finite and non-negative")

    @property
    def content_hash(self) -> str:
        return canonical_sha256(self)

    def with_throughput_duration(self, seconds: float) -> QualificationPlan:
        return replace(self, throughput_duration_seconds=seconds)


@dataclass(frozen=True, slots=True)
class OperatorConfirmation:
    confirmation_id: str
    confirmed: bool
    confirmed_at: datetime
    operator: str
    statement: str
    evidence_reference: str | None = None
    sample_index: int | None = None
    sample_count: int | None = None

    def __post_init__(self) -> None:
        _require_text(self.confirmation_id, "confirmation_id")
        _require_aware(self.confirmed_at, "confirmed_at")
        _require_text(self.operator, "operator")
        _require_text(self.statement, "statement")
        if self.sample_index is not None and self.sample_index < 0:
            raise ValueError("confirmation sample_index must be non-negative")
        if self.sample_count is not None and self.sample_count < 0:
            raise ValueError("confirmation sample_count must be non-negative")


@dataclass(frozen=True, slots=True)
class ConfirmationDocument:
    schema_version: int
    confirmations: tuple[OperatorConfirmation, ...]

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("confirmation schema_version must be 1")
        _require_unique(
            tuple(item.confirmation_id for item in self.confirmations),
            "confirmation IDs",
        )


@dataclass(frozen=True, slots=True)
class QualificationMetric:
    name: str
    value_kind: MetricValueKind
    value: float | bool | str
    unit: str
    signal_stage: SignalStage
    evaluation: MetricEvaluation
    method: str
    software_version: str
    channel_id: str | None = None
    first_sample_index: int | None = None
    sample_count: int | None = None
    limit: QualificationLimit | None = None

    def __post_init__(self) -> None:
        _require_text(self.name, "metric name")
        _require_text(self.unit, "metric unit")
        _require_text(self.method, "metric method")
        _require_text(self.software_version, "metric software_version")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("qualification metrics must be finite")
        if self.first_sample_index is not None and self.first_sample_index < 0:
            raise ValueError("first_sample_index must be non-negative")
        if self.sample_count is not None and self.sample_count < 0:
            raise ValueError("sample_count must be non-negative")


@dataclass(frozen=True, slots=True)
class EvidenceLink:
    artifact_type: str
    relative_path: str
    sha256: str | None
    disposition: str

    def __post_init__(self) -> None:
        _require_text(self.artifact_type, "artifact_type")
        _require_text(self.relative_path, "relative_path")
        _require_text(self.disposition, "disposition")
        if self.sha256 is not None:
            _require_sha256(self.sha256, "sha256")


@dataclass(frozen=True, slots=True)
class QualificationTestResult:
    test_id: str
    version: int
    started_at: datetime
    completed_at: datetime
    status: QualificationStatus
    classification: QualificationClassification
    required: bool
    summary: str
    reason_codes: tuple[str, ...]
    metrics: tuple[QualificationMetric, ...]
    event_codes: tuple[str, ...]
    evidence: tuple[EvidenceLink, ...]
    confirmations: tuple[OperatorConfirmation, ...]
    assumptions: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text(self.test_id, "test_id")
        if self.version < 1:
            raise ValueError("test result version must be positive")
        _require_aware(self.started_at, "started_at")
        _require_aware(self.completed_at, "completed_at")
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")
        _require_text(self.summary, "summary")
        for code in self.reason_codes:
            _require_reason_code(code)

    @property
    def blocking(self) -> bool:
        return self.classification is QualificationClassification.BLOCKING


@dataclass(frozen=True, slots=True)
class QualificationSampleCounts:
    expected_per_channel: int
    acquired_per_channel: int
    persisted_per_channel: tuple[int, ...]
    replayed_per_channel: tuple[int, ...]
    missing: int = 0
    duplicated: int = 0
    reordered: int = 0
    display_dropped: int = 0
    invalid: int = 0

    def __post_init__(self) -> None:
        for name in (
            "expected_per_channel",
            "acquired_per_channel",
            "missing",
            "duplicated",
            "reordered",
            "display_dropped",
            "invalid",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")
        if any(value < 0 for value in self.persisted_per_channel + self.replayed_per_channel):
            raise ValueError("channel sample counts must be non-negative")


@dataclass(frozen=True, slots=True)
class QualificationTelemetry:
    read_count: int = 0
    read_duration_total_seconds: float = 0.0
    read_duration_max_seconds: float = 0.0
    write_duration_total_seconds: float = 0.0
    write_duration_max_seconds: float = 0.0
    recording_queue_high_water_chunks: int = 0
    recording_queue_capacity_chunks: int = 0
    recording_queue_max_utilization: float = 0.0
    backpressure_events: int = 0
    disk_free_before_bytes: int | None = None
    disk_free_after_bytes: int | None = None
    stop_latency_seconds: float | None = None
    cleanup_succeeded: bool | None = None


@dataclass(frozen=True, slots=True)
class QualificationHardwareIdentity:
    backend_id: str
    device_id: str | None
    device_alias: str | None
    device_model: str | None
    device_serial_number: str | None
    module_id: str | None
    module_model: str | None
    module_serial_number: str | None
    ordered_physical_channels: tuple[str, ...]
    driver_version: str | None


@dataclass(frozen=True, slots=True)
class QualificationFingerprintInput:
    plan_hash: str
    hardware_profile_hash: str
    hardware_profile_id: str
    hardware_profile_revision: int
    experiment_config_hash: str
    acquisition_rate_hz: float
    acquisition_duration_seconds: float
    samples_per_read: int
    recording_queue_capacity_chunks: int
    storage_location: str
    ordered_logical_channels: tuple[str, ...]
    ordered_physical_channels: tuple[str, ...]
    requested_input_ranges_v: tuple[tuple[float, float], ...]
    sensor_identities: tuple[str, ...]
    calibration_identities: tuple[str, ...]
    conditioning_identities: tuple[str, ...]
    hardware_identity: QualificationHardwareIdentity

    @property
    def fingerprint(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class QualificationOverride:
    reason_code: str
    operator: str
    rationale: str
    acknowledged_at: datetime
    disposition: OverrideDisposition = OverrideDisposition.ACKNOWLEDGED

    def __post_init__(self) -> None:
        _require_reason_code(self.reason_code)
        _require_text(self.operator, "operator")
        _require_text(self.rationale, "rationale")
        _require_aware(self.acknowledged_at, "acknowledged_at")


@dataclass(frozen=True, slots=True)
class QualificationReport:
    schema_version: int
    report_id: str
    lifecycle: QualificationLifecycle
    created_at: datetime
    updated_at: datetime
    operator: str
    application_version: str
    operating_system: str
    plan_id: str
    plan_hash: str
    hardware_profile_id: str
    hardware_profile_revision: int
    hardware_profile_hash: str
    experiment_config_hash: str
    qualification_fingerprint: str
    fingerprint_input: QualificationFingerprintInput
    hardware_identity: QualificationHardwareIdentity
    results: tuple[QualificationTestResult, ...]
    overall_status: QualificationStatus
    blocking_reason_codes: tuple[str, ...]
    warning_reason_codes: tuple[str, ...]
    skipped_test_ids: tuple[str, ...]
    recommended_actions: tuple[str, ...]
    sample_counts: QualificationSampleCounts
    telemetry: QualificationTelemetry
    evidence: tuple[EvidenceLink, ...]
    session_relative_path: str
    override: QualificationOverride | None = None
    recovered_from_checkpoint: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("qualification report schema_version must be 1")
        _require_text(self.report_id, "report_id")
        _require_aware(self.created_at, "created_at")
        _require_aware(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        for name in (
            "plan_hash",
            "hardware_profile_hash",
            "experiment_config_hash",
            "qualification_fingerprint",
        ):
            _require_sha256(getattr(self, name), name)
        if self.qualification_fingerprint != self.fingerprint_input.fingerprint:
            raise ValueError("qualification_fingerprint does not match fingerprint_input")
        _require_unique(tuple(result.test_id for result in self.results), "result test IDs")

    @property
    def report_hash(self) -> str:
        return canonical_sha256_excluding(self, "updated_at")


@dataclass(frozen=True, slots=True)
class EligibilityReason:
    code: str
    blocking: bool
    message: str
    test_id: str | None = None

    def __post_init__(self) -> None:
        _require_reason_code(self.code)
        _require_text(self.message, "eligibility reason message")


@dataclass(frozen=True, slots=True)
class QualificationEligibility:
    eligible: bool
    evaluated_at: datetime
    report_id: str
    report_status: QualificationStatus
    fingerprint_matches: bool
    age_seconds: float
    reasons: tuple[EligibilityReason, ...]
    override_applied: bool

    def __post_init__(self) -> None:
        _require_aware(self.evaluated_at, "evaluated_at")
        if self.age_seconds < 0:
            raise ValueError("age_seconds must be non-negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "eligible": self.eligible,
            "evaluated_at": self.evaluated_at.isoformat(),
            "report_id": self.report_id,
            "report_status": self.report_status.value,
            "fingerprint_matches": self.fingerprint_matches,
            "age_seconds": self.age_seconds,
            "reasons": [
                {
                    "code": reason.code,
                    "blocking": reason.blocking,
                    "message": reason.message,
                    "test_id": reason.test_id,
                }
                for reason in self.reasons
            ],
            "override_applied": self.override_applied,
        }


def evaluate_limit(
    observed: float | bool | str,
    limit: QualificationLimit,
) -> MetricEvaluation:
    """Evaluate one observed value at exact declared boundaries."""
    if limit.value is None:
        return MetricEvaluation.UNEVALUATED
    passed = _compare(observed, limit.comparison, limit.value)
    if passed:
        return MetricEvaluation.WITHIN_LIMIT
    if limit.consequence is QualificationStatus.FAIL:
        return MetricEvaluation.OUTSIDE_FAILURE_LIMIT
    return MetricEvaluation.OUTSIDE_WARNING_LIMIT


def aggregate_qualification_status(
    plan: QualificationPlan,
    results: tuple[QualificationTestResult, ...],
) -> QualificationStatus:
    """Apply M08 blocking precedence independent of presentation order."""
    by_id = {result.test_id: result for result in results}
    definitions = {definition.test_id: definition for definition in plan.tests}
    for definition in plan.tests:
        result = by_id.get(definition.test_id)
        if definition.required and definition.blocking and result is not None:
            if result.status is QualificationStatus.FAIL:
                return QualificationStatus.FAIL
    for definition in plan.tests:
        result = by_id.get(definition.test_id)
        if (
            definition.required
            and definition.blocking
            and (result is None or result.status is QualificationStatus.NOT_TESTED)
        ):
            return QualificationStatus.NOT_TESTED
    for result in results:
        resolved_definition = definitions.get(result.test_id)
        if result.status is QualificationStatus.FAIL and result.blocking:
            return QualificationStatus.FAIL
        if result.status is QualificationStatus.WARNING:
            return QualificationStatus.WARNING
        if result.assumptions:
            return QualificationStatus.WARNING
        if (
            resolved_definition is not None
            and resolved_definition.required
            and result.status is not QualificationStatus.PASS
        ):
            return QualificationStatus.WARNING
    return QualificationStatus.PASS


def check_qualification_eligibility(
    report: QualificationReport,
    *,
    current_fingerprint: str | None,
    evaluated_at: datetime,
    maximum_age_seconds: float | None,
    plan: QualificationPlan,
) -> QualificationEligibility:
    """Return start eligibility; only plan-declared advisory warnings can be acknowledged."""
    _require_aware(evaluated_at, "evaluated_at")
    age = max(0.0, (evaluated_at - report.updated_at).total_seconds())
    fingerprint_matches = current_fingerprint is not None and (
        current_fingerprint == report.qualification_fingerprint
    )
    reasons: list[EligibilityReason] = []
    if not fingerprint_matches:
        reasons.append(
            EligibilityReason(
                code="DAQ.QUALIFICATION.REPORT_STALE",
                blocking=True,
                message="The report does not match the current hardware and acquisition identity.",
            )
        )
    if maximum_age_seconds is not None and age > maximum_age_seconds:
        reasons.append(
            EligibilityReason(
                code="DAQ.QUALIFICATION.REPORT_STALE",
                blocking=True,
                message="The report is older than the plan permits.",
            )
        )
    if report.lifecycle not in {QualificationLifecycle.COMPLETED, QualificationLifecycle.RECOVERED}:
        reasons.append(
            EligibilityReason(
                code="DAQ.QUALIFICATION.REQUIRED_TEST_NOT_RUN",
                blocking=True,
                message="The qualification report is not finalized.",
            )
        )
    if report.overall_status in {QualificationStatus.FAIL, QualificationStatus.NOT_TESTED}:
        reasons.extend(
            EligibilityReason(
                code=code,
                blocking=True,
                message="A blocking qualification condition prevents acquisition start.",
            )
            for code in (
                report.blocking_reason_codes or ("DAQ.QUALIFICATION.REQUIRED_TEST_NOT_RUN",)
            )
        )

    results_by_id = {result.test_id: result for result in report.results}
    for definition in plan.tests:
        if not definition.required or not definition.blocking:
            continue
        result = results_by_id.get(definition.test_id)
        if result is None or result.status is QualificationStatus.NOT_TESTED:
            reasons.append(
                EligibilityReason(
                    code="DAQ.QUALIFICATION.REQUIRED_TEST_NOT_RUN",
                    blocking=True,
                    message=f"Required qualification test {definition.test_id} was not passed.",
                    test_id=definition.test_id,
                )
            )
        elif result.status is QualificationStatus.FAIL:
            reasons.extend(
                EligibilityReason(
                    code=code,
                    blocking=True,
                    message=f"Required qualification test {definition.test_id} failed.",
                    test_id=definition.test_id,
                )
                for code in (result.reason_codes or ("DAQ.QUALIFICATION.QUALIFICATION_FAILED",))
            )

    advisory_codes = {
        code
        for definition in plan.tests
        for code in definition.advisory_reason_codes
        if definition.classification is QualificationClassification.ADVISORY
    }
    override_applied = False
    for code in report.warning_reason_codes:
        acknowledged = (
            report.override is not None
            and report.override.reason_code == code
            and code in advisory_codes
        )
        override_applied = override_applied or acknowledged
        if not acknowledged:
            reasons.append(
                EligibilityReason(
                    code=code,
                    blocking=False,
                    message="Qualification completed with an advisory finding.",
                )
            )
    eligible = (
        fingerprint_matches
        and not any(reason.blocking for reason in reasons)
        and report.overall_status in {QualificationStatus.PASS, QualificationStatus.WARNING}
    )
    return QualificationEligibility(
        eligible=eligible,
        evaluated_at=evaluated_at,
        report_id=report.report_id,
        report_status=report.overall_status,
        fingerprint_matches=fingerprint_matches,
        age_seconds=age,
        reasons=tuple(reasons),
        override_applied=override_applied,
    )


def _compare(
    observed: float | bool | str,
    comparison: LimitComparison,
    expected: float | bool | str | tuple[float, float],
) -> bool:
    if comparison is LimitComparison.BETWEEN_INCLUSIVE:
        if not isinstance(observed, (int, float)) or isinstance(observed, bool):
            return False
        if not isinstance(expected, tuple):
            return False
        return expected[0] <= float(observed) <= expected[1]
    if comparison in {
        LimitComparison.LESS_THAN,
        LimitComparison.LESS_THAN_OR_EQUAL,
        LimitComparison.GREATER_THAN,
        LimitComparison.GREATER_THAN_OR_EQUAL,
    }:
        if (
            not isinstance(observed, (int, float))
            or isinstance(observed, bool)
            or not isinstance(expected, (int, float))
            or isinstance(expected, bool)
        ):
            return False
        if comparison is LimitComparison.LESS_THAN:
            return observed < expected
        if comparison is LimitComparison.LESS_THAN_OR_EQUAL:
            return observed <= expected
        if comparison is LimitComparison.GREATER_THAN:
            return observed > expected
        return observed >= expected
    if comparison is LimitComparison.EQUAL:
        return observed == expected
    return observed != expected


def _require_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must not be empty")


def _require_unique(values: tuple[str, ...], field: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"{field} must be unique")


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _require_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")


def _require_reason_code(code: str) -> None:
    if not code.startswith("DAQ.QUALIFICATION.") or code.count(".") != 2:
        raise ValueError("qualification reason codes must use DAQ.QUALIFICATION.*")
