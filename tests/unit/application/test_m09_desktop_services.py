from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from labdaq.adapters.configuration_json import JsonConfigurationAdapter
from labdaq.adapters.filesystem import FileSystemCapacityAdapter
from labdaq.adapters.qualification_json import QualificationJsonCodec
from labdaq.adapters.simulated.adapter import SimulatedDaqAdapter
from labdaq.adapters.simulated.scenario import JsonSimulatorScenarioCodec
from labdaq.application.configuration_workspace import (
    ConfigurationWorkspaceService,
    field_affects_hardware_validation,
)
from labdaq.application.destination_service import SessionDestinationService
from labdaq.application.operator_messages import (
    operator_message_from_error,
    unexpected_operator_message,
)
from labdaq.application.qualification_service import BuildQualificationPlan
from labdaq.application.readiness_service import (
    RawDiagnosticConfirmation,
    StartReadinessService,
)
from labdaq.application.session_catalog_service import SessionCatalogService
from labdaq.errors import LabDAQError
from labdaq.storage.policy import SessionPathPolicy

if TYPE_CHECKING:
    from pathlib import Path


def test_hardware_validation_field_policy_exempts_operator_and_scientific_metadata() -> None:
    for path in (
        "experiment.name",
        "experiment.notes",
        "storage.parent_directory",
        "sensors[0].serial_number",
        "calibrations[0].sensitivity_value",
        "conditioning_chains[0].components",
        "channels[0].sensor_id",
        "channels[0].calibration_id",
    ):
        assert not field_affects_hardware_validation(path)

    for path in (
        "acquisition.sample_rate_hz",
        "acquisition.duration_s",
        "acquisition.chunk_size",
        "channels[0].enabled",
        "channels[0].channel_order",
        "channels[0].physical_channel.channel_index",
        "channels[0].requested_input_min",
    ):
        assert field_affects_hardware_validation(path)


def _workspace() -> tuple[ConfigurationWorkspaceService, object]:
    documents = JsonConfigurationAdapter()
    profile = documents.load_bundled_hardware_profile("cdaq-9171-ni-9239-four-channel", 1)
    workspace = ConfigurationWorkspaceService(
        documents,
        active_recipe_id="baseline",
        active_recipe_version="1.0.0",
    )
    return workspace, profile


def _variable_rate_workspace() -> tuple[ConfigurationWorkspaceService, object]:
    documents = JsonConfigurationAdapter()
    profile = documents.load_bundled_hardware_profile("cdaq-9171-ni-9239-four-channel", 2)
    workspace = ConfigurationWorkspaceService(
        documents,
        active_recipe_id="baseline",
        active_recipe_version="v1",
    )
    return workspace, profile


def test_synthetic_template_is_valid_and_invalid_raw_input_is_retained() -> None:
    workspace, profile = _workspace()
    draft = workspace.create_from_template(
        profile,  # type: ignore[arg-type]
        "four-channel-vibration-synthetic-v1",
    )

    assert draft.valid
    assert [item.status for item in draft.calibration_statuses] == ["synthetic"] * 4

    invalid = workspace.update_field("acquisition.sample_rate_hz", "operator-typo")

    assert not invalid.valid
    assert invalid.raw_fields["acquisition"]["sample_rate_hz"] == "operator-typo"  # type: ignore[index]
    assert any(item.field_path == "acquisition.sample_rate_hz" for item in invalid.issues)


def test_workspace_returns_explicit_slug_and_channel_order_normalization() -> None:
    workspace, profile = _workspace()
    workspace.create_from_template(
        profile,  # type: ignore[arg-type]
        "four-channel-vibration-synthetic-v1",
    )

    named = workspace.update_field("experiment.name", "Rotor Run #7")
    assert [(item.field_path, item.normalized_value) for item in named.normalizations] == [
        ("session.experiment_slug", "rotor-run-7")
    ]

    channels = workspace.update_field("channels[3].enabled", False)
    assert channels.normalizations[0].field_path == "acquisition.channel_ids"
    assert channels.normalizations[0].normalized_value == ("accel-x", "accel-y", "accel-z")


@pytest.mark.parametrize("divisor", range(1, 32))
def test_rate_change_atomically_selects_batch_and_recipe(divisor: int) -> None:
    workspace, profile = _variable_rate_workspace()
    workspace.create_from_template(
        profile,  # type: ignore[arg-type]
        "four-channel-vibration-synthetic-v2",
    )
    rate = 50_000.0 / divisor

    draft = workspace.update_sample_rate(rate)

    assert draft.valid
    assert draft.normalized is not None
    assert draft.normalized.acquisition.sample_rate_hz == rate
    assert draft.normalized.acquisition.chunk_config.samples_per_channel == int(rate * 0.1 + 0.5)
    assert draft.normalized.draft_processing_recipe_id == (
        "baseline" if rate >= 25_000.0 else "baseline-low-rate"
    )


def test_manual_batch_override_survives_until_next_rate_change() -> None:
    workspace, profile = _variable_rate_workspace()
    workspace.create_from_template(
        profile,  # type: ignore[arg-type]
        "four-channel-vibration-synthetic-v2",
    )

    overridden = workspace.update_field("acquisition.chunk_size", 777)
    assert overridden.normalized is not None
    assert overridden.normalized.acquisition.chunk_config.samples_per_channel == 777

    reset = workspace.update_sample_rate(50_000.0 / 3)
    assert reset.normalized is not None
    assert reset.normalized.acquisition.chunk_config.samples_per_channel == 1667
    assert reset.active_recipe_id == "baseline-low-rate"
    with pytest.raises(ValueError, match="not an allowed rate"):
        workspace.update_sample_rate(20_000.0)


def test_bound_qualification_uses_selected_rate_and_rate_chunk_fingerprint() -> None:
    workspace, profile = _variable_rate_workspace()
    initial = workspace.create_from_template(
        profile,  # type: ignore[arg-type]
        "four-channel-vibration-synthetic-v2",
    )
    changed = workspace.update_sample_rate(50_000.0 / 3)
    assert initial.normalized is not None and changed.normalized is not None
    template = QualificationJsonCodec().load_plan("preflight-v1")

    original_plan = BuildQualificationPlan().build(
        template,
        profile,  # type: ignore[arg-type]
        initial.normalized,
    )
    changed_plan = BuildQualificationPlan().build(
        template,
        profile,  # type: ignore[arg-type]
        changed.normalized,
    )

    assert changed_plan.acquisition_rate_hz == 50_000.0 / 3
    assert changed_plan.experiment_config_hash != original_plan.experiment_config_hash


def test_destination_preview_is_stable_nonmutating_and_collision_safe(tmp_path: Path) -> None:
    workspace, profile = _workspace()
    workspace.create_from_template(
        profile,  # type: ignore[arg-type]
        "four-channel-vibration-synthetic-v1",
    )
    draft = workspace.update_field("storage.parent_directory", str(tmp_path))
    assert draft.normalized is not None
    filesystem = FileSystemCapacityAdapter()
    service = SessionDestinationService(SessionPathPolicy(), filesystem, filesystem)

    first = service.preview(draft.normalized)
    second = service.preview(draft.normalized)

    assert first.plan == second.plan
    assert first.valid
    assert not first.plan.session_path.exists()

    first.plan.session_path.mkdir()
    collision = service.preview(draft.normalized)
    assert collision.collision
    assert not collision.valid

    service.refresh_identity()
    refreshed = service.preview(draft.normalized)
    assert refreshed.plan.session_id != first.plan.session_id
    assert refreshed.plan.session_path != first.plan.session_path
    assert refreshed.valid


class _Catalog:
    def __init__(self, paths: tuple[Path, ...]) -> None:
        self.paths = paths

    def discover_session_paths(self, parent: Path) -> tuple[Path, ...]:
        del parent
        return self.paths


class _Reader:
    def summarize_session(self, path: Path, *, level: object) -> object:
        del level
        if path.name == "bad":
            raise LabDAQError(
                "Manifest is corrupt.",
                code="DAQ.SESSION.CATALOG_ENTRY_INVALID",
                remediation="Inspect the manifest.",
            )
        age = 1 if path.name == "new" else 2
        return SimpleNamespace(started_at=datetime.now(UTC) - timedelta(days=age))


def test_catalog_sorts_valid_newest_first_and_retains_invalid_rows(tmp_path: Path) -> None:
    paths = tuple(tmp_path / name for name in ("old", "bad", "new"))
    catalog = SessionCatalogService(_Catalog(paths), _Reader()).scan(tmp_path)  # type: ignore[arg-type]

    assert [entry.session_path.name for entry in catalog.entries] == ["new", "old", "bad"]
    assert [entry.valid for entry in catalog.entries] == [True, True, False]
    assert catalog.entries[-1].error_code == "DAQ.SESSION.CATALOG_ENTRY_INVALID"


def test_error_mapping_preserves_typed_action_and_sanitizes_unexpected_failure() -> None:
    typed = operator_message_from_error(
        LabDAQError(
            "Choose a writable folder.",
            code="DAQ.STORAGE.INVALID_SESSION_PARENT",
            remediation="Select another folder.",
        )
    )
    unexpected = unexpected_operator_message()

    assert typed.code == "DAQ.STORAGE.INVALID_SESSION_PARENT"
    assert typed.remediation == "Select another folder."
    assert unexpected.code == "DAQ.GUI.UNEXPECTED_FAILURE"
    assert unexpected.correlation_id
    assert "traceback" not in unexpected.message.casefold()


def test_workspace_rejects_nonwhitelisted_paths() -> None:
    workspace, profile = _workspace()
    workspace.create_from_template(
        profile,  # type: ignore[arg-type]
        "four-channel-vibration-synthetic-v1",
    )

    with pytest.raises(ValueError, match="Unsupported configuration field path"):
        workspace.update_field("future.trigger.sequence", "unsafe")


def test_raw_diagnostic_confirmation_enforces_supply_and_ai3_guard_band() -> None:
    now = datetime.now(UTC)
    disconnected = RawDiagnosticConfirmation(
        operator="operator",
        confirmed_at=now,
        measured_supply_voltage_v=5.0,
        polarity_confirmed=True,
        ai3_connection="disconnected",
    )
    assert disconnected.ai3_observed_min_v is None

    connected = RawDiagnosticConfirmation(
        operator="operator",
        confirmed_at=now,
        measured_supply_voltage_v=5.0,
        polarity_confirmed=True,
        ai3_connection="connected-verified",
        ai3_observed_min_v=-0.2,
        ai3_observed_max_v=0.3,
        high_impedance_instrument_confirmed=True,
    )
    assert connected.ai3_connection == "connected-verified"

    with pytest.raises(ValueError, match="2.8 to 5.5"):
        replace(disconnected, measured_supply_voltage_v=5.6)
    with pytest.raises(ValueError, match="polarity"):
        replace(disconnected, polarity_confirmed=False)
    with pytest.raises(ValueError, match="guard band"):
        replace(connected, ai3_observed_max_v=8.1)


def test_raw_diagnostic_readiness_ignores_qualification_but_requires_current_hardware(
    tmp_path: Path,
) -> None:
    documents = JsonConfigurationAdapter()
    profile = documents.load_bundled_hardware_profile("cdaq-9171-ni-9239-four-channel", 1)
    workspace = ConfigurationWorkspaceService(
        documents,
        active_recipe_id="baseline",
        active_recipe_version="1.0.0",
    )
    workspace.create_from_template(profile, "accelerometer-commissioning-v2")
    draft = workspace.update_field("storage.parent_directory", str(tmp_path))
    assert draft.normalized is not None
    filesystem = FileSystemCapacityAdapter()
    destination = SessionDestinationService(SessionPathPolicy(), filesystem, filesystem).preview(
        draft.normalized
    )
    adapter = SimulatedDaqAdapter(JsonSimulatorScenarioCodec().load_bundled("clean-three-axis"))
    discovery = adapter.discover()
    validation = adapter.validate(profile, draft.normalized)
    readiness = StartReadinessService()

    missing = readiness.evaluate_raw_diagnostic(
        draft=draft,
        destination=destination,
        discovery=None,
        hardware_validation=None,
        live_available=True,
    )
    ready = readiness.evaluate_raw_diagnostic(
        draft=draft,
        destination=destination,
        discovery=discovery,
        hardware_validation=validation,
        live_available=True,
    )

    assert not missing.eligible
    assert {item.code for item in missing.blocking_reasons} == {"DAQ.HARDWARE.DISCOVERY_REQUIRED"}
    assert ready.eligible

    open_ready = readiness.evaluate_open_acquisition(
        draft=draft,
        discovery=discovery,
        hardware_validation=validation,
        live_available=True,
    )
    assert open_ready.eligible
    open_intent = readiness.request_open_acquisition_launch(
        open_ready,
        profile=profile,
        experiment=draft.normalized,
        hardware_validation=validation,
    )
    assert open_intent.authorization is None
    assert open_intent.recorded_channel_ids == draft.normalized.acquisition.channel_ids
    unavailable = readiness.evaluate_open_acquisition(
        draft=draft,
        discovery=None,
        hardware_validation=None,
        live_available=False,
    )
    assert not unavailable.eligible
    assert {issue.code for issue in unavailable.blocking_reasons} == {
        "DAQ.HARDWARE.DISCOVERY_REQUIRED",
        "DAQ.DEPENDENCY.UNAVAILABLE",
    }

    functional = readiness.request_functional_preview_launch(
        ready,
        profile=profile,
        experiment=draft.normalized,
        destination=destination,
        hardware_validation=validation,
    )
    assert functional.experiment.experiment_mode.value == "accelerometer_commissioning"
    assert functional.session_plan == destination.plan

    channel_ids = draft.normalized.acquisition.channel_ids
    channel_units = tuple(channel.raw_unit.value for channel in draft.normalized.channels)
    selected = channel_ids[0]
    intent = readiness.request_open_acquisition_launch(
        ready,
        profile=profile,
        experiment=draft.normalized,
        hardware_validation=validation,
        confirmation=RawDiagnosticConfirmation(
            operator="operator",
            confirmed_at=datetime.now(UTC),
            measured_supply_voltage_v=3.3,
            polarity_confirmed=True,
            ai3_connection="disconnected",
        ),
    )
    assert intent.selected_channel_id == selected
    assert intent.recorded_channel_ids == channel_ids
    assert intent.recorded_channel_units == channel_units
    assert intent.authorization.workflow == "open-acquisition"
    assert intent.authorization.selected_channel_id == selected
    assert intent.authorization.recorded_channel_ids == channel_ids
    assert intent.authorization.recorded_channel_units == channel_units
    assert intent.authorization.ordered_physical_channels == validation.ordered_physical_channels

    legacy = readiness.request_open_acquisition_launch(
        ready,
        profile=profile,
        experiment=draft.normalized,
        hardware_validation=validation,
        confirmation=intent.authorization.confirmation,
        selected_channel_id=channel_ids[1],
    )
    assert legacy.recorded_channel_ids == (channel_ids[1],)
    assert legacy.recorded_channel_units == (channel_units[1],)

    with pytest.raises(LabDAQError) as unavailable:
        readiness.request_open_acquisition_launch(
            missing,
            profile=profile,
            experiment=draft.normalized,
            hardware_validation=validation,
            confirmation=intent.authorization.confirmation,
            selected_channel_id=selected,
        )
    assert unavailable.value.code == "DAQ.APPLICATION.START_INELIGIBLE"

    with pytest.raises(LabDAQError) as invalid_channel:
        readiness.request_open_acquisition_launch(
            ready,
            profile=profile,
            experiment=draft.normalized,
            hardware_validation=validation,
            confirmation=intent.authorization.confirmation,
            selected_channel_id="not-enabled",
        )
    assert invalid_channel.value.code == "DAQ.APPLICATION.OPEN_CHANNEL_INVALID"
