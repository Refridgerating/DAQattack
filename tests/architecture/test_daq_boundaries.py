import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    return imported


def test_daq_application_service_depends_on_port_not_simulator() -> None:
    imports = _imports(PROJECT_ROOT / "src/labdaq/application/daq_service.py")
    assert "labdaq.ports.daq" in imports
    assert not any(module.startswith("labdaq.adapters") for module in imports)


def test_acquisition_engine_depends_on_ports_not_concrete_adapters() -> None:
    path = PROJECT_ROOT / "src/labdaq/application/acquisition_engine.py"
    imports = _imports(path)

    assert "labdaq.ports.daq" in imports
    assert "labdaq.ports.acquisition" in imports
    assert not any(module.startswith("labdaq.adapters") for module in imports)
    source = path.read_text(encoding="utf-8").casefold()
    assert "nidaqmx" not in source
    assert "pyside" not in source
    assert "nptdms" not in source


def test_session_application_service_depends_on_ports_not_storage_adapter() -> None:
    path = PROJECT_ROOT / "src/labdaq/application/session_service.py"
    imports = _imports(path)

    assert "labdaq.ports.session" in imports
    assert not any(module.startswith("labdaq.adapters") for module in imports)
    assert "nptdms" not in path.read_text(encoding="utf-8").casefold()


def test_processing_application_service_depends_on_ports_not_numerical_implementation() -> None:
    path = PROJECT_ROOT / "src/labdaq/application/processing_service.py"
    imports = _imports(path)

    assert not any(module.startswith("labdaq.adapters") for module in imports)
    assert "labdaq.processing" not in imports
    assert "scipy" not in imports
    assert "nptdms" not in path.read_text(encoding="utf-8").casefold()


def test_processing_application_cli_and_bootstrap_import_without_analysis_stack() -> None:
    script = """
import json
import sys

import labdaq.application.processing_service
import labdaq.bootstrap
import labdaq.cli.main

blocked = ("numpy", "scipy", "nidaqmx", "PySide6", "pyqtgraph", "nptdms")
print(json.dumps(sorted(
    name for name in sys.modules
    if any(name == prefix or name.startswith(prefix + ".") for prefix in blocked)
)))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []


def test_missing_analysis_extra_is_translated_at_composition_boundary() -> None:
    script = """
import importlib.abc
import json
import sys

class BlockAnalysis(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "scipy" or fullname.startswith("scipy."):
            raise ModuleNotFoundError("blocked for isolation test", name=fullname)
        return None

sys.meta_path.insert(0, BlockAnalysis())
from labdaq.bootstrap import create_streaming_processing_service
from labdaq.errors import DependencyUnavailableError

try:
    create_streaming_processing_service()
except DependencyUnavailableError as error:
    print(json.dumps({"code": error.code, "dependency": error.context["dependency"]}))
else:
    raise SystemExit("analysis composition unexpectedly succeeded")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "code": "DAQ.DEPENDENCY.UNAVAILABLE",
        "dependency": "scipy",
    }


def test_daq_port_has_no_concrete_or_ni_names() -> None:
    path = PROJECT_ROOT / "src/labdaq/ports/daq.py"
    source = path.read_text(encoding="utf-8")
    imports = _imports(path)

    assert not any(module.startswith("labdaq.adapters") for module in imports)
    assert "nidaqmx" not in source.casefold()


def test_simulator_has_no_forbidden_feature_dependencies() -> None:
    simulator = PROJECT_ROOT / "src/labdaq/adapters/simulated"
    imported = set().union(*(_imports(path) for path in simulator.glob("*.py")))
    forbidden = (
        "nidaqmx",
        "PySide6",
        "pyqtgraph",
        "nptdms",
        "scipy",
        "labdaq.gui",
        "labdaq.storage",
        "labdaq.processing",
        "labdaq.adapters.ni",
    )

    assert not any(
        module == prefix or module.startswith(prefix + ".")
        for module in imported
        for prefix in forbidden
    )


def test_lazy_simulator_package_import_does_not_load_numeric_or_ni_dependencies() -> None:
    script = """
import json
import sys
import labdaq.adapters.simulated

blocked = ("numpy", "scipy", "nidaqmx", "PySide6", "pyqtgraph", "nptdms")
print(json.dumps(sorted(
    name for name in sys.modules
    if any(name == prefix or name.startswith(prefix + ".") for prefix in blocked)
)))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []


def test_simulated_collection_does_not_import_ni() -> None:
    script = """
import json
import sys
from labdaq.bootstrap import create_daq_service

service = create_daq_service(backend="simulated")
report = service.discover()
service.close()
print(json.dumps({
    "backend": report.backend_id,
    "blocked": sorted(name for name in sys.modules if name == "nidaqmx"),
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"backend": "simulated", "blocked": []}


def test_session_contracts_cli_bootstrap_and_lazy_adapters_do_not_import_storage_libraries() -> (
    None
):
    script = """
import json
import sys

import labdaq.ports.session
import labdaq.application.session_service
import labdaq.adapters.tdms
import labdaq.bootstrap
import labdaq.cli.main

print(json.dumps(sorted(
    name for name in sys.modules
    if name == "nptdms" or name.startswith("nptdms.")
    or name == "h5py" or name.startswith("h5py.")
)))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []


def test_missing_hdf5_storage_extra_is_translated_at_composition_boundary() -> None:
    script = """
import importlib.abc
import json
import sys

class BlockStorage(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "h5py" or fullname.startswith("h5py."):
            raise ModuleNotFoundError("blocked for isolation test", name=fullname)
        return None

sys.meta_path.insert(0, BlockStorage())
from labdaq.adapters.session import create_session_store
from labdaq.errors import DependencyUnavailableError

try:
    create_session_store()
except DependencyUnavailableError as error:
    print(json.dumps({"code": error.code, "dependency": error.context["dependency"]}))
else:
    raise SystemExit("storage composition unexpectedly succeeded")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "code": "DAQ.DEPENDENCY.UNAVAILABLE",
        "dependency": "h5py",
    }


def test_missing_storage_extra_is_translated_at_composition_boundary() -> None:
    script = """
import importlib.abc
import json
import sys

class BlockStorage(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "nptdms" or fullname.startswith("nptdms."):
            raise ModuleNotFoundError("blocked for isolation test", name=fullname)
        return None

sys.meta_path.insert(0, BlockStorage())
from labdaq.adapters.tdms import create_tdms_session_store
from labdaq.errors import DependencyUnavailableError

try:
    create_tdms_session_store()
except DependencyUnavailableError as error:
    print(json.dumps({"code": error.code, "dependency": error.context["dependency"]}))
else:
    raise SystemExit("storage composition unexpectedly succeeded")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "code": "DAQ.DEPENDENCY.UNAVAILABLE",
        "dependency": "nptdms",
    }
