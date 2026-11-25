import json
import runpy
import sys
from importlib import import_module

import pytest

from labdaq.errors import LabDAQError

pytestmark = pytest.mark.integration


def test_structured_diagnostic_failure_returns_three(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli = import_module("labdaq.cli.main")
    cause = OSError("read only")
    error = LabDAQError(
        "Probe failed.",
        code="DAQ.CLI.DIAGNOSTIC_FAILURE",
        remediation="Choose a writable temporary directory.",
        cause=cause,
    )

    def fail() -> dict[str, object]:
        raise error

    monkeypatch.setattr(cli, "collect_diagnostics", fail)
    exit_code = cli.main(["diagnostics", "--json"])
    output = capsys.readouterr()

    assert exit_code == 3
    assert output.err == ""
    payload = json.loads(output.out)
    assert payload["overall"] == "error"
    assert payload["error"]["code"] == "DAQ.CLI.DIAGNOSTIC_FAILURE"
    assert error.cause is cause


def test_unexpected_failure_is_translated_and_returns_four(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli = import_module("labdaq.cli.main")

    def fail() -> dict[str, object]:
        raise RuntimeError("unexpected details")

    monkeypatch.setattr(cli, "collect_diagnostics", fail)
    exit_code = cli.main(["diagnostics", "--json"])
    output = capsys.readouterr()

    assert exit_code == 4
    payload = json.loads(output.out)
    assert payload["error"]["code"] == "DAQ.CLI.INTERNAL_ERROR"
    assert payload["error"]["context"] == {"exception_type": "RuntimeError"}


def test_direct_json_and_human_success_paths(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli = import_module("labdaq.cli.main")

    assert cli.main(["diagnostics", "--json"]) == 0
    json_output = capsys.readouterr()
    assert json.loads(json_output.out)["overall"] == "ok"

    assert cli.main(["diagnostics"]) == 0
    human_output = capsys.readouterr()
    assert "Optional dependencies:" in human_output.out
    assert "Overall: ok" in human_output.out


def test_human_structured_failure_uses_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli = import_module("labdaq.cli.main")

    def fail() -> dict[str, object]:
        raise LabDAQError(
            "Probe failed.",
            code="DAQ.CLI.DIAGNOSTIC_FAILURE",
            remediation="Use a writable directory.",
        )

    monkeypatch.setattr(cli, "collect_diagnostics", fail)

    assert cli.main(["diagnostics"]) == 3
    output = capsys.readouterr()
    assert output.out == ""
    assert "DAQ.CLI.DIAGNOSTIC_FAILURE" in output.err
    assert "Use a writable directory." in output.err


def test_module_entry_point_delegates_to_cli(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["labdaq", "diagnostics", "--json"])

    with pytest.raises(SystemExit) as captured:
        runpy.run_module("labdaq", run_name="__main__")

    assert captured.value.code == 0
    assert json.loads(capsys.readouterr().out)["overall"] == "ok"
