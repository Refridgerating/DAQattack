import json
from copy import deepcopy
from importlib.resources import files
from pathlib import Path

import pytest

from labdaq.adapters.configuration_json import JsonConfigurationAdapter
from labdaq.application.configuration_service import ConfigurationService
from labdaq.errors import (
    ConfigurationError,
    ConfigurationValidationError,
    UnsupportedSchemaVersionError,
)

FIXTURE = Path("tests/fixtures/configuration/valid-four-channel-synthetic-v1.json")
INVALID_FIXTURES = Path("tests/fixtures/configuration/invalid")


def load_valid_payload() -> dict[str, object]:
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def write_payload(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(payload, allow_nan=True), encoding="utf-8")
    return path


def apply_invalid_fixture(payload: dict[str, object], fixture: Path) -> None:
    mutation = json.loads(fixture.read_text(encoding="utf-8"))
    assert isinstance(mutation, dict)
    path = mutation["path"]
    assert isinstance(path, list) and path

    def resolve(parts: list[object]) -> object:
        current: object = payload
        for part in parts:
            if isinstance(part, int):
                assert isinstance(current, list)
                current = current[part]
            else:
                assert isinstance(part, str) and isinstance(current, dict)
                current = current[part]
        return current

    parent = resolve(path[:-1]) if len(path) > 1 else payload
    key = path[-1]
    if mutation.get("delete") is True:
        assert isinstance(parent, dict) and isinstance(key, str)
        del parent[key]
    elif "copy_from" in mutation:
        source = mutation["copy_from"]
        assert isinstance(source, list)
        value = deepcopy(resolve(source))
        if isinstance(parent, list):
            assert isinstance(key, int)
            parent[key] = value
        else:
            assert isinstance(parent, dict) and isinstance(key, str)
            parent[key] = value
    else:
        value = mutation.get("value")
        if isinstance(parent, list):
            assert isinstance(key, int)
            parent[key] = value
        else:
            assert isinstance(parent, dict) and isinstance(key, str)
            parent[key] = value


def test_all_document_types_round_trip_without_information_loss() -> None:
    adapter = JsonConfigurationAdapter()
    profile = adapter.load_bundled_hardware_profile(
        "cdaq-9171-ni-9239-four-channel",
        1,
    )
    experiment = adapter.load_experiment_config(FIXTURE)
    recipe_resource = files("labdaq.resources").joinpath(
        "config", "processing-recipes", "baseline-v1-draft.json"
    )
    recipe_payload = recipe_resource.read_text(encoding="utf-8")
    recipe = adapter.loads(recipe_payload, "processing_recipe")

    for document, document_type in (
        (profile, "hardware_profile"),
        (experiment, "experiment_config"),
        (recipe, "processing_recipe"),
    ):
        assert adapter.loads(adapter.dumps(document), document_type) == document


def test_revision_two_profile_and_low_rate_recipe_are_bundled() -> None:
    adapter = JsonConfigurationAdapter()
    legacy = adapter.load_bundled_hardware_profile("cdaq-9171-ni-9239-four-channel", 1)
    variable = adapter.load_bundled_hardware_profile("cdaq-9171-ni-9239-four-channel", 2)
    recipe = adapter.load_bundled_processing_recipe("baseline-low-rate-v1")
    template = adapter.load_bundled_experiment_template("four-channel-vibration-synthetic-v2")

    assert legacy.application_supported_sample_rates_hz == (50_000.0,)
    assert variable.application_supported_sample_rates_hz == tuple(
        50_000.0 / divisor for divisor in range(1, 32)
    )
    assert recipe.recipe_id == "baseline-low-rate"
    assert [stage.cutoff_hz for stage in recipe.stages] == [None, 1.0]
    assert template["compatible_profile_revision"] == 2


def test_paths_dates_enums_and_channel_order_survive_round_trip() -> None:
    adapter = JsonConfigurationAdapter()
    config = adapter.load_experiment_config(FIXTURE)
    reconstructed = adapter.loads(adapter.dumps(config), "experiment_config")

    assert reconstructed == config
    assert str(config.session.parent_output_directory) == r"C:\Synthetic\LabDAQ"
    assert config.calibrations[0].effective_date.isoformat() == "2026-01-01"
    assert [channel.channel_id for channel in config.channels] == [
        "accel-x",
        "accel-y",
        "accel-z",
        "charge-signal",
    ]


def test_missing_and_future_schema_versions_fail_before_construction() -> None:
    adapter = JsonConfigurationAdapter()
    for received in (None, 0, -1, "1", 2):
        payload = load_valid_payload()
        if received is None:
            del payload["schema_version"]
        else:
            payload["schema_version"] = received
        with pytest.raises(UnsupportedSchemaVersionError) as captured:
            adapter.decode_experiment_config(payload)
        assert captured.value.code == "DAQ.CONFIG.UNSUPPORTED_SCHEMA_VERSION"
        assert captured.value.to_dict()["context"]["supported_versions"] == [1]


@pytest.mark.parametrize(
    "invalid_fixture",
    sorted(INVALID_FIXTURES.glob("*.json")),
    ids=lambda path: path.stem,
)
def test_major_invalid_configuration_categories_are_rejected(
    tmp_path: Path,
    invalid_fixture: Path,
) -> None:
    payload = load_valid_payload()
    apply_invalid_fixture(payload, invalid_fixture)
    service = ConfigurationService(JsonConfigurationAdapter())

    with pytest.raises(ConfigurationError):
        service.validate_experiment_file(write_payload(tmp_path, payload))


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("channels", 2, "enabled"), False),
        (("acquisition", "sample_rate_hz"), float("nan")),
    ],
)
def test_additional_invalid_numeric_and_enablement_cases(
    tmp_path: Path,
    path: tuple[object, ...],
    value: object,
) -> None:
    payload = load_valid_payload()
    parent: object = payload
    for part in path[:-1]:
        parent = parent[part]  # type: ignore[index]
    parent[path[-1]] = value  # type: ignore[index]

    with pytest.raises(ConfigurationValidationError):
        ConfigurationService(JsonConfigurationAdapter()).validate_experiment_file(
            write_payload(tmp_path, payload)
        )


def test_duplicate_physical_channel_error_names_both_channel_ids(tmp_path: Path) -> None:
    payload = load_valid_payload()
    channels = payload["channels"]
    assert isinstance(channels, list)
    first = channels[0]
    second = channels[1]
    assert isinstance(first, dict) and isinstance(second, dict)
    second["physical_channel"] = deepcopy(first["physical_channel"])

    with pytest.raises(ConfigurationValidationError) as captured:
        ConfigurationService(JsonConfigurationAdapter()).validate_experiment_file(
            write_payload(tmp_path, payload)
        )

    context = captured.value.to_dict()["context"]
    assert "accel-x" in str(context)
    assert "accel-y" in str(context)


def test_unknown_properties_and_unknown_profile_are_rejected(tmp_path: Path) -> None:
    payload = load_valid_payload()
    payload["unexpected"] = True
    with pytest.raises(ConfigurationValidationError):
        JsonConfigurationAdapter().load_experiment_config(write_payload(tmp_path, payload))

    payload = load_valid_payload()
    payload["hardware_profile_id"] = "unknown-profile"
    with pytest.raises(ConfigurationValidationError, match="not found"):
        ConfigurationService(JsonConfigurationAdapter()).validate_experiment_file(
            write_payload(tmp_path, payload)
        )


def test_invalid_json_and_unreadable_path_are_structured(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    adapter = JsonConfigurationAdapter()

    with pytest.raises(ConfigurationValidationError) as invalid_json:
        adapter.load_experiment_config(invalid)
    assert invalid_json.value.cause is not None
    with pytest.raises(ConfigurationValidationError) as unreadable:
        adapter.load_experiment_config(tmp_path / "missing.json")
    assert unreadable.value.cause is not None


def test_operator_template_is_intentionally_blocked() -> None:
    template = Path(
        "src/labdaq/resources/config/experiments/four-channel-vibration-template-v1.json"
    )
    with pytest.raises(ConfigurationValidationError) as captured:
        JsonConfigurationAdapter().load_experiment_config(template)

    context = captured.value.to_dict()["context"]
    assert "sensitivity_value" in str(context)
    assert "source_reference" in str(context)
    assert "calibration_reference" in str(context)
