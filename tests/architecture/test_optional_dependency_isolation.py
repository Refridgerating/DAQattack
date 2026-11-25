import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture


def test_core_imports_have_no_optional_import_or_runtime_side_effects(tmp_path: Path) -> None:
    script = """
import json
import logging
import pathlib
import sys
import threading

before_files = sorted(path.name for path in pathlib.Path.cwd().iterdir())
before_handlers = len(logging.getLogger().handlers)
before_threads = {thread.ident for thread in threading.enumerate()}

import labdaq
import labdaq.application
import labdaq.application.export_service
import labdaq.application.plotting_service
import labdaq.application.processing_service
import labdaq.domain
import labdaq.domain.plotting
import labdaq.ports

blocked_prefixes = (
    "nidaqmx",
    "PySide6",
    "pyqtgraph",
    "nptdms",
    "h5py",
    "numpy",
    "scipy",
    "matplotlib",
    "labdaq.adapters",
    "labdaq.gui",
)
loaded = sorted(
    name for name in sys.modules
    if any(name == prefix or name.startswith(prefix + ".") for prefix in blocked_prefixes)
)
result = {
    "loaded": loaded,
    "files_unchanged": before_files == sorted(path.name for path in pathlib.Path.cwd().iterdir()),
    "handlers_unchanged": before_handlers == len(logging.getLogger().handlers),
    "threads_unchanged": before_threads == {thread.ident for thread in threading.enumerate()},
    "exports": sorted(labdaq.__all__),
}
print(json.dumps(result, sort_keys=True))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert evidence == {
        "exports": ["__version__"],
        "files_unchanged": True,
        "handlers_unchanged": True,
        "loaded": [],
        "threads_unchanged": True,
    }


def test_static_renderer_module_import_is_lazy_without_matplotlib(tmp_path: Path) -> None:
    script = """
import json
import sys
import labdaq.adapters.export.matplotlib_renderer
print(json.dumps({"loaded": sorted(name for name in sys.modules if name == "matplotlib" or name.startswith("matplotlib."))}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"loaded": []}


def test_gui_package_import_is_lazy_without_optional_dependencies(tmp_path: Path) -> None:
    script = """
import json
import sys
import labdaq.gui
blocked = ("PySide6", "nidaqmx", "nptdms", "h5py", "scipy", "matplotlib", "pyqtgraph")
print(json.dumps({"loaded": sorted(name for name in sys.modules if any(name == item or name.startswith(item + ".") for item in blocked))}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"loaded": []}


def test_ni_desktop_opens_disabled_state_when_nidaqmx_is_blocked(tmp_path: Path) -> None:
    script = """
import importlib.abc
import json
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"

class BlockNidaqmx(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "nidaqmx" or fullname.startswith("nidaqmx."):
            raise ModuleNotFoundError("blocked for M09 isolation test", name="nidaqmx")
        return None

sys.meta_path.insert(0, BlockNidaqmx())
from labdaq.bootstrap import create_desktop_application
from labdaq.ports.preferences import UserPreferences

class Preferences:
    def load(self): return UserPreferences()
    def save(self, preferences): pass
    def sync(self): pass

application, window = create_desktop_application(
    backend="ni", argv=[], preferences=Preferences()
)
print(json.dumps({
    "opened": window is not None,
    "daq": window.presenter.services.daq is not None,
    "code": window.presenter.services.startup_message.code,
}))
window.close()
application.processEvents()
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "code": "DAQ.HARDWARE.PACKAGE_UNAVAILABLE",
        "daq": False,
        "opened": True,
    }
