import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def console_entry_point() -> Path:
    executable_name = "labdaq.exe" if os.name == "nt" else "labdaq"
    return Path(sys.executable).parent / executable_name


def test_console_and_module_entry_points_agree() -> None:
    console = console_entry_point()
    assert console.is_file()

    console_result = subprocess.run(
        [console, "diagnostics", "--json"],
        check=False,
        capture_output=True,
        text=True,
    )
    module_result = subprocess.run(
        [sys.executable, "-m", "labdaq", "diagnostics", "--json"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert console_result.returncode == 0
    assert module_result.returncode == 0
    assert json.loads(console_result.stdout) == json.loads(module_result.stdout)


def test_console_and_module_versions_agree() -> None:
    console = console_entry_point()
    assert console.is_file()

    console_result = subprocess.run(
        [console, "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    module_result = subprocess.run(
        [sys.executable, "-m", "labdaq", "--version"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert console_result.returncode == module_result.returncode == 0
    assert console_result.stdout == module_result.stdout
