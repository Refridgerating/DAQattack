from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from labdaq.adapters.configuration_json import JsonConfigurationAdapter
from labdaq.adapters.qualification_json import (
    AtomicQualificationReportRepository,
    QualificationJsonCodec,
)
from labdaq.adapters.qualification_prompts import (
    ConfirmationFilePrompt,
    InteractiveOperatorPrompt,
)
from labdaq.application.qualification_service import (
    BuildQualificationPlan,
    RecoverQualificationReport,
    _new_report,
    _simple_result,
    build_qualification_fingerprint_input,
)
from labdaq.domain.qualification import (
    ConfirmationDocument,
    EvidenceLink,
    MetricEvaluation,
    MetricValueKind,
    OperatorConfirmation,
    QualificationHardwareIdentity,
    QualificationMetric,
    QualificationOverride,
    QualificationStatus,
    SignalStage,
)
from labdaq.errors import ConfigurationValidationError, QualificationError

if TYPE_CHECKING:
    from labdaq.domain.models import ExperimentConfig

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _report(tmp_path: Path) -> tuple[object, object]:
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
    plan = BuildQualificationPlan().build(
        QualificationJsonCodec().load_plan("throughput-v1"), profile, experiment
    )
    identity = QualificationHardwareIdentity(
        "simulated",
        "device",
        "Dev1",
        "cDAQ-9171",
        "serial",
        "module",
        "NI-9239",
        "module-serial",
        ("Dev1Mod1/ai0", "Dev1Mod1/ai1", "Dev1Mod1/ai2", "Dev1Mod1/ai3"),
        "test",
    )
    fingerprint = build_qualification_fingerprint_input(plan, profile, experiment, identity)
    report = _new_report(plan, profile, experiment, identity, fingerprint, NOW, "session")
    metric = QualificationMetric(
        "count",
        MetricValueKind.NUMBER,
        10.0,
        "samples",
        SignalStage.ACQUISITION,
        MetricEvaluation.WITHIN_LIMIT,
        "count-v1",
        "1",
    )
    confirmation = OperatorConfirmation("confirmed", True, NOW, "tester", "statement")
    result = _simple_result(
        plan.tests[0],
        NOW,
        QualificationStatus.PASS,
        (),
        "passed",
        metrics=(metric,),
        confirmations=(confirmation,),
    )
    report = replace(
        report,
        results=(result,),
        evidence=(EvidenceLink("raw", "raw.tdms", "0" * 64, "retained"),),
        override=QualificationOverride(
            "DAQ.QUALIFICATION.RENDERING_UNAVAILABLE",
            "tester",
            "optional renderer absent",
            NOW,
        ),
    )
    return plan, report


def test_plan_report_and_confirmation_codecs_round_trip_atomically(tmp_path: Path) -> None:
    plan, report = _report(tmp_path)
    assert len(report.report_hash) == 64  # type: ignore[attr-defined]
    assert QualificationJsonCodec().encode(report)["schema_version"] == 1
    with pytest.raises(TypeError, match="objects"):
        QualificationJsonCodec().encode(3)
    repository = AtomicQualificationReportRepository()
    session = tmp_path / "session"
    session.mkdir()
    repository.initialize(session, plan)  # type: ignore[arg-type]
    repository.initialize(session, plan)  # idempotent exact plan
    path = repository.checkpoint(session, report)  # type: ignore[arg-type]
    assert repository.load(path) == report
    assert repository.load_plan(session / "qualification-plan.json") == plan

    confirmation_path = tmp_path / "confirmations.json"
    confirmation_path.write_text(
        '{"schema_version":1,"confirmations":[{"confirmation_id":"ok",'
        '"confirmed":true,"confirmed_at":"2026-08-13T00:00:00Z",'
        '"operator":"tester","statement":"ready","evidence_reference":null}]}',
        encoding="utf-8",
    )
    document = QualificationJsonCodec().load_confirmations(confirmation_path)
    assert document.confirmations[0].confirmed_at == NOW.replace(hour=0)


def test_atomic_repository_refuses_plan_replacement_and_bad_documents(tmp_path: Path) -> None:
    plan, _ = _report(tmp_path)
    repository = AtomicQualificationReportRepository()
    session = tmp_path / "session"
    session.mkdir()
    repository.initialize(session, plan)  # type: ignore[arg-type]
    changed = replace(plan, throughput_duration_seconds=1.0)  # type: ignore[arg-type]
    with pytest.raises(QualificationError, match="different plan"):
        repository.initialize(session, changed)

    invalid = tmp_path / "invalid.json"
    invalid.write_text("[]", encoding="utf-8")
    with pytest.raises(QualificationError, match="JSON object"):
        QualificationJsonCodec().load_report(invalid)
    invalid.write_text("{", encoding="utf-8")
    with pytest.raises(QualificationError, match="not valid JSON"):
        QualificationJsonCodec().load_report(invalid)
    with pytest.raises(QualificationError, match="could not be read"):
        QualificationJsonCodec().load_report(tmp_path / "missing.json")
    with pytest.raises(ConfigurationValidationError):
        QualificationJsonCodec().decode_plan({"schema_version": 2})


def test_recovery_preserves_checkpoint_and_marks_unfinished_tests_not_tested(
    tmp_path: Path,
) -> None:
    plan, report = _report(tmp_path)
    repository = AtomicQualificationReportRepository()
    session = tmp_path / "session"
    session.mkdir()
    repository.initialize(session, plan)  # type: ignore[arg-type]
    repository.checkpoint(session, report)  # type: ignore[arg-type]

    class Reader:
        def __init__(self) -> None:
            self.paths: list[Path] = []

        def recover_session(self, path: Path) -> object:
            self.paths.append(path)
            return object()

    reader = Reader()
    recovered = RecoverQualificationReport(repository, reader).recover(session)  # type: ignore[arg-type]
    assert reader.paths == [session]
    assert recovered.recovered_from_checkpoint
    assert recovered.lifecycle.value == "recovered"
    assert recovered.results[0] == report.results[0]  # type: ignore[attr-defined]
    assert all(result.status is QualificationStatus.NOT_TESTED for result in recovered.results[1:])
    assert repository.load(session / "qualification-report.json") == recovered


def test_file_and_interactive_prompt_adapters_are_explicit() -> None:
    confirmation = OperatorConfirmation("ready", True, NOW, "tester", "ready")
    prompt = ConfirmationFilePrompt(ConfirmationDocument(1, (confirmation,)))
    assert prompt.obtain("ready", "statement", requested_at=NOW) == confirmation
    assert prompt.obtain("absent", "statement", requested_at=NOW) is None
    assert ConfirmationFilePrompt().obtain("absent", "statement", requested_at=NOW) is None

    messages: list[str] = []
    yes = InteractiveOperatorPrompt(
        operator="tester",
        input_function=lambda _: "yes",
        output_function=messages.append,
    )
    received = yes.obtain("ready", "Apply fixture", requested_at=NOW)
    assert received is not None and received.confirmed
    assert messages == ["Apply fixture"]
    no = InteractiveOperatorPrompt(
        operator="tester",
        input_function=lambda _: "no",
        output_function=messages.append,
    )
    assert no.obtain("ready", "Do not apply", requested_at=NOW) is None

    example = QualificationJsonCodec().load_confirmations(
        Path("docs/examples/qualification-confirmations-v1.json")
    )
    assert example.confirmations[0].confirmation_id == "charge-conditioning-compatible"
