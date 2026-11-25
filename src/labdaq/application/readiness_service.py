"""Backend-owned DAQ-M09 readiness and acquisition-launch boundary."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from labdaq.application.configuration_workspace import FieldIssue
from labdaq.application.qualification_service import (
    CheckQualificationEligibility,
    build_qualification_fingerprint_input,
    build_qualification_hardware_identity,
)
from labdaq.domain.serialization import canonical_sha256
from labdaq.errors import ApplicationStateError, ErrorSeverity

if TYPE_CHECKING:
    from pathlib import Path

    from labdaq.application.configuration_workspace import ConfigurationDraftSnapshot
    from labdaq.application.destination_service import SessionDestinationPreview
    from labdaq.domain.models import ExperimentConfig, HardwareProfile
    from labdaq.domain.qualification import (
        QualificationEligibility,
        QualificationPlan,
        QualificationReport,
    )
    from labdaq.ports.daq import DaqDiscoveryReport, DaqValidationReport
    from labdaq.ports.session import QualificationAttachment, SessionPlan


@dataclass(frozen=True, slots=True)
class ReadinessDecision:
    eligible: bool
    evaluated_at: datetime
    configuration_valid: bool
    hardware_compatible: bool
    destination_valid: bool
    qualification_state: Literal[
        "not-qualified", "current", "warning", "failed", "stale", "interrupted"
    ]
    configuration_fingerprint: str | None
    qualification_report_id: str | None
    qualification_fingerprint: str | None
    issues: tuple[FieldIssue, ...]
    qualification_eligibility: QualificationEligibility | None = None

    @property
    def blocking_reasons(self) -> tuple[FieldIssue, ...]:
        return tuple(
            issue
            for issue in self.issues
            if issue.severity in {ErrorSeverity.ERROR, ErrorSeverity.CRITICAL}
        )

    @property
    def warnings(self) -> tuple[FieldIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is ErrorSeverity.WARNING)


@dataclass(frozen=True, slots=True)
class AcquisitionLaunchIntent:
    profile: HardwareProfile
    experiment: ExperimentConfig
    session_plan: SessionPlan
    qualification_report_path: Path
    qualification_report_id: str
    qualification_fingerprint: str
    eligibility_evaluated_at: datetime
    qualification_attachment: QualificationAttachment | None = None


@dataclass(frozen=True, slots=True)
class RawDiagnosticConfirmation:
    operator: str
    confirmed_at: datetime
    measured_supply_voltage_v: float
    polarity_confirmed: bool
    ai3_connection: Literal["disconnected", "connected-verified"]
    ai3_observed_min_v: float | None = None
    ai3_observed_max_v: float | None = None
    high_impedance_instrument_confirmed: bool = False

    def __post_init__(self) -> None:
        if not self.operator.strip():
            raise ValueError("diagnostic authorization requires an operator")
        if self.confirmed_at.tzinfo is None or self.confirmed_at.utcoffset() is None:
            raise ValueError("diagnostic authorization time must be timezone-aware")
        if not math.isfinite(self.measured_supply_voltage_v) or not (
            2.8 <= self.measured_supply_voltage_v <= 5.5
        ):
            raise ValueError("measured sensor-board supply must be within 2.8 to 5.5 V")
        if not self.polarity_confirmed:
            raise ValueError("sensor-board supply polarity must be confirmed")
        if self.ai3_connection == "disconnected":
            if self.ai3_observed_min_v is not None or self.ai3_observed_max_v is not None:
                raise ValueError("disconnected ai3 must not claim observed voltage bounds")
            return
        if not self.high_impedance_instrument_confirmed:
            raise ValueError("connected ai3 requires a high-impedance instrument confirmation")
        bounds = (self.ai3_observed_min_v, self.ai3_observed_max_v)
        if any(value is None or not math.isfinite(value) for value in bounds):
            raise ValueError("connected ai3 requires finite observed minimum and maximum voltages")
        assert self.ai3_observed_min_v is not None
        assert self.ai3_observed_max_v is not None
        minimum = float(self.ai3_observed_min_v)
        maximum = float(self.ai3_observed_max_v)
        if minimum > maximum or minimum < -8.0 or maximum > 8.0:
            raise ValueError(
                "connected ai3 observed voltage must remain inside the +/-8 V guard band"
            )


@dataclass(frozen=True, slots=True)
class RawDiagnosticAuthorization:
    confirmation: RawDiagnosticConfirmation
    hardware_validation_sha256: str
    selected_device_id: str | None
    selected_module_id: str | None
    ordered_physical_channels: tuple[str, ...]
    workflow: Literal["single-run", "open-acquisition"] = "single-run"
    selected_channel_id: str | None = None
    recorded_channel_ids: tuple[str, ...] = ()
    recorded_channel_units: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(self.hardware_validation_sha256) != 64:
            raise ValueError("diagnostic hardware validation identity must be a SHA-256 digest")
        if self.workflow == "open-acquisition":
            channel_ids = self.recorded_channel_ids
            if not channel_ids and self.selected_channel_id is not None:
                channel_ids = (self.selected_channel_id,)
                object.__setattr__(self, "recorded_channel_ids", channel_ids)
            if not channel_ids or len(channel_ids) != len(set(channel_ids)):
                raise ValueError("open acquisition authorization requires unique recorded channels")
            if self.selected_channel_id != channel_ids[0]:
                raise ValueError("open acquisition selected channel must be first")
            channel_units = self.recorded_channel_units
            if not channel_units:
                channel_units = ("V",) * len(channel_ids)
                object.__setattr__(self, "recorded_channel_units", channel_units)
            if len(channel_units) != len(channel_ids) or any(not unit for unit in channel_units):
                raise ValueError("open acquisition authorization channels and units must align")
        elif (
            self.selected_channel_id is not None
            or self.recorded_channel_ids
            or self.recorded_channel_units
        ):
            raise ValueError(
                "single-run diagnostic authorization does not select recorded channels"
            )

    def to_dict(self) -> dict[str, object]:
        confirmation = self.confirmation
        return {
            "authorization_scope": "raw-voltage-diagnostic-only",
            "operator": confirmation.operator,
            "authorized_at": confirmation.confirmed_at.isoformat(),
            "measured_supply_voltage_v": confirmation.measured_supply_voltage_v,
            "polarity_confirmed": confirmation.polarity_confirmed,
            "ai3_connection": confirmation.ai3_connection,
            "ai3_observed_min_v": confirmation.ai3_observed_min_v,
            "ai3_observed_max_v": confirmation.ai3_observed_max_v,
            "high_impedance_instrument_confirmed": (
                confirmation.high_impedance_instrument_confirmed
            ),
            "hardware_validation_sha256": self.hardware_validation_sha256,
            "selected_device_id": self.selected_device_id,
            "selected_module_id": self.selected_module_id,
            "ordered_physical_channels": list(self.ordered_physical_channels),
            "workflow": self.workflow,
            "selected_channel_id": self.selected_channel_id,
            "recorded_channel_ids": list(self.recorded_channel_ids),
            "recorded_channel_units": list(self.recorded_channel_units),
            "warnings": [
                "RAW VOLTAGE DIAGNOSTIC ONLY; NO CALIBRATED MEASUREMENT RELEASE.",
                "ai3 is UNPOWERED / UNQUALIFIED / NOT CHARGE.",
            ],
        }


@dataclass(frozen=True, slots=True)
class DiagnosticReadinessDecision:
    eligible: bool
    evaluated_at: datetime
    issues: tuple[FieldIssue, ...]

    @property
    def blocking_reasons(self) -> tuple[FieldIssue, ...]:
        return tuple(
            issue
            for issue in self.issues
            if issue.severity in {ErrorSeverity.ERROR, ErrorSeverity.CRITICAL}
        )


@dataclass(frozen=True, slots=True)
class RawDiagnosticLaunchIntent:
    profile: HardwareProfile
    experiment: ExperimentConfig
    session_plan: SessionPlan
    authorization: RawDiagnosticAuthorization
    eligibility_evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class FunctionalPreviewLaunchIntent:
    """Authorize an explicitly unqualified commissioning run after fresh validation."""

    profile: HardwareProfile
    experiment: ExperimentConfig
    session_plan: SessionPlan
    eligibility_evaluated_at: datetime

    def __post_init__(self) -> None:
        if self.experiment.experiment_mode.value != "accelerometer_commissioning":
            raise ValueError("functional preview intent requires commissioning mode")


@dataclass(frozen=True, slots=True)
class OpenAcquisitionIntent:
    profile: HardwareProfile
    experiment: ExperimentConfig
    selected_channel_id: str
    eligibility_evaluated_at: datetime
    authorization: RawDiagnosticAuthorization | None = None
    recorded_channel_ids: tuple[str, ...] = ()
    recorded_channel_units: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.authorization is not None and self.authorization.workflow != "open-acquisition":
            raise ValueError("open acquisition requires open-acquisition authorization")
        channel_ids = self.recorded_channel_ids
        if not channel_ids:
            channel_ids = (self.selected_channel_id,)
            object.__setattr__(self, "recorded_channel_ids", channel_ids)
        configured = self.experiment.acquisition.channel_ids
        if (
            not channel_ids
            or len(channel_ids) != len(set(channel_ids))
            or tuple(channel_id for channel_id in configured if channel_id in channel_ids)
            != channel_ids
        ):
            raise ValueError("recorded monitor channels must be an ordered configured subset")
        channel_units = self.recorded_channel_units
        if not channel_units:
            units_by_id = {
                channel.channel_id: channel.raw_unit.value for channel in self.experiment.channels
            }
            channel_units = tuple(units_by_id[channel_id] for channel_id in channel_ids)
            object.__setattr__(self, "recorded_channel_units", channel_units)
        if self.authorization is not None:
            if self.authorization.selected_channel_id != self.selected_channel_id:
                raise ValueError("open acquisition primary channel must match its authorization")
            if self.authorization.recorded_channel_ids != channel_ids:
                raise ValueError("open acquisition recorded channels must match its authorization")
            if self.authorization.recorded_channel_units != channel_units:
                raise ValueError("open acquisition recorded channel units must match authorization")
        if len(channel_units) != len(channel_ids) or any(not unit for unit in channel_units):
            raise ValueError("recorded monitor channel identities and units must align")
        if self.selected_channel_id != channel_ids[0]:
            raise ValueError("open acquisition primary channel must be first")


class StartReadinessService:
    def evaluate_open_acquisition(
        self,
        *,
        draft: ConfigurationDraftSnapshot,
        discovery: DaqDiscoveryReport | None,
        hardware_validation: DaqValidationReport | None,
        live_available: bool,
        evaluated_at: datetime | None = None,
    ) -> DiagnosticReadinessDecision:
        """Validate the hardware path without requiring a recording destination or evidence."""
        issues = list(draft.issues)
        if discovery is None or hardware_validation is None:
            issues.append(
                FieldIssue(
                    "DAQ.HARDWARE.DISCOVERY_REQUIRED",
                    "hardware.profile_id",
                    ErrorSeverity.ERROR,
                    "Current hardware discovery and validation are required.",
                    "Refresh hardware discovery after the final configuration edit.",
                )
            )
        elif not hardware_validation.accepted:
            issues.extend(
                FieldIssue(
                    issue.code,
                    issue.field_path,
                    ErrorSeverity.ERROR,
                    issue.message,
                    issue.correction,
                )
                for issue in hardware_validation.errors
            )
        if not live_available:
            issues.append(
                FieldIssue(
                    "DAQ.DEPENDENCY.UNAVAILABLE",
                    "acquisition.backend",
                    ErrorSeverity.ERROR,
                    "Live acquisition is unavailable.",
                    "Install the required dependencies and verify the selected backend.",
                )
            )
        deduplicated = _deduplicate_issues(issues)
        return DiagnosticReadinessDecision(
            eligible=draft.normalized is not None
            and not any(
                issue.severity in {ErrorSeverity.ERROR, ErrorSeverity.CRITICAL}
                for issue in deduplicated
            ),
            evaluated_at=evaluated_at or datetime.now(UTC),
            issues=deduplicated,
        )

    def evaluate_raw_diagnostic(
        self,
        *,
        draft: ConfigurationDraftSnapshot,
        destination: SessionDestinationPreview | None,
        discovery: DaqDiscoveryReport | None,
        hardware_validation: DaqValidationReport | None,
        live_available: bool,
        evaluated_at: datetime | None = None,
    ) -> DiagnosticReadinessDecision:
        now = evaluated_at or datetime.now(UTC)
        issues = list(draft.issues)
        experiment = draft.normalized
        if experiment is None or experiment.experiment_mode.value != "accelerometer_commissioning":
            issues.append(
                FieldIssue(
                    "DAQ.DIAGNOSTIC.MODE_REQUIRED",
                    "experiment_mode",
                    ErrorSeverity.ERROR,
                    "Raw diagnostic acquisition is available only in commissioning mode.",
                    "Launch the explicit accelerometer commissioning GUI.",
                )
            )
        if destination is None:
            issues.append(
                FieldIssue(
                    "DAQ.STORAGE.DESTINATION_NOT_PREVIEWED",
                    "storage.parent_directory",
                    ErrorSeverity.ERROR,
                    "The session destination has not been validated.",
                    "Choose an existing writable session parent directory.",
                )
            )
        else:
            issues.extend(destination.issues)
        if discovery is None or hardware_validation is None:
            issues.append(
                FieldIssue(
                    "DAQ.HARDWARE.DISCOVERY_REQUIRED",
                    "hardware.profile_id",
                    ErrorSeverity.ERROR,
                    "Current hardware discovery and validation are required.",
                    "Refresh hardware discovery after the final configuration edit.",
                )
            )
        elif not hardware_validation.accepted:
            issues.extend(
                FieldIssue(
                    issue.code,
                    issue.field_path,
                    ErrorSeverity.ERROR,
                    issue.message,
                    issue.correction,
                )
                for issue in hardware_validation.errors
            )
        if not live_available:
            issues.append(
                FieldIssue(
                    "DAQ.DEPENDENCY.UNAVAILABLE",
                    "acquisition.backend",
                    ErrorSeverity.ERROR,
                    "Live acquisition is unavailable.",
                    'Install LabDAQ with the "full" extra and verify the selected backend.',
                )
            )
        deduplicated = _deduplicate_issues(issues)
        return DiagnosticReadinessDecision(
            eligible=not any(
                issue.severity in {ErrorSeverity.ERROR, ErrorSeverity.CRITICAL}
                for issue in deduplicated
            ),
            evaluated_at=now,
            issues=deduplicated,
        )

    def request_raw_diagnostic_launch(
        self,
        decision: DiagnosticReadinessDecision,
        *,
        profile: HardwareProfile,
        experiment: ExperimentConfig,
        destination: SessionDestinationPreview,
        hardware_validation: DaqValidationReport,
        confirmation: RawDiagnosticConfirmation,
    ) -> RawDiagnosticLaunchIntent:
        if not decision.eligible or not hardware_validation.accepted:
            raise ApplicationStateError(
                "Raw diagnostic launch was refused because diagnostic readiness is false.",
                code="DAQ.APPLICATION.START_INELIGIBLE",
                context={
                    "blocking_reason_codes": [issue.code for issue in decision.blocking_reasons]
                },
                remediation="Resolve every raw-diagnostic blocker and refresh hardware.",
            )
        authorization = RawDiagnosticAuthorization(
            confirmation=confirmation,
            hardware_validation_sha256=canonical_sha256(hardware_validation),
            selected_device_id=hardware_validation.selected_device_id,
            selected_module_id=hardware_validation.selected_module_id,
            ordered_physical_channels=hardware_validation.ordered_physical_channels,
        )
        return RawDiagnosticLaunchIntent(
            profile=profile,
            experiment=experiment,
            session_plan=destination.plan,
            authorization=authorization,
            eligibility_evaluated_at=decision.evaluated_at,
        )

    def request_functional_preview_launch(
        self,
        decision: DiagnosticReadinessDecision,
        *,
        profile: HardwareProfile,
        experiment: ExperimentConfig,
        destination: SessionDestinationPreview,
        hardware_validation: DaqValidationReport,
    ) -> FunctionalPreviewLaunchIntent:
        """Permit commissioning acquisition without creating qualification evidence."""
        if not decision.eligible or not hardware_validation.accepted:
            raise ApplicationStateError(
                "Functional preview launch was refused because readiness is false.",
                code="DAQ.APPLICATION.START_INELIGIBLE",
                context={
                    "blocking_reason_codes": [issue.code for issue in decision.blocking_reasons]
                },
                remediation="Resolve every functional-run blocker and refresh hardware.",
            )
        if experiment.experiment_mode.value != "accelerometer_commissioning":
            raise ApplicationStateError(
                "Functional preview launch requires the commissioning configuration.",
                code="DAQ.APPLICATION.START_INELIGIBLE",
                remediation="Use the bundled functional-preview configuration.",
            )
        return FunctionalPreviewLaunchIntent(
            profile=profile,
            experiment=experiment,
            session_plan=destination.plan,
            eligibility_evaluated_at=decision.evaluated_at,
        )

    def request_open_acquisition_launch(
        self,
        decision: DiagnosticReadinessDecision,
        *,
        profile: HardwareProfile,
        experiment: ExperimentConfig,
        hardware_validation: DaqValidationReport,
        confirmation: RawDiagnosticConfirmation | None = None,
        selected_channel_id: str | None = None,
        recorded_channel_ids: tuple[str, ...] | None = None,
    ) -> OpenAcquisitionIntent:
        if not decision.eligible or not hardware_validation.accepted:
            raise ApplicationStateError(
                "Open acquisition was refused because diagnostic readiness is false.",
                code="DAQ.APPLICATION.START_INELIGIBLE",
                context={
                    "blocking_reason_codes": [issue.code for issue in decision.blocking_reasons]
                },
                remediation="Resolve every raw-diagnostic blocker and refresh hardware.",
            )
        channel_ids = recorded_channel_ids or (
            (selected_channel_id,)
            if selected_channel_id is not None
            else experiment.acquisition.channel_ids
        )
        if channel_ids != tuple(
            channel_id
            for channel_id in experiment.acquisition.channel_ids
            if channel_id in channel_ids
        ) or len(channel_ids) != len(set(channel_ids)):
            raise ApplicationStateError(
                "The open-acquisition channel set is not an ordered enabled subset.",
                code="DAQ.APPLICATION.OPEN_CHANNEL_INVALID",
                context={"recorded_channel_ids": list(channel_ids)},
                remediation="Use the enabled configured channel order.",
            )
        selected_channel_id = channel_ids[0]
        units_by_id = {
            channel.channel_id: channel.raw_unit.value for channel in experiment.channels
        }
        channel_units = tuple(units_by_id[channel_id] for channel_id in channel_ids)
        authorization = (
            RawDiagnosticAuthorization(
                confirmation=confirmation,
                hardware_validation_sha256=canonical_sha256(hardware_validation),
                selected_device_id=hardware_validation.selected_device_id,
                selected_module_id=hardware_validation.selected_module_id,
                ordered_physical_channels=hardware_validation.ordered_physical_channels,
                workflow="open-acquisition",
                selected_channel_id=selected_channel_id,
                recorded_channel_ids=channel_ids,
                recorded_channel_units=channel_units,
            )
            if confirmation is not None
            else None
        )
        return OpenAcquisitionIntent(
            profile=profile,
            experiment=experiment,
            selected_channel_id=selected_channel_id,
            authorization=authorization,
            eligibility_evaluated_at=decision.evaluated_at,
            recorded_channel_ids=channel_ids,
            recorded_channel_units=channel_units,
        )

    def evaluate(
        self,
        *,
        profile: HardwareProfile,
        draft: ConfigurationDraftSnapshot,
        destination: SessionDestinationPreview | None,
        discovery: DaqDiscoveryReport | None,
        hardware_validation: DaqValidationReport | None,
        plan: QualificationPlan,
        report: QualificationReport | None,
        evaluated_at: datetime | None = None,
    ) -> ReadinessDecision:
        now = evaluated_at or datetime.now(UTC)
        issues = list(draft.issues)
        configuration_valid = draft.valid
        if destination is None:
            issues.append(
                FieldIssue(
                    "DAQ.STORAGE.DESTINATION_NOT_PREVIEWED",
                    "storage.parent_directory",
                    ErrorSeverity.ERROR,
                    "The session destination has not been validated.",
                    "Choose an existing writable session parent directory.",
                )
            )
            destination_valid = False
        else:
            issues.extend(destination.issues)
            destination_valid = destination.valid

        hardware_compatible = False
        if discovery is None or hardware_validation is None:
            issues.append(
                FieldIssue(
                    "DAQ.HARDWARE.DISCOVERY_REQUIRED",
                    "hardware.profile_id",
                    ErrorSeverity.ERROR,
                    "Current hardware discovery and validation are required.",
                    "Refresh hardware discovery for this configuration.",
                )
            )
        else:
            hardware_compatible = hardware_validation.accepted
            issues.extend(
                FieldIssue(
                    issue.code,
                    issue.field_path,
                    ErrorSeverity.ERROR,
                    issue.message,
                    issue.correction,
                )
                for issue in hardware_validation.errors
            )
            issues.extend(
                FieldIssue(
                    issue.code,
                    issue.field_path,
                    ErrorSeverity.WARNING,
                    issue.message,
                    issue.correction,
                )
                for issue in hardware_validation.warnings
            )

        current_fingerprint: str | None = None
        qualification_eligibility: QualificationEligibility | None = None
        qualification_state: Literal[
            "not-qualified", "current", "warning", "failed", "stale", "interrupted"
        ] = "not-qualified"
        if report is None:
            issues.append(
                FieldIssue(
                    "DAQ.QUALIFICATION.REPORT_MISSING",
                    "qualification.current",
                    ErrorSeverity.ERROR,
                    "No qualification report is selected for this configuration.",
                    "Run hardware qualification or load matching current evidence.",
                )
            )
        elif (
            draft.normalized is not None
            and discovery is not None
            and hardware_validation is not None
        ):
            identity = build_qualification_hardware_identity(discovery, hardware_validation)
            current_fingerprint = build_qualification_fingerprint_input(
                plan,
                profile,
                draft.normalized,
                identity,
            ).fingerprint
            qualification_eligibility = CheckQualificationEligibility().check(
                report,
                plan,
                current_fingerprint=current_fingerprint,
                evaluated_at=now,
            )
            if not qualification_eligibility.fingerprint_matches:
                qualification_state = "stale"
            elif report.lifecycle.value in {"cancelled", "faulted", "in-progress"}:
                qualification_state = "interrupted"
            elif report.overall_status.value == "fail":
                qualification_state = "failed"
            elif qualification_eligibility.eligible:
                qualification_state = (
                    "warning" if report.overall_status.value == "warning" else "current"
                )
            else:
                qualification_state = "failed"
            issues.extend(
                FieldIssue(
                    reason.code,
                    "qualification.current",
                    ErrorSeverity.ERROR if reason.blocking else ErrorSeverity.WARNING,
                    reason.message,
                    "Rerun qualification for the current hardware and configuration."
                    if not qualification_eligibility.fingerprint_matches
                    else "Review the qualification report and required test evidence.",
                )
                for reason in qualification_eligibility.reasons
            )
        eligible = (
            configuration_valid
            and destination_valid
            and hardware_compatible
            and qualification_eligibility is not None
            and qualification_eligibility.eligible
            and not any(
                issue.severity in {ErrorSeverity.ERROR, ErrorSeverity.CRITICAL} for issue in issues
            )
        )
        return ReadinessDecision(
            eligible=eligible,
            evaluated_at=now,
            configuration_valid=configuration_valid,
            hardware_compatible=hardware_compatible,
            destination_valid=destination_valid,
            qualification_state=qualification_state,
            configuration_fingerprint=current_fingerprint,
            qualification_report_id=report.report_id if report else None,
            qualification_fingerprint=(report.qualification_fingerprint if report else None),
            issues=_deduplicate_issues(issues),
            qualification_eligibility=qualification_eligibility,
        )

    def request_launch(
        self,
        decision: ReadinessDecision,
        *,
        profile: HardwareProfile,
        experiment: ExperimentConfig,
        destination: SessionDestinationPreview,
        report_path: Path,
    ) -> AcquisitionLaunchIntent:
        if not decision.eligible or decision.qualification_report_id is None:
            raise ApplicationStateError(
                "Acquisition launch was refused because readiness is false.",
                code="DAQ.APPLICATION.START_INELIGIBLE",
                context={
                    "blocking_reason_codes": [issue.code for issue in decision.blocking_reasons]
                },
                remediation="Resolve every blocking readiness issue and qualify again.",
            )
        if decision.qualification_fingerprint is None:
            raise ApplicationStateError(
                "Acquisition launch lacks a qualification fingerprint.",
                code="DAQ.APPLICATION.START_INELIGIBLE",
                remediation="Rerun qualification for the current configuration.",
            )
        return AcquisitionLaunchIntent(
            profile=profile,
            experiment=experiment,
            session_plan=destination.plan,
            qualification_report_path=report_path,
            qualification_report_id=decision.qualification_report_id,
            qualification_fingerprint=decision.qualification_fingerprint,
            eligibility_evaluated_at=decision.evaluated_at,
        )


def _deduplicate_issues(issues: list[FieldIssue]) -> tuple[FieldIssue, ...]:
    unique: dict[tuple[str, str, str], FieldIssue] = {}
    for issue in issues:
        unique[(issue.code, issue.field_path, issue.message)] = issue
    return tuple(unique.values())
