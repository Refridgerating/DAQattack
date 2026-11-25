import json
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from labdaq.adapters.configuration_json import JsonConfigurationAdapter
from labdaq.cli.main import EXIT_QUALIFICATION_FAILURE, main

if TYPE_CHECKING:
    from labdaq.domain.models import ExperimentConfig


def _write_configuration(tmp_path: Path) -> tuple[Path, Path]:
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
    config = tmp_path / "experiment.json"
    config.write_text(documents.dumps(experiment), encoding="utf-8")
    profile = (
        Path("src/labdaq/resources/config/hardware-profiles")
        / "cdaq-9171-ni-9239-four-channel-v1.json"
    )
    return profile, config


def test_qualify_show_and_check_json_exit_codes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile, config = _write_configuration(tmp_path)
    assert (
        main(
            [
                "qualify",
                "hardware",
                "--backend",
                "simulated",
                "--profile",
                str(profile),
                "--config",
                str(config),
                "--plan",
                "throughput-v1",
                "--duration",
                "0.02",
                "--json",
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert payload["eligibility"]["eligible"] is True
    report_path = payload["report_path"]

    assert main(["qualification", "show", report_path, "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["report"]["schema_version"] == 1

    assert (
        main(
            [
                "qualification",
                "check",
                report_path,
                "--profile",
                str(profile),
                "--config",
                str(config),
                "--json",
            ]
        )
        == EXIT_QUALIFICATION_FAILURE
    )
    offline = json.loads(capsys.readouterr().out)
    assert offline["eligibility"]["fingerprint_matches"] is False

    assert (
        main(
            [
                "qualification",
                "check",
                report_path,
                "--profile",
                str(profile),
                "--config",
                str(config),
                "--backend",
                "simulated",
                "--json",
            ]
        )
        == 0
    )
    checked = json.loads(capsys.readouterr().out)
    assert checked["eligibility"]["fingerprint_matches"] is True
