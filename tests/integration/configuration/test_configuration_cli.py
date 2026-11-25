import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from labdaq.cli.main import EXIT_CONFIGURATION_FAILURE, main

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[3]
PROFILE = (
    ROOT
    / "src/labdaq/resources/config/hardware-profiles"
    / "cdaq-9171-ni-9239-four-channel-v1.json"
)
RECIPE = ROOT / "src/labdaq/resources/config/processing-recipes" / "baseline-v1-draft.json"
ACTIVE_RECIPE = ROOT / "src/labdaq/resources/config/processing-recipes" / "baseline-v1.json"
EXPERIMENT = ROOT / "tests/fixtures/configuration/valid-four-channel-synthetic-v1.json"
TEMPLATE = (
    ROOT / "src/labdaq/resources/config/experiments" / "four-channel-vibration-template-v1.json"
)


@pytest.mark.parametrize(
    ("command", "path", "document_type"),
    [
        ("profile", PROFILE, "hardware_profile"),
        ("config", EXPERIMENT, "experiment_config"),
        ("recipe", RECIPE, "processing_recipe"),
        ("recipe", ACTIVE_RECIPE, "processing_recipe"),
    ],
)
def test_cli_validation_success_is_complete_and_structured(
    capsys: pytest.CaptureFixture[str],
    command: str,
    path: Path,
    document_type: str,
) -> None:
    assert main([command, "validate", str(path), "--json"]) == 0
    output = capsys.readouterr()
    payload = json.loads(output.out)

    assert output.err == ""
    assert payload["document_type"] == document_type
    assert payload["schema_version"] == 1
    assert payload["overall"] == "ok"
    assert set(payload) == {
        "channel_count",
        "document_id",
        "document_type",
        "hardware_profile_id",
        "overall",
        "revision",
        "sample_rate_hz",
        "schema_version",
    }


def test_human_validation_summary_and_failure_exit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["config", "validate", str(EXPERIMENT)]) == 0
    success = capsys.readouterr()
    assert "Document type: experiment_config" in success.out
    assert "Channel count: 4" in success.out
    assert "Sample rate: 50000.0" in success.out
    assert "Overall: ok" in success.out

    assert main(["config", "validate", str(TEMPLATE)]) == EXIT_CONFIGURATION_FAILURE
    failure = capsys.readouterr()
    assert failure.out == ""
    assert "DAQ.CONFIG.VALIDATION_FAILED" in failure.err


def test_recipe_show_returns_active_baseline_and_hash(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["recipe", "show", "baseline-v1", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["recipe_id"] == "baseline"
    assert payload["recipe_version"] == "v1"
    assert payload["status"] == "active"
    assert len(payload["stages"]) == 3
    assert len(payload["recipe_hash"]) == 64


def test_console_and_module_configuration_commands_agree() -> None:
    executable_name = "labdaq.exe" if os.name == "nt" else "labdaq"
    console = Path(sys.executable).parent / executable_name
    arguments = ["config", "validate", str(EXPERIMENT), "--json"]

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
    assert json.loads(console_result.stdout) == json.loads(module_result.stdout)


def test_configuration_validation_does_not_import_ni_or_gui_packages() -> None:
    script = f"""
import json
import sys
from pathlib import Path
from labdaq.bootstrap import create_configuration_service

summary = create_configuration_service().validate_experiment_file(Path({str(EXPERIMENT)!r}))
blocked = ("nidaqmx", "PySide6", "pyqtgraph", "nptdms")
print(json.dumps({{
    "overall": summary.overall,
    "blocked": sorted(name for name in sys.modules if name in blocked),
}}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"overall": "ok", "blocked": []}
