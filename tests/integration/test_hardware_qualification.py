import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from labdaq.adapters.configuration_json import JsonConfigurationAdapter
from labdaq.adapters.qualification_json import QualificationJsonCodec
from labdaq.adapters.simulated.scenario import (
    JsonSimulatorScenarioCodec,
    SourceIndexFault,
)
from labdaq.adapters.tdms import create_tdms_session_store
from labdaq.application.qualification_service import (
    AttachQualificationToSession,
    BuildQualificationPlan,
)
from labdaq.application.session_service import RecordingSessionService
from labdaq.bootstrap import create_qualification_runner, load_qualification_plan
from labdaq.domain.enums import SamplingMode
from labdaq.domain.models import ExperimentConfig, HardwareProfile
from labdaq.domain.qualification import ConfirmationDocument, OperatorConfirmation
from labdaq.errors import QualificationError
from labdaq.ports.session import SessionType


def _configuration(tmp_path: Path) -> tuple[HardwareProfile, ExperimentConfig, object]:
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
    recipe = documents.load_bundled_processing_recipe("baseline-v1")
    return profile, experiment, recipe


def _confirmations() -> ConfirmationDocument:
    ids = (
        "charge-conditioning-compatible",
        "stimulus-axis-x",
        "stimulus-axis-y",
        "stimulus-axis-z",
        "known-common-input",
        "crosstalk-fixture-applied",
    )
    return ConfirmationDocument(
        1,
        tuple(
            OperatorConfirmation(item, True, datetime.now(UTC), "tester", "synthetic fixture")
            for item in ids
        ),
    )


def test_full_simulated_qualification_records_replays_and_attaches_exact_report(
    tmp_path: Path,
) -> None:
    profile, experiment, recipe = _configuration(tmp_path)
    plan = BuildQualificationPlan().build(
        load_qualification_plan("full-v1"),
        profile,
        experiment,
        throughput_duration_seconds=0.02,
    )
    runner = create_qualification_runner(
        backend="simulated",
        scenario_path=Path(
            "src/labdaq/resources/config/simulator-scenarios/qualification-full-healthy-v1.json"
        ),
        confirmations=_confirmations(),
    )
    try:
        outcome = runner.run(plan, profile, experiment, recipe)  # type: ignore[arg-type]
    finally:
        runner.close()

    assert outcome.report.lifecycle.value == "completed"
    assert outcome.report.overall_status.value == "warning"
    assert outcome.eligibility.eligible
    assert outcome.session.persisted_samples_per_channel == 1_401_000
    assert outcome.report.sample_counts.persisted_per_channel == (1_401_000,) * 4
    assert outcome.report.sample_counts.replayed_per_channel == (1_401_000,) * 4
    assert outcome.report.telemetry.read_count > 0
    assert outcome.report.telemetry.recording_queue_high_water_chunks > 0
    assert (outcome.session.session_path / "qualification-plan.json").is_file()
    assert (outcome.session.session_path / "qualification-report.json").is_file()
    evidence_paths = {item.relative_path for item in outcome.report.evidence}
    assert "plots/qualification-powered-idle.png" in evidence_paths
    assert "plots/qualification-powered-idle-psd.png" in evidence_paths
    assert any(path.startswith("plots/export-manifest-") for path in evidence_paths)
    for relative_path in evidence_paths:
        assert (outcome.session.session_path / relative_path).is_file()
    manifest = json.loads((outcome.session.session_path / "manifest.json").read_text())
    assert manifest["session"]["session_type"] == "hardware-qualification"
    assert QualificationJsonCodec().load_report(outcome.report_path) == outcome.report
    assisted = {result.test_id: result for result in outcome.report.results if result.confirmations}
    assert assisted["axis-response-x"].confirmations[0].sample_index == 700_000
    assert assisted["cross-channel-alignment"].confirmations[0].sample_index == 1_200_000
    assert (
        next(
            metric
            for metric in assisted["cross-channel-alignment"].metrics
            if metric.name == "lag-samples"
        ).value
        == 0.0
    )

    attachment = AttachQualificationToSession().build_attachment(
        outcome.report_path,
        outcome.report,
        outcome.eligibility,
    )
    expected_bytes = outcome.report_path.read_bytes()
    assert attachment.report_sha256 == hashlib.sha256(expected_bytes).hexdigest()

    next_experiment = replace(
        experiment,
        acquisition=replace(
            experiment.acquisition,
            sampling_mode=SamplingMode.FINITE,
            requested_duration_seconds=0.02,
        ),
        session=replace(
            experiment.session,
            experiment_name="Qualified experiment",
            experiment_slug="qualified-experiment",
        ),
    )
    from labdaq.adapters.simulated.adapter import SimulatedDaqAdapter

    scenario = JsonSimulatorScenarioCodec().load_bundled("clean-four-channel")
    store = create_tdms_session_store()
    recording = RecordingSessionService(SimulatedDaqAdapter(scenario), store, store, store)
    try:
        session_plan = recording.preview(next_experiment.session)
        recording.prepare(
            session_plan,
            profile,
            next_experiment,
            recipe,  # type: ignore[arg-type]
            session_type=SessionType.EXPERIMENT,
            qualification_attachment=attachment,
        )
        recording.start()
        completed = recording.wait_for_terminal(10.0)
    finally:
        recording.close()
    assert completed.persisted_samples_per_channel == 1000
    copied = completed.session_path / "qualification-report.json"
    assert copied.read_bytes() == expected_bytes
    attached_manifest = json.loads((completed.session_path / "manifest.json").read_text())
    assert attached_manifest["qualification"]["report_id"] == outcome.report.report_id
    assert attached_manifest["qualification"]["eligible"] is True


def test_commissioning_preflight_completes_without_missing_definition(
    tmp_path: Path,
) -> None:
    documents = JsonConfigurationAdapter()
    experiment = documents.decode_experiment_config(
        documents.load_bundled_experiment_template("accelerometer-commissioning-v2")
    )
    experiment = replace(
        experiment,
        session=replace(experiment.session, parent_output_directory=tmp_path),
    )
    profile = documents.load_bundled_hardware_profile(
        experiment.hardware_profile_id,
        experiment.compatible_profile_revision,
    )
    recipe = documents.load_bundled_processing_recipe("baseline-v1")
    plan = BuildQualificationPlan().build(
        load_qualification_plan("preflight-v1"),
        profile,
        experiment,
    )
    plan = replace(plan, powered_idle_duration_seconds=0.02)
    runner = create_qualification_runner(
        backend="simulated",
        scenario_path=Path(
            "src/labdaq/resources/config/simulator-scenarios/clean-three-axis-v1.json"
        ),
    )
    try:
        outcome = runner.run(plan, profile, experiment, recipe)
    finally:
        runner.close()

    assert outcome.report.lifecycle.value == "completed"
    assert outcome.eligibility.eligible
    assert tuple(result.test_id for result in outcome.report.results) == (
        "discovery-identity",
        "capability-configuration",
        "self-test-lifecycle",
        "powered-idle",
        "accelerometer-bias",
    )


def test_source_index_gap_fault_is_blocking_and_keeps_recoverable_evidence(
    tmp_path: Path,
) -> None:
    profile, experiment, recipe = _configuration(tmp_path)
    scenario_codec = JsonSimulatorScenarioCodec()
    scenario = scenario_codec.load_bundled("clean-four-channel")
    scenario = replace(
        scenario,
        scenario_id="source-gap",
        fault_schedule=(
            SourceIndexFault(
                fault_id="gap",
                read_sequence=1,
                offset_samples=100,
                mode="gap",
            ),
        ),
    )
    scenario_path = tmp_path / "source-gap.json"
    scenario_path.write_text(scenario_codec.dumps(scenario), encoding="utf-8")
    plan = BuildQualificationPlan().build(
        load_qualification_plan("throughput-v1"),
        profile,
        experiment,
        throughput_duration_seconds=0.2,
    )
    runner = create_qualification_runner(
        backend="simulated",
        scenario_path=scenario_path,
    )
    try:
        outcome = runner.run(plan, profile, experiment, recipe)  # type: ignore[arg-type]
    finally:
        runner.close()

    assert not outcome.eligibility.eligible
    assert outcome.report.overall_status.value == "fail"
    throughput = next(
        result for result in outcome.report.results if result.test_id == "sustained-throughput"
    )
    assert "DAQ.QUALIFICATION.THROUGHPUT_GAP_DETECTED" in throughput.reason_codes
    assert outcome.report.sample_counts.missing == 100
    assert (outcome.session.session_path / "raw.h5").is_file()
    assert (outcome.session.session_path / "events.jsonl").is_file()


def test_legacy_unequal_channel_fixture_names_blocking_reason() -> None:
    fixture = json.loads(
        Path("tests/fixtures/qualification/legacy-unequal-channel-counts-v1.json").read_text()
    )
    assert len(set(fixture["ordered_channel_counts"])) == 2
    assert fixture["expected_reason_code"] == "DAQ.QUALIFICATION.THROUGHPUT_CHANNEL_LENGTH_MISMATCH"


def test_cancellation_finalizes_raw_evidence_and_marks_remaining_tests_not_tested(
    tmp_path: Path,
) -> None:
    profile, experiment, recipe = _configuration(tmp_path)
    codec = JsonSimulatorScenarioCodec()
    scenario = replace(
        codec.load_bundled("clean-four-channel"),
        scenario_id="qualification-cancellation",
        pacing_mode="real_time",
    )
    scenario_path = tmp_path / "cancellation.json"
    scenario_path.write_text(codec.dumps(scenario), encoding="utf-8")
    plan = BuildQualificationPlan().build(
        load_qualification_plan("throughput-v1"),
        profile,
        experiment,
        throughput_duration_seconds=2.0,
    )
    runner = create_qualification_runner(
        backend="simulated",
        scenario_path=scenario_path,
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(runner.run, plan, profile, experiment, recipe)
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                try:
                    runner.cancel()
                    break
                except QualificationError as cause:
                    if future.done():
                        raise AssertionError(
                            "qualification finished before cancellation"
                        ) from cause
                time.sleep(0.01)
            else:
                raise AssertionError("qualification did not enter recording state")
            outcome = future.result(timeout=10.0)
    finally:
        runner.close()

    assert outcome.report.lifecycle.value == "cancelled"
    assert not outcome.eligibility.eligible
    assert all(result.status.value == "not-tested" for result in outcome.report.results[2:])
    assert (outcome.session.session_path / "raw.h5").is_file()
    assert (outcome.session.session_path / "qualification-report.json").is_file()
