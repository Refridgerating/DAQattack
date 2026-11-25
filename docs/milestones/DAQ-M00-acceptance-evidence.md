# DAQ-M00 Acceptance Evidence

Date: 2026-07-28  
Local environment: Windows, CPython 3.11.15  
Milestone state: Accepted  
Physical hardware used: No

## Changes and architecture

- Created the installable `src/labdaq` foundation, layered package boundaries,
  tests, development configuration, governance documents, and CI workflow.
- Established presentation → application → domain/ports dependency direction.
- Established `labdaq.bootstrap` as the composition root.
- Kept NI, GUI, TDMS, analysis, and release dependencies in optional extras.
- Added stable errors, explicit LabDAQ-owned logging, environment diagnostics,
  and equivalent console/module CLI surfaces.
- Retained the legacy script under `references/legacy`; production code does not
  import it.
- Preserved and ignored local capture data and the manufacturer PDF.

## Local verification

The clean `.venv-m00` environment was created from isolated CPython 3.11.15 and
installed with only `python -m pip install -e ".[dev]"`. Diagnostics confirmed
that NumPy, SciPy, NI-DAQmx, PySide6, PyQtGraph, npTDMS, and PyInstaller were not
installed.

| Command | Result |
|---|---|
| `ruff format --check .` | Passed; 45 files formatted |
| `ruff check .` | Passed |
| `mypy src` | Passed; 21 source files |
| `lint-imports` | Passed; 3 contracts kept |
| `pytest -q tests/unit --no-cov` | Passed; 33 tests |
| `pytest -q` | Passed; 51 tests, 94.23% coverage |
| `python -m build` | Passed; sdist and wheel built |
| `labdaq --version` | Passed; `LabDAQ 0.1.0` |
| `labdaq diagnostics` | Passed; overall `ok` |
| `labdaq diagnostics --json` | Passed and parsed; overall `ok` |
| `python -m labdaq diagnostics --json` | Passed and parsed; overall `ok` |

The explicit import probe loaded none of `nidaqmx`, `PySide6`, `pyqtgraph`,
`nptdms`, `labdaq.adapters`, or `labdaq.gui`; root logging handler and thread
counts were unchanged.

## Acceptance status

All DAQ-M00 acceptance criteria have evidence. In particular, criterion 15 is
satisfied by the complete local Windows CPython 3.11.15 verification suite
using only core plus development dependencies.

No Git remote is configured, so hosted Windows CI remains pending external
verification. Its absence does not block M00 acceptance. The retained Ubuntu
job is an advisory portability check; it has not run and does not establish
Linux product support.

DAQ-M00 is Accepted, the roadmap marks it Complete, and DAQ-M01 may proceed.

## Acceptance policy revision verification

The Windows-supported/advisory-Ubuntu policy revision changed documentation,
CI semantics, ADRs, and documentation tests only. No application or M01
implementation changed.

| Command | Result |
|---|---|
| `ruff format --check .` | Passed; 46 files formatted |
| `ruff check .` | Passed |
| `mypy src` | Passed; 21 source files |
| `lint-imports` | Passed; 3 contracts kept |
| `pytest -q tests/architecture/test_documentation.py --no-cov` | Passed; 6 tests |
| `pytest -q` | Passed; 52 tests, 94.23% coverage |

## Assumptions and risks

- Python 3.11 remains the only claimed runtime.
- Windows is the supported v1 product platform.
- GitHub Actions remains the selected hosted CI provider.
- Hosted Windows CI is pending external verification until a remote exists.
- Ubuntu CI is advisory and non-blocking.
- The workspace filesystem does not record ownership, causing Git to require a
  per-command `safe.directory` override locally; no global Git configuration was
  changed.
- Optional feature extras are declared but intentionally not exercised in M00.
- No NI driver, DAQ hardware, wiring, sensor power, or conditioner was tested.
