"""Side-effect-bounded software-environment diagnostics."""

from __future__ import annotations

import importlib.util
import os
import platform
import sys
import tempfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Final

from labdaq.build_info import get_build_info
from labdaq.errors import LabDAQError
from labdaq.version import APP_NAME, __version__

_OPTIONAL_DEPENDENCIES: Final = {
    "numpy": ("numpy", "numpy"),
    "scipy": ("scipy", "scipy"),
    "nidaqmx": ("nidaqmx", "nidaqmx"),
    "PySide6": ("PySide6", "PySide6"),
    "pyqtgraph": ("pyqtgraph", "pyqtgraph"),
    "nptdms": ("nptdms", "nptdms"),
    "h5py": ("h5py", "h5py"),
    "matplotlib": ("matplotlib", "matplotlib"),
    "PyInstaller": ("PyInstaller", "pyinstaller"),
}


def collect_diagnostics(*, temp_directory: Path | None = None) -> dict[str, object]:
    """Collect core diagnostics without importing optional feature packages."""
    try:
        temporary_file = _probe_temporary_file(temp_directory)
    except OSError as cause:
        raise LabDAQError(
            "The temporary-file diagnostic failed.",
            code="DAQ.CLI.DIAGNOSTIC_FAILURE",
            context={"directory": str(temp_directory) if temp_directory is not None else None},
            remediation="Verify that the operating-system temporary directory is writable.",
            cause=cause,
        ) from cause

    optional_dependencies = {
        name: _dependency_status(import_name, distribution_name)
        for name, (import_name, distribution_name) in _OPTIONAL_DEPENDENCIES.items()
    }
    return {
        "application": {
            "name": APP_NAME,
            "version": __version__,
            "build": get_build_info().to_dict(),
        },
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "working_directory": str(Path.cwd()),
        "core_import": {"status": "ok"},
        "optional_dependencies": optional_dependencies,
        "temporary_file": temporary_file,
        "overall": "ok",
    }


def _dependency_status(import_name: str, distribution_name: str) -> dict[str, object]:
    if importlib.util.find_spec(import_name) is None:
        return {"status": "not_installed", "version": None}
    try:
        discovered_version: str | None = version(distribution_name)
    except PackageNotFoundError:
        discovered_version = None
    return {"status": "installed", "version": discovered_version}


def _probe_temporary_file(temp_directory: Path | None) -> dict[str, str]:
    descriptor, raw_path = tempfile.mkstemp(
        prefix="labdaq-diagnostic-",
        dir=temp_directory,
    )
    path = Path(raw_path)
    try:
        os.close(descriptor)
        path.write_bytes(b"labdaq")
    finally:
        if path.exists():
            path.unlink()
    return {"status": "ok"}
