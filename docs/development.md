# LabDAQ Development Guide

## Environment

CPython 3.11 on Windows is the normative runtime and supported development
platform. Create a clean virtual environment and install `.[dev]` for
development. This includes the core `jsonschema` dependency and the NumPy
dependency needed by M02 tests. Operators may install only `.[simulated]` for
the simulator. Do not install `full` to make core tests pass. Hardware, GUI,
storage, and release dependency isolation remains an acceptance requirement.

Ubuntu CI is an advisory portability check, not a Linux support claim. Its
result is non-blocking unless Linux support is added to the project charter.

Canonical setup and verification commands are maintained in the repository
[README](../README.md) and `pyproject.toml`.

## M09 desktop workflow

The `dev` extra includes PySide6 and pytest-qt; the deployable GUI remains an
optional `gui` extra. Run Qt tests offscreen when a desktop session is unavailable:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
pytest tests\gui tests\integration\test_m09_readiness_workflow.py -q
python -m labdaq.gui --daq simulated
```

Keep `gui.presentation` free of Qt and concrete adapters. Put blocking discovery,
qualification, destination, and catalog calls through the keyed runner. A widget
must render `ReadinessDecision`; it must never reconstruct eligibility. Preserve
the shutdown order documented in
[ADR-0022](adr/0022-desktop-presentation-background-preferences.md).

Session-review panel controls live in `gui.review_layout`. `ReviewPanel` owns only
its title bar and existing content widget. `ReviewLayoutController` retains normal
splitter proportions and minimized/maximized identities in memory, hiding sibling
branches to maximize without reparenting or rebuilding plots. Empty branches hide
until one of their panels is restored. Content updates still use the existing
runner and presentation paths. Panel state is not persisted through preferences
or written into scientific session metadata. Live-acquisition layouts are separate.

## Change discipline

1. Read `AGENTS.md`, the roadmap, active milestone, architecture, and ADRs.
2. Implement only the active milestone.
3. Add deterministic tests at the lowest useful layer and relevant boundary.
4. Run format, lint, mypy, import contracts, focused tests, and full tests.
5. Record local, hosted-CI, simulated, and physical evidence separately.
6. Update roadmap status only when its stated acceptance gate is met.

Hosted Windows CI is expected after a GitHub remote exists. Before then, a
complete passing local Windows suite satisfies milestones whose acceptance
criteria do not explicitly require hosted execution.

Core tests must not require NI drivers, DAQ hardware, a display, or optional
runtime extras. Use temporary directories and never delete recorded user data.

## M01 configuration workflow

Configuration documents are schema-versioned and validated in three steps:

1. Reject a missing, malformed, or unsupported `schema_version`.
2. Validate the complete wire document against its closed Draft 2020-12 schema.
3. Construct frozen records and validate cross-record scientific constraints.

Canonical schemas and the initial profile are package resources under
`src/labdaq/resources`. Tests load those resources through the real JSON
adapter so installed-wheel behavior remains covered. Invalid loaders must not
repair slugs, infer calibration, discard unknown fields, or return partial
records.

The operator template deliberately leaves the external charge-chain
calibration unresolved. Use only authoritative sensitivity, unit, polarity,
and source data for a production configuration. Synthetic calibration belongs
only in clearly named `test_fixture` records.

## Package rules

Public interfaces require type annotations. Domain records should be immutable.
Use `pathlib.Path`, timezone-aware timestamps, structured errors, and explicit
dependency injection. No import may create files, configure logging, start
threads, probe hardware, or launch GUI components.

## M02 simulator workflow

The simulator is selected explicitly through the composition root. It never
acts as a fallback for a requested NI backend. Scenario JSON is schema checked
before immutable records are constructed, then checked against experiment
channel IDs and Nyquist limits.

Signal generators evaluate absolute indices and must remain invariant to read
partitioning. `sim-v1` noise uses a keyed SplitMix64 counter and Box–Muller
pairing based on absolute sample index. Any deliberate byte-level output change
requires a new generator version and updated acceptance evidence.

Use the reusable DAQ-port contract suite for later adapters. M02 adapter-local
read sequence and sample cursor are diagnostic only; M04 will own session
identity and authoritative continuity accounting.

## M03 NI workflow

Install `.[dev,ni]` only on an NI development host. `adapters.ni.driver` is the
sole vendor API boundary; the rest of the adapter consumes narrow snapshots and
task protocols so failure paths can be tested without hardware. Do not mock the
domain, application service, or DAQ port.

Normal test runs skip physical tests. Explicitly set
`LABDAQ_RUN_NI_HARDWARE=1` and select `pytest -m hardware` only after confirming
safe inputs and exclusive device ownership. Local profile and experiment paths
may be supplied through `LABDAQ_HARDWARE_PROFILE` and
`LABDAQ_EXPERIMENT_CONFIG`. Never place laboratory aliases, serial constraints,
credentials, or machine paths into universal fixtures.

Physical reset, channel stimulus, and disconnect checks are separate supervised
actions. Simulator or fake-driver evidence cannot satisfy them. See the
[NI hardware guide](ni-hardware.md).

## M04 acquisition-engine workflow

`application.acquisition_engine` is the only owner of acquisition state,
session IDs, authoritative sample/sequence continuity, the blocking read worker,
and recording/display fan-out. Keep NI and simulator details in their adapters;
application tests should inject a `DaqPort`, clocks, and session-ID generation.

The engine accepts validated domain records in `configure()`, reserves the task
in `arm()`, and starts the adapter before its one worker in `start()`. Attach a
recording stream consumer after arm and before start. The recording FIFO is
lossless and faults on bounded backpressure, so test code must drain it for any
run larger than its capacity. A display consumer may be absent or slow; whole
chunks are superseded and counted without blocking acquisition.

Always join a run with `wait_for_terminal()` and release composition-owned
resources with `close()`. Tests that request stop must allow a valid read already
returned by the adapter to become a partial final chunk. Verify acquired and
recording-accepted counts separately on backpressure failures.

Use these focused checks while editing M04 behavior:

```powershell
pytest tests\unit\application\test_acquisition_engine.py `
  tests\unit\application\test_acquisition_records.py -q
pytest tests\contract\test_daq_port_contract.py `
  tests\integration\test_acquisition_engine_pipeline.py -q
pytest tests\unit\adapters\ni\test_adapter.py -q
```

Run physical engine tests only with `LABDAQ_RUN_NI_HARDWARE=1`. The Python NI
API and fake driver prove the task-abort call contract; they do not prove how a
particular driver/device combination interrupts an in-flight physical read.
See the [acquisition engine guide](acquisition-engine.md) and M04 ADRs before
changing queue or terminal semantics.

## M05 durable-session workflow

Install `.[dev]` or `.[simulated,storage]`; npTDMS must remain imported only by
`adapters.tdms.session`. Application services depend on `ports.session`, and the
`storage` package owns format-neutral naming, estimates, serialization, and
digest policy only.

Create a preview once and pass the same preallocated `SessionPlan` to
`RecordingSessionService.prepare()`. Start only after prepare returns, drain both
M04 streams through the service, call `wait_for_terminal()`, and close
idempotently. Tests must distinguish acquired, recording-accepted, and persisted
counts. A failed append cannot return a receipt or advance persistence.

Use fast inspection for browsing and full verification before replay. Recovery
is never implicit: inject a platform-aware process probe in tests, refuse live
locks, preserve raw/event bytes, and accept only a verified synchronized prefix.
See the [session format](storage/session-format.md),
[manifest schema](storage/manifest-schema.md),
[HDF5 layout](storage/hdf5-layout.md), legacy
[TDMS layout](storage/tdms-layout.md), and
[recovery guide](storage/recovery.md).

Focused M05 checks:

```powershell
pytest tests\unit\storage tests\integration\test_durable_sessions.py -q
pytest tests\architecture -q
labdaq session summary path\to\session --verify --json
```

Run the physical durable round trip only with `LABDAQ_RUN_NI_HARDWARE=1`. A
simulator round trip proves exact format behavior but not chassis/module wiring,
physical channel order, actual driver rate, or task release on the laboratory
system.

## M06 processing workflow

Install `.[dev,analysis]`. Keep calibration resolution and replay/range
orchestration in `application.processing_service`; numerical algorithms belong
in `processing`, and JSON/TDMS details remain in adapters. Application imports
must continue to work when NumPy and SciPy are unavailable. Use synthetic,
explicitly labeled calibrations in automated tests and never substitute a real
charge-chain coefficient.

Process streaming chunks in strict order. Tests must prove that a rejected gap,
overlap, reorder, rate change, context change, or nonfinite chunk cannot advance
filter state. For a selected replay range, feed the verified prefix from sample
zero and discard preceding conditioned output. Numerical changes require
analytical tolerances plus fixed reference hashes because NumPy/SciPy versions
are part of provenance.

Focused M06 checks:

```powershell
pytest tests\unit\processing tests\contract tests\integration\test_durable_sessions.py -q
pytest tests\architecture -q
labdaq recipe show baseline-v1 --json
labdaq process-session path\to\session --recipe baseline-v1 --summary --json
```

M06 processing is read-only: tests should byte-compare source records and assert
that `processed/` remains empty. M07 owns persistent derived artifacts and
exports. See [calibration and processing](processing.md), the
[processing CLI](cli/processing.md), and ADR-0014/0015.

## M07 plotting/export workflow

Install `.[dev]`, which includes the bounded `export` extra. Keep NumPy-backed
plot carriers in `ports.plotting`, orchestration in application services, and
Matplotlib/filesystem/JSON details in adapters. Do not make plot data a source
for CSV or processing. Conditioned arbitrary ranges must use the M06
`RecordedSignalStreamService` so causal state is reconstructed from zero.

Tests should instrument replay chunk sizes, verify that time CSV writes multiple
chunks, reject nonfinite decimator input, and exercise FFT's preallocation guard
and bounded Welch PSD. Every export test should schema-validate sidecars and
manifests, recompute checksums, and byte-compare source records. Use controlled
plot doubles for incompatible spectral grids and renderer faults.

Focused M07 checks:

```powershell
pytest tests\unit\plotting tests\integration\test_plotting_and_export.py -q
pytest tests\unit\processing tests\contract\test_processing_port_contract.py -q
pytest tests\architecture -q
labdaq plot-data path\to\session --view raw --max-points 2000 --json
labdaq export csv path\to\session --view raw --name raw-data --json
labdaq export figure path\to\session --kind overlay --view filtered `
  --recipe baseline-v1 --format png --name filtered-overlay --json
```

M07 hardware evidence is simulator-backed only; static export makes no claim
about wiring, sensor power, conditioner polarity, or physical channel mapping.
See [plotting and export](plotting-export.md), the
[CLI reference](cli/plotting-export.md), and ADR-0016/0017.

## M10 live operator workflow

`application.live_service` owns a run after the M09 handoff. Construct it with a
factory that returns a fresh `RecordingSessionService`; attach its display worker
before `start()`. Never add another reader, DAQ adapter, or Qt-owned sample queue.
The display worker advances causal processing and the fixed raw/conditioned rings.
Qt workers may request immutable, decimated snapshots, but widgets must only be
mutated by queued callbacks on the GUI thread.

The default live limits are ten seconds retained, five seconds visible, 2,000
points/channel, 100 ms requested refresh, 500 ms metrics, and a one-second metric
window. Pause and scale state belong to presentation. Do not stop draining the
M04 display stream when the operator pauses rendering. A processing-source gap
must reset live filter state and conditioned history; it must not fault or relabel
the independent recording path.

`application.review_service` composes M05 full inspection, bounded adapter-parsed
events, M06/M07 plot data, exports, and adapter-discovered export manifests. All
review paths must reopen disk. GUI modules may import PySide6/PyQtGraph and domain
or application contracts, but not NI, npTDMS, SciPy, Matplotlib, storage
implementations, or serialization APIs.

Thread/event flow:

```text
DAQ adapter -> M04 read worker
  -> lossless FIFO -> M05 recording worker -> HDF5/session finalization
  -> superseding FIFO -> M10 display worker -> fixed raw/conditioned rings
Qt timer -> keyed frame worker -> immutable frame/metrics -> Qt callback -> widgets
terminal result -> finalizer worker -> full disk inspection -> shared review page
review/export request -> keyed worker -> M05/M06/M07 -> Qt callback -> widgets
```

Supported close during a run is asynchronous: request stop, await terminal
recording/finalization/cleanup, then close. Avoid waiting for that sequence on the
Qt thread. See [ADR-0023](adr/0023-live-operator-buffering-and-disk-review.md).

Focused M10 checks:

```powershell
$env:QT_QPA_PLATFORM="offscreen"
pytest tests\unit\application\test_m10_live_service.py `
  tests\integration\test_m10_operator_workflow.py tests\gui -q
pytest tests\architecture -q
```

## M10A open-acquisition workflow

`application.open_acquisition_service` owns a continuous monitor lifecycle and
its nested detachable recording lifecycle. It reuses one M04 engine and drains
both fan-out queues continuously. Do not add a GUI reader or second DAQ task.
The selected raw channels enter the rolling ring and segment writer in stable
configured order, which remains attached as source provenance.

The writer-initialization bridge is bounded and lossless. Source chunks outside
a recording are deliberately discarded after their display data is retained.
Every segment is locally rebased and finalized through
`RecordingSegmentCompletion`, which deliberately carries no hardware cleanup
claim. Segment storage failure and monitor DAQ failure have separate terminal
effects. See [ADR-0029](adr/0029-detachable-open-acquisition-recording.md).

Focused M10A checks:

```powershell
$env:QT_QPA_PLATFORM="offscreen"
pytest tests\unit\application\test_open_acquisition_service.py `
  tests\integration\test_open_acquisition_workflow.py tests\gui -q
```

## ADRs

The dev7 Windows preview uses an elevated all-users NSIS installer. Keep registry
operations in the 64-bit HKLM view, shell shortcuts in the all-users context, and
normal application launches unelevated. `release_metadata.py inventory` generates
the UTF-16LE owned-file list after PyInstaller finishes. The shared path-policy
include is exercised by a non-elevating NSIS harness against temporary directories;
it does not install the application or write shared registry keys. Run
`pytest tests/unit/packaging -q --no-cov` for those checks. Runtime tests require
Windows and `.tools/nsis/makensis.exe`; they skip explicitly elsewhere.

The build script fails on unsuccessful Python, PyInstaller, and NSIS commands.
Retain old versioned release artifacts, freeze the source identity before building,
and only mark manifest acceptance fields PASS after their checks complete.
Full administrator/UAC, second-user, upgrade, and uninstall acceptance belongs in
a disposable Windows environment. Do not infer those results from the harness.

Create an ADR for persistent schemas, public ports, concurrency policies, event
semantics, processing recipes, hardware compatibility, major dependencies, or
durable tradeoffs. Follow [the ADR process](adr/README.md).

## Legacy material

Offline spectral eligibility belongs to `PlotDataService`: group channels by actual
input stage and retain per-series `spectral_input_stage` in bundles and sidecars.
Keep this policy out of GUI numerical code and storage adapters. Raw-only channels
use raw voltage even in mixed-stage spectra; raw-diagnostic sessions reject non-raw
requests. See [ADR-0032](adr/0032-raw-voltage-spectra-in-session-review.md).

`references/legacy/ReadAllChannels.py` is excluded from formatting, typing,
architecture checks, and package discovery. It may be inspected for historical
channel intent only.
