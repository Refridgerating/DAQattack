import json
import subprocess
import sys
from pathlib import Path

import pytest

from labdaq.version import get_version_label

pytestmark = pytest.mark.integration


def run_module(*arguments: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "labdaq", *arguments],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


def test_module_version() -> None:
    result = run_module("--version")

    assert result.returncode == 0
    assert result.stdout.strip() == get_version_label()
    assert result.stderr == ""


def test_human_diagnostics() -> None:
    result = run_module("diagnostics")

    assert result.returncode == 0
    assert "LabDAQ" in result.stdout
    assert "Python:" in result.stdout
    assert "Operating system:" in result.stdout
    assert "Overall: ok" in result.stdout
    assert result.stderr == ""


def test_json_diagnostics_parse_and_missing_ni_is_not_a_fault() -> None:
    result = run_module("diagnostics", "--json")

    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["overall"] == "ok"
    assert report["application"]["name"] == "LabDAQ"
    assert report["optional_dependencies"]["nidaqmx"]["status"] in {
        "installed",
        "not_installed",
    }
    assert result.stderr == ""


def test_invalid_usage_returns_two() -> None:
    result = run_module()

    assert result.returncode == 2
    assert "a command is required" in result.stderr


def test_feedback_bundle_cli_creates_local_sanitized_zip(tmp_path: Path) -> None:
    result = run_module(
        "feedback-bundle",
        "--output",
        str(tmp_path),
        "--category",
        "installation",
        "--summary",
        "Installer opened",
        "--json",
    )

    assert result.returncode == 0
    report = json.loads(result.stdout)
    bundle = Path(report["feedback_bundle"])
    assert report["transmitted"] is False
    assert bundle.is_file()
