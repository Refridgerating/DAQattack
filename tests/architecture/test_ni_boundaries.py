import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = PROJECT_ROOT / "src/labdaq"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    return imported


def test_nidaqmx_imports_are_confined_to_ni_adapter_package() -> None:
    violations: list[str] = []
    for path in PACKAGE_ROOT.rglob("*.py"):
        imports = _imports(path)
        if any(name == "nidaqmx" or name.startswith("nidaqmx.") for name in imports):
            relative = path.relative_to(PACKAGE_ROOT).as_posix()
            if not relative.startswith("adapters/ni/"):
                violations.append(relative)

    assert violations == []


def test_no_public_annotation_outside_ni_adapter_names_nidaqmx() -> None:
    violations = [
        path.relative_to(PACKAGE_ROOT).as_posix()
        for path in PACKAGE_ROOT.rglob("*.py")
        if "adapters/ni/" not in path.as_posix().replace("\\", "/")
        and "nidaqmx" in path.read_text(encoding="utf-8").casefold()
        and path.name != "diagnostics.py"
    ]

    assert violations == []


def test_simulator_still_runs_when_nidaqmx_import_is_blocked() -> None:
    script = """
import importlib.abc
import json
import sys

class BlockNidaqmx(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "nidaqmx" or fullname.startswith("nidaqmx."):
            raise ModuleNotFoundError("blocked", name="nidaqmx")
        return None

sys.meta_path.insert(0, BlockNidaqmx())
from labdaq.bootstrap import create_daq_service
service = create_daq_service(backend="simulated")
report = service.discover()
service.close()
print(json.dumps({"backend": report.backend_id, "nidaqmx_loaded": "nidaqmx" in sys.modules}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "backend": "simulated",
        "nidaqmx_loaded": False,
    }


def test_selecting_ni_without_package_returns_typed_error() -> None:
    script = """
import importlib.abc
import json
import sys

class BlockNidaqmx(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "nidaqmx" or fullname.startswith("nidaqmx."):
            raise ModuleNotFoundError("blocked", name="nidaqmx")
        return None

sys.meta_path.insert(0, BlockNidaqmx())
from labdaq.adapters.ni import create_ni_adapter
from labdaq.ports.daq import DaqDriverUnavailableError
try:
    create_ni_adapter()
except DaqDriverUnavailableError as error:
    print(json.dumps({"code": error.code, "cause": type(error.cause).__name__}))
else:
    raise AssertionError("NI adapter unexpectedly loaded")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "code": "DAQ.HARDWARE.PACKAGE_UNAVAILABLE",
        "cause": "ModuleNotFoundError",
    }
