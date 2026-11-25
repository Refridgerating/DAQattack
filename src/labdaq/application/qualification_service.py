"""Headless DAQ-M08 hardware qualification orchestration and decisions."""

from __future__ import annotations

import hashlib
import math
import platform
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from threading import Event
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from labdaq.domain.enums import ChannelRole, ExperimentMode, SamplingMode
from labdaq.domain.qualification import (
    EvidenceLink,
    MetricEvaluation,
    MetricValueKind,
    QualificationClassification,
    QualificationEligibility,
    QualificationExecutionMode,
    QualificationFingerprintInput,
    QualificationHardwareIdentity,
    QualificationLifecycle,
    QualificationMetric,
    QualificationPlan,
    QualificationReport,
    QualificationSampleCounts,
    QualificationStatus,
    QualificationTelemetry,
    QualificationTestDefinition,
    QualificationTestResult,
    SignalStage,
    aggregate_qualification_status,
    check_qualification_eligibility,
    evaluate_limit,
)
from labdaq.domain.serialization import canonical_sha256, to_canonical_primitive
from labdaq.domain.timing import rounded_sample_count
from labdaq.errors import QualificationError
from labdaq.ports.qualification import QualificationProgress
from labdaq.ports.session import (
    IntegrityStatus,
    QualificationAttachment,
    RecordedSessionCompletion,
    SessionCompletionReason,
    SessionType,
    VerificationLevel,
)
from labdaq.version import __version__

if TYPE_CHECKING:
    from pathlib import Path

    import numpy as np

    from labdaq.application.session_service import RecordingSessionService
    from labdaq.domain.models import ExperimentConfig, HardwareProfile, ProcessingRecipeEnvelope
    from labdaq.domain.qualification import OperatorConfirmation
    from labdaq.ports.daq import (
        DaqDiscoveryReport,
        DaqPort,
        DaqSelfTestResult,
        DaqValidationReport,
    )
    from labdaq.ports.qualification import (
        OperatorPromptPort,
        QualificationAnalysisPort,
        QualificationArtifactPort,
        QualificationProgressPort,
        QualificationReportRepositoryPort,
    )
    from labdaq.ports.session import (
        RecordingStatus,
        ReplaySourcePort,
        SessionIntegrityReport,
        SessionReaderPort,
    )


@dataclass(frozen=True, slots=True)
class HardwareCompatibility:
    accepted: bool
    status: QualificationStatus
    reason_codes: tuple[str, ...]
    summary: str
    assumptions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QualificationRunOutcome:
    report: QualificationReport
    report_path: Path
    eligibility: QualificationEligibility
    session: RecordedSessionCompletion


class BuildQualificationPlan:
    def build(
        self,
        template: QualificationPlan,
        profile: HardwareProfile,
        experiment: ExperimentConfig,
        *,
        throughput_duration_seconds: float | None = None,
    ) -> QualificationPlan:
        bound = replace(
            template,
            hardware_profile_id=profile.hardware_profile_id,
            hardware_profile_revision=profile.profile_revision,
            experiment_config_hash=qualification_experiment_hash(experiment),
            acquisition_rate_hz=experiment.acquisition.sample_rate_hz,
        )
        if experiment.experiment_mode is ExperimentMode.ACCELEROMETER_COMMISSIONING:
            allowed = {
                "discovery-identity",
                "capability-configuration",
                "self-test-lifecycle",
                "powered-idle",
                "accelerometer-bias",
                "axis-response-x",
                "axis-response-y",
                "axis-response-z",
            }
            bound = replace(
                bound, tests=tuple(item for item in bound.tests if item.test_id in allowed)
            )
        if throughput_duration_seconds is not None:
            bound = bound.with_throughput_duration(throughput_duration_seconds)
        _validate_bound_plan(bound)
        return bound


def _validate_bound_plan(plan: QualificationPlan) -> None:
    """Reject plans the implemented runner cannot execute before hardware is touched."""
    test_ids = {item.test_id for item in plan.tests}
    required_core = {
        "discovery-identity",
        "capability-configuration",
    }
    missing_core = tuple(sorted(required_core - test_ids))
    missing_prerequisites = tuple(
        sorted(
            (definition.test_id, prerequisite)
            for definition in plan.tests
            for prerequisite in definition.prerequisites
            if prerequisite not in test_ids
        )
    )
    if not missing_core and not missing_prerequisites:
        return
    raise QualificationError(
        "The qualification plan is not executable by the current runner.",
        code="DAQ.QUALIFICATION.PLAN_INVALID",
        context={
            "plan_id": plan.plan_id,
            "missing_core_test_ids": missing_core,
            "missing_prerequisites": missing_prerequisites,
        },
        remediation=(
            "Restore the required discovery and capability tests and every declared prerequisite."
        ),
    )


class EvaluateHardwareCompatibility:
    """Apply M08-only scientific completeness rules after the DAQ contract validates."""

    def evaluate(
        self,
        validation: DaqValidationReport,
        experiment: ExperimentConfig,
        *,
        backend_id: str,
    ) -> HardwareCompatibility:
        reasons: list[str] = []
        assumptions: list[str] = []
        if not validation.accepted:
            identity_terms = {"model", "serial", "device", "module", "channel"}
            if any(
                any(term in issue.field_path.casefold() for term in identity_terms)
                for issue in validation.errors
            ):
                reasons.append("DAQ.QUALIFICATION.HARDWARE_IDENTITY_MISMATCH")
            reasons.append("DAQ.QUALIFICATION.HARDWARE_CAPABILITY_MISMATCH")
        enabled = tuple(channel for channel in experiment.channels if channel.enabled)
        physical = tuple(
            (channel.physical_channel.module_id, channel.physical_channel.channel_index)
            for channel in enabled
        )
        count_valid = (
            len(enabled) in {3, 4}
            if experiment.experiment_mode is ExperimentMode.ACCELEROMETER_COMMISSIONING
            else len(enabled) == 4
        )
        if not count_valid or len(set(physical)) != len(physical):
            reasons.append("DAQ.QUALIFICATION.HARDWARE_IDENTITY_MISMATCH")

        chains = {chain.conditioning_chain_id: chain for chain in experiment.conditioning_chains}
        charge_channels = tuple(
            channel for channel in enabled if channel.channel_role is ChannelRole.CONDITIONED_CHARGE
        )
        if (
            experiment.experiment_mode is ExperimentMode.ACCELEROMETER_COMMISSIONING
            and charge_channels
        ):
            reasons.append("DAQ.QUALIFICATION.CHARGE_CONDITIONING_INCOMPATIBLE")
        diagnostic_channels = tuple(
            channel
            for channel in enabled
            if channel.channel_role is ChannelRole.DIAGNOSTIC_RAW_VOLTAGE
        )
        if diagnostic_channels:
            assumptions.append(
                "ai3 is UNPOWERED / UNQUALIFIED / NOT CHARGE; commissioning eligibility applies only to X/Y/Z."
            )
        for channel in charge_channels:
            chain = chains.get(channel.conditioning_chain_id or "")
            if chain is None:
                reasons.append("DAQ.QUALIFICATION.CHARGE_CONDITIONING_INCOMPATIBLE")
                continue
            text = " ".join((chain.description, *chain.components)).casefold()
            claims_422e03 = "422e03" in text or (
                chain.charge_converter is not None
                and "422e03" in chain.charge_converter.model.casefold()
            )
            conditioning = chain.ni_terminal_conditioning
            if claims_422e03 and (conditioning is None or not conditioning.bias_removal_documented):
                reasons.append("DAQ.QUALIFICATION.CHARGE_CONDITIONING_INCOMPATIBLE")
            elif chain.charge_converter is None or conditioning is None:
                if (
                    backend_id == "simulated"
                    and "synthetic" in chain.calibration_reference.casefold()
                ):
                    assumptions.append(
                        "Synthetic charge conditioning is test-fixture evidence, not physical wiring evidence."
                    )
                else:
                    reasons.append("DAQ.QUALIFICATION.REQUIRED_TEST_NOT_RUN")

        unique_reasons = tuple(dict.fromkeys(reasons))
        if any(code != "DAQ.QUALIFICATION.REQUIRED_TEST_NOT_RUN" for code in unique_reasons):
            status = QualificationStatus.FAIL
        elif unique_reasons:
            status = QualificationStatus.NOT_TESTED
        elif assumptions:
            status = QualificationStatus.WARNING
        else:
            status = QualificationStatus.PASS
        return HardwareCompatibility(
            accepted=status in {QualificationStatus.PASS, QualificationStatus.WARNING},
            status=status,
            reason_codes=unique_reasons,
            summary=(
                "Hardware and configuration are compatible with the qualification plan."
                if status in {QualificationStatus.PASS, QualificationStatus.WARNING}
                else "Hardware or conditioning evidence is incomplete or incompatible."
            ),
            assumptions=tuple(assumptions),
        )


class EvaluateQualificationReport:
    def evaluate(
        self,
        report: QualificationReport,
        plan: QualificationPlan,
        *,
        updated_at: datetime,
        lifecycle: QualificationLifecycle,
    ) -> QualificationReport:
        overall = aggregate_qualification_status(plan, report.results)
        blocking: list[str] = []
        warnings: list[str] = []
        skipped: list[str] = []
        for result in report.results:
            if result.status is QualificationStatus.NOT_TESTED:
                skipped.append(result.test_id)
            if result.blocking and result.status in {
                QualificationStatus.FAIL,
                QualificationStatus.NOT_TESTED,
            }:
                blocking.extend(result.reason_codes)
            elif result.status in {QualificationStatus.WARNING, QualificationStatus.FAIL}:
                warnings.extend(result.reason_codes)
        return replace(
            report,
            lifecycle=lifecycle,
            updated_at=updated_at,
            overall_status=overall,
            blocking_reason_codes=tuple(dict.fromkeys(blocking)),
            warning_reason_codes=tuple(dict.fromkeys(warnings)),
            skipped_test_ids=tuple(skipped),
        )


class CheckQualificationEligibility:
    def check(
        self,
        report: QualificationReport,
        plan: QualificationPlan,
        *,
        current_fingerprint: str | None,
        evaluated_at: datetime,
    ) -> QualificationEligibility:
        return check_qualification_eligibility(
            report,
            current_fingerprint=current_fingerprint,
            evaluated_at=evaluated_at,
            maximum_age_seconds=plan.maximum_report_age_seconds,
            plan=plan,
        )


class LoadQualificationReport:
    def __init__(self, repository: QualificationReportRepositoryPort) -> None:
        self._repository = repository

    def load(self, report_path: Path) -> QualificationReport:
        return self._repository.load(report_path)


class RecoverQualificationReport:
    """Combine the last atomic report checkpoint with M05 prefix recovery."""

    def __init__(
        self,
        repository: QualificationReportRepositoryPort,
        reader: SessionReaderPort,
    ) -> None:
        self._repository = repository
        self._reader = reader

    def recover(self, session_path: Path) -> QualificationReport:
        self._reader.recover_session(session_path)
        report = self._repository.load(session_path / "qualification-report.json")
        plan = self._repository.load_plan(session_path / "qualification-plan.json")
        completed = {result.test_id for result in report.results}
        results = list(report.results)
        for definition in plan.tests:
            if definition.test_id in completed:
                continue
            results.append(
                _simple_result(
                    definition,
                    datetime.now(UTC),
                    QualificationStatus.NOT_TESTED,
                    ("DAQ.QUALIFICATION.REQUIRED_TEST_NOT_RUN",),
                    "Recovery retained completed evidence; this unfinished test was not rerun.",
                )
            )
        recovered = replace(
            report,
            lifecycle=QualificationLifecycle.RECOVERED,
            updated_at=datetime.now(UTC),
            results=tuple(results),
            recovered_from_checkpoint=True,
        )
        recovered = EvaluateQualificationReport().evaluate(
            recovered,
            plan,
            updated_at=datetime.now(UTC),
            lifecycle=QualificationLifecycle.RECOVERED,
        )
        self._repository.checkpoint(session_path, recovered)
        return recovered


class AttachQualificationToSession:
    """Build a byte-exact attachment for the next, newly-created experiment session."""

    def build_attachment(
        self,
        report_path: Path,
        report: QualificationReport,
        eligibility: QualificationEligibility,
    ) -> QualificationAttachment:
        payload = report_path.read_bytes()
        return QualificationAttachment(
            report_id=report.report_id,
            report_sha256=hashlib.sha256(payload).hexdigest(),
            qualification_fingerprint=report.qualification_fingerprint,
            overall_status=report.overall_status.value,
            evaluated_at=eligibility.evaluated_at,
            age_seconds=eligibility.age_seconds,
            eligible=eligibility.eligible,
            report_json=payload,
            override_reason_code=(
                report.override.reason_code if report.override is not None else None
            ),
        )


class RunQualification:
    """Run one qualification session through the production M04/M05 stream."""

    def __init__(
        self,
        daq: DaqPort,
        recording: RecordingSessionService,
        reader: SessionReaderPort,
        replay: ReplaySourcePort,
        analysis: QualificationAnalysisPort,
        repository: QualificationReportRepositoryPort,
        prompt: OperatorPromptPort,
        artifacts: QualificationArtifactPort | None = None,
        progress: QualificationProgressPort | None = None,
    ) -> None:
        self._daq = daq
        self._recording = recording
        self._reader = reader
        self._replay = replay
        self._analysis = analysis
        self._repository = repository
        self._prompt = prompt
        self._artifacts = artifacts
        self._progress = progress
        self._cancel_requested = Event()
        self._compatibility = EvaluateHardwareCompatibility()
        self._evaluator = EvaluateQualificationReport()
        self._phase_confirmations: dict[str, tuple[OperatorConfirmation, ...]] = {}

    def close(self) -> None:
        self._recording.close()

    def cancel(self) -> None:
        """Request an orderly stop; the running call finalizes a cancelled report."""
        self._cancel_requested.set()
        try:
            self._recording.request_stop("qualification_cancelled")
        except Exception:
            # Cancellation is intentionally idempotent and may arrive before recording
            # enters a stoppable state. The event is checked immediately after start.
            return

    def run(
        self,
        plan: QualificationPlan,
        profile: HardwareProfile,
        experiment: ExperimentConfig,
        recipe: ProcessingRecipeEnvelope,
    ) -> QualificationRunOutcome:
        created_at = datetime.now(UTC)
        self._publish_progress(
            phase="starting",
            message="Discovering and validating the configured DAQ hardware.",
            plan=plan,
        )
        discovery = self._daq.discover()
        validation = self._daq.validate(profile, experiment)
        identity = build_qualification_hardware_identity(discovery, validation)
        fingerprint_input = build_qualification_fingerprint_input(
            plan,
            profile,
            experiment,
            identity,
        )
        compatibility = self._compatibility.evaluate(
            validation,
            experiment,
            backend_id=identity.backend_id,
        )
        self_test = self._daq.self_test()
        preliminary = (
            _simple_result(
                _definition(plan, "discovery-identity"),
                created_at,
                QualificationStatus.PASS if discovery.devices else QualificationStatus.FAIL,
                () if discovery.devices else ("DAQ.QUALIFICATION.HARDWARE_IDENTITY_MISMATCH",),
                "Live discovery captured the configured hardware identity."
                if discovery.devices
                else "No configured hardware identity was discovered.",
            ),
            _simple_result(
                _definition(plan, "capability-configuration"),
                created_at,
                compatibility.status,
                compatibility.reason_codes,
                compatibility.summary,
                assumptions=compatibility.assumptions,
            ),
            _self_test_result(plan, self_test, created_at),
        )
        relevant_preliminary = tuple(
            result for result in preliminary if any(d.test_id == result.test_id for d in plan.tests)
        )
        capture_duration = _capture_duration(plan)
        guided = identity.backend_id != "simulated" and any(
            item.execution_mode is not QualificationExecutionMode.AUTOMATED for item in plan.tests
        )
        qualified_experiment = _qualification_experiment(
            experiment,
            capture_duration,
            continuous=guided,
        )
        session_plan = self._recording.preview(qualified_experiment.session)
        self._recording.prepare(
            session_plan,
            profile,
            qualified_experiment,
            recipe,
            session_type=SessionType.HARDWARE_QUALIFICATION,
        )
        self._repository.initialize(session_plan.session_path, plan)
        report = _new_report(
            plan,
            profile,
            experiment,
            identity,
            fingerprint_input,
            created_at,
            session_plan.session_path.name,
        )
        for result in relevant_preliminary:
            report = replace(
                report, results=(*report.results, result), updated_at=datetime.now(UTC)
            )
            self._repository.checkpoint(session_plan.session_path, report)
            self._publish_progress(
                phase="preflight",
                message=result.summary,
                plan=plan,
                report=report,
                current_test_id=result.test_id,
                indeterminate=False,
                report_path=session_plan.session_path / "qualification-report.json",
            )

        try:
            self._publish_progress(
                phase="acquiring",
                message="Acquiring qualification evidence.",
                plan=plan,
                report=report,
                report_path=session_plan.session_path / "qualification-report.json",
            )
            self._recording.start()
            if guided:
                phase_ranges = self._run_guided_phases(plan, qualified_experiment)
            else:
                phase_ranges = _phase_ranges(plan)
            if self._cancel_requested.is_set():
                self._recording.request_stop("qualification_cancelled")
            elif guided:
                self._recording.request_stop("qualification_complete")
            session = self._recording.wait_for_terminal(max(capture_duration + 30.0, 120.0))
            recording_status = self._recording.status()
        except BaseException as cause:
            report = self._finish_faulted_report(
                report,
                plan,
                session_plan.session_path,
                cause,
            )
            self._publish_progress(
                phase="faulted",
                message="Qualification faulted; partial evidence was retained.",
                plan=plan,
                report=report,
                report_path=session_plan.session_path / "qualification-report.json",
            )
            self._cancel_requested.clear()
            raise QualificationError(
                "Qualification acquisition failed; a checkpoint was retained.",
                code="DAQ.QUALIFICATION.QUALIFICATION_FAILED",
                context={
                    "report_path": str(session_plan.session_path / "qualification-report.json")
                },
                cause=cause,
            ) from cause

        self._publish_progress(
            phase="verifying",
            message="Verifying the retained qualification session.",
            plan=plan,
            report=report,
            report_path=session_plan.session_path / "qualification-report.json",
        )
        integrity = self._reader.inspect_session(
            session_plan.session_path,
            level=VerificationLevel.FULL,
        )
        cancelled = session.reason is SessionCompletionReason.MANUAL_STOP and (
            self._cancel_requested.is_set() or not guided
        )
        samples = (
            None
            if cancelled
            else _read_bounded_window(
                self._replay,
                session_plan.session_path,
                sample_limit=min(
                    int(plan.powered_idle_duration_seconds * plan.acquisition_rate_hz),
                    int(10.0 * plan.acquisition_rate_hz),
                ),
                chunk_size=(qualified_experiment.acquisition.chunk_config.samples_per_channel),
            )
        )
        post_results = (
            ()
            if cancelled
            else self._data_results(
                plan,
                qualified_experiment,
                identity,
                cast("np.ndarray", samples),
                session,
                integrity,
                recording_status,
                session_plan.session_path,
                phase_ranges,
            )
        )
        artifact_evidence: tuple[EvidenceLink, ...] = ()
        if not cancelled and "powered-idle" in {item.test_id for item in plan.tests}:
            try:
                artifact_bundle = (
                    self._artifacts.generate(
                        session_plan.session_path,
                        ordered_channel_ids=qualified_experiment.acquisition.channel_ids,
                        sample_rate_hz=plan.acquisition_rate_hz,
                        sample_count=cast("np.ndarray", samples).shape[1],
                    )
                    if self._artifacts is not None
                    else None
                )
            except Exception:
                artifact_bundle = None
            artifact_evidence = artifact_bundle.evidence if artifact_bundle else ()
            artifact_warnings = (
                artifact_bundle.warning_reason_codes
                if artifact_bundle
                else ("DAQ.QUALIFICATION.RENDERING_UNAVAILABLE",)
            )
            post_results = tuple(
                replace(
                    result,
                    status=(
                        QualificationStatus.WARNING
                        if artifact_warnings and result.status is QualificationStatus.PASS
                        else result.status
                    ),
                    reason_codes=tuple(dict.fromkeys((*result.reason_codes, *artifact_warnings))),
                    evidence=(*result.evidence, *artifact_evidence),
                )
                if result.test_id == "powered-idle"
                else result
                for result in post_results
            )
        for result in post_results:
            report = replace(
                report, results=(*report.results, result), updated_at=datetime.now(UTC)
            )
            report = replace(
                report,
                sample_counts=_sample_counts(session, integrity, recording_status),
                telemetry=_telemetry(recording_status),
            )
            self._repository.checkpoint(session_plan.session_path, report)
            self._publish_progress(
                phase="evaluating",
                message=result.summary,
                plan=plan,
                report=report,
                current_test_id=result.test_id,
                indeterminate=False,
                report_path=session_plan.session_path / "qualification-report.json",
            )
        report = self._append_unexecuted(plan, report, session_plan.session_path)
        report = replace(
            report,
            evidence=(
                EvidenceLink("raw-container", "raw.h5", None, "retained"),
                EvidenceLink("event-log", "events.jsonl", None, "retained"),
                *artifact_evidence,
            ),
        )
        report = self._evaluator.evaluate(
            report,
            plan,
            updated_at=datetime.now(UTC),
            lifecycle=(
                QualificationLifecycle.CANCELLED if cancelled else QualificationLifecycle.COMPLETED
            ),
        )
        report_path = self._repository.checkpoint(session_plan.session_path, report)
        eligibility = CheckQualificationEligibility().check(
            report,
            plan,
            current_fingerprint=fingerprint_input.fingerprint,
            evaluated_at=datetime.now(UTC),
        )
        self._publish_progress(
            phase="cancelled" if cancelled else "completed",
            message=(
                "Qualification was cancelled; partial evidence was retained."
                if cancelled
                else "Qualification evaluation completed."
            ),
            plan=plan,
            report=report,
            indeterminate=False,
            report_path=report_path,
        )
        self._cancel_requested.clear()
        self._phase_confirmations.clear()
        return QualificationRunOutcome(report, report_path, eligibility, session)

    def _run_guided_phases(
        self,
        plan: QualificationPlan,
        experiment: ExperimentConfig,
    ) -> dict[str, tuple[tuple[int, int], ...]]:
        """Prompt each assisted phase while one continuous recording remains active."""
        phases: dict[str, tuple[tuple[int, int], ...]] = {}
        if plan.powered_idle_duration_seconds > 0:
            time.sleep(plan.powered_idle_duration_seconds)
        for definition in plan.tests:
            if definition.execution_mode is QualificationExecutionMode.AUTOMATED:
                continue
            windows: list[tuple[int, int]] = []
            confirmations: list[OperatorConfirmation] = []
            ids = definition.required_confirmation_ids or (definition.test_id,)
            for confirmation_id in ids:
                if self._cancel_requested.is_set():
                    break
                # Axis response needs a matched baseline and stimulus window;
                # the baseline is captured before asking the operator to apply
                # the manual stimulus.  Fixture-based tests must not be
                # rewired while this continuous task is active (those remain
                # explicitly pending until task-stopped sub-runs are added).
                if definition.test_id.startswith("axis-response-"):
                    baseline_start = (
                        self._recording.status().acquisition.acquired_samples_per_channel
                    )
                    time.sleep(plan.assisted_window_duration_seconds)
                    baseline_stop = (
                        self._recording.status().acquisition.acquired_samples_per_channel
                    )
                    windows.append((baseline_start, max(baseline_stop - baseline_start, 0)))
                confirmation = self._prompt.obtain(
                    confirmation_id,
                    _guided_prompt(definition.test_id, definition.display_name),
                    requested_at=datetime.now(UTC),
                )
                if confirmation is None or not confirmation.confirmed:
                    continue
                confirmations.append(confirmation)
                if plan.assisted_settling_duration_seconds > 0:
                    time.sleep(plan.assisted_settling_duration_seconds)
                start = self._recording.status().acquisition.acquired_samples_per_channel
                time.sleep(plan.assisted_window_duration_seconds)
                stop = self._recording.status().acquisition.acquired_samples_per_channel
                windows.append((start, max(stop - start, 0)))
            self._phase_confirmations[definition.test_id] = tuple(confirmations)
            if windows:
                phases[definition.test_id] = tuple(windows)
        if "sustained-throughput" in {item.test_id for item in plan.tests}:
            time.sleep(plan.throughput_duration_seconds)
        return phases

    def _publish_progress(
        self,
        *,
        phase: str,
        message: str,
        plan: QualificationPlan,
        report: QualificationReport | None = None,
        current_test_id: str | None = None,
        indeterminate: bool = True,
        report_path: Path | None = None,
    ) -> None:
        if self._progress is None:
            return
        completed = tuple(result.test_id for result in report.results) if report else ()
        self._progress.publish(
            QualificationProgress(
                phase=phase,
                message=message,
                current_test_id=current_test_id,
                completed_test_ids=completed,
                total_test_count=len(plan.tests),
                report_path=report_path,
                indeterminate=indeterminate,
            )
        )

    def _data_results(
        self,
        plan: QualificationPlan,
        experiment: ExperimentConfig,
        identity: QualificationHardwareIdentity,
        samples: np.ndarray,
        session: RecordedSessionCompletion,
        integrity: SessionIntegrityReport,
        recording_status: RecordingStatus,
        session_path: Path,
        phase_ranges: dict[str, tuple[tuple[int, int], ...]],
    ) -> tuple[QualificationTestResult, ...]:
        now = datetime.now(UTC)
        results: list[QualificationTestResult] = []
        test_ids = {definition.test_id for definition in plan.tests}
        if "powered-idle" in test_ids:
            results.append(_powered_idle_result(plan, experiment, samples, self._analysis, now))
        if "accelerometer-bias" in test_ids:
            results.append(
                _simple_result(
                    _definition(plan, "accelerometer-bias"),
                    now,
                    QualificationStatus.WARNING,
                    ("DAQ.QUALIFICATION.BIAS_LIMIT_UNRESOLVED",),
                    "Bias metrics were retained, but the lab-specific excitation limit is unresolved.",
                    assumptions=(
                        "A plausible voltage does not verify sensitivity or axis orientation.",
                    ),
                )
            )
        if "charge-conditioning" in test_ids:
            results.append(
                self._assisted_result(
                    plan,
                    "charge-conditioning",
                    identity,
                    experiment,
                    session_path,
                    phase_ranges.get("charge-conditioning", ()),
                )
            )
        for test_id in ("axis-response-x", "axis-response-y", "axis-response-z"):
            if test_id in test_ids:
                results.append(
                    self._assisted_result(
                        plan,
                        test_id,
                        identity,
                        experiment,
                        session_path,
                        phase_ranges.get(test_id, ()),
                    )
                )
        if "cross-channel-alignment" in test_ids:
            results.append(
                self._assisted_result(
                    plan,
                    "cross-channel-alignment",
                    identity,
                    experiment,
                    session_path,
                    phase_ranges.get("cross-channel-alignment", ()),
                )
            )
        if "crosstalk" in test_ids:
            results.append(
                self._assisted_result(
                    plan,
                    "crosstalk",
                    identity,
                    experiment,
                    session_path,
                    phase_ranges.get("crosstalk", ()),
                )
            )
        if "sustained-throughput" in test_ids:
            results.append(
                _throughput_result(
                    plan,
                    session,
                    integrity,
                    recording_status,
                    now,
                )
            )
        return tuple(results)

    def _assisted_result(
        self,
        plan: QualificationPlan,
        test_id: str,
        identity: QualificationHardwareIdentity,
        experiment: ExperimentConfig,
        session_path: Path,
        phase_ranges: tuple[tuple[int, int], ...],
    ) -> QualificationTestResult:
        definition = _definition(plan, test_id)
        now = datetime.now(UTC)
        stored = self._phase_confirmations.get(definition.test_id)
        confirmations = (
            tuple(
                replace(
                    confirmation,
                    sample_index=(
                        phase_ranges[index + 1][0]
                        if definition.test_id.startswith("axis-response-")
                        and index + 1 < len(phase_ranges)
                        else (phase_ranges[index][0] if index < len(phase_ranges) else None)
                    ),
                    sample_count=(
                        phase_ranges[index + 1][1]
                        if definition.test_id.startswith("axis-response-")
                        and index + 1 < len(phase_ranges)
                        else (phase_ranges[index][1] if index < len(phase_ranges) else None)
                    ),
                )
                for index, confirmation in enumerate(stored)
                if getattr(confirmation, "confirmed", False)
            )
            if stored is not None
            else tuple(
                replace(
                    confirmation,
                    sample_index=(phase_ranges[-1][0] if phase_ranges else None),
                    sample_count=(phase_ranges[-1][1] if phase_ranges else None),
                )
                for confirmation_id in definition.required_confirmation_ids
                if (
                    confirmation := self._prompt.obtain(
                        confirmation_id,
                        f"Confirm evidence for {definition.display_name}.",
                        requested_at=now,
                    )
                )
                is not None
                and confirmation.confirmed
            )
        )
        if len(confirmations) != len(definition.required_confirmation_ids):
            return _simple_result(
                definition,
                now,
                QualificationStatus.NOT_TESTED,
                ("DAQ.QUALIFICATION.REQUIRED_TEST_NOT_RUN",),
                "Required external stimulus or conditioning confirmation was not supplied.",
            )
        metrics = _assisted_metrics(
            test_id,
            experiment,
            session_path,
            phase_ranges,
            self._replay,
            self._analysis,
        )
        metrics, limit_reasons = _evaluate_assisted_limits(definition, metrics)
        if limit_reasons:
            return _simple_result(
                definition,
                now,
                QualificationStatus.FAIL,
                limit_reasons,
                "The captured assisted-test window violated a declared blocking limit.",
                metrics=metrics,
                confirmations=confirmations,
            )
        if identity.backend_id == "simulated":
            status, reasons = _simulated_assisted_outcome(definition, metrics)
            return _simple_result(
                definition,
                now,
                status,
                reasons,
                "The deterministic simulator and explicit confirmation supplied test-fixture evidence.",
                metrics=metrics,
                confirmations=confirmations,
                assumptions=("Simulator evidence is not physical hardware verification.",),
            )
        if any(limit.value is None for limit in definition.limits):
            return _simple_result(
                definition,
                now,
                QualificationStatus.NOT_TESTED,
                ("DAQ.QUALIFICATION.REQUIRED_TEST_NOT_RUN",),
                "The physical test has confirmation but no authoritative laboratory limit.",
                metrics=metrics,
                confirmations=confirmations,
            )
        return _simple_result(
            definition,
            now,
            QualificationStatus.PASS,
            (),
            "The externally confirmed physical test met its declared limits.",
            metrics=metrics,
            confirmations=confirmations,
        )

    def _append_unexecuted(
        self,
        plan: QualificationPlan,
        report: QualificationReport,
        session_path: Path,
    ) -> QualificationReport:
        completed = {result.test_id for result in report.results}
        updated = report
        for definition in plan.tests:
            if definition.test_id in completed:
                continue
            result = _simple_result(
                definition,
                datetime.now(UTC),
                QualificationStatus.NOT_TESTED,
                ("DAQ.QUALIFICATION.REQUIRED_TEST_NOT_RUN",),
                "The test was not executed by this qualification run.",
            )
            updated = replace(
                updated, results=(*updated.results, result), updated_at=datetime.now(UTC)
            )
            self._repository.checkpoint(session_path, updated)
        return updated

    def _finish_faulted_report(
        self,
        report: QualificationReport,
        plan: QualificationPlan,
        session_path: Path,
        cause: BaseException,
    ) -> QualificationReport:
        completed = {result.test_id for result in report.results}
        results = list(report.results)
        for definition in plan.tests:
            if definition.test_id not in completed:
                results.append(
                    _simple_result(
                        definition,
                        datetime.now(UTC),
                        QualificationStatus.NOT_TESTED,
                        ("DAQ.QUALIFICATION.REQUIRED_TEST_NOT_RUN",),
                        "The test was not completed because qualification faulted.",
                    )
                )
        faulted = replace(
            report,
            lifecycle=QualificationLifecycle.FAULTED,
            updated_at=datetime.now(UTC),
            results=tuple(results),
            recommended_actions=(
                f"Inspect retained acquisition evidence after {type(cause).__name__}.",
            ),
        )
        faulted = self._evaluator.evaluate(
            faulted,
            plan,
            updated_at=datetime.now(UTC),
            lifecycle=QualificationLifecycle.FAULTED,
        )
        self._repository.checkpoint(session_path, faulted)
        return faulted


def build_qualification_fingerprint_input(
    plan: QualificationPlan,
    profile: HardwareProfile,
    experiment: ExperimentConfig,
    identity: QualificationHardwareIdentity,
) -> QualificationFingerprintInput:
    channels = tuple(
        sorted(
            (item for item in experiment.channels if item.enabled), key=lambda x: x.channel_order
        )
    )
    physical = tuple(
        f"{channel.physical_channel.module_id}/ai{channel.physical_channel.channel_index}"
        for channel in channels
    )
    duration = experiment.acquisition.requested_duration_seconds or 0.0
    return QualificationFingerprintInput(
        plan_hash=plan.content_hash,
        hardware_profile_hash=canonical_sha256(profile),
        hardware_profile_id=profile.hardware_profile_id,
        hardware_profile_revision=profile.profile_revision,
        experiment_config_hash=qualification_experiment_hash(experiment),
        acquisition_rate_hz=experiment.acquisition.sample_rate_hz,
        acquisition_duration_seconds=duration,
        samples_per_read=experiment.acquisition.chunk_config.samples_per_channel,
        recording_queue_capacity_chunks=(
            experiment.acquisition.chunk_config.recording_queue_capacity_chunks
        ),
        storage_location=str(experiment.session.parent_output_directory.resolve()).casefold(),
        ordered_logical_channels=tuple(channel.channel_id for channel in channels),
        ordered_physical_channels=physical,
        requested_input_ranges_v=tuple(
            (channel.requested_input_min, channel.requested_input_max) for channel in channels
        ),
        sensor_identities=tuple(
            canonical_sha256(sensor)
            for sensor in sorted(experiment.sensors, key=lambda x: x.sensor_id)
        ),
        calibration_identities=tuple(
            canonical_sha256(calibration)
            for calibration in sorted(experiment.calibrations, key=lambda x: x.calibration_id)
        ),
        conditioning_identities=tuple(
            canonical_sha256(chain)
            for chain in sorted(
                experiment.conditioning_chains,
                key=lambda x: x.conditioning_chain_id,
            )
        ),
        hardware_identity=identity,
    )


def build_qualification_hardware_identity(
    discovery: DaqDiscoveryReport,
    validation: DaqValidationReport,
) -> QualificationHardwareIdentity:
    device = next(
        (
            item
            for item in discovery.devices
            if item.device_id == validation.selected_device_id
            or item.alias == validation.selected_device_id
        ),
        discovery.devices[0] if discovery.devices else None,
    )
    module = None
    if device is not None:
        module = next(
            (
                item
                for item in device.modules
                if item.module_id == validation.selected_module_id
                or item.reported_model == validation.selected_module_id
            ),
            device.modules[0] if device.modules else None,
        )
    return QualificationHardwareIdentity(
        backend_id=discovery.backend_id,
        device_id=device.device_id if device else None,
        device_alias=device.alias if device else None,
        device_model=device.model if device else None,
        device_serial_number=device.serial_number if device else None,
        module_id=module.module_id if module else None,
        module_model=module.model if module else None,
        module_serial_number=module.serial_number if module else None,
        ordered_physical_channels=validation.ordered_physical_channels,
        driver_version=discovery.driver.driver_version,
    )


def qualification_experiment_hash(experiment: ExperimentConfig) -> str:
    """Hash scientific configuration while excluding operator presentation text."""
    primitive = to_canonical_primitive(experiment)
    if not isinstance(primitive, dict):
        raise TypeError("experiment configuration must encode to an object")
    scientific = dict(primitive)
    scientific.pop("session", None)
    return canonical_sha256(scientific)


def _qualification_experiment(
    experiment: ExperimentConfig,
    capture_duration: float,
    *,
    continuous: bool = False,
) -> ExperimentConfig:
    session = replace(
        experiment.session,
        experiment_name="Hardware qualification",
        experiment_slug="hardware-qualification",
        description="DAQ-M08 synchronized hardware qualification raw evidence.",
    )
    acquisition = replace(
        experiment.acquisition,
        sampling_mode=SamplingMode.CONTINUOUS if continuous else SamplingMode.FINITE,
        requested_duration_seconds=None if continuous else capture_duration,
    )
    return replace(experiment, session=session, acquisition=acquisition)


def _capture_duration(plan: QualificationPlan) -> float:
    phases = _phase_ranges(plan)
    phase_stop = max(
        (start + count for ranges in phases.values() for start, count in ranges),
        default=0,
    )
    duration = phase_stop / plan.acquisition_rate_hz
    if any(item.test_id == "sustained-throughput" for item in plan.tests):
        duration += plan.throughput_duration_seconds
    if duration == 0.0:
        duration = plan.throughput_duration_seconds
    minimum = 1.0 / plan.acquisition_rate_hz
    return max(minimum, duration)


def _phase_ranges(plan: QualificationPlan) -> dict[str, tuple[tuple[int, int], ...]]:
    test_ids = {definition.test_id for definition in plan.tests}
    rate = plan.acquisition_rate_hz
    cursor = 0
    phases: dict[str, tuple[tuple[int, int], ...]] = {}
    if "powered-idle" in test_ids:
        count = rounded_sample_count(rate, plan.powered_idle_duration_seconds)
        phases["powered-idle"] = ((cursor, count),)
        cursor += count
    window = rounded_sample_count(rate, plan.assisted_window_duration_seconds)
    settling = (
        rounded_sample_count(rate, plan.assisted_settling_duration_seconds)
        if plan.assisted_settling_duration_seconds > 0
        else 0
    )
    if "charge-conditioning" in test_ids:
        cursor += settling
        phases["charge-conditioning"] = ((cursor, window),)
        cursor += window
    for test_id in ("axis-response-x", "axis-response-y", "axis-response-z"):
        if test_id in test_ids:
            cursor += settling
            phases[test_id] = ((cursor, window), (cursor + window, window))
            cursor += 2 * window + settling
    for test_id in ("cross-channel-alignment", "crosstalk"):
        if test_id in test_ids:
            cursor += settling
            phases[test_id] = ((cursor, window),)
            cursor += window
    return phases


def _new_report(
    plan: QualificationPlan,
    profile: HardwareProfile,
    experiment: ExperimentConfig,
    identity: QualificationHardwareIdentity,
    fingerprint: QualificationFingerprintInput,
    created_at: datetime,
    session_path: str,
) -> QualificationReport:
    return QualificationReport(
        schema_version=1,
        report_id=str(uuid4()),
        lifecycle=QualificationLifecycle.IN_PROGRESS,
        created_at=created_at,
        updated_at=created_at,
        operator=experiment.session.operator,
        application_version=__version__,
        operating_system=f"{platform.system()} {platform.version()}",
        plan_id=plan.plan_id,
        plan_hash=plan.content_hash,
        hardware_profile_id=profile.hardware_profile_id,
        hardware_profile_revision=profile.profile_revision,
        hardware_profile_hash=canonical_sha256(profile),
        experiment_config_hash=canonical_sha256(experiment),
        qualification_fingerprint=fingerprint.fingerprint,
        fingerprint_input=fingerprint,
        hardware_identity=identity,
        results=(),
        overall_status=QualificationStatus.NOT_TESTED,
        blocking_reason_codes=(),
        warning_reason_codes=(),
        skipped_test_ids=(),
        recommended_actions=(),
        sample_counts=QualificationSampleCounts(0, 0, (), ()),
        telemetry=QualificationTelemetry(),
        evidence=(),
        session_relative_path=session_path,
    )


def _definition(plan: QualificationPlan, test_id: str) -> QualificationTestDefinition:
    try:
        return next(item for item in plan.tests if item.test_id == test_id)
    except StopIteration as cause:
        raise KeyError(test_id) from cause


def _simple_result(
    definition: QualificationTestDefinition,
    timestamp: datetime,
    status: QualificationStatus,
    reasons: tuple[str, ...],
    summary: str,
    *,
    metrics: tuple[QualificationMetric, ...] = (),
    confirmations: tuple[OperatorConfirmation, ...] = (),
    assumptions: tuple[str, ...] = (),
) -> QualificationTestResult:
    return QualificationTestResult(
        test_id=definition.test_id,
        version=definition.version,
        started_at=timestamp,
        completed_at=timestamp,
        status=status,
        classification=definition.classification,
        required=definition.required,
        summary=summary,
        reason_codes=reasons,
        metrics=metrics,
        event_codes=(),
        evidence=(),
        confirmations=confirmations,
        assumptions=assumptions,
    )


def _self_test_result(
    plan: QualificationPlan,
    self_test: DaqSelfTestResult,
    timestamp: datetime,
) -> QualificationTestResult:
    definition = (
        _definition(plan, "self-test-lifecycle")
        if any(item.test_id == "self-test-lifecycle" for item in plan.tests)
        else QualificationTestDefinition(
            "self-test-lifecycle",
            1,
            "Self-test",
            "Backend self-test",
            True,
            QualificationClassification.BLOCKING,
            QualificationExecutionMode.AUTOMATED,
            (),
            (),
            (),
            (),
            (),
            (),
        )
    )
    supported = self_test.supported
    passed = self_test.passed
    reasons: tuple[str, ...]
    if not supported:
        status = QualificationStatus.NOT_TESTED
        reasons = ("DAQ.QUALIFICATION.HARDWARE_SELF_TEST_NOT_SUPPORTED",)
    elif passed:
        status = QualificationStatus.PASS
        reasons = ()
    else:
        status = QualificationStatus.FAIL
        reasons = ("DAQ.QUALIFICATION.HARDWARE_SELF_TEST_FAILED",)
    return _simple_result(definition, timestamp, status, reasons, self_test.message)


def _powered_idle_result(
    plan: QualificationPlan,
    experiment: ExperimentConfig,
    samples: np.ndarray,
    analysis: QualificationAnalysisPort,
    timestamp: datetime,
) -> QualificationTestResult:
    definition = _definition(plan, "powered-idle")
    channels = tuple(
        sorted(
            (item for item in experiment.channels if item.enabled), key=lambda x: x.channel_order
        )
    )
    metrics: list[QualificationMetric] = []
    reasons: list[str] = []
    status = QualificationStatus.PASS
    for index, channel in enumerate(channels):
        summary = analysis.channel_diagnostics(
            samples[index],
            input_range_v=(channel.requested_input_min, channel.requested_input_max),
        )
        values = {
            "mean-v": (summary.mean_v, "V"),
            "median-v": (summary.median_v, "V"),
            "standard-deviation-v": (summary.standard_deviation_v, "V"),
            "rms-v": (summary.rms_v, "V"),
            "robust-noise-v": (summary.robust_noise_v, "V"),
            "range-utilization": (summary.range_utilization, "fraction"),
            "clipping-fraction": (summary.clipping_fraction, "fraction"),
            "nonfinite-count": (float(summary.nonfinite_count), "samples"),
            "longest-repeated-value-run": (
                float(summary.longest_repeated_value_run),
                "samples",
            ),
        }
        for name, (value, unit) in values.items():
            metrics.append(
                QualificationMetric(
                    name=name,
                    value_kind=MetricValueKind.NUMBER,
                    value=value,
                    unit=unit,
                    signal_stage=SignalStage.RAW,
                    evaluation=MetricEvaluation.UNEVALUATED,
                    method="bounded-numpy-diagnostic-v1",
                    software_version="1",
                    channel_id=channel.channel_id,
                    first_sample_index=0,
                    sample_count=summary.sample_count,
                )
            )
        if summary.clipping_fraction > 0 or summary.range_utilization > 0.98:
            status = QualificationStatus.FAIL
            reasons.append("DAQ.QUALIFICATION.CHANNEL_CLIPPING_DETECTED")
        elif summary.range_utilization > 0.8 and status is QualificationStatus.PASS:
            status = QualificationStatus.WARNING
            reasons.append("DAQ.QUALIFICATION.CHANNEL_NEAR_CLIPPING")
        if summary.nonfinite_count:
            status = QualificationStatus.FAIL
            reasons.append("DAQ.QUALIFICATION.CHANNEL_NONFINITE_SAMPLE")
        if summary.longest_repeated_value_run >= experiment.acquisition.sample_rate_hz:
            status = QualificationStatus.FAIL
            reasons.append("DAQ.QUALIFICATION.CHANNEL_FROZEN")
    return _simple_result(
        definition,
        timestamp,
        status,
        tuple(dict.fromkeys(reasons)),
        "Powered-idle raw voltage diagnostics completed for every synchronized channel.",
        metrics=tuple(metrics),
    )


def _assisted_metrics(
    test_id: str,
    experiment: ExperimentConfig,
    session_path: Path,
    phase_ranges: tuple[tuple[int, int], ...],
    replay: ReplaySourcePort,
    analysis: QualificationAnalysisPort,
) -> tuple[QualificationMetric, ...]:
    if not phase_ranges:
        return ()
    channels = tuple(
        sorted(
            (item for item in experiment.channels if item.enabled),
            key=lambda item: item.channel_order,
        )
    )
    chunk_size = experiment.acquisition.chunk_config.samples_per_channel

    def window(phase: tuple[int, int]) -> np.ndarray:
        start, count = phase
        return _read_bounded_window(
            replay,
            session_path,
            sample_limit=count,
            chunk_size=chunk_size,
            start_sample=start,
        )

    if test_id == "charge-conditioning":
        samples = window(phase_ranges[0])
        index = next(
            index
            for index, channel in enumerate(channels)
            if channel.channel_role is ChannelRole.CONDITIONED_CHARGE
        )
        charge_summary = analysis.channel_diagnostics(
            samples[index],
            input_range_v=(
                channels[index].requested_input_min,
                channels[index].requested_input_max,
            ),
        )
        return (
            _diagnostic_metric(
                "terminal-bias-v",
                charge_summary.median_v,
                "V",
                channels[index].channel_id,
                phase_ranges[0],
                SignalStage.RAW,
                "bounded-charge-terminal-diagnostic-v1",
            ),
        )
    axis_roles = {
        "axis-response-x": ChannelRole.ACCELEROMETER_X,
        "axis-response-y": ChannelRole.ACCELEROMETER_Y,
        "axis-response-z": ChannelRole.ACCELEROMETER_Z,
    }
    if test_id in axis_roles and len(phase_ranges) == 2:
        baseline = window(phase_ranges[0])
        stimulus = window(phase_ranges[1])
        index = next(
            index
            for index, channel in enumerate(channels)
            if channel.channel_role is axis_roles[test_id]
        )
        response_summary = analysis.response(baseline[index], stimulus[index])
        return (
            _diagnostic_metric(
                "baseline-energy-v2",
                response_summary.baseline_energy,
                "V^2",
                channels[index].channel_id,
                phase_ranges[0],
                SignalStage.RAW,
                "response-energy-ratio-v1",
            ),
            _diagnostic_metric(
                "stimulus-energy-v2",
                response_summary.stimulus_energy,
                "V^2",
                channels[index].channel_id,
                phase_ranges[1],
                SignalStage.RAW,
                "response-energy-ratio-v1",
            ),
            _diagnostic_metric(
                "response-energy-ratio",
                response_summary.response_ratio,
                "ratio",
                channels[index].channel_id,
                phase_ranges[1],
                SignalStage.RAW,
                "response-energy-ratio-v1",
            ),
        )
    if test_id == "cross-channel-alignment":
        samples = window(phase_ranges[0])
        metrics: list[QualificationMetric] = []
        for index in range(1, len(channels)):
            alignment_summary = analysis.alignment(
                samples[0],
                samples[index],
                sample_rate_hz=experiment.acquisition.sample_rate_hz,
            )
            metrics.extend(
                (
                    _diagnostic_metric(
                        "lag-samples",
                        float(alignment_summary.lag_samples),
                        "samples",
                        channels[index].channel_id,
                        phase_ranges[0],
                        SignalStage.RAW,
                        "raw-cross-correlation-v1",
                    ),
                    _diagnostic_metric(
                        "normalized-correlation",
                        alignment_summary.normalized_correlation,
                        "fraction",
                        channels[index].channel_id,
                        phase_ranges[0],
                        SignalStage.RAW,
                        "raw-cross-correlation-v1",
                    ),
                )
            )
        return tuple(metrics)
    if test_id == "crosstalk":
        samples = window(phase_ranges[0])
        return tuple(
            _diagnostic_metric(
                "coupling-db",
                analysis.crosstalk(samples[0], samples[index]).coupling_db,
                "dB",
                channels[index].channel_id,
                phase_ranges[0],
                SignalStage.RAW,
                "ac-rms-crosstalk-v1",
            )
            for index in range(1, len(channels))
        )
    return ()


def _diagnostic_metric(
    name: str,
    value: float,
    unit: str,
    channel_id: str | None,
    phase: tuple[int, int],
    signal_stage: SignalStage,
    method: str,
) -> QualificationMetric:
    finite = math.isfinite(value)
    return QualificationMetric(
        name=name,
        value_kind=MetricValueKind.NUMBER if finite else MetricValueKind.TEXT,
        value=value if finite else str(value),
        unit=unit,
        signal_stage=signal_stage,
        evaluation=MetricEvaluation.UNEVALUATED,
        method=method,
        software_version="1",
        channel_id=channel_id,
        first_sample_index=phase[0],
        sample_count=phase[1],
    )


def _simulated_assisted_outcome(
    definition: QualificationTestDefinition,
    metrics: tuple[QualificationMetric, ...],
) -> tuple[QualificationStatus, tuple[str, ...]]:
    metric_values = {
        metric.name: metric.value
        for metric in metrics
        if metric.value_kind is MetricValueKind.NUMBER
    }
    if definition.test_id.startswith("axis-response-"):
        ratio = metric_values.get("response-energy-ratio")
        if isinstance(ratio, float) and ratio <= 1.0:
            return QualificationStatus.FAIL, ("DAQ.QUALIFICATION.SENSOR_RESPONSE_MISSING",)
    if definition.test_id == "cross-channel-alignment":
        lag = metric_values.get("lag-samples")
        if isinstance(lag, float) and abs(lag) > 0.0:
            return QualificationStatus.FAIL, ("DAQ.QUALIFICATION.CROSS_CHANNEL_MISALIGNMENT",)
    if definition.classification is QualificationClassification.ADVISORY:
        return QualificationStatus.WARNING, definition.advisory_reason_codes
    return QualificationStatus.PASS, ()


def _evaluate_assisted_limits(
    definition: QualificationTestDefinition,
    metrics: tuple[QualificationMetric, ...],
) -> tuple[tuple[QualificationMetric, ...], tuple[str, ...]]:
    metric_name = next(
        (limit.metric_name for limit in definition.limits if limit.metric_name is not None),
        None,
    ) or {
        "charge-conditioning": "terminal-bias-v",
        "axis-response-x": "response-energy-ratio",
        "axis-response-y": "response-energy-ratio",
        "axis-response-z": "response-energy-ratio",
        "cross-channel-alignment": "lag-samples",
        "crosstalk": "coupling-db",
    }.get(definition.test_id)
    if metric_name is None:
        return metrics, ()
    selected_limit = next((limit for limit in definition.limits if limit.value is not None), None)
    if selected_limit is None:
        return metrics, ()
    updated: list[QualificationMetric] = []
    failure = False
    for metric in metrics:
        if metric.name != metric_name or metric.value_kind is not MetricValueKind.NUMBER:
            updated.append(metric)
            continue
        observed = metric.value
        assert isinstance(observed, float)
        if definition.test_id == "cross-channel-alignment":
            observed = abs(observed)
        evaluation = evaluate_limit(observed, selected_limit)
        updated.append(replace(metric, evaluation=evaluation, limit=selected_limit))
        failure = evaluation is MetricEvaluation.OUTSIDE_FAILURE_LIMIT
    if not failure:
        return tuple(updated), ()
    code = {
        "charge-conditioning": "DAQ.QUALIFICATION.CHARGE_CONDITIONING_INCOMPATIBLE",
        "axis-response-x": "DAQ.QUALIFICATION.SENSOR_RESPONSE_MISSING",
        "axis-response-y": "DAQ.QUALIFICATION.SENSOR_RESPONSE_MISSING",
        "axis-response-z": "DAQ.QUALIFICATION.SENSOR_RESPONSE_MISSING",
        "cross-channel-alignment": "DAQ.QUALIFICATION.CROSS_CHANNEL_MISALIGNMENT",
        "crosstalk": "DAQ.QUALIFICATION.CROSSTALK_ABOVE_LIMIT",
    }[definition.test_id]
    return tuple(updated), (code,)


def _throughput_result(
    plan: QualificationPlan,
    session: RecordedSessionCompletion,
    integrity: SessionIntegrityReport,
    recording_status: RecordingStatus,
    timestamp: datetime,
) -> QualificationTestResult:
    definition = _definition(plan, "sustained-throughput")
    acquisition = recording_status.acquisition
    telemetry = acquisition.telemetry
    reasons: list[str] = []
    if (
        telemetry.source_missing_samples
        or telemetry.source_duplicated_samples
        or telemetry.source_reordered_reads
    ):
        reasons.append("DAQ.QUALIFICATION.THROUGHPUT_GAP_DETECTED")
    if (
        acquisition.target_sample_count is not None
        and session.acquired_samples_per_channel != acquisition.target_sample_count
    ):
        reasons.append("DAQ.QUALIFICATION.THROUGHPUT_GAP_DETECTED")
    if acquisition.last_warning_or_fault_code == "DAQ.ACQUISITION.RECORDING_QUEUE_SATURATED":
        reasons.append("DAQ.QUALIFICATION.THROUGHPUT_STORAGE_BACKPRESSURE")
    per_channel = tuple(integrity.per_channel_lengths.values())
    if len(set(per_channel)) > 1:
        reasons.append("DAQ.QUALIFICATION.THROUGHPUT_CHANNEL_LENGTH_MISMATCH")
    if session.fault is not None or integrity.overall is IntegrityStatus.FAIL:
        reasons.append("DAQ.QUALIFICATION.QUALIFICATION_FAILED")
    status = QualificationStatus.FAIL if reasons else QualificationStatus.PASS
    metrics = tuple(
        QualificationMetric(
            name=name,
            value_kind=MetricValueKind.NUMBER,
            value=float(value),
            unit=unit,
            signal_stage=SignalStage.ACQUISITION,
            evaluation=MetricEvaluation.UNEVALUATED,
            method="acquisition-session-telemetry-v1",
            software_version="1",
        )
        for name, value, unit in (
            ("acquired-count", session.acquired_samples_per_channel, "samples/channel"),
            ("persisted-count", session.persisted_samples_per_channel, "samples/channel"),
            ("missing-count", telemetry.source_missing_samples, "samples"),
            ("duplicated-count", telemetry.source_duplicated_samples, "samples"),
            ("reordered-count", telemetry.source_reordered_reads, "reads"),
            ("queue-utilization", telemetry.recording_queue_max_utilization, "fraction"),
        )
    )
    return _simple_result(
        definition,
        timestamp,
        status,
        tuple(dict.fromkeys(reasons)),
        "End-to-end acquisition, recording, full verification, and replay counts agree."
        if not reasons
        else "End-to-end throughput evidence contains a blocking fault.",
        metrics=metrics,
    )


def _read_bounded_window(
    replay: ReplaySourcePort,
    path: Path,
    *,
    sample_limit: int,
    chunk_size: int,
    start_sample: int = 0,
) -> np.ndarray:
    import numpy as np

    stream = replay.open_replay(path, chunk_size=chunk_size)
    parts: list[np.ndarray] = []
    retained = 0
    try:
        for chunk in stream:
            chunk_stop = chunk.start_sample_index + chunk.sample_count
            overlap_start = max(start_sample, chunk.start_sample_index)
            overlap_stop = min(start_sample + sample_limit, chunk_stop)
            take = max(0, overlap_stop - overlap_start)
            if take > 0:
                left = overlap_start - chunk.start_sample_index
                parts.append(np.array(chunk.samples[:, left : left + take], copy=True))
                retained += take
            if retained >= sample_limit:
                break
    finally:
        stream.close()
    if not parts:
        raise QualificationError(
            "The qualification session contains no replayable powered-idle samples.",
            code="DAQ.QUALIFICATION.QUALIFICATION_FAILED",
        )
    return np.ascontiguousarray(np.concatenate(parts, axis=1), dtype=np.float64)


def _sample_counts(
    session: RecordedSessionCompletion,
    integrity: SessionIntegrityReport,
    recording_status: RecordingStatus,
) -> QualificationSampleCounts:
    telemetry = recording_status.acquisition.telemetry
    channel_counts = tuple(int(value) for value in integrity.per_channel_lengths.values())
    return QualificationSampleCounts(
        expected_per_channel=recording_status.acquisition.target_sample_count or 0,
        acquired_per_channel=session.acquired_samples_per_channel,
        persisted_per_channel=channel_counts,
        replayed_per_channel=channel_counts,
        missing=telemetry.source_missing_samples,
        duplicated=telemetry.source_duplicated_samples,
        reordered=telemetry.source_reordered_reads,
        display_dropped=recording_status.acquisition.display_frames_dropped,
        invalid=0,
    )


def _telemetry(recording_status: RecordingStatus) -> QualificationTelemetry:
    acquisition = recording_status.acquisition
    telemetry = acquisition.telemetry
    return QualificationTelemetry(
        read_count=telemetry.read_count,
        read_duration_total_seconds=telemetry.read_duration_total_seconds,
        read_duration_max_seconds=telemetry.read_duration_max_seconds,
        write_duration_total_seconds=recording_status.write_duration_total_seconds,
        write_duration_max_seconds=recording_status.write_duration_max_seconds,
        recording_queue_high_water_chunks=telemetry.recording_queue_high_water_chunks,
        recording_queue_capacity_chunks=acquisition.recording_queue_capacity,
        recording_queue_max_utilization=telemetry.recording_queue_max_utilization,
        backpressure_events=telemetry.recording_backpressure_events,
        stop_latency_seconds=telemetry.stop_latency_seconds,
        cleanup_succeeded=None,
    )


def _guided_prompt(test_id: str, display_name: str) -> str:
    prompts = {
        "charge-conditioning": (
            "Verify the approved constant-current conditioner and bias-removed output are "
            "connected, range-safe, and polarity-checked; then confirm."
        ),
        "axis-response-x": "Apply the controlled X-axis manual stimulus without rewiring; then confirm.",
        "axis-response-y": "Apply the controlled Y-axis manual stimulus without rewiring; then confirm.",
        "axis-response-z": "Apply the controlled Z-axis manual stimulus without rewiring; then confirm.",
        "cross-channel-alignment": (
            "Verify the stopped-task, approved common-input fixture is ready; do not rewire an active task; then confirm."
        ),
        "crosstalk": (
            "Verify the stopped-task, approved single-channel fixture is ready; do not rewire an active task; then confirm."
        ),
    }
    return prompts.get(
        test_id,
        f"{display_name}: perform the approved safe action without rewiring an active task, then confirm.",
    )
