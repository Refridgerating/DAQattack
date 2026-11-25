# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for the LabDAQ feedback-preview onedir payload."""

from __future__ import annotations

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

ROOT = Path(SPEC).resolve().parents[2]
BUILD_INFO = Path(os.environ["LABDAQ_BUILD_INFO"]).resolve()
BUILD_ID = os.environ["LABDAQ_BUILD_ID"]
APP_VERSION = "0.1.0.dev7"


def windows_version(filename: str, description: str) -> VSVersionInfo:
    strings = [
        StringStruct("CompanyName", "LabDAQ internal laboratory project"),
        StringStruct("FileDescription", description),
        StringStruct("FileVersion", f"{APP_VERSION} feedback-preview {BUILD_ID}"),
        StringStruct("InternalName", Path(filename).stem),
        StringStruct("LegalCopyright", "Copyright (c) 2026. All rights reserved."),
        StringStruct("OriginalFilename", filename),
        StringStruct("ProductName", "LabDAQ Feedback Preview"),
        StringStruct("ProductVersion", f"{APP_VERSION} feedback-preview {BUILD_ID}"),
    ]
    return VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=(0, 1, 0, 7),
            prodvers=(0, 1, 0, 7),
            mask=0x3F,
            flags=0x0,
            OS=0x40004,
            fileType=0x1,
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo([StringTable("040904B0", strings)]),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )


resource_data = [
    item
    for item in collect_data_files("labdaq.resources")
    if Path(item[0]).name != "build-info.json"
]
resource_data.append((str(BUILD_INFO), "labdaq/resources"))
for distribution_name in (
    "labdaq",
    "jsonschema",
    "numpy",
    "scipy",
    "h5py",
    "nptdms",
    "nidaqmx",
    "matplotlib",
    "PySide6",
    "pyqtgraph",
):
    resource_data.extend(
        copy_metadata(distribution_name, recursive=distribution_name == "nidaqmx")
    )

hidden_imports = collect_submodules("labdaq")
hidden_imports.extend(collect_submodules("nidaqmx"))
hidden_imports.extend(
    [
        "h5py",
        "matplotlib.backends.backend_agg",
        "nptdms",
        "numpy",
        "pyqtgraph",
        "scipy",
    ]
)

analysis = Analysis(
    [str(ROOT / "packaging/windows/entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=resource_data,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["mypy", "pytest", "ruff"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

gui = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="LabDAQ",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=windows_version("LabDAQ.exe", "LabDAQ unqualified feedback-preview desktop"),
)
cli = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="LabDAQ-CLI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=windows_version("LabDAQ-CLI.exe", "LabDAQ feedback-preview command line"),
)
payload = COLLECT(
    gui,
    cli,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LabDAQ",
)
