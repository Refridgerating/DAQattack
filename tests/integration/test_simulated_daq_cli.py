import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from labdaq.adapters.configuration_json import JsonConfigurationAdapter
from labdaq.cli.main import EXIT_DAQ_FAILURE, main
from labdaq.domain.enums import SamplingMode
from labdaq.domain.models import ExperimentConfig

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
PROFILE = (
    ROOT
    / "src/labdaq/resources/config/hardware-profiles"
    / "cdaq-9171-ni-9239-four-channel-v1.json"
)
EXPERIMENT = ROOT / "tests/fixtures/configuration/valid-four-channel-synthetic-v1.json"
SCENARIOS = ROOT / "src/labdaq/resources/config/simulator-scenarios"


@pytest.mark.parametrize("action", ["list", "inspect"])
def test_hardware_discovery_cli_is_explicit_and_structured(
    action: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["hardware", action, "--backend", "simulated", "--json"]) == 0
    output = capsys.readouterr()
    payload = json.loads(output.out)

    assert output.err == ""
    assert payload["backend_id"] == "simulated"
    assert payload["devices"][0]["simulated"] is True
    assert payload["devices"][0]["modules"][0]["available_channels"][-1].endswith("ai3")


def test_hardware_validation_cli_reports_scenario_and_channel_order(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = main(
        [
            "hardware",
            "validate",
            "--backend",
            "simulated",
            "--profile",
            str(PROFILE),
            "--experiment",
            str(EXPERIMENT),
            "--scenario",
            str(SCENARIOS / "analytical-sine-reference-v1.json"),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["accepted"] is True
    assert payload["scenario_id"] == "analytical-sine-reference"
    assert payload["generator_version"] == "sim-v1"
    assert payload["ordered_logical_channels"] == [
        "accel-x",
        "accel-y",
        "accel-z",
        "charge-signal",
    ]


def test_bounded_acquisition_cli_reports_checksum_not_raw_samples(
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = [
        "hardware",
        "acquire-test",
        "--backend",
        "simulated",
        "--experiment",
        str(EXPERIMENT),
        "--scenario",
        str(SCENARIOS / "clean-four-channel-v1.json"),
        "--duration",
        "0.02",
        "--chunk-size",
        "257",
        "--json",
    ]
    assert main(arguments) == 0
    output = capsys.readouterr()
    payload = json.loads(output.out)

    assert payload["overall"] == "ok"
    assert payload["samples_per_channel"] == 1000
    assert payload["array_shape"] == [4, 1000]
    assert payload["chunk_count"] == 4
    assert len(payload["canonical_sha256"]) == 64
    assert "samples" not in payload
    assert payload["cleanup_succeeded"] is True


def test_faulted_cli_returns_daq_exit_with_partial_cleanup_evidence(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = main(
        [
            "hardware",
            "acquire-test",
            "--backend",
            "simulated",
            "--experiment",
            str(EXPERIMENT),
            "--scenario",
            str(SCENARIOS / "dropout-v1.json"),
            "--samples",
            "20000",
            "--chunk-size",
            "20000",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == EXIT_DAQ_FAILURE
    assert payload["overall"] == "error"
    assert payload["samples_per_channel"] == 12345
    assert payload["fault"]["code"] == "DAQ.ACQUISITION.SAMPLE_LOSS"
    assert payload["cleanup_succeeded"] is True


def test_continuous_cli_requires_explicit_bound(
    tmp_path: Path,
    synthetic_experiment: ExperimentConfig,
    capsys: pytest.CaptureFixture[str],
) -> None:
    continuous = replace(
        synthetic_experiment,
        acquisition=replace(
            synthetic_experiment.acquisition,
            sampling_mode=SamplingMode.CONTINUOUS,
            requested_duration_seconds=None,
        ),
    )
    path = tmp_path / "continuous.json"
    path.write_text(JsonConfigurationAdapter().dumps(continuous), encoding="utf-8")

    result = main(
        [
            "hardware",
            "acquire-test",
            "--backend",
            "simulated",
            "--experiment",
            str(path),
            "--scenario",
            str(SCENARIOS / "clean-four-channel-v1.json"),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == EXIT_DAQ_FAILURE
    assert payload["error"]["code"] == "DAQ.HARDWARE.CONFIGURATION_UNSUPPORTED"


def test_unknown_backend_never_falls_back_to_simulator(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = main(["hardware", "list", "--backend", "unknown", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert result == EXIT_DAQ_FAILURE
    assert payload["error"]["code"] == "DAQ.HARDWARE.BACKEND_UNAVAILABLE"
    assert payload["error"]["context"]["requested_backend"] == "unknown"


@pytest.mark.parametrize("duration", ["nan", "-1"])
def test_cli_rejects_invalid_duration_bounds(
    duration: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = main(
        [
            "hardware",
            "acquire-test",
            "--backend",
            "simulated",
            "--experiment",
            str(EXPERIMENT),
            "--scenario",
            str(SCENARIOS / "clean-four-channel-v1.json"),
            "--duration",
            duration,
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == EXIT_DAQ_FAILURE
    assert payload["error"]["code"] == "DAQ.HARDWARE.CONFIGURATION_UNSUPPORTED"


def test_cli_half_sample_duration_rounds_up_to_one_sample(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = main(
        [
            "hardware",
            "acquire-test",
            "--backend",
            "simulated",
            "--experiment",
            str(EXPERIMENT),
            "--scenario",
            str(SCENARIOS / "clean-four-channel-v1.json"),
            "--duration",
            "0.00001",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["samples_per_channel"] == 1


def test_console_and_module_hardware_commands_agree_without_ni_import() -> None:
    executable_name = "labdaq.exe" if os.name == "nt" else "labdaq"
    console = Path(sys.executable).parent / executable_name
    arguments = ["hardware", "list", "--backend", "simulated", "--json"]
    console_result = subprocess.run(
        [console, *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    module_result = subprocess.run(
        [sys.executable, "-m", "labdaq", *arguments],
        check=False,
        capture_output=True,
        text=True,
    )

    assert console_result.returncode == module_result.returncode == 0
    console_payload = json.loads(console_result.stdout)
    module_payload = json.loads(module_result.stdout)
    for payload in (console_payload, module_payload):
        payload.pop("discovered_at")
    assert console_payload == module_payload
    assert "nidaqmx" not in console_result.stderr
