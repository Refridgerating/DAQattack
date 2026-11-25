# DAQ-M10 Acceptance Evidence

Status: Software/simulator accepted on Windows; physical NI workflow pending

## 2026-09-08 review-panel window controls

All eight session-review panels now support minimize, maximize, and restore inside
the existing workspace. A horizontally scrollable restore bar keeps minimized
panels reachable, including when every panel is minimized. Escape restores a
maximized panel; Reset layout reveals all panels with the default proportions.
The FFT/PSD/Extrema tabs remain one panel. Page-level analysis, detection, export,
progress, and status controls remain visible during maximization.

### Changes and architecture

- Added `src/labdaq/gui/review_layout.py` and `tests/gui/test_review_layout.py`.
- Updated the review-page construction in `src/labdaq/gui/main_window.py` and
  simulator/regression coverage in `tests/gui/test_m09_desktop.py`.
- Updated the GUI and developer guides, M10 specification, and roadmap note.
  No files removed.
- The GUI controller changes nested-branch visibility without recreating or
  reparenting plot widgets. It retains normal splitter proportions and panel
  states in memory. No public service, port, persisted schema, preference format,
  processing, or acquisition change was required.

### Verification and acceptance

- Focused command: `python -m pytest tests/gui/test_review_layout.py tests/gui/test_m09_desktop.py tests/integration/test_m10_operator_workflow.py tests/architecture -q --no-cov --durations=5`.
  Result: **87 passed** on Windows with `QT_QPA_PLATFORM=offscreen`.
- Qt tests cover all eight panels, empty nested branches, all panels minimized,
  narrow restore-bar scrolling, Escape, reset, resizing, page navigation, and
  fresh-window defaults. Existing draggable splitters and unavailable-plotting
  placeholders remain covered.
- Full-suite command: `python -m pytest -q --durations=5` using the repository's
  `.venv-m00` interpreter and `QT_QPA_PLATFORM=offscreen`. Result: **634 passed,
  6 skipped in 165.76 seconds**, **85.64% coverage** (85% required). This includes
  the added saved-session reload regression preserving minimized/maximized state.
- The simulator GUI workflow completes qualification, acquisition, hidden-panel
  analysis, transient detection, and CSV/PNG/SVG exports. Layout transitions
  preserve plot objects, data, zoom, cursor range, channel selection, and tab state.
- `ruff format --check .`, `ruff check .`, `mypy src`, and `lint-imports` pass:
  242 formatted files, 92 typed source files, and five kept import contracts.
- Offscreen normal/maximized layouts were inspected; this is software evidence,
  not physical NI or target-display acceptance.

Panel state lasts only for the current application window. Individual charts,
live-acquisition panels, detached windows, and persistent layout preferences are
outside this change. No installer was rebuilt or installed. Existing physical
M10 acceptance remains pending; this presentation change requires no hardware.

## 2026-09-08 ai3 voltage FFT/PSD correction

Checked raw-only ai3 now appears in offline FFT/PSD as raw voltage, including
alongside calibrated or conditioned acceleration channels. Raw-diagnostic and
saved open-acquisition recordings also permit voltage spectra. Per-channel stage,
unit, disposition, warning, and absent diagnostic calibration are retained through
plot bundles and CSV/PNG/SVG sidecars. See
[ADR-0032](../adr/0032-raw-voltage-spectra-in-session-review.md).

### Changes and architecture

- Added `docs/adr/0032-raw-voltage-spectra-in-session-review.md`; no files removed.
- Application/contract changes: `src/labdaq/application/plotting_service.py`,
  `src/labdaq/application/export_service.py`, `src/labdaq/ports/plotting.py`, and
  `src/labdaq/resources/schemas/v1/export-sidecar.schema.json`.
- GUI changes: `src/labdaq/gui/main_window.py` and `src/labdaq/gui/signal_plot.py`.
  Product-specific selection, unit-separated spectra, explicit exclusions,
  captured request stage, and clearing failed/obsolete results remain presentation work.
- Regression updates: `tests/integration/test_m10_operator_workflow.py`,
  `tests/integration/test_plotting_and_export.py`,
  `tests/integration/test_open_acquisition_workflow.py`,
  `tests/gui/test_m09_desktop.py`, and `tests/architecture/test_documentation.py`.
- Documentation updated: README, architecture, roadmap, M10 specification,
  GUI/developer/plotting/CLI/hardware-qualification guides, ADR index and ADRs
  0025/0026, and M07/M10/M10A evidence.

Application services resolve each spectral channel's actual stage and process
stage groups through existing numerical routines. The optional per-series
`spectral_input_stage` also appears in v1 sidecars; old sidecars still validate.
Source-session schemas, acquisition, live processing, and hardware eligibility
are unchanged. No installer was built and the installed software was not updated.

### Verification and acceptance

- Deterministic 80 Hz, 1 mV ai3 fixture: selected interior source range, correct
  FFT peak/amplitude, PSD integrated power, stable channel order and labels, and
  identical raw-ai3 output across raw/calibrated/conditioned requests.
- Mixed and ai3-only requests, different replay chunk sizes, diagnostic session
  restrictions, open-acquisition segment spectra, and missing recipe provenance
  are exercised without hardware.
- FFT/PSD CSV values and PNG/SVG metadata include ai3 with null calibration;
  raw data, manifest, and recipe bytes remain unchanged. Existing legacy-session
  workflows remain in the full regression suite.
- GUI coverage includes the simulated timed functional run, mixed g/V and
  g^2/Hz/V^2/Hz axes, ai3-only analysis, tab/stage changes, raw-diagnostic review,
  checked-channel exclusions, and failed spectra clearing stale curves.
- Focused command: `python -m pytest -q tests/integration/test_m10_operator_workflow.py tests/integration/test_plotting_and_export.py tests/integration/test_open_acquisition_workflow.py tests/gui/test_m09_desktop.py tests/unit/plotting/test_records_and_decimation.py tests/architecture --no-cov --tb=short`.
  Result: 101 passed; the additional timed functional GUI regression also passed.
- Quality commands: `ruff format --check .`, `ruff check .`, `mypy src`, and
  `lint-imports` pass (240 formatted files, 91 typed source files, five contracts).
- Final full-suite command: `python -m pytest -q --cov-report=term:skip-covered`.
  Result: **622 passed, 6 hardware-skipped, 85.50% coverage**, in 152.14 seconds.
  The repository's 85% coverage gate passes. All commands ran in `.venv-m00`.
- The full suite includes the raw-diagnostic FFT CLI check, CSV/PNG/SVG exports,
  HDF5 simulator workflows, and schema-v1 TDMS compatibility checks.

All requested software acceptance criteria are satisfied. The initial full-suite
run passed its tests but fell below coverage at 84.95%; the final run includes the
additional actual GUI mixed-stage/failure workflow and missing-provenance checks.

Physical NI acceptance remains pending. The original installed session was not
inspected; the correction follows recorded channel metadata, not physical channel
number. No hardware qualification or charge-chain calibration is claimed.

## Implemented scope

- Application-owned live-acquisition coordination over one existing M04 DAQ read,
  one lossless recording path, and the existing bounded disposable display path.
- Fixed-capacity raw and causal `baseline-v1` history, on-demand peak-preserving
  frames, rolling M06 metrics, display degradation, immutable consolidated status,
  idempotent stop/close, and finalized-run summaries.
- Disk-backed session review through M05 inspection/replay, M06 processing, and M07
  plot/export services, including bounded events and export-artifact discovery.
- Acquisition and Sessions/Review desktop pages with raw and conditioned live plots,
  status and metrics, pause/resume, viewport and scaling controls, automatic finalized
  review with a full-run raw plot from reopened TDMS data, all five supported plot
  representations, selected half-open ranges, and CSV/PNG/SVG export.
- Background start, finalization, replay, processing, and export work; GUI-thread-only
  immutable result rendering; orderly asynchronous stop-and-close; and post-recovery
  fault actions without automatic task retry.
- PyQtGraph in the optional `gui` extra with lazy GUI imports and explicit disabled
  capabilities when a required storage, analysis, export, NI, or plot dependency is
  unavailable.
- ADR-0023 records the single-stream, fixed-buffer, on-demand-frame, causal-processing,
  and disk-backed-review decisions. Persistent session and export schemas are unchanged.

Changed files are grouped by ownership:

- Domain/application contracts and services: `ports/acquisition.py`,
  `ports/session.py`, `ports/operator.py`, `application/acquisition_engine.py`,
  `application/readiness_service.py`, `application/session_service.py`,
  `application/live_service.py`, and `application/review_service.py`.
- Adapters and composition: `adapters/tdms/session.py`,
  `adapters/export/history.py`, export adapter initialization, and `bootstrap.py`.
- GUI: `gui/presentation.py`, `gui/main_window.py`, `gui/signal_plot.py`, and GUI
  package/entry-point labels.
- Tests: M10 unit and simulator integration suites plus expanded GUI, architecture,
  record, and ADR tests.
- Packaging and documentation: `pyproject.toml`, README, operator/developer/fault and
  plotting/export guides, ADR-0023 and its index, the M10 specification, and roadmap.

No files were removed.

## Automated evidence

Final local Windows results:

- `ruff format --check .`: passed, 195 files formatted.
- `ruff check .`: passed.
- `mypy src/labdaq`: passed in strict mode for 80 source files.
- `lint-imports`: 5 contracts kept, 0 broken.
- `pytest -q` with `QT_QPA_PLATFORM=offscreen`: 453 passed, 6 physical-hardware
  tests skipped, 85.03% total coverage.
- `python -m build`: source distribution and wheel built successfully.
- `python -m labdaq.gui --help`: passed and identifies the M10 desktop.
- Timed offscreen simulated GUI smoke: the window opened, closed through its normal
  idle path, and exited with result 0.

### 2026-08-13 setup-page operability regression

- The Experiment Setup page was verified at a 1760 by 776 client size without
  forcing the window beyond the requested height. Session destination, readiness,
  and acquisition handoff remain prominent while the complete calibration form is
  reachable through vertical scrolling.
- The channel overview displays the schema-backed roles `accelerometer_x`,
  `accelerometer_y`, `accelerometer_z`, and `conditioned_charge`.
- The regression confirms that a missing session parent and missing qualification
  remain the two explicit initial blockers; no destination is created implicitly.
- Focused GUI and M10 simulator verification passed: 10 tests in 19.18 seconds. The
  user-action workflow selected a temporary destination, ran simulated preflight,
  acquired and persisted 500,000 samples per channel, finalized the session, and
  reopened it for review.
- Full verification passed with 454 tests, 6 physical-hardware tests skipped, and
  85.01% total coverage. Formatting, lint, strict typing, all five import contracts,
  source/wheel builds, and the GUI help entry point also passed.

### 2026-08-13 plot-workspace operability regression

- At a 1760 by 776 client size, Acquisition assigns approximately 75% of its
  resizable workspace to simultaneous raw and conditioned plots. Sessions/Review
  assigns approximately 60% to its disk-backed review plot.
- Visible eight-pixel splitter handles resize acquisition status/metrics, live plots,
  session browser/details, review channels/canvas, event/artifact diagnostics, and
  multiple compatible-unit or independent plot canvases without allowing supporting
  tiles to disappear completely.
- The header navigation control hides and restores the left pane, expands the active
  page, and preserves the selected page. Navigation and splitter proportions reset
  to visible plot-focused defaults on each launch.
- The empty-plot state reserves canvas space before the first frame, and missing
  PyQtGraph fallback labels remain usable inside the same resizable workspaces.
- Focused GUI and M10 simulator verification passed: 12 tests in 18.39 seconds,
  including qualification, 500,000 acquired and persisted samples per channel,
  automatic review, all five plot representations, and CSV/PNG/SVG exports.
- Full verification passed with 456 tests, 6 physical-hardware tests skipped, and
  85.02% total coverage. Formatting, lint, strict typing, all five import contracts,
  source/wheel builds, and the GUI help entry point also passed.

### 2026-08-14 automatic finalized-run plotting regression

- Finalized runs now reopen through M05/npTDMS and automatically submit a full-range
  raw plot for every recorded channel through the existing M07 bounded plotting path.
  Completed, orderly-stopped, and recoverable faulted sessions share this GUI path.
- Sessions without persisted raw samples keep their summary, events, and diagnostics
  visible and do not submit an empty plot request. Sessions opened manually from the
  browser retain the explicit **Plot range** workflow.
- Focused offscreen GUI verification passed: 2 tests in 20.51 seconds, including a
  500,000-sample/channel finalized session, a 1,000-sample/channel raw diagnostic,
  full half-open ranges, all-channel rendering, stopped/faulted routing, and the
  zero-sample guard.
- Full verification passed with 486 tests, 6 physical-hardware tests skipped, and
  85.22% total coverage. Formatting, lint, strict typing, and all five import
  contracts passed. No physical NI hardware claim was added.

The new full-service simulator integration acquired and persisted 2,000 samples per
channel, reopened the finalized TDMS session, and exercised causal review plus CSV
export. Live raw data and source indices matched replay exactly. Live causal
`baseline-v1` data matched replay with zero relative tolerance and absolute tolerance
`1e-12`.

The two-minute real-time four-channel stress at 50 kS/s per channel recorded:

- 6,000,000 acquired and 6,000,000 persisted samples per channel.
- 0 acquisition gaps, 0 persistence-loss samples, and 0 display drops.
- Maximum recording queue depth 1 and maximum display queue depth 1.
- 1,000,000 samples acquired and persisted during a 20-second display pause.
- 1,414 on-demand render requests; the final five-second, 250,000-sample source
  viewport was represented by at most 2,000 peak-preserving points per channel.
- Completed duration reason, available raw/manifest source artifacts, metrics
  available, and final integrity result `pass`.

Existing simulator fault and recovery coverage remains green for explicit display
overflow, reader timeout and disconnect, writer/storage failure, partial-session
replay, duplicate and manual stop commands, final chunks, export collision/cancel,
and bounded-memory acquisition. M10 unit, integration, GUI, and architecture tests
add coordinator, pause/resume, disk-review, export-history, fault-presentation,
automatic-review, and orderly-close coverage at the new boundaries.

## Raw-diagnostic launch and commissioning qualification repair (2026-08-14)

The commissioning plan now retains the discovery and capability definitions
required by the runner, so `preflight-v1` completes instead of faulting with
`KeyError('discovery-identity')`. Invalid bound plans fail before hardware
access with `DAQ.QUALIFICATION.PLAN_INVALID`.

The GUI also provides a separately authorized `raw-diagnostic` launch. Simulator
evidence confirms four equal persisted raw-voltage rows, raw-only live display,
null qualification attachment, durable operator/hardware-validation provenance,
disk reopen, raw export, and fail-closed calibrated/conditioned/FFT/PSD requests
under the original diagnostic policy. ADR-0032 now permits offline raw-voltage
FFT/PSD; calibrated/conditioned diagnostic requests still fail closed.
The qualified launch intent remains unchanged. The full suite reports 486 passed,
6 hardware-skipped, and 85.20 percent branch coverage.

Physical NI execution of the diagnostic authorization, recording, stop, reopen,
and count comparison remains pending.

## Acceptance disposition

### 2026-08-18 non-destructive review post-processing enhancement

- Sessions / Review now keeps a time waveform visible with a synchronized draggable
  half-open range, explicit raw/calibrated/conditioned stage, FFT and PSD tabs, and
  maximum/minimum/absolute-peak locations for each checked channel.
- Automatic waveform suggestions use a deterministic bounded two-pass median/MAD
  detector. Detections remain separate per channel; selecting one applies its padded
  range to all checked synchronized channels. Detector settings and manual cursors
  are operator-visible and ephemeral.
- Unit evidence covers detector validation, zero-MAD impulses, padding, merging,
  separate channels, nonfinite inputs, earliest extrema ties, signed absolute peaks,
  and chunk-boundary invariance.
- Simulator HDF5 integration reopens a finalized source, detects raw-stage events,
  computes located extrema, builds a raw FFT, exports the selected range, and confirms
  that the logical raw digest is unchanged. GUI evidence covers event-table cursor
  selection and extrema rendering.
- This is software and simulator evidence only. It does not establish physical event
  thresholds, physical signal quality, or target-computer performance.
- Final verification passed with 504 tests, 6 physical-hardware tests skipped, and
  85.02% total branch coverage. Formatting, lint, strict typing, all five import
  contracts, and the architecture suite passed.

All software and simulator criteria are accepted. In particular, automated evidence
confirms the application-only GUI boundary, one DAQ read, bounded display memory,
recording isolation, GUI event-loop responsiveness, disk-backed review, deterministic
range selection, scientific live/replay agreement, immutable raw data, and export
provenance.

NI discovery on 2026-08-14 reported NI-DAQmx driver 16.1.0, Python package
`nidaqmx` 1.6.0, accessible cDAQ-9171 serial `0x024D68A5`, and NI-9239 (BNC)
serial `0x024D471A` with ai0-ai3, differential +/-10 V, simultaneous sampling,
and 50 kS/s capability. This verifies discovery only. The following physical
criteria remain pending:

- Qualification of the discovered target cDAQ-9171 and NI-9239.
- Verification of wiring, sensor excitation, grounding, voltage range, external
  conditioner sensitivity/polarity, and calibration certificates.
- Four physical channels at 50 kS/s per channel for two minutes while exercising
  pause, visibility, viewport, scaling, stop, review, and export controls.
- Physical task cleanup after normal stop, GUI stop-and-close, timeout, disconnect,
  and storage fault.
- Operator-observed responsiveness and plotting behavior on the target laboratory
  computer.

No physical acquisition, safety, signal-quality, or target-computer performance claim
is made from simulator results.

## Remaining assumptions and limitations

- Only the accepted causal `baseline-v1` recipe is exposed; zero-phase processing and
  a general recipe editor remain out of scope.
- Live display-source gaps deliberately clear conditioned history and reset filter
  state. This is display degradation, not proof of authoritative data loss; disk
  review remains the source of record.
- Acquired-versus-persisted lag is progress while running and is classified as loss
  only from terminal evidence.
- Formal ten-minute physical soak, 100-cycle start/stop, installer, and clean-machine
  release acceptance remain DAQ-M11 work.
