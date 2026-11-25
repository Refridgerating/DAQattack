from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def harness(tmp_path_factory: pytest.TempPathFactory) -> Path:
    compiler = ROOT / ".tools/nsis/makensis.exe"
    if os.name != "nt" or not compiler.exists():
        pytest.skip("Executable NSIS path-policy checks require Windows and local NSIS")
    output = tmp_path_factory.mktemp("nsis-policy") / "path-test.exe"
    subprocess.run(
        [
            str(compiler),
            "/V2",
            f"/DREPOSITORY_ROOT={ROOT}",
            f"/DHARNESS_EXE={output}",
            str(ROOT / "tests/fixtures/packaging/install-path-harness.nsi"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return output


def run_policy(harness: Path, path: Path, mode: str = "fresh") -> str:
    subprocess.run(f'"{harness}" /S /MODE={mode} /PATH="{path}"', check=True, timeout=20)
    return (harness.parent / "result.txt").read_text(encoding="utf-8-sig")


def inventory(path: Path, names: list[str]) -> None:
    (path / "installed-files.txt").write_text(
        "".join(name + "\r\n" for name in names),
        encoding="utf-16-le",
        newline="",
    )


def test_custom_empty_destination_and_unrelated_nonempty_directory(
    harness: Path, tmp_path: Path
) -> None:
    custom = tmp_path / "Custom LabDAQ"
    assert run_policy(harness, custom) == ""
    custom.mkdir()
    assert run_policy(harness, custom) == ""
    sentinel = custom / "notes.txt"
    sentinel.write_text("keep", encoding="utf-8")
    assert "empty folder" in run_policy(harness, custom)
    assert sentinel.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("root", ["drive", "windows", "program_files", "profile"])
def test_system_roots_rejected(harness: Path, root: str) -> None:
    paths = {
        "drive": Path(os.environ["SystemDrive"] + "\\"),
        "windows": Path(os.environ["WINDIR"]),
        "program_files": Path(os.environ["ProgramW6432"]),
        "profile": Path(os.environ["USERPROFILE"]),
    }
    assert "dedicated LabDAQ subfolder" in run_policy(harness, paths[root])


def test_shared_upgrade_requires_inventory_and_reuses_location(
    harness: Path, tmp_path: Path
) -> None:
    assert "inventory" in run_policy(harness, tmp_path, "shared")
    inventory(tmp_path, ["LabDAQ.exe"])
    assert run_policy(harness, tmp_path, "shared") == ""
    assert "Uninstall it before changing" in run_policy(harness, tmp_path, "relocate")


def test_remove_owned_files_preserves_unknown_files_in_nested_directories(
    harness: Path, tmp_path: Path
) -> None:
    runtime = tmp_path / "_internal"
    runtime.mkdir()
    owned = runtime / "old-version.dist-info"
    owned.mkdir()
    (owned / "METADATA").write_text("old", encoding="utf-8")
    (tmp_path / "LabDAQ.exe").write_bytes(b"app")
    sentinel = runtime / "user-notes.txt"
    sentinel.write_text("keep", encoding="utf-8")
    inventory(tmp_path, ["LabDAQ.exe", "_internal\\old-version.dist-info\\METADATA"])
    assert run_policy(harness, tmp_path, "remove") == ""
    assert not (tmp_path / "LabDAQ.exe").exists()
    assert not owned.exists()
    assert sentinel.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("bad_path", ["..\\outside.txt", "*", "notes.txt:stream"])
def test_invalid_inventory_aborts_before_deleting_files(
    harness: Path, tmp_path: Path, bad_path: str
) -> None:
    app = tmp_path / "LabDAQ.exe"
    app.write_bytes(b"app")
    inventory(tmp_path, ["LabDAQ.exe", bad_path])
    assert "invalid path" in run_policy(harness, tmp_path, "remove")
    assert app.read_bytes() == b"app"
