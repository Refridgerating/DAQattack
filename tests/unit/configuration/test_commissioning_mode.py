from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pytest

from labdaq.adapters.configuration_json import JsonConfigurationAdapter
from labdaq.adapters.simulated.adapter import SimulatedDaqAdapter
from labdaq.adapters.simulated.scenario import JsonSimulatorScenarioCodec
from labdaq.application.processing_service import CalibrationResolutionService
from labdaq.application.qualification_service import (
    BuildQualificationPlan,
    EvaluateHardwareCompatibility,
    _guided_prompt,
    _powered_idle_result,
)
from labdaq.bootstrap import load_qualification_plan
from labdaq.domain.enums import (
    CalibrationSourceKind,
    ChannelRole,
    ExperimentMode,
    Polarity,
    ProcessingDisposition,
)
from labdaq.domain.validation import validate_experiment_config
from labdaq.errors import ConfigurationValidationError, ProcessingError, QualificationError
from labdaq.processing.qualification import NumPyQualificationAnalysis


def test_commissioning_template_has_three_calibrated_axes_and_raw_ai3() -> None:
    documents = JsonConfigurationAdapter()
    profile = documents.load_bundled_hardware_profile("cdaq-9171-ni-9239-four-channel", 1)
    raw = documents.load_bundled_experiment_template("accelerometer-commissioning-v2")
    config = documents.decode_experiment_config(raw)

    assert config.schema_version == 2
    assert config.experiment_mode is ExperimentMode.ACCELEROMETER_COMMISSIONING
    assert config.acquisition.channel_ids == (
        "accel-x",
        "accel-y",
        "accel-z",
        "pcb-422e03-raw-diagnostic",
    )
    assert raw["conditioning_chains"] == []
    assert "REQUIRED" not in str(raw)
    assert tuple(item.sensitivity_value for item in config.calibrations) == (6.914, 6.791, 7.398)
    assert all(item.source_kind.value == "operator_transcribed" for item in config.calibrations)
    diagnostic = config.channels[3]
    assert diagnostic.channel_role is ChannelRole.DIAGNOSTIC_RAW_VOLTAGE
    assert diagnostic.processing_disposition is ProcessingDisposition.RAW_ONLY_DIAGNOSTIC
    assert diagnostic.calibration_id is None
    assert diagnostic.conditioning_chain_id is None
    assert diagnostic.engineering_unit.value == "V"
    assert validate_experiment_config(config, profile) == ()


def test_commissioning_simulator_acquires_four_raw_channels() -> None:
    documents = JsonConfigurationAdapter()
    profile = documents.load_bundled_hardware_profile("cdaq-9171-ni-9239-four-channel", 1)
    config = documents.decode_experiment_config(
        documents.load_bundled_experiment_template("accelerometer-commissioning-v2")
    )
    scenario = JsonSimulatorScenarioCodec().load_bundled("clean-three-axis")
    adapter = SimulatedDaqAdapter(scenario)

    validation = adapter.validate(profile, config)
    assert validation.accepted
    adapter.configure(config)
    adapter.start()
    result = adapter.read(32, 1.0)
    assert result.samples.shape == (4, 32)
    adapter.stop()
    adapter.close()


def test_commissioning_preflight_retains_executable_core_and_excludes_charge() -> None:
    documents = JsonConfigurationAdapter()
    profile = documents.load_bundled_hardware_profile("cdaq-9171-ni-9239-four-channel", 1)
    config = documents.decode_experiment_config(
        documents.load_bundled_experiment_template("accelerometer-commissioning-v2")
    )

    plan = BuildQualificationPlan().build(load_qualification_plan("preflight-v1"), profile, config)

    assert tuple(item.test_id for item in plan.tests) == (
        "discovery-identity",
        "capability-configuration",
        "self-test-lifecycle",
        "powered-idle",
        "accelerometer-bias",
    )
    assert all(
        prerequisite in {item.test_id for item in plan.tests}
        for item in plan.tests
        for prerequisite in item.prerequisites
    )


def test_commissioning_plan_reports_missing_runner_core_as_typed_error() -> None:
    documents = JsonConfigurationAdapter()
    profile = documents.load_bundled_hardware_profile("cdaq-9171-ni-9239-four-channel", 1)
    config = documents.decode_experiment_config(
        documents.load_bundled_experiment_template("accelerometer-commissioning-v2")
    )
    template = load_qualification_plan("preflight-v1")
    broken = replace(
        template,
        tests=tuple(item for item in template.tests if item.test_id != "discovery-identity"),
    )

    with pytest.raises(QualificationError) as captured:
        BuildQualificationPlan().build(broken, profile, config)

    assert captured.value.code == "DAQ.QUALIFICATION.PLAN_INVALID"
    assert captured.value.context["missing_core_test_ids"] == ("discovery-identity",)


def test_v1_configuration_defaults_to_production_mode() -> None:
    documents = JsonConfigurationAdapter()
    config = documents.load_experiment_config(
        Path("tests/fixtures/configuration/valid-four-channel-synthetic-v1.json")
    )
    assert config.experiment_mode is ExperimentMode.PRODUCTION
    assert all(
        channel.processing_disposition is ProcessingDisposition.CALIBRATED
        for channel in config.channels
    )


def test_production_rejects_raw_diagnostic_role() -> None:
    documents = JsonConfigurationAdapter()
    raw = documents.load_bundled_experiment_template("accelerometer-commissioning-v2")
    raw["experiment_mode"] = "production"
    config = documents.decode_experiment_config(raw)
    profile = documents.load_bundled_hardware_profile("cdaq-9171-ni-9239-four-channel", 1)

    issues = validate_experiment_config(config, profile)
    assert any("production" in issue.correction.lower() for issue in issues)


def test_schema_v2_requires_explicit_processing_disposition() -> None:
    documents = JsonConfigurationAdapter()
    raw = documents.load_bundled_experiment_template("accelerometer-commissioning-v2")
    del raw["channels"][0]["processing_disposition"]

    with pytest.raises(ConfigurationValidationError, match="processing_disposition"):
        documents.decode_experiment_config(raw)


def test_schema_v2_rejects_unknown_experiment_mode() -> None:
    documents = JsonConfigurationAdapter()
    raw = documents.load_bundled_experiment_template("accelerometer-commissioning-v2")
    raw["experiment_mode"] = "diagnostic-production"

    with pytest.raises(ConfigurationValidationError, match="experiment_mode"):
        documents.decode_experiment_config(raw)


def test_three_channel_commissioning_remains_valid_without_optional_diagnostic() -> None:
    documents = JsonConfigurationAdapter()
    raw = documents.load_bundled_experiment_template("accelerometer-commissioning-v2")
    raw["channels"] = raw["channels"][:3]
    raw["acquisition"]["channel_ids"] = ["accel-x", "accel-y", "accel-z"]
    raw["sensors"] = raw["sensors"][:1]
    raw["coordinate_frames"] = raw["coordinate_frames"][:1]
    config = documents.decode_experiment_config(raw)
    profile = documents.load_bundled_hardware_profile("cdaq-9171-ni-9239-four-channel", 1)

    assert validate_experiment_config(config, profile) == ()


def test_raw_diagnostic_rejects_calibration_or_charge_interpretation() -> None:
    documents = JsonConfigurationAdapter()
    config = documents.decode_experiment_config(
        documents.load_bundled_experiment_template("accelerometer-commissioning-v2")
    )
    profile = documents.load_bundled_hardware_profile("cdaq-9171-ni-9239-four-channel", 1)
    diagnostic = replace(
        config.channels[3],
        calibration_id=config.calibrations[0].calibration_id,
        engineering_unit=config.calibrations[0].output_unit,
    )

    issues = validate_experiment_config(
        replace(config, channels=(*config.channels[:3], diagnostic)), profile
    )
    rules = " ".join(issue.rule for issue in issues)
    assert "cannot claim calibration" in rules
    assert "uninterpreted volts" in rules


def test_disposition_and_role_cannot_masquerade_as_each_other() -> None:
    documents = JsonConfigurationAdapter()
    config = documents.decode_experiment_config(
        documents.load_bundled_experiment_template("accelerometer-commissioning-v2")
    )
    profile = documents.load_bundled_hardware_profile("cdaq-9171-ni-9239-four-channel", 1)

    raw_axis = replace(
        config.channels[0],
        processing_disposition=ProcessingDisposition.RAW_ONLY_DIAGNOSTIC,
        calibration_id=None,
    )
    calibrated_diagnostic = replace(
        config.channels[3],
        processing_disposition=ProcessingDisposition.CALIBRATED,
        calibration_id=config.calibrations[0].calibration_id,
    )
    missing_calibration = replace(config.channels[0], calibration_id=None)

    raw_axis_rules = " ".join(
        issue.rule
        for issue in validate_experiment_config(
            replace(config, channels=(raw_axis, *config.channels[1:])), profile
        )
    )
    calibrated_diagnostic_rules = " ".join(
        issue.rule
        for issue in validate_experiment_config(
            replace(config, channels=(*config.channels[:3], calibrated_diagnostic)), profile
        )
    )
    missing_rules = " ".join(
        issue.rule
        for issue in validate_experiment_config(
            replace(config, channels=(missing_calibration, *config.channels[1:])), profile
        )
    )

    assert "reserved for diagnostic voltage" in raw_axis_rules
    assert "cannot enter calibrated processing" in calibrated_diagnostic_rules
    assert "calibration reference must resolve" in missing_rules


def test_commissioning_compatibility_warns_that_ai3_is_excluded() -> None:
    documents = JsonConfigurationAdapter()
    profile = documents.load_bundled_hardware_profile("cdaq-9171-ni-9239-four-channel", 1)
    config = documents.decode_experiment_config(
        documents.load_bundled_experiment_template("accelerometer-commissioning-v2")
    )
    scenario = JsonSimulatorScenarioCodec().load_bundled("clean-three-axis")
    validation = SimulatedDaqAdapter(scenario).validate(profile, config)

    compatibility = EvaluateHardwareCompatibility().evaluate(
        validation, config, backend_id="simulated"
    )

    assert compatibility.accepted
    assert compatibility.status.value == "warning"
    assert any("NOT CHARGE" in assumption for assumption in compatibility.assumptions)


def test_compatibility_rejects_charge_disguised_as_commissioning_diagnostic() -> None:
    documents = JsonConfigurationAdapter()
    profile = documents.load_bundled_hardware_profile("cdaq-9171-ni-9239-four-channel", 1)
    config = documents.decode_experiment_config(
        documents.load_bundled_experiment_template("accelerometer-commissioning-v2")
    )
    scenario = JsonSimulatorScenarioCodec().load_bundled("clean-three-axis")
    validation = SimulatedDaqAdapter(scenario).validate(profile, config)
    disguised = replace(config.channels[3], channel_role=ChannelRole.CONDITIONED_CHARGE)

    compatibility = EvaluateHardwareCompatibility().evaluate(
        validation,
        replace(config, channels=(*config.channels[:3], disguised)),
        backend_id="simulated",
    )

    assert not compatibility.accepted
    assert "DAQ.QUALIFICATION.CHARGE_CONDITIONING_INCOMPATIBLE" in (compatibility.reason_codes)


def test_physical_compatibility_keeps_incomplete_conditioner_not_tested() -> None:
    documents = JsonConfigurationAdapter()
    config = documents.load_experiment_config(
        Path("tests/fixtures/configuration/valid-four-channel-synthetic-v1.json")
    )
    profile = documents.load_bundled_hardware_profile(
        config.hardware_profile_id, config.compatible_profile_revision
    )
    validation = SimulatedDaqAdapter(
        JsonSimulatorScenarioCodec().load_bundled("clean-four-channel")
    ).validate(profile, config)
    incomplete_chain = replace(
        config.conditioning_chains[0],
        charge_converter=None,
        ni_terminal_conditioning=None,
    )

    compatibility = EvaluateHardwareCompatibility().evaluate(
        validation,
        replace(config, conditioning_chains=(incomplete_chain,)),
        backend_id="ni",
    )

    assert not compatibility.accepted
    assert compatibility.status.value == "not-tested"
    assert compatibility.reason_codes == ("DAQ.QUALIFICATION.REQUIRED_TEST_NOT_RUN",)


def test_production_wiring_guards_reject_range_chain_and_axis_conflicts() -> None:
    documents = JsonConfigurationAdapter()
    config = documents.load_experiment_config(
        Path("tests/fixtures/configuration/valid-four-channel-synthetic-v1.json")
    )
    profile = documents.load_bundled_hardware_profile(
        config.hardware_profile_id, config.compatible_profile_revision
    )
    accel_x = replace(
        config.channels[0],
        requested_input_min=-11.0,
        conditioning_chain_id=config.conditioning_chains[0].conditioning_chain_id,
    )
    accel_y = replace(config.channels[1], sensor_output_axis=config.channels[0].sensor_output_axis)
    charge = replace(config.channels[3], conditioning_chain_id="missing-conditioner")

    issues = validate_experiment_config(
        replace(config, channels=(accel_x, accel_y, config.channels[2], charge)), profile
    )
    rules = " ".join(issue.rule for issue in issues)

    assert "hardware profile range" in rules
    assert "accelerometer channels do not use" in rules
    assert "conditioning-chain reference must resolve" in rules
    assert "sensor output axis may be used only once" in rules

    inverted_charge_calibration = replace(config.calibrations[3], polarity=Polarity.NEGATIVE)
    mismatch_issues = validate_experiment_config(
        replace(
            config,
            calibrations=(*config.calibrations[:3], inverted_charge_calibration),
        ),
        profile,
    )
    assert any("conditioning chain must match" in issue.rule for issue in mismatch_issues)


def test_production_rejects_three_axis_commissioning_roles() -> None:
    documents = JsonConfigurationAdapter()
    raw = documents.load_bundled_experiment_template("accelerometer-commissioning-v2")
    raw["experiment_mode"] = "production"
    config = documents.decode_experiment_config(raw)
    profile = documents.load_bundled_hardware_profile("cdaq-9171-ni-9239-four-channel", 1)

    assert any(
        "production" in f"{issue.rule} {issue.correction}".lower()
        for issue in validate_experiment_config(config, profile)
    )


def test_operator_transcription_retains_missing_date_and_timestamp() -> None:
    documents = JsonConfigurationAdapter()
    raw = documents.load_bundled_experiment_template("accelerometer-commissioning-v2")
    calibration = raw["calibrations"][0]

    assert calibration["source_kind"] == "operator_transcribed"
    assert calibration["effective_date"] is None
    assert isinstance(calibration["transcription_timestamp"], str)


def test_commissioning_provenance_survives_resolution_and_guided_prompt_mapping() -> None:
    documents = JsonConfigurationAdapter()
    profile = documents.load_bundled_hardware_profile("cdaq-9171-ni-9239-four-channel", 1)
    config = documents.decode_experiment_config(
        documents.load_bundled_experiment_template("accelerometer-commissioning-v2")
    )
    first = replace(
        config.calibrations[0],
        characterization_session_ids=("commissioning-run-1",),
        owner_approval="owner-review-2026-08-14",
    )
    resolved, _ = CalibrationResolutionService().resolve_experiment(
        profile,
        replace(config, calibrations=(first, *config.calibrations[1:])),
        config.acquisition.channel_ids,
    )

    assert resolved[0].quality.value == "unverified"
    assert resolved[0].source_kind.value == "operator_transcribed"
    assert _guided_prompt("axis-response-x", "X-axis response").startswith("Apply")
    assert "constant-current" in _guided_prompt("charge-conditioning", "Charge").lower()
    assert "active task" in _guided_prompt("crosstalk", "Crosstalk")


def test_experiment_calibration_resolution_rejects_unknown_channel() -> None:
    documents = JsonConfigurationAdapter()
    profile = documents.load_bundled_hardware_profile("cdaq-9171-ni-9239-four-channel", 1)
    config = documents.decode_experiment_config(
        documents.load_bundled_experiment_template("accelerometer-commissioning-v2")
    )

    with pytest.raises(ProcessingError, match="could not be resolved"):
        CalibrationResolutionService().resolve_experiment(profile, config, ("missing-diagnostic",))


def test_incomplete_provenance_is_restricted_to_operator_transcription() -> None:
    documents = JsonConfigurationAdapter()
    config = documents.decode_experiment_config(
        documents.load_bundled_experiment_template("accelerometer-commissioning-v2")
    )
    with pytest.raises(ValueError, match="transcription_timestamp"):
        replace(config.calibrations[0], transcription_timestamp=None)
    with pytest.raises(ValueError, match="only valid"):
        replace(
            config.calibrations[0],
            source_kind=CalibrationSourceKind.CERTIFICATE,
            effective_date=date(2026, 8, 14),
            transcription_timestamp=None,
            owner_approval="should-not-be-here",
        )


def test_powered_idle_blocks_nonfinite_raw_ai3_without_charge_interpretation() -> None:
    documents = JsonConfigurationAdapter()
    profile = documents.load_bundled_hardware_profile("cdaq-9171-ni-9239-four-channel", 1)
    config = documents.decode_experiment_config(
        documents.load_bundled_experiment_template("accelerometer-commissioning-v2")
    )
    plan = BuildQualificationPlan().build(load_qualification_plan("full-v1"), profile, config)
    samples = np.zeros((4, 100), dtype=np.float64)
    samples[3, 12] = np.nan

    result = _powered_idle_result(
        plan, config, samples, NumPyQualificationAnalysis(), datetime.now(UTC)
    )

    assert result.status.value == "fail"
    assert "DAQ.QUALIFICATION.CHANNEL_NONFINITE_SAMPLE" in result.reason_codes
    diagnostic_metrics = [
        metric for metric in result.metrics if metric.channel_id == "pcb-422e03-raw-diagnostic"
    ]
    assert diagnostic_metrics
    assert all(metric.signal_stage.value == "raw" for metric in diagnostic_metrics)
