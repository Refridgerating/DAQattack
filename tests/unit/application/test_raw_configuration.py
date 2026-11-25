"""Raw monitor configuration has no invented sensor or calibration records."""

import pytest

from labdaq.adapters.configuration_json import JsonConfigurationAdapter
from labdaq.application.configuration_workspace import ConfigurationWorkspaceService
from labdaq.domain.enums import ProcessingDisposition


@pytest.mark.parametrize("revision", [1, 2])
def test_raw_template_accepts_selected_channels_without_sensor_metadata(revision: int) -> None:
    documents = JsonConfigurationAdapter()
    profile = documents.load_bundled_hardware_profile("cdaq-9171-ni-9239-four-channel", revision)
    workspace = ConfigurationWorkspaceService(
        documents, active_recipe_id="baseline", active_recipe_version="v1"
    )
    snapshot = workspace.create_from_template(profile, f"raw-voltage-v{revision}")
    assert snapshot.valid
    assert snapshot.normalized is not None
    assert not snapshot.normalized.sensors
    assert not snapshot.normalized.calibrations
    assert all(
        channel.sensor_id is None
        and channel.processing_disposition is ProcessingDisposition.RAW_ONLY_DIAGNOSTIC
        for channel in snapshot.normalized.channels
    )

    for index in (0, 2, 3):
        snapshot = workspace.update_field(f"channels[{index}].enabled", False)
    assert snapshot.valid
    assert snapshot.normalized is not None
    assert snapshot.normalized.acquisition.channel_ids == ("ai1",)

    snapshot = workspace.update_field("channels[1].requested_input_max", 11.0)
    assert not snapshot.valid
    assert any("requested_input_min" in issue.field_path for issue in snapshot.issues)
