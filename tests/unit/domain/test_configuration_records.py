from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from labdaq.adapters.configuration_json import JsonConfigurationAdapter
from labdaq.domain import (
    AcquisitionConfig,
    DataChunkConfig,
    SamplingMode,
    TerminalConfiguration,
    TriggerConfig,
    TriggerEdge,
    TriggerMode,
    Unit,
    make_slug,
    validate_identifier,
    validate_slug,
)
from labdaq.domain.validation import validate_experiment_config


def test_loaded_records_are_frozen_value_objects_and_preserve_order() -> None:
    adapter = JsonConfigurationAdapter()
    profile = adapter.load_bundled_hardware_profile(
        "cdaq-9171-ni-9239-four-channel",
        1,
    )
    reconstructed = adapter.loads(adapter.dumps(profile), "hardware_profile")

    assert reconstructed == profile
    assert isinstance(profile.devices, tuple)
    assert [channel.channel_index for channel in profile.supported_physical_channels] == [
        0,
        1,
        2,
        3,
    ]
    with pytest.raises(FrozenInstanceError):
        profile.profile_revision = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    "identifier",
    ["", "UPPER", "has space", "../escape", "slash/name", "-leading", "trailing-"],
)
def test_identifier_rejects_malformed_values(identifier: str) -> None:
    with pytest.raises(ValueError):
        validate_identifier(identifier)


@pytest.mark.parametrize(
    "slug",
    [
        "",
        "CON",
        "con",
        "com1",
        "lpt9",
        "../escape",
        "a/b",
        "a\\b",
        "two--hyphens",
        " leading",
        "trailing ",
        "trailing.",
        "x" * 65,
    ],
)
def test_slug_rejects_unsafe_windows_filename_components(slug: str) -> None:
    with pytest.raises(ValueError):
        validate_slug(slug)


def test_slug_construction_is_explicit_and_deterministic() -> None:
    assert make_slug("  Test Résumé / Run #1  ") == "test-resume-run-1"
    with pytest.raises(ValueError):
        make_slug("***")


def test_controlled_units_reject_unrecognized_free_text() -> None:
    assert Unit("m/s^2") is Unit.METRES_PER_SECOND_SQUARED
    with pytest.raises(ValueError):
        Unit("meters/sec2")


def test_trigger_rules_reject_contradictions() -> None:
    assert TriggerConfig(mode=TriggerMode.IMMEDIATE) == TriggerConfig(mode=TriggerMode.IMMEDIATE)
    digital = TriggerConfig(
        mode=TriggerMode.DIGITAL_EDGE,
        source="pfi0",
        edge=TriggerEdge.RISING,
    )
    assert digital.edge is TriggerEdge.RISING

    with pytest.raises(ValueError, match="immediate trigger"):
        TriggerConfig(mode=TriggerMode.IMMEDIATE, source="pfi0")
    with pytest.raises(ValueError, match="requires source and edge"):
        TriggerConfig(mode=TriggerMode.DIGITAL_EDGE, source="pfi0")
    with pytest.raises(ValueError, match="non-negative"):
        TriggerConfig(mode=TriggerMode.IMMEDIATE, pretrigger_samples=-1)


def test_duration_and_chunk_rules_are_exact() -> None:
    chunk = DataChunkConfig(samples_per_channel=5000, read_timeout_seconds=2.0)
    finite = AcquisitionConfig(
        sample_rate_hz=50000.0,
        sampling_mode=SamplingMode.FINITE,
        requested_duration_seconds=0.1,
        channel_ids=("a",),
        trigger=TriggerConfig(mode=TriggerMode.IMMEDIATE),
        chunk_config=chunk,
    )
    continuous = replace(
        finite,
        sampling_mode=SamplingMode.CONTINUOUS,
        requested_duration_seconds=None,
    )
    assert continuous.requested_duration_seconds is None

    assert replace(finite, requested_duration_seconds=0.00001).requested_duration_seconds == 0.00001
    with pytest.raises(ValueError, match="at least one sample"):
        replace(finite, requested_duration_seconds=0.000009)
    with pytest.raises(ValueError, match="requires"):
        replace(finite, requested_duration_seconds=None)
    with pytest.raises(ValueError, match="forbids"):
        replace(continuous, requested_duration_seconds=1.0)
    with pytest.raises(ValueError, match="positive integer"):
        replace(chunk, samples_per_channel=0)
    with pytest.raises(ValueError, match="finite"):
        replace(chunk, read_timeout_seconds=float("inf"))


def test_coordinate_matrix_orthonormality_is_semantic() -> None:
    adapter = JsonConfigurationAdapter()
    fixture = Path("tests/fixtures/configuration/valid-four-channel-synthetic-v1.json")
    config = adapter.load_experiment_config(fixture)
    profile = adapter.load_bundled_hardware_profile(config.hardware_profile_id, 1)
    bad_frame = replace(
        config.coordinate_frames[0],
        direction_cosine_matrix=((1.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    )
    issues = validate_experiment_config(
        replace(config, coordinate_frames=(bad_frame, config.coordinate_frames[1])),
        profile,
    )

    assert any("orthogonal" in issue.rule for issue in issues)


def test_calibration_dates_are_typed_and_ordered() -> None:
    config = JsonConfigurationAdapter().load_experiment_config(
        Path("tests/fixtures/configuration/valid-four-channel-synthetic-v1.json")
    )
    calibration = config.calibrations[0]

    with pytest.raises(TypeError, match="datetime.date"):
        replace(calibration, effective_date="2026-01-01")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="must not precede"):
        replace(
            calibration,
            expiration_date=calibration.effective_date.replace(year=2025),
        )


def test_physical_channel_is_plain_domain_data() -> None:
    adapter = JsonConfigurationAdapter()
    profile = adapter.load_bundled_hardware_profile(
        "cdaq-9171-ni-9239-four-channel",
        1,
    )
    channel = profile.supported_physical_channels[0]

    assert channel.module_id == "voltage-module"
    assert channel.channel_index == 0
    assert channel.terminal_configuration is TerminalConfiguration.DIFFERENTIAL
    assert all(
        "nidaq" not in str(field_type).casefold() for field_type in channel.__annotations__.values()
    )
