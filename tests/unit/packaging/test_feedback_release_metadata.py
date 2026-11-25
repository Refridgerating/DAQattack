from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def _metadata_module() -> object:
    path = Path("packaging/windows/release_metadata.py").resolve()
    spec = importlib.util.spec_from_file_location("labdaq_release_metadata", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _entry_module() -> object:
    path = Path("packaging/windows/entry.py").resolve()
    spec = importlib.util.spec_from_file_location("labdaq_frozen_entry", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_tree_digest_is_path_ordered_and_ignores_generated_outputs(tmp_path: Path) -> None:
    module = _metadata_module()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "b.py").write_text("b\n", encoding="utf-8")
    (tmp_path / "src" / "a.py").write_text("a\n", encoding="utf-8")
    first = module.source_tree_digest(tmp_path)  # type: ignore[attr-defined]
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "ignored.json").write_text("{}", encoding="utf-8")
    (tmp_path / "src" / "labdaq.egg-info").mkdir()
    (tmp_path / "src" / "labdaq.egg-info" / "SOURCES.txt").write_text(
        "generated\n", encoding="utf-8"
    )

    assert module.source_tree_digest(tmp_path) == first  # type: ignore[attr-defined]
    (tmp_path / "src" / "a.py").write_text("changed\n", encoding="utf-8")
    assert module.source_tree_digest(tmp_path) != first  # type: ignore[attr-defined]
    second = module.source_tree_digest(tmp_path)  # type: ignore[attr-defined]
    (tmp_path / "policy.nsh").write_text("; installer policy", encoding="utf-8")
    assert module.source_tree_digest(tmp_path) != second  # type: ignore[attr-defined]


def test_install_inventory_covers_runtime_and_docs_but_excludes_uninstaller(tmp_path: Path) -> None:
    module = _metadata_module()
    payload = tmp_path / "payload"
    runtime = payload / "_internal" / "labdaq-0.1.0.dev7.dist-info"
    runtime.mkdir(parents=True)
    for path in (payload / "LabDAQ.exe", payload / "LabDAQ-CLI.exe", runtime / "METADATA"):
        path.write_bytes(b"payload")
    output = tmp_path / "inventory.txt"
    module.write_install_inventory(payload, output)  # type: ignore[attr-defined]
    entries = output.read_text(encoding="utf-16-le").splitlines()
    assert "LabDAQ.exe" in entries
    assert "_internal\\labdaq-0.1.0.dev7.dist-info\\METADATA" in entries
    assert "docs\\DAQ-0.1.0.dev7-feedback-preview.md" in entries
    assert "Uninstall.exe" not in entries
    assert "installed-files.txt" not in entries
    assert len(entries) == len(set(entries)) == 14
    (payload / "LabDAQ.exe").unlink()
    with pytest.raises(ValueError, match="both frozen launchers"):
        module.write_install_inventory(payload, output)  # type: ignore[attr-defined]


def test_finalize_metadata_hashes_unsigned_installer_and_keeps_m11_unverified(
    tmp_path: Path,
) -> None:
    module = _metadata_module()
    installer = tmp_path / "LabDAQ-0.1.0.dev7-feedback-win64-setup.exe"
    installer.write_bytes(b"preview-installer")
    build_info = tmp_path / "build-info.json"
    build_info.write_text(
        json.dumps({"build_id": "feedback-test", "source_tree_sha256": "a" * 64}),
        encoding="utf-8",
    )
    module.finalize_metadata(  # type: ignore[attr-defined]
        installer,
        build_info,
        tmp_path,
        automated_baseline="PASS",
        local_package_smoke="PASS",
    )

    manifest = json.loads(
        (tmp_path / "LabDAQ-0.1.0.dev7-feedback-win64-release.json").read_text(encoding="utf-8")
    )
    assert manifest["artifact"]["signed"] is False
    assert manifest["artifact"]["sha256"] == module.file_sha256(installer)  # type: ignore[attr-defined]
    assert manifest["qualification"]["automated_baseline"] == "PASS"
    assert manifest["qualification"]["non_administrator_account_install"] == "NOT_VERIFIED"
    assert manifest["qualification"]["packaged_simulator_gui_workflow"] == "NOT_VERIFIED"
    assert manifest["qualification"]["m11_unconditional_release"] == "NOT_VERIFIED"


def test_nsis_installer_is_shared_and_preserves_session_and_preferences_policy() -> None:
    script = Path("packaging/windows/LabDAQ-feedback-preview.nsi").read_text(encoding="utf-8")

    assert "RequestExecutionLevel admin" in script
    assert 'InstallDir "$PROGRAMFILES64\\LabDAQ Feedback Preview"' in script
    assert "LabDAQ Feedback Preview — Simulated.lnk" in script
    assert "LabDAQ Feedback Preview — NI Hardware (Unqualified).lnk" in script
    assert "Create LabDAQ Feedback Bundle.lnk" in script
    assert (
        'CreateShortcut "$SMPROGRAMS\\LabDAQ Feedback Preview\\LabDAQ Feedback Preview — Simulated.lnk"'
        not in script
    )
    assert (
        'Delete "$SMPROGRAMS\\LabDAQ Feedback Preview\\LabDAQ Feedback Preview — Simulated.lnk"'
        in script
    )
    assert script.count("CreateShortcut ") == 2
    assert "--daq simulated" not in script
    assert "MUI_FINISHPAGE_RUN_PARAMETERS" not in script
    assert 'DeleteRegKey HKCU "Software\\LabDAQ"' not in script
    assert "HKCU" not in script
    assert "InstallDirRegKey" not in script
    assert "MUI_PAGE_DIRECTORY" in script
    assert "MUI_FINISHPAGE_RUN" not in script
    assert script.count("SetShellVarContext all") == 3
    assert script.count("SetRegView 64") == 3
    assert (
        'ReadRegStr $RegisteredDir HKLM "Software\\LabDAQ\\FeedbackPreview" "InstallDir"' in script
    )
    assert '!define APP_VERSION "0.1.0.dev7"' in script
    assert 'VIProductVersion "0.1.0.7"' in script
    assert "RMDir /r" not in script
    assert "Call RemoveOwnedFiles" in script
    assert "Call un.RemoveOwnedFiles" in script


def test_build_script_requires_nsis_3_12_and_utf8_input() -> None:
    script = Path("packaging/windows/build-feedback-preview.ps1").read_text(encoding="utf-8")

    assert '$nsisVersion -ne "v3.12"' in script
    assert '"/INPUTCHARSET" "UTF8"' in script
    assert "hardware list --backend ni --json" in script
    assert "DAQ.HARDWARE.DRIVER_UNAVAILABLE" in script
    for distribution in ("nidaqmx", "nitypes", "hightime"):
        assert f'"{distribution}"' in script
    for release_file in (
        "LabDAQ-0.1.0.dev7-feedback-win64-build-info.json",
        "LabDAQ-0.1.0.dev7-feedback-win64-third-party-notices.txt",
        "LabDAQ-0.1.0.dev7-feedback-win64-release-notes.md",
        "LabDAQ-0.1.0.dev7-feedback-win64-internal-evaluation-notice.txt",
    ):
        assert release_file in script


def test_pyinstaller_recursively_collects_ni_dependency_metadata() -> None:
    spec = Path("packaging/windows/labdaq-feedback.spec").read_text(encoding="utf-8")
    requirements = Path("packaging/windows/requirements-release.txt").read_text(encoding="utf-8")
    metadata_source = Path("packaging/windows/release_metadata.py").read_text(encoding="utf-8")

    assert 'recursive=distribution_name == "nidaqmx"' in spec
    assert 'APP_VERSION = "0.1.0.dev7"' in spec
    assert "filevers=(0, 1, 0, 7)" in spec
    assert "prodvers=(0, 1, 0, 7)" in spec
    for requirement in ("nidaqmx==1.6.0", "nitypes==1.1.0", "hightime==1.0.0"):
        assert requirement in requirements
    for distribution in ('"nidaqmx"', '"nitypes"', '"hightime"'):
        assert distribution in metadata_source


def test_frozen_windowed_entry_forces_ni_functional_mode_and_keeps_feedback_backend_free() -> None:
    module = _entry_module()

    expected = [
        "--daq",
        "ni",
        "--mode",
        "accelerometer_commissioning",
        "--functional-preview",
    ]
    assert module._windowed_arguments([]) == expected  # type: ignore[attr-defined]
    assert module._windowed_arguments(["--mode", "production"]) == [  # type: ignore[attr-defined]
        *expected,
    ]
    assert module._windowed_arguments(["--feedback"]) == ["--feedback"]  # type: ignore[attr-defined]
    assert module._windowed_arguments(["--daq", "simulated"]) == expected  # type: ignore[attr-defined]


def test_frozen_windowed_entry_bypasses_preview_acknowledgement(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    module = _entry_module()
    calls: dict[str, object] = {}

    def gui_main(
        argv: list[str],
        *,
        require_preview_acknowledgement: bool,
    ) -> int:
        calls["argv"] = argv
        calls["require_preview_acknowledgement"] = require_preview_acknowledgement
        return 17

    monkeypatch.setattr(module.sys, "executable", str(tmp_path / "LabDAQ.exe"))  # type: ignore[attr-defined]
    monkeypatch.setattr(module.sys, "argv", ["LabDAQ.exe"])  # type: ignore[attr-defined]
    monkeypatch.setattr("labdaq.gui.__main__.main", gui_main)  # type: ignore[attr-defined]

    assert module.main() == 17  # type: ignore[attr-defined]
    assert calls == {
        "argv": [
            "--daq",
            "ni",
            "--mode",
            "accelerometer_commissioning",
            "--functional-preview",
        ],
        "require_preview_acknowledgement": False,
    }
