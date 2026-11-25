import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_configuration_service_has_no_concrete_adapter_import() -> None:
    source = (PROJECT_ROOT / "src/labdaq/application/configuration_service.py").read_text(
        encoding="utf-8"
    )
    imported_modules = {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert "labdaq.adapters.configuration_json" not in imported_modules


@pytest.mark.parametrize(
    "relative_path",
    [
        "src/labdaq/application/plotting_service.py",
        "src/labdaq/application/export_service.py",
        "src/labdaq/application/processing_service.py",
        "src/labdaq/application/live_service.py",
        "src/labdaq/application/review_service.py",
    ],
)
def test_m07_application_services_do_not_import_adapters_or_renderers(
    relative_path: str,
) -> None:
    source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
    modules = {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module is not None
    } | {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    assert not any(
        module.startswith(("labdaq.adapters", "matplotlib", "nptdms", "PySide6"))
        for module in modules
    )


def test_import_linter_rejects_application_to_adapter_violation(tmp_path: Path) -> None:
    shutil.copytree(PROJECT_ROOT / "src" / "labdaq", tmp_path / "labdaq")
    violation = tmp_path / "labdaq" / "application" / "violation.py"
    violation.write_text("import labdaq.adapters.ni\n", encoding="utf-8")
    configuration = tmp_path / ".importlinter"
    configuration.write_text(
        """
[importlinter]
root_packages =
    labdaq

[importlinter:contract:application]
name = Application cannot import adapters
type = forbidden
source_modules =
    labdaq.application
forbidden_modules =
    labdaq.adapters
""".strip(),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(tmp_path)
    executable_name = "lint-imports.exe" if os.name == "nt" else "lint-imports"
    lint_imports = Path(sys.executable).parent / executable_name

    result = subprocess.run(
        [lint_imports, "--config", str(configuration)],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "Application cannot import adapters" in result.stdout
    assert "BROKEN" in result.stdout


@pytest.mark.parametrize(
    "relative_path",
    [
        "src/labdaq/domain/qualification.py",
        "src/labdaq/application/qualification_artifact_service.py",
        "src/labdaq/application/qualification_service.py",
        "src/labdaq/ports/qualification.py",
    ],
)
def test_m08_qualification_contracts_do_not_import_infrastructure(
    relative_path: str,
) -> None:
    source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
    modules = {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module is not None
    } | {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    forbidden = (
        "labdaq.adapters",
        "nidaqmx",
        "nptdms",
        "matplotlib",
        "PySide6",
        "pyqtgraph",
    )
    assert not any(module.startswith(forbidden) for module in modules)


@pytest.mark.parametrize(
    "relative_path",
    [
        "src/labdaq/application/configuration_workspace.py",
        "src/labdaq/application/destination_service.py",
        "src/labdaq/application/readiness_service.py",
        "src/labdaq/application/session_catalog_service.py",
        "src/labdaq/gui/presentation.py",
    ],
)
def test_m09_headless_services_and_presenters_do_not_import_qt(
    relative_path: str,
) -> None:
    source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
    modules = {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module is not None
    } | {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not any(module.startswith(("PySide6", "nidaqmx", "nptdms")) for module in modules)


def test_m10_widgets_do_not_import_hardware_storage_or_processing_implementations() -> None:
    gui_root = PROJECT_ROOT / "src/labdaq/gui"
    modules: set[str] = set()
    for path in gui_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules.update(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        modules.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
    forbidden = (
        "nidaqmx",
        "nptdms",
        "scipy",
        "matplotlib",
        "labdaq.adapters.ni",
        "labdaq.adapters.tdms",
        "labdaq.processing",
        "labdaq.storage",
    )
    assert not any(module.startswith(forbidden) for module in modules)


def test_m10_widgets_do_not_serialize_or_write_session_artifacts() -> None:
    gui_root = PROJECT_ROOT / "src/labdaq/gui"
    forbidden_calls = {"open", "write_text", "write_bytes", "dump", "dumps"}
    for path in gui_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        } | {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert not calls.intersection(forbidden_calls), path
