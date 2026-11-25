# DAQ-M10: Live Acquisition, Plots, Stop Control, and Post-Run Analysis

> Historical specification: raw monitoring and optional recording are the primary workflow under [DAQ-M12](DAQ-M12-raw-first-monitoring.md); qualification is optional. Existing runtime behavior is unchanged until implementation.

Status: software/simulator accepted; physical NI workflow pending  
Roadmap phase: operator workflow  
Depends on: DAQ-M04, DAQ-M05, DAQ-M06, DAQ-M07, DAQ-M08, and DAQ-M09  
Enables: DAQ-M11

2026-09-08 review correction: checked raw-only ai3 participates in offline FFT/PSD
as raw volts alongside acceleration spectra. Saved raw-diagnostic/open-acquisition
sessions also allow voltage spectra, without calibrated interpretation. Per-channel
stage provenance and existing unit-separated axes make mixed spectra explicit.
See [ADR-0032](../docs/adr/0032-raw-voltage-spectra-in-session-review.md) and
[acceptance evidence](../docs/milestones/DAQ-M10-acceptance-evidence.md).

## 1. Goal

Complete the laboratory operator workflow by connecting the DAQ-M09 desktop shell to the already tested acquisition, recording, processing, plotting, replay, and export services.

At the end of this milestone, an operator shall be able to configure and qualify the supported four-channel experiment, start acquisition, observe live signals and system health, stop safely, review the finalized or partial session, generate offline time and frequency products, and export selected results without using the CLI.

The GUI remains a client of application services. It does not become an acquisition engine, recorder, signal-processing implementation, or source of scientific truth.

## 2. Milestone outcome

DAQ-M10 is complete when the graphical application supports one uninterrupted workflow:

1. Open or create an experiment configuration.
2. Confirm backend validation and qualification state.
3. Start a four-channel acquisition at the configured rate.
4. Observe live raw and conditioned signals without affecting recording.
5. Observe acquisition, persistence, display, warning, and fault status.
6. Pause, resume, rescale, or reconfigure the display without pausing the DAQ task.
7. Request an orderly stop.
8. Wait for the acquisition engine to stop, the recorder to drain, and the session to finalize.
9. Review the completed or partial session from disk.
10. Select channels and time ranges.
11. Generate calibrated time histories, conditioned signals, FFT, and PSD products.
12. Export data and figures with complete provenance.

## 3. Scope

This milestone includes:

- GUI orchestration of existing application services
- Start and orderly stop controls
- Live status and progress presentation
- Live raw-voltage plots
- Live calibrated and baseline-conditioned plots
- Live metrics produced by backend processing services
- Bounded display buffering and explicit display-drop accounting
- Display pause and resume behavior
- End-of-run summary
- Automatic transition from acquisition to session review
- Replay-backed post-run plotting
- Offline baseline processing, FFT, and PSD generation
- Channel and time-range selection
- CSV and figure export through DAQ-M07 services
- Fault presentation that preserves partial sessions and diagnostics
- GUI tests, backend integration tests, documentation, and acceptance evidence

## 4. Explicit non-goals

DAQ-M10 shall not add:

- A second NI task for plotting
- A second authoritative raw dataset
- GUI-owned file writing or serialization
- GUI-owned filtering, FFT, PSD, calibration, or decimation
- User-defined processing recipes or a recipe editor
- Trigger configuration beyond contracts already completed in earlier milestones
- Multi-session acquisition
- Multi-device synchronization
- Modal analysis, coherence, transfer functions, or order tracking
- Automated IV or vacuum experiment sequencing
- Release packaging or installer work assigned to DAQ-M11
- Final long-duration laboratory qualification assigned to DAQ-M11

## 5. Architectural constraints

### 5.1 GUI boundary

The PySide6 layer may:

- Submit commands to application services
- Subscribe to immutable status, event, plot-frame, metric, and session-summary records
- Maintain transient presentation state such as selected tab, visible channels, viewport, and display pause
- Render backend-produced plot data
- Request replay, processing, plot-data generation, and export operations

The PySide6 layer shall not:

- Import `nidaqmx`
- Read directly from an NI task
- Open or write TDMS files
- Write the session manifest or event log
- Apply calibration coefficients
- Execute SciPy filters
- Calculate FFT, PSD, or time-domain metrics
- Decide whether data loss occurred
- Mutate raw samples
- Infer session completion from widget state

Architecture tests shall enforce these boundaries.

### 5.2 Single acquisition stream

The acquisition engine established in DAQ-M04 remains the only source of live samples.

```text
DAQ task
  -> acquisition engine
      -> lossless recording queue -> recorder -> authoritative session
      -> bounded display queue -> plot-data service -> GUI
```

The recorder receives every acquired chunk unless the run faults. The display path may discard superseded frames when the GUI cannot keep up. Display loss must never be reported as acquisition or recording loss.

### 5.3 Disk-backed post-run review

After finalization, the post-run review shall reopen the session through the DAQ-M05 parser and replay interfaces. It shall not treat the live plot buffer as the source record.

This requirement proves that the saved session is sufficient for later interpretation and prevents live-only state from becoming hidden data provenance.

### 5.4 Immutable raw data

All calibrated, conditioned, decimated, spectral, and exported outputs are derivatives of the format-dispatched raw container and session metadata. Raw samples shall never be overwritten.

### 5.5 Thread ownership

- Qt widgets are updated only on the Qt GUI thread.
- Acquisition, recording, replay, processing, and export work run outside the GUI thread.
- Cross-thread communication uses immutable records, queued signals, or application-level event subscriptions.
- No GUI callback may perform blocking DAQ, disk, replay, processing, or export work.

## 6. Operator workflow

### 6.1 Ready state

The acquisition page shall show:

- Active hardware profile
- Active experiment configuration
- Active baseline recipe version
- Hardware discovery state
- Qualification result
- Selected session parent directory
- Filename preview
- Start eligibility
- Validation errors and warnings

Start is enabled only when the backend start-eligibility result is valid. The widget layer shall not reproduce or override validation rules.

### 6.2 Starting

When the operator selects Start:

1. The GUI submits one start command to the application service.
2. The start control becomes disabled immediately to prevent duplicate commands.
3. The state changes through configured or armed states into running according to the DAQ-M04 state machine.
4. The session identity and path become visible as soon as the application allocates them.
5. Plot panels initialize from backend channel metadata and units.
6. Any start failure is presented as a structured fault or validation failure.

The GUI shall not report running until the acquisition service emits the running state.

### 6.3 Running

During acquisition, the operator shall see at least:

- Elapsed acquisition time
- Requested duration, when duration-based operation is active
- Acquired sample count per channel
- Persisted sample count per channel
- Recording queue depth or normalized health state
- Display-frame drop count
- Acquisition or storage gap count
- Warning count
- Fault state
- Active sample rate
- Active channel order
- Session path

Status values shall come from backend snapshots or events. Counts shall be monotonic except when a new session begins.

### 6.4 Stopping

The normal Stop control requests an orderly stop through the application service.

An orderly stop shall:

1. Transition the state to stopping.
2. Prevent new stop requests from creating duplicate work.
3. Stop acquisition at the next safe engine boundary.
4. Deliver the final valid chunk to the recorder.
5. Drain the recording queue.
6. Close the DAQ task.
7. Finalize the manifest and event log.
8. Emit a completed, stopped, or faulted session summary.
9. Open the finalized session in the review workflow.

The GUI shall not mark the run complete merely because the Stop button was pressed.

### 6.5 Fault path

On acquisition, hardware, storage, processing, or finalization fault:

1. The application service owns shutdown and session recovery.
2. The GUI presents the typed fault code, severity, operator-facing summary, recommended action, and session path.
3. The partial session remains available if any source record was created.
4. The session manifest records the actual completion state.
5. The GUI offers access to the partial session summary and diagnostic report.
6. The operator may return to configuration only after resource cleanup is confirmed.

The fault dialog must not block recorder draining or session finalization.

## 7. Live display requirements

### 7.1 Raw-voltage view

The raw view shall present voltage samples for all four configured channels using channel identity and units supplied by the backend.

Required controls:

- Channel visibility
- Shared or independent vertical scaling
- Manual vertical range
- Autoscale request
- Visible time window
- Display pause and resume
- Cursor or coordinate inspection where supported by the selected plotting library

The raw view displays backend plot frames. It does not read the DAQ task or replay storage directly.

### 7.2 Calibrated and conditioned view

The conditioned view shall expose:

- Calibrated engineering units for each channel
- Output from the active causal `baseline-v1` live recipe
- Clear labeling of the active recipe version
- Clear labeling of units after each conversion stage
- A visible distinction between raw voltage and processed engineering quantities

Only live-compatible causal stages may be used. Offline-only, zero-phase, or noncausal operations shall not appear in the live path.

### 7.3 Plot frame generation

The backend plot-data service shall transform acquisition chunks into viewport-appropriate display frames.

Plot frames should contain at least:

- Session ID
- Frame sequence number
- Source sample-index start and end
- Relative-time start and end
- Channel identities
- Units
- Data arrays prepared for rendering
- Processing recipe identity, when conditioned
- Count of raw samples represented
- Decimation method or version

The plot service shall preserve time span and extrema according to the DAQ-M07 decimation contract.

### 7.4 Bounded display behavior

The display path is best effort and bounded.

- The GUI shall never accumulate an unbounded backlog of live frames.
- When the GUI is slow, older unrendered display frames may be superseded by newer frames.
- Each discarded display frame increments a display-drop counter.
- Display drops do not increment acquisition-gap or persistence-loss counters.
- The latest available frame shall be preferred after a display backlog.
- Plot rendering shall consume bounded memory for an arbitrarily long acquisition.

The initial implementation should target approximately 10 plot refreshes per second and should make the refresh target an application or presentation configuration rather than a constant embedded in widgets.

### 7.5 Visible time window

The live plot shall maintain a fixed-duration rolling window selected by the operator. The initial default should be 5 seconds.

Changing the visible window:

- Changes only plot-data requests and presentation buffers
- Does not reconfigure the DAQ task
- Does not alter the recording queue
- Does not alter stored samples
- May cause the backend plot service to choose a different decimation level

### 7.6 Pause display

Pause freezes the visual presentation only.

While paused:

- Acquisition continues
- Recording continues
- Status counters continue updating
- The bounded display path may continue replacing or discarding frames
- The currently displayed frame remains unchanged
- Display-drop counts remain visible and accurate

On resume, the GUI shall render the newest available display frame rather than replaying a stale frame backlog.

The control shall be labeled `Pause display` or equivalent. It shall not be labeled simply `Pause`, which could imply acquisition pause.

### 7.7 Axis scaling

Axis changes are presentation operations.

- Autoscale shall operate on the displayed frame or a bounded backend summary.
- Manual limits shall persist during the active session unless reset.
- Unit changes require backend conversion and may not be implemented as relabeling.
- Shared scaling shall only combine channels with compatible units.

## 8. Live metrics

Live metrics shall be computed by backend processing services over a declared rolling sample window.

At minimum, expose metrics already validated in DAQ-M06 when applicable:

- RMS
- Peak
- Peak-to-peak
- Crest factor
- Clipping fraction

The interface shall state:

- Metric update interval
- Rolling-window duration or sample count
- Signal stage used, such as calibrated or baseline-conditioned
- Effective bandwidth or filter recipe
- Units

Recommended initial defaults:

- Metric update interval: 500 ms
- Rolling metric window: 1 second
- Signal stage: baseline causal output

These defaults shall be configurable outside widget implementation and recorded in diagnostics when relevant.

Live metric computation shall not delay acquisition or recording. If metric computation falls behind, it shall follow an explicit bounded or superseding policy and emit a warning if metrics become stale.

## 9. Acquisition status model

The GUI should consume one consolidated immutable status snapshot rather than assembling run truth from unrelated widget callbacks.

The status record should include or reference:

- Session ID
- Experiment name
- Acquisition state
- Start time
- Elapsed time
- Requested duration
- Actual sample rate
- Channel order
- Acquired samples
- Persisted samples
- Recording queue health
- Display queue health
- Display frames produced
- Display frames dropped
- Detected acquisition gaps
- Detected persistence loss
- Clipping count or fraction summary
- Warning count
- Current fault, when present
- Session path
- Finalization state

Exact class names are implementation choices. The information and ownership boundaries are requirements.

## 10. End-of-run summary

When the application reports that the run has finalized, the GUI shall display a summary containing at least:

- Session identity and path
- Experiment name and operator metadata
- Completion state
- Start and end times
- Requested and acquired duration
- Actual sample rate
- Channel list and units
- Acquired sample count
- Persisted sample count
- Acquisition gaps
- Persistence loss
- Display drops
- Clipping, warnings, and faults
- Hardware profile and discovered hardware identity
- Qualification report identity
- Baseline recipe version
- Manifest, event log, and raw-data availability

The summary shall distinguish:

- Completed normally
- Stopped by operator
- Faulted with recoverable partial session
- Incomplete or unfinalized session

The GUI shall not convert a faulted or incomplete session into a successful state because files are present.

## 11. Post-run session review

### 11.1 Source of review data

Review data shall be loaded through the session parser and replay services from DAQ-M05.

The review page shall not rely on:

- The last live plot frame
- The live rolling buffer
- Widget-held metric values
- Unsaved GUI state

### 11.2 Review views

Provide at least:

- Session metadata summary
- Raw voltage time history
- Calibrated time history
- Baseline-conditioned time history
- FFT amplitude
- PSD
- Events, warnings, and fault summary
- Export history or generated artifact list where available

### 11.3 Channel selection

The operator may select one or more channels for review. Channel labels shall include stable logical identity and units. Physical channel names may be shown as secondary metadata.

### 11.4 Time-range selection

The operator may select a review interval using relative time, with sample index remaining authoritative.

The application service shall convert selected time boundaries into deterministic sample-index boundaries and return the actual included range.

Range selection shall:

- Clamp or reject values outside the recorded session
- Preserve channel alignment
- Use the same sample range for overlaid channels
- Record the selected sample range in derived-artifact provenance
- Avoid loading the complete session into memory when a bounded range is requested

### 11.5 Offline processing

Offline processing may use stages identified by DAQ-M06 as offline-only. Any zero-phase or noncausal result must be clearly labeled as offline.

DAQ-M10 shall expose the established baseline recipe and its valid offline form. It shall not introduce a general recipe editor.

### 11.6 FFT and PSD

FFT and PSD requests shall be delegated to DAQ-M06 processing services and DAQ-M07 plot-data services.

The review interface shall display:

- Selected channel
- Selected sample range
- Sample rate
- Window
- Overlap, when applicable
- Normalization
- Frequency units
- Amplitude or density units
- Recipe and software version

The GUI shall not duplicate spectral formulas.

## 12. Export workflow

Exports shall use DAQ-M07 services.

The operator shall be able to export:

- Selected raw channels and time range to CSV
- Selected calibrated or conditioned channels and time range to CSV
- Time-history figures to PNG and the supported vector format
- FFT and PSD figures to PNG and the supported vector format

Each export shall:

- Be written under the session `exports/` or `plots/` directory unless the existing export contract allows an explicit alternate destination
- Refuse silent overwrite
- Include or reference metadata sufficient to identify the source session
- Record selected channels and sample range
- Record calibration provenance
- Record processing-recipe version
- Record software and schema versions
- Record spectral parameters where applicable
- Become visible in the session artifact listing or export manifest

Long exports shall run outside the GUI thread and report progress, cancellation, completion, or fault through application-level records.

## 13. Fault and warning presentation

### 13.1 Required distinction

The GUI shall separately represent:

- Informational events
- Warnings
- Acquisition faults
- Storage faults
- Processing or export faults
- Display degradation

A dropped display frame is not an acquisition fault. A stale metric is not a storage fault. These distinctions shall remain visible in status and logs.

### 13.2 Fault dialog content

The operator-facing fault dialog shall include:

- Fault code
- Severity
- Plain-language summary
- Technical detail that can be copied
- Recommended operator action
- Acquisition state
- Session path, when allocated
- Whether the session is recoverable
- Whether raw data and manifest are available
- Acquired and persisted sample counts
- Diagnostic report location or viewer action

The dialog may offer actions such as:

- Open session review
- Open session folder
- View diagnostics
- Copy technical details
- Return to configuration after cleanup

The dialog shall not offer a retry that reuses a still-reserved or faulted DAQ task.

### 13.3 GUI close behavior

If the operator attempts to close the GUI during a running or stopping acquisition, the GUI shall not terminate the process silently.

It shall offer an orderly stop-and-close path and a cancel-close path. The normal close path waits for backend resource cleanup and session finalization. Process termination outside the normal path remains recoverable through DAQ-M05 and is formally stress-tested in DAQ-M11.

## 14. Required deliverables

### 14.1 Application services and contracts

Add or complete application-facing contracts for:

- Acquisition start and orderly stop commands
- Consolidated acquisition-status subscription or polling
- Live plot-frame subscription
- Live metric subscription
- Display-drop accounting
- Session-finalized notification
- Session-review loading
- Time-range and channel selection
- Offline processing requests
- FFT and PSD requests
- Export commands and progress
- Diagnostic-report access

Reuse existing DAQ-M04 through DAQ-M08 services wherever they already satisfy these responsibilities. Do not create GUI-specific copies of domain behavior.

### 14.2 GUI components

The accepted session-review layout extension adds title-bar minimize/maximize/
restore controls to the eight review panels, a scrollable restore bar, Escape to
restore the maximized panel, and Reset layout. Layout state lasts for the current
application window, including recorded-session changes. FFT/PSD/Extrema remain a
single tabbed panel. Existing widgets, scientific selections/results, and running
background work survive layout transitions. The extension does not alter live
acquisition panels, processing, public service contracts, or persisted schemas.

Add or complete:

- Live-acquisition page
- Run-control panel
- Acquisition and persistence status panel
- Warning and fault indicator
- Raw-voltage plot panel
- Calibrated or conditioned plot panel
- Channel visibility controls
- Time-window and axis controls
- Pause-display control
- Live-metrics panel
- End-of-run summary
- Session-review page
- FFT and PSD views
- Time-range selector
- Export controls and progress presentation
- Fault and diagnostic dialog

### 14.3 Supporting adapters

Add only the adapters needed to connect Qt event handling to application contracts. These adapters shall not contain DAQ, storage, calibration, filter, spectral, or export algorithms.

### 14.4 Documentation

Update:

- Root README with the complete GUI workflow
- GUI architecture documentation
- Operator guide for start, stop, display pause, review, and export
- Developer guide for thread boundaries and event flow
- Fault-code documentation
- Session and export provenance documentation where contracts changed
- DAQ-M10 milestone record with implementation and acceptance evidence
- Roadmap status after acceptance

## 15. Acceptance criteria

DAQ-M10 is accepted only when all criteria below are satisfied.

### 15.1 Architectural acceptance

- Start and stop controls call application services only.
- GUI packages do not import `nidaqmx`, TDMS libraries, or SciPy signal-processing functions.
- GUI code does not write session, manifest, event, processing, or export files directly.
- Post-run review reads the session through DAQ-M05 interfaces.
- Live and offline processing use DAQ-M06 services.
- Plot preparation, decimation, and export use DAQ-M07 services.
- Qt widgets are updated only from the GUI thread.

### 15.2 Functional acceptance

- A user can configure, qualify, acquire, inspect, condition, plot, and export without using the CLI.
- Start eligibility matches backend validation.
- Duplicate Start and Stop actions are safely rejected or made idempotent.
- Normal Stop produces an orderly final chunk and finalized session.
- The end-of-run summary matches the saved manifest and event log.
- A faulted run opens as faulted or incomplete and preserves available raw data and diagnostics.
- Selected time ranges map to deterministic sample-index ranges.
- Exported artifacts contain source-session and processing provenance.

### 15.3 Performance and isolation acceptance

- The GUI remains responsive during sustained four-channel acquisition at 50 kS/s per channel on the target computer.
- Slow rendering does not reduce acquired or persisted sample counts.
- Pausing the display does not pause acquisition or recording.
- Changing channel visibility, time window, or axis scaling does not reconfigure the DAQ task.
- Display memory remains bounded during long runs.
- Display drops, if any, are counted separately from acquisition gaps and persistence loss.
- Long replay, processing, and export operations do not block the Qt event loop.

### 15.4 Scientific consistency acceptance

- Live and replayed raw views agree for the same sample range.
- Live and replayed conditioned outputs agree when both use the same samples and the baseline causal recipe.
- Units, channel order, sample rate, and recipe identity remain consistent between live status, session review, and exports.
- FFT and PSD products report their window, overlap, normalization, frequency units, and amplitude units.
- Raw data remains unchanged after any review, conditioning, plotting, or export operation.

### 15.5 Recovery acceptance

- Hardware disconnect produces a typed fault and recoverable partial session.
- Injected recorder failure produces a typed storage fault and accurate acquired-versus-persisted counts.
- Plot-render slowdown or failure does not fault the acquisition unless an independent acquisition or storage problem occurs.
- Closing the GUI through the supported stop-and-close path releases resources and finalizes the session.
- After any handled fault, a new acquisition cannot begin until backend cleanup reports a safe idle state.

## 16. Required test plan

### 16.1 Unit tests

Test at least:

- Mapping backend state and status records into presentation state
- Start eligibility and disabled-control behavior
- Duplicate Start and Stop handling
- Monotonic status counters
- Bounded newest-frame display policy
- Display-drop counting
- Pause-display freeze and newest-frame resume
- Time-window changes without acquisition reconfiguration
- Axis-unit compatibility rules
- Live metric update and stale-metric indication
- End-of-run summary mapping
- Sample-index conversion for selected time ranges
- Fault-dialog mapping from typed errors
- Export request construction and provenance fields

### 16.2 Application and integration tests with the simulator

Test at least:

- Complete GUI-facing workflow from configuration through export
- Manual stop with final chunk and finalized session
- Duration-based completion
- Slow display consumer with zero acquisition and persistence loss
- Paused display during continued acquisition
- Display queue overflow with accurate display-drop count
- Hardware disconnect
- Reader timeout
- Injected TDMS or manifest write failure
- Disk-full simulation where supported by the storage test adapter
- Replay of completed and partial sessions
- Live versus replay raw equivalence
- Live versus replay causal baseline equivalence
- Offline FFT and PSD generation from a selected range
- CSV and figure export
- Cancellation or fault of a long export

### 16.3 GUI tests

Use Qt-capable automated tests to verify:

- Correct control enablement across idle, starting, running, stopping, completed, and faulted states
- GUI-thread-only widget mutation
- Event-loop responsiveness while background acquisition and processing run
- Correct channel labels and units
- Pause-display semantics
- End-of-run transition into session review
- Fault-dialog actions
- Session-browser transition into the same review surface used after a run

### 16.4 Architecture tests

Fail the suite if GUI modules import or directly reference:

- `nidaqmx`
- TDMS writer or reader implementations
- SciPy filtering or spectral implementations
- Raw file serialization
- NI task objects

### 16.5 Optional physical tests during development

Before final DAQ-M10 acceptance, perform a physical operator workflow on the target cDAQ-9171 and NI-9239 system:

- Discover and qualify the hardware
- Run four channels at 50 kS/s per channel for at least 2 minutes
- Exercise display pause, visibility, time-window, and scaling controls
- Request normal stop
- Reopen the session
- Compare counts and metadata
- Generate one conditioned plot, one FFT or PSD, one CSV export, and one figure export

The formal 10-minute run, 100-cycle start-stop test, packaging checks, and clean-computer acceptance remain DAQ-M11 work.

## 17. Acceptance evidence to record

The implementation report shall include:

- Changed files grouped by layer
- Architecture decisions and any new ADRs
- Focused test commands and results
- Full test-suite result
- Simulator workflow result
- Acquisition, persistence, and display-drop counts from the stress test
- Live-versus-replay comparison result
- Screenshot or recorded evidence of the completed operator workflow where practical
- Physical hardware result, if performed
- Any criterion not physically verified
- Remaining assumptions and known limitations

## 18. Implementation sequence

Recommended implementation order within DAQ-M10:

1. Audit DAQ-M04 through DAQ-M09 contracts and identify missing GUI-facing application records.
2. Add consolidated status, plot-frame, metric, finalization, review, and export contracts without adding algorithms to GUI code.
3. Implement the live acquisition page against the simulator.
4. Implement bounded display-frame handling and explicit drop counters.
5. Add raw and conditioned live plot panels.
6. Add metrics and status presentation.
7. Implement orderly stop and end-of-run transition.
8. Implement disk-backed session review.
9. Add time-range selection, offline FFT or PSD, and exports.
10. Add fault, partial-session, and close handling.
11. Add unit, integration, GUI, and architecture tests.
12. Run the complete simulator workflow and target-hardware workflow.
13. Update documentation and record acceptance evidence.

## 19. Non-negotiable design decisions

- Recording has priority over display.
- Display backlog is discarded, not accumulated.
- A paused display is not a paused acquisition.
- Sample index remains the authoritative relative-time coordinate.
- Post-run review is disk-backed.
- Raw data is immutable.
- Live processing is causal.
- Offline-only processing is labeled.
- GUI controls submit intent and render state. They do not implement scientific or hardware behavior.
- Completion state comes from backend finalization, not button state.
- Display degradation, acquisition loss, and storage loss are separate conditions.

## 20. Codex implementation prompt

> Implement DAQ-M10 only, using `DAQ-Software-Roadmap.md`, the repository architecture documents, and `Milestones/DAQ-M10-live-acquisition-plots-stop-control-post-run-analysis.md` as authoritative requirements. First inspect the existing DAQ-M04 through DAQ-M09 implementation, tests, contracts, and GUI composition root. Preserve all completed behavior and architecture boundaries. Complete the operator workflow for live acquisition, bounded live plots, orderly stop, end-of-run summary, disk-backed session review, offline processing, FFT or PSD, selected-range export, and faulted partial-session handling. The GUI must call application services only and must not import NI, TDMS, SciPy signal-processing, or file-serialization implementations. Preserve the single acquisition stream with a lossless recording path and bounded disposable display path. Add unit, integration, GUI, architecture, and simulator stress tests. Perform the physical DAQ-M10 workflow when supported hardware is available, but do not claim physical verification when it was not performed. Update the README, operator and developer documentation, fault documentation, milestone record, ADRs where needed, and roadmap status. Run focused tests and the full test suite. Report changed files, architecture decisions, test results, acquisition versus persistence counts, display-drop counts, live-versus-replay agreement, physical verification status, remaining assumptions, and any unmet acceptance criterion. Do not implement DAQ-M11 packaging, installer, final soak, or release work.
