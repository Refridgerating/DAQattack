"""Generate immutable preview build identity, notices, hashes, and release metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import stat
import subprocess
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

APP_VERSION = "0.1.0.dev7"
RELEASE_CHANNEL = "feedback-preview"
RUNTIME_DISTRIBUTIONS = (
    "labdaq",
    "jsonschema",
    "numpy",
    "scipy",
    "h5py",
    "nptdms",
    "nidaqmx",
    "nitypes",
    "hightime",
    "matplotlib",
    "PySide6",
    "pyqtgraph",
)
NOTICE_DISTRIBUTIONS = (*RUNTIME_DISTRIBUTIONS, "pyinstaller")
EXCLUDED_DIRECTORIES = {
    ".git",
    ".github",
    ".import_linter_cache",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tools",
    "Data",
    "build",
    "dist",
    "htmlcov",
}
SOURCE_SUFFIXES = {".json", ".md", ".nsh", ".nsi", ".ps1", ".py", ".spec", ".toml", ".txt", ".yml"}


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--root", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--installer", type=Path, required=True)
    finalize.add_argument("--build-info", type=Path, required=True)
    finalize.add_argument("--output", type=Path, required=True)
    inventory = subparsers.add_parser("inventory")
    inventory.add_argument("--payload", type=Path, required=True)
    inventory.add_argument("--output", type=Path, required=True)
    finalize.add_argument(
        "--automated-baseline",
        choices=("PASS", "FAIL", "NOT_VERIFIED"),
        default="NOT_VERIFIED",
    )
    finalize.add_argument(
        "--local-package-smoke",
        choices=("PASS", "FAIL", "NOT_VERIFIED"),
        default="NOT_VERIFIED",
    )
    arguments = parser.parse_args()
    if arguments.command == "prepare":
        prepare_metadata(arguments.root.resolve(), arguments.output.resolve())
    elif arguments.command == "inventory":
        write_install_inventory(arguments.payload.resolve(), arguments.output.resolve())
    else:
        finalize_metadata(
            arguments.installer.resolve(),
            arguments.build_info.resolve(),
            arguments.output.resolve(),
            automated_baseline=arguments.automated_baseline,
            local_package_smoke=arguments.local_package_smoke,
        )
    return 0


def write_install_inventory(payload: Path, output: Path) -> None:
    """List owned files so upgrades/uninstall never recursively delete user files."""
    files = {
        "LICENSE",
        "INTERNAL-EVALUATION-NOTICE.txt",
        "build-info.json",
        "THIRD-PARTY-NOTICES.txt",
        f"docs/DAQ-{APP_VERSION}-feedback-preview.md",
        "docs/gui.md",
        "docs/ni-hardware.md",
        "docs/recovery.md",
        "examples/cdaq-9171-ni-9239-four-channel-v1.json",
        "examples/four-channel-vibration-synthetic-v1.json",
        "examples/clean-four-channel-v1.json",
    }
    if not (payload / "LabDAQ.exe").is_file() or not (payload / "LabDAQ-CLI.exe").is_file():
        raise ValueError("Install inventory requires both frozen launchers")
    for path in payload.rglob("*"):
        if path.is_symlink() or (
            getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError("Install payload must not contain filesystem links")
        if path.is_file():
            files.add(path.relative_to(payload).as_posix())
    for name in files:
        if len(name) > 900 or any(char in name for char in '\r\n:*?"<>|'):
            raise ValueError("Install inventory contains an unsupported Windows path")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(name.replace("/", "\\") + "\r\n" for name in sorted(files)),
        encoding="utf-16-le",
        newline="",
    )


def prepare_metadata(root: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    digest = source_tree_digest(root)
    revision, source_state = git_identity(root)
    build_id = f"feedback-{digest[:12]}"
    payload = {
        "schema_version": 1,
        "release_channel": RELEASE_CHANNEL,
        "build_id": build_id,
        "build_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source_revision": revision,
        "source_tree_sha256": digest,
        "source_state": source_state,
        "architecture": platform.machine(),
        "python_version": platform.python_version(),
        "runtime_dependencies": installed_versions(RUNTIME_DISTRIBUTIONS),
        "schema_versions": {
            "session_manifest": [1, 2],
            "experiment_configuration": [1, 2],
            "hardware_profile": [1],
            "processing_recipe": [1],
            "qualification_report": [1],
            "feedback_bundle": [1],
        },
    }
    write_json(output / "build-info.json", payload)
    (output / "THIRD-PARTY-NOTICES.txt").write_text(
        render_notices(),
        encoding="utf-8",
        newline="\n",
    )


def finalize_metadata(
    installer: Path,
    build_info_path: Path,
    output: Path,
    *,
    automated_baseline: str,
    local_package_smoke: str,
) -> None:
    if not installer.is_file():
        raise FileNotFoundError(installer)
    output.mkdir(parents=True, exist_ok=True)
    build_info = json.loads(build_info_path.read_text(encoding="utf-8"))
    digest = file_sha256(installer)
    release = {
        "schema_version": 1,
        "release_channel": RELEASE_CHANNEL,
        "application_version": APP_VERSION,
        "build": build_info,
        "artifact": {
            "name": installer.name,
            "size_bytes": installer.stat().st_size,
            "sha256": digest,
            "signed": False,
        },
        "qualification": {
            "automated_baseline": automated_baseline,
            "local_package_smoke": local_package_smoke,
            "non_administrator_account_install": "NOT_VERIFIED",
            "shared_install_admin_approval": "NOT_VERIFIED",
            "second_standard_user_launch": "NOT_VERIFIED",
            "custom_path_upgrade_uninstall": "NOT_VERIFIED",
            "clean_windows_without_python_or_ni": "NOT_VERIFIED",
            "packaged_simulator_gui_workflow": "NOT_VERIFIED",
            "packaged_functional_ni_gui_workflow": "NOT_VERIFIED",
            "ni_missing_prerequisite_workflow": "NOT_VERIFIED",
            "physical_ni_workflow": "NOT_VERIFIED",
            "ten_minute_physical_run": "NOT_VERIFIED",
            "long_duration_soak": "NOT_VERIFIED",
            "one_hundred_physical_start_stop_cycles": "NOT_VERIFIED",
            "packaged_disconnect_disk_full_forced_close": "NOT_VERIFIED",
            "independent_operator": "NOT_VERIFIED",
            "m11_unconditional_release": "NOT_VERIFIED",
        },
    }
    manifest = output / "LabDAQ-0.1.0.dev7-feedback-win64-release.json"
    checksum = output / "LabDAQ-0.1.0.dev7-feedback-win64-setup.exe.sha256"
    write_json(manifest, release)
    checksum.write_text(f"{digest}  {installer.name}\n", encoding="ascii", newline="\n")


def source_tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    candidates = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if not path.is_file() or any(part in EXCLUDED_DIRECTORIES for part in relative.parts):
            continue
        if any(part.startswith(".venv") or part.endswith(".egg-info") for part in relative.parts):
            continue
        if path.suffix.lower() not in SOURCE_SUFFIXES and path.name not in {
            "LICENSE",
            ".gitignore",
            ".editorconfig",
        }:
            continue
        candidates.append(path)
    for path in sorted(candidates, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def git_identity(root: Path) -> tuple[str | None, str]:
    revision_result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    revision = revision_result.stdout.strip() if revision_result.returncode == 0 else None
    status_result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    dirty = bool(status_result.stdout.strip()) or status_result.returncode != 0
    if revision is None:
        return None, "unborn-dirty" if dirty else "unborn-clean"
    return revision, "dirty" if dirty else "clean"


def installed_versions(names: tuple[str, ...]) -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in names:
        versions[name] = metadata.version(name)
    return versions


def render_notices() -> str:
    sections = [
        "LabDAQ Feedback Preview — Third-Party Notices\n",
        "Runtime and packaging components are listed with metadata supplied by each distribution.\n",
    ]
    seen_texts: set[str] = set()
    for name in NOTICE_DISTRIBUTIONS:
        distribution = metadata.distribution(name)
        project_name = distribution.metadata.get("Name", name)
        project_version = distribution.version
        license_expression = (
            distribution.metadata.get("License-Expression")
            or distribution.metadata.get("License")
            or "not declared in package metadata"
        )
        sections.append(
            f"\n{'=' * 72}\n{project_name} {project_version}\nLicense: {license_expression}\n"
        )
        for license_text in distribution_license_texts(distribution):
            fingerprint = hashlib.sha256(license_text.encode("utf-8")).hexdigest()
            if fingerprint in seen_texts:
                continue
            seen_texts.add(fingerprint)
            sections.append(f"\n{license_text.rstrip()}\n")
    sections.append(
        "\n========================================================================\n"
        "NSIS 3.12 is used to create the installer. NSIS is distributed under\n"
        "zlib/libpng and component-specific licenses documented at:\n"
        "https://nsis.sourceforge.io/Docs/AppendixI.html\n"
    )
    return "".join(sections)


def distribution_license_texts(distribution: metadata.Distribution) -> tuple[str, ...]:
    texts: list[str] = []
    for file in distribution.files or ():
        filename = Path(str(file)).name.casefold()
        if not filename.startswith(("license", "copying", "notice")):
            continue
        located = Path(distribution.locate_file(file))
        if not located.is_file():
            continue
        try:
            text = located.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = located.read_text(encoding="latin-1")
        if text.strip():
            texts.append(text)
    return tuple(texts)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    raise SystemExit(main())
