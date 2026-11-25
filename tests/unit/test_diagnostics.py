import importlib.util
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest

from labdaq import diagnostics
from labdaq.errors import LabDAQError


def test_diagnostics_have_stable_top_level_schema(tmp_path: Path) -> None:
    report = diagnostics.collect_diagnostics(temp_directory=tmp_path)

    assert list(report) == [
        "application",
        "python",
        "platform",
        "working_directory",
        "core_import",
        "optional_dependencies",
        "temporary_file",
        "overall",
    ]
    assert report["overall"] == "ok"
    application = report["application"]
    assert isinstance(application, dict)
    assert application["build"]["release_channel"] == "lab-release"
    assert report["temporary_file"] == {"status": "ok"}
    assert list(tmp_path.iterdir()) == []


def test_optional_dependencies_have_stable_status_fields(tmp_path: Path) -> None:
    report = diagnostics.collect_diagnostics(temp_directory=tmp_path)
    dependencies = report["optional_dependencies"]

    assert isinstance(dependencies, dict)
    assert set(dependencies) == {
        "numpy",
        "h5py",
        "scipy",
        "nidaqmx",
        "PySide6",
        "pyqtgraph",
        "nptdms",
        "matplotlib",
        "PyInstaller",
    }
    for status in dependencies.values():
        assert set(status) == {"status", "version"}
        assert status["status"] in {"installed", "not_installed"}


def test_missing_optional_dependency_is_not_a_fault(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    original_find_spec = importlib.util.find_spec

    def fake_find_spec(name: str, package: str | None = None) -> object:
        if name == "nidaqmx":
            return None
        return original_find_spec(name, package)

    monkeypatch.setattr(diagnostics.importlib.util, "find_spec", fake_find_spec)
    report = diagnostics.collect_diagnostics(temp_directory=tmp_path)
    dependencies = report["optional_dependencies"]

    assert isinstance(dependencies, dict)
    assert dependencies["nidaqmx"] == {"status": "not_installed", "version": None}
    assert report["overall"] == "ok"


def test_temporary_file_failure_becomes_structured_error(tmp_path: Path) -> None:
    missing_directory = tmp_path / "missing"

    with pytest.raises(LabDAQError) as captured:
        diagnostics.collect_diagnostics(temp_directory=missing_directory)

    error = captured.value
    assert error.code == "DAQ.CLI.DIAGNOSTIC_FAILURE"
    assert isinstance(error.cause, OSError)
    assert error.remediation is not None


def test_installed_module_without_distribution_metadata_has_null_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(diagnostics.importlib.util, "find_spec", lambda name: object())

    def missing_version(distribution_name: str) -> str:
        raise PackageNotFoundError(distribution_name)

    monkeypatch.setattr(diagnostics, "version", missing_version)

    assert diagnostics._dependency_status("example", "example") == {
        "status": "installed",
        "version": None,
    }


def test_failed_write_still_removes_temporary_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail_write(path: Path, data: bytes) -> int:
        raise OSError("write failed")

    monkeypatch.setattr(Path, "write_bytes", fail_write)

    with pytest.raises(LabDAQError):
        diagnostics.collect_diagnostics(temp_directory=tmp_path)

    assert list(tmp_path.iterdir()) == []
