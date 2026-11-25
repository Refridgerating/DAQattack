# DAQ-M00: Design Charter and Repository Foundation

Status: Accepted  
Project display name: LabDAQ  
Repository context: Accel+DAQ  
Python distribution and import package: `labdaq`  
Milestone type: Architectural foundation  
Physical hardware required: No  

Roadmap: [DAQ-Software-Roadmap.md](../../DAQ-Software-Roadmap.md)
Acceptance evidence: [DAQ-M00-acceptance-evidence.md](DAQ-M00-acceptance-evidence.md)

## 1. Objective

Establish the project rules, vocabulary, repository structure, development environment, dependency boundaries, observability conventions, and minimum executable surface required for every later LabDAQ milestone.

DAQ-M00 does not acquire measurements. It creates a repository in which later acquisition, storage, processing, and GUI behavior can be added without coupling scientific logic to NI hardware, files, plots, or Qt widgets.

The milestone is complete when a fresh development environment can install the project, run all quality checks without NI software or hardware, execute a minimal diagnostic CLI, and prove the principal import boundaries automatically.

Windows with CPython 3.11 is the required supported platform. Hosted Windows CI
is expected once a GitHub remote exists, but absence of a remote does not block
M00 when the complete local Windows verification suite passes. Ubuntu CI is an
advisory portability check and does not establish or require Linux product
support.

## 2. Architectural Question Being Answered

How shall LabDAQ be organized so that:

1. Scientific records remain interpretable and reproducible.
2. Hardware-specific behavior remains replaceable.
3. Acquisition cannot be slowed or corrupted by plotting.
4. Storage and processing remain usable without the GUI.
5. Simulated and physical DAQ implementations can satisfy the same contracts.
6. Optional NI, GUI, processing, and storage dependencies do not contaminate the core package.
7. Coding agents can extend one milestone at a time without silently collapsing the architecture.

## 3. Engineering Interpretation

This project can be described in several equivalent ways:

- In software architecture, it is a ports-and-adapters application with explicit composition roots.
- In measurement science, it is a chain-of-custody system for sampled physical quantities and calibration provenance.
- In queueing theory, it is one synchronized producer feeding a lossless recorder and a bounded, disposable display consumer.
- In signal processing, sample index is the authoritative discrete-time coordinate and wall-clock time is session metadata.
- In reliability engineering, faults must become typed, observable state rather than console text or silent data corruption.

The foundation must support all five interpretations without placing their responsibilities in one script.

## 4. Source Baseline

The initial supported experiment is:

- NI cDAQ-9171 chassis
- NI-9239 analog input module
- Three TE 830M1-0200 accelerometer channels
- One externally conditioned charge-converter channel
- Four simultaneous channels
- Initial target rate of 50 kS/s per channel

The existing `ReadAllChannels.py` script is a behavioral reference only. It demonstrates that the hardware can be addressed and that four channels can be read, but it shall not define the new architecture. It directly combines NI access, configuration, file creation, acquisition, sample bookkeeping, data conversion, and plotting. No production package shall import it.

The legacy script may be retained under `references/legacy/` with a README explaining that it is excluded from the installed package and automated architecture.

## 5. In Scope

### 5.1 Governance and Documentation

Create or formalize:

- `PROJECT_CHARTER.md`
- `ARCHITECTURE.md`
- `DAQ-Software-Roadmap.md`
- `AGENTS.md`
- `docs/glossary.md`
- `docs/development.md`
- `docs/adr/README.md`
- `docs/milestones/DAQ-M00-design-charter-repository-foundation.md`
- Initial architectural decision records

Documentation shall distinguish current implemented behavior from planned behavior.

### 5.2 Repository Foundation

Create the installable `src/` package layout, test layout, configuration files, CI workflow, ignore rules, and canonical development commands.

### 5.3 Dependency Isolation

Define optional dependency groups so the core and test suite can install without:

- NI-DAQmx
- NI hardware
- PySide6
- PyQtGraph
- TDMS support
- SciPy processing support
- PyInstaller

### 5.4 Error and Logging Foundation

Provide:

- A structured application error base class
- Stable error-code conventions
- Broad subsystem error categories
- Actionable human-readable messages
- Machine-readable serialization
- Idempotent logging configuration
- Human-readable console logging
- Structured context fields

### 5.5 Minimal CLI

Provide:

- Application version reporting
- Human-readable environment diagnostics
- JSON environment diagnostics
- Equivalent execution through the console command and `python -m labdaq`

### 5.6 Automated Quality Gates

Configure:

- Formatting
- Linting
- Static type checking
- Unit tests
- Integration tests
- Architecture tests
- Required CI configuration for Windows and an advisory Ubuntu portability job

## 6. Explicitly Out of Scope

DAQ-M00 shall not implement:

- DAQ device discovery
- NI task creation
- Simulated waveform generation
- Acquisition configuration records
- Hardware profiles or experiment schemas
- Acquisition state machines
- Recording queues
- TDMS writing or parsing
- Session creation
- Calibration calculations
- Filters, FFTs, PSDs, or metrics
- Plot generation
- PySide6 windows
- Hardware qualification
- Packaging into a Windows executable
- Plugin discovery
- Trigger behavior
- Multi-device synchronization

Placeholder modules shall not contain speculative interfaces for these later milestones unless required to establish an import boundary.

## 7. Design Charter

### 7.1 Mission

Build a dependable, reusable, offline-capable desktop data-acquisition application for laboratory users. The application shall eventually discover and qualify supported hardware, acquire synchronized measurements, preserve raw data and metadata, reproduce derived results, and expose the workflow through both headless services and a desktop GUI.

### 7.2 Priority Order

When requirements conflict, use this order:

1. Raw-data integrity
2. Correct sample identity and ordering
3. Explicit faults and recoverable state
4. Measurement provenance
5. Deterministic reproducibility
6. Hardware and storage isolation
7. Operator usability
8. Performance optimization
9. Visual polish

### 7.3 Non-Negotiable Rules

1. The GUI is a client of application services.
2. Core packages do not import NI or Qt libraries.
3. Raw measurements are immutable.
4. Sample index is the authoritative relative-time coordinate.
5. Hardware access occurs through ports implemented by adapters.
6. One acquisition stream is authoritative.
7. Recorded samples are lossless or the run faults explicitly.
8. Display data may be decimated or superseded, but display loss is distinct from acquisition loss.
9. Interrupted runs remain identifiable.
10. Live conditioning is causal.
11. Derived artifacts retain recipe and software provenance.
12. No essential workflow requires internet access.
13. Importing a package shall not open hardware, create files, configure logging globally, or launch a GUI.
14. Physical validation shall never be claimed from simulator or import tests.

## 8. Canonical Terminology

M00 shall define the following terms without yet implementing all related records:

- **Physical channel:** Driver-facing hardware channel identifier such as `Dev1/ai0`.
- **Logical channel:** Experiment-facing identity such as `accel_x` or `charge_signal`.
- **Sample:** One scalar measurement from one channel at one sample index.
- **Sample frame:** Synchronized samples from all active channels at one sample index.
- **Data chunk:** A bounded array of sequential sample frames.
- **Sample index:** Monotonic integer position in the acquisition stream.
- **Session:** One configured acquisition attempt and its metadata, events, raw record, and completion state.
- **Raw data:** Values returned by the DAQ before engineering conversion or conditioning.
- **Calibrated data:** Raw values converted through traceable channel calibration.
- **Conditioned data:** Calibrated or raw values transformed by a declared processing recipe.
- **Acquired count:** Samples successfully returned by the DAQ adapter.
- **Persisted count:** Samples durably accepted by the authoritative recorder.
- **Displayed count:** Samples presented to the live-view consumer.
- **Warning:** A condition that requires visibility but does not necessarily invalidate the run.
- **Fault:** A condition that changes run eligibility, completion state, or data trust.
- **Hardware profile:** Versioned declaration of expected devices, modules, channels, and capabilities.
- **Experiment configuration:** Versioned operator-selected acquisition and metadata settings.
- **Processing recipe:** Versioned declaration of reproducible calibration and conditioning stages.
- **Qualification:** Evidence that the configured hardware chain is ready for a stated use.
- **Diagnostics:** Information about the software environment and optional dependency availability.

## 9. Required Repository Layout

```text
.
|-- AGENTS.md
|-- ARCHITECTURE.md
|-- PROJECT_CHARTER.md
|-- DAQ-Software-Roadmap.md
|-- README.md
|-- LICENSE
|-- pyproject.toml
|-- .gitignore
|-- .editorconfig
|-- .github/
|   `-- workflows/
|       `-- ci.yml
|-- docs/
|   |-- adr/
|   |   |-- README.md
|   |   |-- 0001-package-and-layer-boundaries.md
|   |   |-- 0002-optional-dependency-isolation.md
|   |   `-- 0003-structured-errors-and-logging.md
|   |-- milestones/
|   |   `-- DAQ-M00-design-charter-repository-foundation.md
|   |-- development.md
|   `-- glossary.md
|-- references/
|   `-- legacy/
|       |-- README.md
|       `-- ReadAllChannels.py
|-- src/
|   `-- labdaq/
|       |-- __init__.py
|       |-- __main__.py
|       |-- version.py
|       |-- errors.py
|       |-- diagnostics.py
|       |-- bootstrap.py
|       |-- domain/
|       |   `-- __init__.py
|       |-- application/
|       |   |-- __init__.py
|       |   `-- logging.py
|       |-- ports/
|       |   `-- __init__.py
|       |-- processing/
|       |   `-- __init__.py
|       |-- storage/
|       |   `-- __init__.py
|       |-- adapters/
|       |   |-- __init__.py
|       |   |-- ni/
|       |   |   `-- __init__.py
|       |   |-- simulated/
|       |   |   `-- __init__.py
|       |   |-- tdms/
|       |   |   `-- __init__.py
|       |   `-- export/
|       |       `-- __init__.py
|       |-- cli/
|       |   |-- __init__.py
|       |   `-- main.py
|       `-- gui/
|           `-- __init__.py
`-- tests/
    |-- architecture/
    |   |-- test_import_boundaries.py
    |   `-- test_optional_dependency_isolation.py
    |-- integration/
    |   `-- test_cli_diagnostics.py
    `-- unit/
        |-- test_diagnostics.py
        |-- test_errors.py
        |-- test_logging.py
        `-- test_version.py
```

Empty packages shall contain boundary documentation or minimal docstrings. They shall not contain speculative implementation.

## 10. Layer Responsibilities and Import Contracts

### 10.1 `domain`

Owns stable scientific and operational concepts. It shall not import:

- `application`
- `adapters`
- `storage`
- `gui`
- `cli`
- `nidaqmx`
- `PySide6`
- `pyqtgraph`
- `nptdms`

### 10.2 `ports`

Owns abstract contracts used by application services. It may import domain types and Python typing facilities. It shall not import concrete adapters, GUI code, or CLI code.

### 10.3 `application`

Owns use-case orchestration and policy. It may import `domain` and `ports`. It shall not import concrete NI, TDMS, GUI, or exporter adapters.

### 10.4 `processing`

Owns signal-processing services and processing-specific records when implemented. It may use approved numerical dependencies. It shall not import GUI or NI code.

### 10.5 `storage`

Owns format-independent session layout, naming policy, manifest concepts, and storage-facing services when implemented. Concrete TDMS and filesystem integrations belong in adapters.

### 10.6 `adapters`

Owns integration with external systems and libraries. Adapter modules may import domain and port contracts. No adapter may be imported automatically by `labdaq`, `domain`, `ports`, or `application`.

### 10.7 `cli` and `gui`

Own input and presentation. They call application services or bootstrap functions. They shall not own scientific calculations, acquisition state, direct file serialization, or DAQ task logic.

### 10.8 `bootstrap`

Acts as the composition root. It is the only normal production location allowed to bind concrete adapters to ports. In M00 it shall remain minimal and shall not import unavailable optional dependencies during ordinary core import.

### 10.9 Root Package

`import labdaq` shall expose only stable package metadata. It shall not eagerly import subpackages, optional dependencies, configure logging, probe hardware, or access the filesystem.

## 11. Dependency and Environment Configuration

Use `pyproject.toml` as the authoritative build, dependency, tool, and command configuration.

### 11.1 Canonical Python

- Python 3.11 is the normative development and laboratory runtime for v1.
- Additional Python versions may be added only after CI verification.
- M00 shall not claim compatibility with versions that are not tested.

### 11.2 Dependency Groups

Define separate optional groups with bounded, non-exact version ranges:

- `dev`: pytest, pytest-cov, ruff, mypy, import-linter, build tooling
- `analysis`: NumPy and SciPy
- `storage`: TDMS library
- `ni`: `nidaqmx`
- `gui`: PySide6 and PyQtGraph
- `release`: PyInstaller
- `full`: all runtime features required by the eventual laboratory build

The default editable development install for M00 shall not require `ni`, `gui`, `storage`, or `analysis`.

### 11.3 Console Entry Point

Define:

```text
labdaq = labdaq.cli.main:main
```

### 11.4 Canonical Commands

Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
ruff format --check .
ruff check .
mypy src
lint-imports
pytest -q
labdaq --version
labdaq diagnostics
labdaq diagnostics --json
python -m labdaq diagnostics --json
```

POSIX shell:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
ruff format --check .
ruff check .
mypy src
lint-imports
pytest -q
labdaq diagnostics --json
```

## 12. Structured Error Hierarchy

Implement a base `LabDAQError` carrying:

- `code`
- `message`
- `severity`
- `context`
- `remediation`
- Optional wrapped cause for debugging
- `to_dict()` machine-readable representation

Error objects shall not include raw sample arrays or secrets in their serialized context.

Define broad categories without milestone-specific behavior:

- `ConfigurationError`
- `DependencyUnavailableError`
- `HardwareError`
- `AcquisitionError`
- `StorageError`
- `ProcessingError`
- `ExportError`
- `ApplicationStateError`

Error code format:

```text
DAQ.<SUBSYSTEM>.<CONDITION>
```

Examples:

```text
DAQ.CORE.INVALID_ARGUMENT
DAQ.CORE.DEPENDENCY_UNAVAILABLE
DAQ.CLI.DIAGNOSTIC_FAILURE
DAQ.APPLICATION.INVALID_STATE
DAQ.HARDWARE.UNAVAILABLE
DAQ.STORAGE.WRITE_FAILURE
```

Subclasses shall preserve the stable base serialization contract.

## 13. Logging and Observability

Use Python standard-library logging in M00.

Provide an idempotent configuration function that supports:

- Log level selection
- Human-readable console output
- Optional structured JSON output
- UTC ISO 8601 timestamps
- Logger name
- Severity
- Message
- Event code
- Component
- Optional session, device, and channel identifiers when later available

Rules:

1. Importing `labdaq` shall not configure global logging.
2. Calling logging configuration twice shall not duplicate handlers.
3. Raw sample arrays shall not be emitted to normal logs.
4. Exceptions shall retain tracebacks in debug logs.
5. Operator-facing errors shall include remediation where known.
6. Logs are operational evidence, not the scientific measurement record.

## 14. Minimal CLI Contract

### 14.1 Version

```text
labdaq --version
```

Expected output contains the application name and installed package version.

### 14.2 Diagnostics

```text
labdaq diagnostics
labdaq diagnostics --json
python -m labdaq diagnostics --json
```

Diagnostics shall report:

- Application name and version
- Python version and implementation
- Python executable path
- Operating system and release
- Machine architecture
- Current working directory
- Core package import status
- Optional dependency presence and discovered version when available
- Whether the process can create and remove a temporary file
- Overall result

Diagnostics shall not:

- Create an NI task
- Enumerate DAQ hardware
- Create a session directory
- Import the GUI
- Fail merely because optional NI or GUI dependencies are absent

JSON output shall be deterministic in field names and valid for machine parsing. Missing optional dependencies shall be reported as `not_installed`, not as faults.

Exit codes:

- `0`: Core environment is operational
- `2`: Invalid CLI usage
- `3`: Core diagnostic failure
- `4`: Unexpected internal error translated to a structured error

## 15. Quality and CI Configuration

### 15.1 Ruff

Configure Ruff as formatter and linter. Enable rules for:

- Import hygiene
- Undefined names
- Unused imports
- Common correctness defects
- Modern Python syntax
- Exception handling
- Type-checking imports

### 15.2 Mypy

Use strict or near-strict checking for `src/labdaq`. Any relaxed rule shall be documented and narrowly scoped.

### 15.3 Pytest

Configure:

- `tests/unit`
- `tests/integration`
- `tests/architecture`
- Warning treatment
- Test markers
- Coverage collection

Coverage is a regression indicator, not proof of scientific correctness. M00 may establish a modest global threshold, but later milestone acceptance shall depend on behavior-specific tests.

### 15.4 Import-Linter or Equivalent

Automate the high-value import contracts. CI shall fail if:

- Domain imports an outer layer
- Ports import adapters
- Application imports concrete adapters
- Core imports GUI or NI packages
- Root package import triggers optional dependencies

### 15.5 GitHub Actions

The configured workflow shall retain:

- `windows-latest` as the required supported-platform job
- `ubuntu-latest` as an advisory, non-blocking portability job

Use Python 3.11. Install only the project and `dev` extras. Run:

1. Format check
2. Lint
3. Type check
4. Import-boundary check
5. Full pytest suite
6. CLI smoke test

No CI job shall require NI hardware, an NI driver, a graphical display, or internet access beyond dependency installation.

Hosted Windows CI is expected once a GitHub remote exists and shall be recorded
as external verification. Before a remote exists, passing the same complete
suite locally on Windows satisfies M00. Ubuntu failure remains visible but does
not fail the workflow or block milestone acceptance.

## 16. Required Tests

### 16.1 Version Tests

- Installed or fallback version is a non-empty string.
- `labdaq --version` exits successfully.
- `python -m labdaq --version` agrees with the console entry point.

### 16.2 Diagnostic Tests

- Human-readable diagnostics include the application, Python, and OS.
- JSON diagnostics parse successfully.
- Expected top-level keys are stable.
- Missing `nidaqmx` is represented as optional dependency absence.
- Temporary-file probe cleans up after itself.
- Diagnostics perform no NI or GUI import.
- Console and module entry points return equivalent results.

### 16.3 Error Tests

- Base and subclass errors serialize to the required dictionary form.
- Context is copied or made read-only to prevent mutation after construction.
- Wrapped exceptions preserve useful debug information.
- `str(error)` remains concise and operator-readable.
- Missing remediation is represented explicitly.

### 16.4 Logging Tests

- Configuration is idempotent.
- Event code and context fields are present.
- JSON log output is parseable.
- Human-readable output remains useful.
- Sensitive or large array fields are rejected, summarized, or omitted according to documented policy.

### 16.5 Architecture Tests

- `import labdaq` does not import `nidaqmx`, `PySide6`, `pyqtgraph`, or `nptdms`.
- Importing `labdaq.domain`, `labdaq.ports`, and `labdaq.application` succeeds without optional extras.
- Core imports do not create files.
- Core imports do not add logging handlers.
- Core imports do not start threads.
- Static contracts reject reverse dependencies.

### 16.6 Documentation Tests

At minimum, verify that:

- Required governing documents exist.
- ADR files follow the numbering and status convention.
- Canonical command examples match configured tools.
- Roadmap and M00 specification link to each other.

## 17. Acceptance Scenarios

### Scenario A: Fresh Windows Core Installation

Given a clean Windows Python 3.11 virtual environment with no NI software and no optional runtime extras:

1. Install `.[dev]`.
2. Import `labdaq`.
3. Run all static checks.
4. Run the full test suite.
5. Run diagnostics.

Pass criteria:

- Installation succeeds.
- Imports succeed.
- All checks pass.
- Diagnostics report optional packages as absent where applicable.
- Exit code is zero.
- No hardware is accessed.

### Scenario B: Optional Dependency Isolation

Given a process that records imported module names:

1. Import `labdaq`.
2. Import `labdaq.domain`.
3. Import `labdaq.ports`.
4. Import `labdaq.application`.

Pass criteria:

- NI, Qt, PyQtGraph, TDMS, and concrete adapter modules are absent from the import graph.
- No file is created.
- No logging handler is added.
- No worker thread starts.

### Scenario C: CLI Equivalence

Run:

```text
labdaq diagnostics --json
python -m labdaq diagnostics --json
```

Pass criteria:

- Both commands exit zero.
- Both outputs parse as JSON.
- Stable fields agree except fields that are expected to differ, such as executable invocation details.
- No optional dependency is required.

### Scenario D: Structured Failure

Inject a diagnostic probe failure using a test double.

Pass criteria:

- The failure becomes a typed `LabDAQError`.
- The CLI emits an actionable message.
- JSON mode emits a machine-readable error object.
- Exit code is nonzero and documented.
- The original cause is available to debug logging.

### Scenario E: CI Rejection of Boundary Violation

Introduce a temporary test fixture in which application code imports an NI adapter.

Pass criteria:

- The architecture test fails.
- Removal of the violation restores the passing suite.
- The test does not require `nidaqmx` to be installed.

### Scenario F: Required and Advisory CI Policy

Given the configured GitHub Actions matrix:

1. Inspect the Windows and Ubuntu entries.
2. Introduce a failing command independently on each entry.

Pass criteria:

- `windows-latest` is labeled required and its failure fails the workflow.
- `ubuntu-latest` is labeled advisory and its failure remains visible without failing the workflow.
- Both entries continue to run the complete M00 verification workload.

## 18. Acceptance Criteria

DAQ-M00 is accepted only when all of the following are true:

1. The project installs in a fresh Python 3.11 virtual environment.
2. The complete M00 test suite runs without NI hardware or NI-DAQmx.
3. `import labdaq` has no file, logging, thread, hardware, or GUI side effects.
4. Core, domain, port, and application imports do not import optional adapters.
5. Architecture tests enforce the highest-value boundaries.
6. Ruff formatting and linting pass.
7. Mypy passes for `src/labdaq`.
8. The CLI reports version information.
9. Human and JSON diagnostics execute successfully.
10. Missing optional dependencies are reported without failing the core environment.
11. Structured errors contain code, severity, context, and remediation.
12. Logging setup is explicit and idempotent.
13. Governing documents and ADR process are present.
14. README documents canonical setup and verification commands.
15. The complete M00 verification suite passes on Windows with Python 3.11 using only core plus development dependencies.
16. No later-milestone acquisition, storage, processing, or GUI behavior is implemented.
17. No physical-hardware validation is claimed.

## 19. Required ADRs

### ADR-0001: Package and Layer Boundaries

Record:

- Ports-and-adapters architecture
- Permitted dependency direction
- Composition-root exception
- Root-package import rules
- Reason architecture tests are required

### ADR-0002: Optional Dependency Isolation

Record:

- Core installation excludes NI and GUI dependencies
- Runtime features use optional extras
- Adapters perform local imports where necessary
- Missing optional dependencies become typed errors when the feature is requested

### ADR-0003: Structured Errors and Logging

Record:

- Stable error code format
- Error serialization
- Logging configuration ownership
- No import-time configuration
- Logs are not scientific records

### ADR-0004: Supported Platform and CI Acceptance

Record:

- Windows with Python 3.11 is the supported v1 platform.
- Local Windows verification can satisfy a milestone before a GitHub remote exists.
- Hosted Windows CI is expected external verification but is not retroactively blocking.
- Ubuntu CI is advisory and does not claim Linux product support.

## 20. Assumptions

1. Python 3.11 is acceptable as the normative v1 runtime.
2. A single-process desktop architecture is sufficient for the first four-channel instrument.
3. Optional dependency isolation is practical for NI, GUI, processing, storage, and packaging libraries.
4. GitHub Actions is the initial CI service.
5. The repository will use Git and source-controlled ADRs.
6. The package name `labdaq` is acceptable even when the repository is called Accel+DAQ.
7. M00 diagnostics describe the software environment only. Hardware discovery belongs to later milestones.
8. The legacy script is not a compatibility target.
9. Windows is the supported v1 platform because the laboratory application will use Windows NI-DAQmx drivers.
10. Ubuntu CI is advisory and does not constitute Linux product support.

These are engineering choices, not first-principles requirements. They may be revised through ADRs when evidence justifies a change.

## 21. Risks and Mitigations

### Risk 1: Premature Abstraction

Creating empty interfaces for all future behavior could harden incorrect assumptions.

Mitigation: M00 defines package boundaries and vocabulary, not detailed future contracts.

### Risk 2: Optional Dependency Leakage

Top-level imports from NI, Qt, TDMS, or SciPy could make headless testing impossible.

Mitigation: Architecture tests inspect the import graph and run without optional extras installed.

### Risk 3: Duplicate Storage Ownership

A top-level `storage` package and storage adapters could become ambiguous.

Mitigation: `storage` owns format-independent session policy. `adapters/tdms` owns TDMS integration. The architecture document shall state this distinction.

### Risk 4: CLI Becomes an Application Layer

Future commands could accumulate acquisition and storage logic.

Mitigation: CLI modules parse input, invoke application services, render results, and translate errors only.

### Risk 5: Global Logging Side Effects

Import-time logging configuration can interfere with tests, GUI behavior, and embedding.

Mitigation: Logging is configured only by entry points through an explicit idempotent function.

### Risk 6: Coverage Metric Distortion

A high coverage number may encourage trivial tests while missing data-integrity failures.

Mitigation: Treat coverage as a floor. Milestones require behavior-specific and fault-injection acceptance evidence.

## 22. Foundation Hypotheses

These hypotheses guide later tests but are not proven by M00.

1. Coupling acquisition to plotting and text conversion is more likely to cause lost or miscounted samples than the nominal NI read path.  
   Plausibility: High  
   Testability: High

2. Missing or incorrect metadata can make an otherwise valid raw dataset scientifically unusable.  
   Plausibility: High  
   Testability: High

3. A deterministic simulator and strict port contract will reduce physical hardware debugging time more than an early GUI prototype.  
   Plausibility: High  
   Testability: High

4. Most future extensibility will come from stable data and hardware contracts, not from a general-purpose plugin framework created in M00.  
   Plausibility: Moderate to high  
   Testability: Moderate

## 23. Completion Evidence Required From Codex

The implementation report shall contain:

### Changes

List all added and modified files.

### Architecture

State:

- Dependency direction established
- Composition-root location
- Optional-dependency strategy
- Error and logging ownership
- Legacy-script disposition

### Tests

Report exact commands and results for:

- Ruff format check
- Ruff lint
- Mypy
- Import-linter or equivalent
- Focused M00 tests
- Full pytest suite
- CLI smoke tests

### Acceptance Evidence

Include evidence that:

- Tests ran without NI software
- Core imports did not load NI or GUI modules
- Diagnostics JSON parsed successfully
- CI configuration contains required Windows and advisory Ubuntu jobs
- Hosted Windows CI status is recorded separately from local acceptance
- No physical acquisition was attempted

### Assumptions

List any package naming, Python version, CI, or repository-hosting assumptions.

### Risks

List unresolved setup, compatibility, or optional-dependency risks.

### Next Step

DAQ-M01 may proceed after DAQ-M00 acceptance.

## 24. Expected Completion State

After DAQ-M00:

- The repository is installable.
- The project rules are explicit.
- Architectural drift is automatically detectable.
- Optional hardware and GUI dependencies are isolated.
- Errors and logs have stable foundations.
- The CLI can identify the application and software environment.
- No acquisition behavior exists yet.
- DAQ-M01 can define the domain records and versioned configurations without restructuring the repository.

## 25. Codex Implementation Prompt

```text
Implement DAQ-M00 only.

Authoritative inputs:
- PROJECT_CHARTER.md if present
- AGENTS.md if present
- ARCHITECTURE.md if present
- DAQ-Software-Roadmap.md
- docs/milestones/DAQ-M00-design-charter-repository-foundation.md
- existing ADRs
- the repository's current files and tests

First inspect the repository and the legacy ReadAllChannels.py reference. Do not treat the legacy script as the target architecture.

Requirements:
- establish the documented repository structure and package boundaries
- use the Python package name labdaq unless the repository already has a coherent package name
- keep NI, GUI, storage, processing, and release dependencies optional
- make core and application imports work without nidaqmx or NI hardware
- implement explicit, side-effect-free version and diagnostics services
- implement the structured LabDAQError hierarchy
- implement explicit, idempotent logging configuration
- add labdaq --version and labdaq diagnostics with human and JSON output
- make python -m labdaq equivalent to the console entry point
- configure formatting, linting, type checking, pytest, coverage, and architecture checks
- add required Windows CI and advisory Ubuntu CI using Python 3.11
- create or update the charter, architecture, glossary, development guide, ADR process, README, AGENTS.md, roadmap links, and M00 specification
- retain the legacy script only as an excluded reference if it is kept
- do not implement DAQ discovery, simulated signals, acquisition, TDMS storage, processing, plots, or GUI behavior
- do not add speculative future interfaces beyond what M00 needs

Before completion:
- install the project in a clean virtual environment using core plus development dependencies only
- run the format check
- run lint
- run mypy
- run architecture checks
- run focused tests
- run the full test suite
- run both CLI entry points
- prove that core imports do not import nidaqmx, PySide6, pyqtgraph, or nptdms
- report changed files, architecture decisions, exact commands, results, assumptions, risks, and any acceptance criterion not satisfied
- do not claim physical hardware validation
- stop after DAQ-M00
```
