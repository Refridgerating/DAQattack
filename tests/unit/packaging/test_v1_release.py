from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load(name: str, filename: str) -> object:
    spec = importlib.util.spec_from_file_location(name, Path("packaging/windows") / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_desktop_defaults_to_raw_ni_and_allows_simulator() -> None:
    entry = _load("labdaq_v1_entry", "entry_v1.py")
    assert entry._windowed_arguments([]) == ["--daq", "ni"]  # type: ignore[attr-defined]
    assert entry._windowed_arguments(["--daq", "simulated"]) == [  # type: ignore[attr-defined]
        "--daq",
        "simulated",
    ]
    assert entry._windowed_arguments(["--feedback"]) == ["--feedback"]  # type: ignore[attr-defined]


def test_v1_inventory_and_manifest_report_unknown_physical_validation(tmp_path: Path) -> None:
    metadata = _load("labdaq_v1_metadata", "release_metadata_v1.py")
    payload = tmp_path / "payload"
    payload.mkdir()
    for name in ("LabDAQ.exe", "LabDAQ-CLI.exe"):
        (payload / name).write_bytes(b"frozen")
    inventory = tmp_path / "installed-files.txt"
    metadata.write_install_inventory(payload, inventory)  # type: ignore[attr-defined]
    files = inventory.read_text(encoding="utf-16-le").splitlines()
    assert "docs\\DAQ-1.0.0-lab-release.md" in files
    assert "LAB-RELEASE-NOTICE.txt" in files

    installer = tmp_path / "LabDAQ-1.0.0-win64-setup.exe"
    installer.write_bytes(b"unsigned installer")
    build_info = tmp_path / "build-info.json"
    build_info.write_text('{"build_id":"lab-test"}', encoding="utf-8")
    metadata.finalize_metadata(  # type: ignore[attr-defined]
        installer,
        build_info,
        tmp_path,
        automated_baseline="PASS",
        local_package_smoke="NOT_VERIFIED",
    )
    manifest = json.loads((tmp_path / "LabDAQ-1.0.0-win64-release.json").read_text())
    assert manifest["application_version"] == "1.0.0"
    assert manifest["artifact"]["signed"] is False
    assert manifest["artifact"]["sha256"] == metadata.file_sha256(installer)  # type: ignore[attr-defined]
    assert manifest["qualification"]["physical_ni_workflow"] == "NOT_VERIFIED"


def test_installer_is_side_by_side_and_has_both_launchers() -> None:
    installer = Path("packaging/windows/LabDAQ-v1.nsi").read_text(encoding="utf-8")
    assert 'InstallDir "$PROGRAMFILES64\\LabDAQ"' in installer
    assert '"Software\\LabDAQ\\Release"' in installer
    assert '"--daq simulated"' in installer
    assert "LabDAQ — NI Hardware.lnk" in installer
    assert "LabDAQ — Simulated.lnk" in installer
    assert "Call un.RemoveOwnedFiles" in installer
    assert "RMDir /r" not in installer
