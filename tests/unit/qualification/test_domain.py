from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from labdaq.adapters.configuration_json import JsonConfigurationAdapter
from labdaq.adapters.qualification_json import QualificationJsonCodec
from labdaq.application.qualification_service import (
    BuildQualificationPlan,
    build_qualification_fingerprint_input,
)
from labdaq.domain.models import ExperimentConfig, HardwareProfile
from labdaq.domain.qualification import (
    EligibilityReason,
    EvidenceLink,
    LimitComparison,
    LimitSourceCategory,
    MetricEvaluation,
    MetricValueKind,
    OperatorConfirmation,
    QualificationClassification,
    QualificationHardwareIdentity,
    QualificationLifecycle,
    QualificationLimit,
    QualificationMetric,
    QualificationOverride,
    QualificationPlan,
    QualificationReasonCode,
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

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _configuration(tmp_path: Path) -> tuple[HardwareProfile, ExperimentConfig]:
    documents = JsonConfigurationAdapter()
    experiment = cast(
        "ExperimentConfig",
        documents.load_experiment_config(
            Path("tests/fixtures/configuration/valid-four-channel-synthetic-v1.json")
        ),
    )
    experiment = replace(
        experiment,
        session=replace(experiment.session, parent_output_directory=tmp_path),
    )
    profile = documents.load_bundled_hardware_profile(
        experiment.hardware_profile_id,
        experiment.compatible_profile_revision,
    )
    return profile, experiment


def _identity() -> QualificationHardwareIdentity:
    return QualificationHardwareIdentity(
        backend_id="simulated",
        device_id="device",
        device_alias="Dev1",
        device_model="cDAQ-9171",
        device_serial_number="serial",
        module_id="module",
        module_model="NI-9239",
        module_serial_number="module-serial",
        ordered_physical_channels=("Dev1Mod1/ai0",),
        driver_version="test",
    )


def test_reason_code_vocabulary_covers_m08_operator_and_pipeline_findings() -> None:
    values = {code.value for code in QualificationReasonCode}
    assert "DAQ.QUALIFICATION.QUALIFICATION_STARTED" in values
    assert "DAQ.QUALIFICATION.CHANNEL_BIAS_OUT_OF_RANGE" in values
    assert "DAQ.QUALIFICATION.SENSOR_RESPONSE_MISSING" in values
    assert "DAQ.QUALIFICATION.CROSS_CHANNEL_MISALIGNMENT" in values
    assert "DAQ.QUALIFICATION.THROUGHPUT_STORAGE_BACKPRESSURE" in values
    assert "DAQ.QUALIFICATION.REPORT_STALE" in values


def _result(
    definition: QualificationTestDefinition,
    status: QualificationStatus,
    *,
    reasons: tuple[str, ...] = (),
    assumptions: tuple[str, ...] = (),
) -> QualificationTestResult:
    return QualificationTestResult(
        test_id=definition.test_id,
        version=definition.version,
        started_at=NOW,
        completed_at=NOW,
        status=status,
        classification=definition.classification,
        required=definition.required,
        summary="result",
        reason_codes=reasons,
        metrics=(),
        event_codes=(),
        evidence=(),
        confirmations=(),
        assumptions=assumptions,
    )


def _report(
    plan: QualificationPlan,
    fingerprint_input: object,
    results: tuple[QualificationTestResult, ...],
    status: QualificationStatus,
) -> QualificationReport:
    typed = fingerprint_input
    return QualificationReport(
        schema_version=1,
        report_id="report-id",
        lifecycle=QualificationLifecycle.COMPLETED,
        created_at=NOW,
        updated_at=NOW,
        operator="operator",
        application_version="test",
        operating_system="test",
        plan_id=plan.plan_id,
        plan_hash=plan.content_hash,
        hardware_profile_id=typed.hardware_profile_id,  # type: ignore[attr-defined]
        hardware_profile_revision=typed.hardware_profile_revision,  # type: ignore[attr-defined]
        hardware_profile_hash=typed.hardware_profile_hash,  # type: ignore[attr-defined]
        experiment_config_hash=typed.experiment_config_hash,  # type: ignore[attr-defined]
        qualification_fingerprint=typed.fingerprint,  # type: ignore[attr-defined]
        fingerprint_input=typed,  # type: ignore[arg-type]
        hardware_identity=_identity(),
        results=results,
        overall_status=status,
        blocking_reason_codes=(),
        warning_reason_codes=(),
        skipped_test_ids=(),
        recommended_actions=(),
        sample_counts=QualificationSampleCounts(10, 10, (10,), (10,)),
        telemetry=QualificationTelemetry(),
        evidence=(),
        session_relative_path="session",
    )


def test_bundled_plans_bind_and_fingerprint_only_scientific_identity(tmp_path: Path) -> None:
    profile, experiment = _configuration(tmp_path)
    codec = QualificationJsonCodec()
    hashes = set()
    for name, expected_count in (("preflight-v1", 6), ("full-v1", 12), ("throughput-v1", 3)):
        template = codec.load_plan(name)
        plan = BuildQualificationPlan().build(
            template,
            profile,
            experiment,
            throughput_duration_seconds=0.02,
        )
        assert len(plan.tests) == expected_count
        assert plan.hardware_profile_id == profile.hardware_profile_id
        assert plan.experiment_config_hash is not None
        assert plan.with_throughput_duration(0.03).content_hash != plan.content_hash
        hashes.add(plan.content_hash)
    assert len(hashes) == 3

    plan = BuildQualificationPlan().build(codec.load_plan("throughput-v1"), profile, experiment)
    first = build_qualification_fingerprint_input(plan, profile, experiment, _identity())
    second = build_qualification_fingerprint_input(
        plan,
        profile,
        replace(experiment, session=replace(experiment.session, notes="presentation only")),
        _identity(),
    )
    assert first.fingerprint == second.fingerprint
    assert first.fingerprint == first.fingerprint
    changed = replace(first, acquisition_rate_hz=25_000.0)
    assert changed.fingerprint != first.fingerprint


@pytest.mark.parametrize(
    ("comparison", "observed", "expected", "passes"),
    [
        (LimitComparison.LESS_THAN, 1.0, 2.0, True),
        (LimitComparison.LESS_THAN_OR_EQUAL, 2.0, 2.0, True),
        (LimitComparison.GREATER_THAN, 3.0, 2.0, True),
        (LimitComparison.GREATER_THAN_OR_EQUAL, 2.0, 2.0, True),
        (LimitComparison.EQUAL, "ok", "ok", True),
        (LimitComparison.NOT_EQUAL, "bad", "ok", True),
        (LimitComparison.BETWEEN_INCLUSIVE, 2.0, (1.0, 2.0), True),
        (LimitComparison.LESS_THAN, 2.0, 2.0, False),
        (LimitComparison.BETWEEN_INCLUSIVE, "x", (1.0, 2.0), False),
        (LimitComparison.LESS_THAN, "x", 2.0, False),
    ],
)
def test_limit_evaluation_has_exact_boundaries(
    comparison: LimitComparison,
    observed: object,
    expected: object,
    passes: bool,
) -> None:
    limit = QualificationLimit(
        "limit",
        comparison,
        expected,  # type: ignore[arg-type]
        "unit",
        SignalStage.RAW,
        LimitSourceCategory.ENGINEERING_POLICY,
        "source",
        "rationale",
        QualificationStatus.FAIL,
    )
    evaluation = evaluate_limit(observed, limit)  # type: ignore[arg-type]
    assert evaluation is (
        MetricEvaluation.WITHIN_LIMIT if passes else MetricEvaluation.OUTSIDE_FAILURE_LIMIT
    )
    warning = replace(limit, consequence=QualificationStatus.WARNING)
    if not passes:
        assert evaluate_limit(observed, warning) is MetricEvaluation.OUTSIDE_WARNING_LIMIT  # type: ignore[arg-type]


def test_unresolved_limit_is_never_a_fabricated_pass() -> None:
    unresolved = QualificationLimit(
        "noise",
        LimitComparison.LESS_THAN_OR_EQUAL,
        None,
        "V RMS",
        SignalStage.CONDITIONED,
        LimitSourceCategory.UNRESOLVED,
        "lab value required",
        "No authoritative value supplied.",
        QualificationStatus.NOT_TESTED,
    )
    assert evaluate_limit(0.0, unresolved) is MetricEvaluation.UNEVALUATED
    with pytest.raises(ValueError, match="must not fabricate"):
        replace(unresolved, value=1.0)
    with pytest.raises(ValueError, match="only warn"):
        replace(unresolved, consequence=QualificationStatus.FAIL)
    with pytest.raises(ValueError, match="require a value"):
        replace(
            unresolved,
            source_category=LimitSourceCategory.ENGINEERING_POLICY,
        )


def test_status_aggregation_precedence_and_assumptions() -> None:
    plan = QualificationJsonCodec().load_plan("throughput-v1")
    definitions = plan.tests
    passing = tuple(_result(item, QualificationStatus.PASS) for item in definitions)
    assert aggregate_qualification_status(plan, passing) is QualificationStatus.PASS
    assert aggregate_qualification_status(plan, passing[:-1]) is QualificationStatus.NOT_TESTED
    failed = (*passing[:-1], _result(definitions[-1], QualificationStatus.FAIL))
    assert aggregate_qualification_status(plan, failed) is QualificationStatus.FAIL
    warning = replace(
        passing[0],
        status=QualificationStatus.WARNING,
        classification=QualificationClassification.ADVISORY,
    )
    assert (
        aggregate_qualification_status(plan, (warning, *passing[1:])) is QualificationStatus.WARNING
    )
    assumed = replace(passing[0], assumptions=("unverified",))
    assert (
        aggregate_qualification_status(plan, (assumed, *passing[1:])) is QualificationStatus.WARNING
    )


def test_eligibility_requires_current_identity_age_and_final_blocking_results(
    tmp_path: Path,
) -> None:
    profile, experiment = _configuration(tmp_path)
    plan = BuildQualificationPlan().build(
        QualificationJsonCodec().load_plan("throughput-v1"), profile, experiment
    )
    fingerprint = build_qualification_fingerprint_input(plan, profile, experiment, _identity())
    passing = tuple(_result(item, QualificationStatus.PASS) for item in plan.tests)
    report = _report(plan, fingerprint, passing, QualificationStatus.PASS)
    eligible = check_qualification_eligibility(
        report,
        current_fingerprint=fingerprint.fingerprint,
        evaluated_at=NOW + timedelta(seconds=1),
        maximum_age_seconds=None,
        plan=plan,
    )
    assert eligible.eligible
    assert eligible.to_dict()["fingerprint_matches"] is True

    stale = check_qualification_eligibility(
        report,
        current_fingerprint="0" * 64,
        evaluated_at=NOW + timedelta(seconds=2),
        maximum_age_seconds=1.0,
        plan=plan,
    )
    assert not stale.eligible
    assert [reason.code for reason in stale.reasons] == [
        "DAQ.QUALIFICATION.REPORT_STALE",
        "DAQ.QUALIFICATION.REPORT_STALE",
    ]

    incomplete = replace(
        report,
        lifecycle=QualificationLifecycle.CANCELLED,
        overall_status=QualificationStatus.NOT_TESTED,
        blocking_reason_codes=("DAQ.QUALIFICATION.REQUIRED_TEST_NOT_RUN",),
    )
    decision = check_qualification_eligibility(
        incomplete,
        current_fingerprint=fingerprint.fingerprint,
        evaluated_at=NOW,
        maximum_age_seconds=None,
        plan=plan,
    )
    assert not decision.eligible
    assert any(reason.blocking for reason in decision.reasons)


def test_advisory_override_cannot_acknowledge_blocking_or_undeclared_codes(tmp_path: Path) -> None:
    profile, experiment = _configuration(tmp_path)
    plan = BuildQualificationPlan().build(
        QualificationJsonCodec().load_plan("preflight-v1"), profile, experiment
    )
    fingerprint = build_qualification_fingerprint_input(plan, profile, experiment, _identity())
    passing = tuple(_result(item, QualificationStatus.PASS) for item in plan.tests)
    report = _report(plan, fingerprint, passing, QualificationStatus.WARNING)
    report = replace(
        report,
        warning_reason_codes=("DAQ.QUALIFICATION.BIAS_LIMIT_UNRESOLVED",),
        override=QualificationOverride(
            "DAQ.QUALIFICATION.BIAS_LIMIT_UNRESOLVED",
            "operator",
            "accepted for synthetic test",
            NOW,
        ),
    )
    decision = check_qualification_eligibility(
        report,
        current_fingerprint=fingerprint.fingerprint,
        evaluated_at=NOW,
        maximum_age_seconds=None,
        plan=plan,
    )
    assert decision.eligible and decision.override_applied
    assert not decision.reasons

    unacknowledged = replace(
        report,
        warning_reason_codes=("DAQ.QUALIFICATION.CHANNEL_NEAR_CLIPPING",),
    )
    decision = check_qualification_eligibility(
        unacknowledged,
        current_fingerprint=fingerprint.fingerprint,
        evaluated_at=NOW,
        maximum_age_seconds=None,
        plan=plan,
    )
    assert decision.eligible
    assert decision.reasons[0].code == "DAQ.QUALIFICATION.CHANNEL_NEAR_CLIPPING"


def test_records_serialize_evidence_metrics_and_confirmations() -> None:
    confirmation = OperatorConfirmation("confirm", True, NOW, "op", "statement", sample_index=4)
    metric = QualificationMetric(
        "mean",
        MetricValueKind.NUMBER,
        1.0,
        "V",
        SignalStage.RAW,
        MetricEvaluation.WITHIN_LIMIT,
        "mean-v1",
        "1",
        first_sample_index=0,
        sample_count=4,
    )
    evidence = EvidenceLink("raw", "raw.tdms", "0" * 64, "retained")
    reason = EligibilityReason("DAQ.QUALIFICATION.REPORT_STALE", True, "stale")
    assert confirmation.sample_index == 4
    assert metric.value == 1.0
    assert evidence.sha256 == "0" * 64
    assert reason.blocking
