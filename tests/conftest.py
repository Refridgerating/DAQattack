from pathlib import Path

import pytest

from labdaq.adapters.configuration_json import JsonConfigurationAdapter


@pytest.fixture
def synthetic_experiment() -> object:
    return JsonConfigurationAdapter().load_experiment_config(
        Path("tests/fixtures/configuration/valid-four-channel-synthetic-v1.json")
    )


@pytest.fixture
def baseline_profile() -> object:
    return JsonConfigurationAdapter().load_bundled_hardware_profile(
        "cdaq-9171-ni-9239-four-channel",
        1,
    )
