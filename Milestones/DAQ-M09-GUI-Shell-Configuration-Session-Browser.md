# DAQ-M09: GUI Shell, Configuration, and Session Browser

> Historical specification: qualification-based start gates are superseded by [DAQ-M12](DAQ-M12-raw-first-monitoring.md). Current runtime may still enforce them until M12 is implemented.

**Status:** Software/simulator accepted; supervised physical NI GUI verification pending  
**Milestone type:** Desktop presentation layer  
**Depends on:** DAQ-M00 through DAQ-M08  
**Primary interface:** PySide6  
**Excluded successor scope:** DAQ-M10 live acquisition and analysis workflow

## 1. Goal

Provide a usable desktop interface for configuring an experiment, selecting and qualifying supported hardware, reviewing readiness, and browsing previously recorded sessions.

DAQ-M09 shall expose the backend capabilities established in DAQ-M00 through DAQ-M08 without duplicating hardware access, validation, storage, calibration, processing, or qualification logic inside Qt widgets.

The GUI is a human-machine interface over application services. In control-system language, it is an observation and command surface. It is not the controller, plant model, acquisition engine, recorder, or signal-processing pipeline.

## 2. Milestone outcome

At completion, an operator shall be able to:

1. Launch the application in simulated or NI hardware mode.
2. Select a supported hardware profile.
3. Discover available devices and modules.
4. Configure the four-channel experiment.
5. Enter experiment and specimen metadata.
6. Select a session parent directory.
7. Preview the proposed session folder name.
8. See field-level configuration errors.
9. Run the DAQ-M08 hardware qualification workflow.
10. Review and save the qualification report.
11. See whether the current configuration is eligible to start acquisition.
12. Browse previously recorded sessions.
13. Inspect session metadata, completion state, hardware provenance, channel configuration, and fault summary.
14. Close the application without leaving NI tasks, workers, files, or other resources active.

DAQ-M09 does not implement live acquisition controls, live plotting, stop handling, offline signal analysis, or export controls. Those belong to DAQ-M10.

## 3. Architectural requirements

### 3.1 Layer boundaries

The GUI may:

- Render application-service results.
- Collect operator input.
- Convert widget values into application command objects or draft records.
- Display validation issues, progress, warnings, and faults.
- Submit commands to application services.
- Maintain presentation-only state such as selected rows, expanded panels, and window geometry.

The GUI must not:

- Import `nidaqmx`.
- Create or manage NI tasks.
- Read or write TDMS directly.
- Parse session manifests directly.
- Serialize experiment configurations directly.
- Calculate calibration conversions.
- Apply SciPy filters.
- Calculate FFT, PSD, or metrics.
- Inspect raw session arrays.
- Determine hardware compatibility itself.
- Reimplement filename, path, schema, or acquisition validation.
- Infer whether acquisition is safe to start.

Concrete adapters shall be selected only by the application composition root. Widgets and presenters shall depend on application interfaces, query services, command services, and stable data-transfer records.

### 3.2 Composition root

Provide one explicit desktop composition root responsible for constructing and connecting:

- Application services
- DAQ adapter
- Session repository or session catalog adapter
- Configuration repository
- Qualification service
- User-preference adapter
- GUI presenters or view models
- Main window
- Background task runner
- Application shutdown service

The composition root may import concrete adapters. Individual pages, dialogs, presenters, and widgets may not.

Suggested entry point:

```text
python -m labdaq.gui
```

The composition root shall support at least:

```text
--daq simulated
--daq ni
```

Simulated mode must remain usable on systems where NI-DAQmx is not installed.

### 3.3 Presentation pattern

Use an explicit presentation boundary such as presenters, controllers, or view models. Business state shall not be distributed across signal callbacks.

A suitable flow is:

```text
Qt widget event
    -> presentation controller
    -> application command or query service
    -> application result
    -> presentation state
    -> widget rendering
```

Qt models may represent tables and lists, but they shall receive already validated application records or presentation records rather than interrogating hardware or files.

## 4. Required application-facing contracts

Reuse existing contracts where available. Add only the minimum query and command surfaces needed by DAQ-M09.

The GUI will require equivalents of the following capabilities.

### 4.1 Application bootstrap query

Returns:

- Application version
- Schema versions
- Available DAQ implementations
- Active DAQ implementation
- Available hardware profiles
- Active baseline processing-recipe ID and version
- Recent session parent locations
- Environment diagnostics relevant to startup

### 4.2 Hardware discovery query

Returns normalized application records for:

- Chassis
- Installed modules
- Physical channels
- Models
- Serial numbers when available
- Driver version
- Capabilities
- Discovery warnings
- Profile compatibility

No NI objects may cross this boundary.

### 4.3 Configuration workspace service

The GUI shall edit a configuration draft through an application-facing service.

Required operations:

- Create a draft from a hardware profile.
- Load an existing experiment configuration.
- Update experiment metadata.
- Update acquisition settings.
- Update channel assignments.
- Update calibration references.
- Update session destination settings.
- Validate the complete draft.
- Produce a normalized configuration suitable for later acquisition.
- Return field-addressable validation issues.

The service shall remain the authority for all configuration validity.

### 4.4 Session naming preview

The parent directory, experiment name, timestamp policy, sanitization rules, and collision checks shall be evaluated by backend services.

The GUI receives a result containing:

- Proposed session folder name
- Proposed absolute destination
- Sanitized experiment-name component
- Collision status
- Path validation status
- Actionable validation issues

Previewing a name shall not create the session directory.

### 4.5 Qualification service

The GUI shall invoke the existing DAQ-M08 qualification workflow through application services.

The presentation layer shall receive:

- Qualification state
- Current test
- Progress when meaningful
- Structured test outcomes
- Warnings and faults
- Final pass, fail, warning, or not-tested status
- Report identifier
- Report creation time
- Hardware and configuration provenance
- Saved report location when applicable

Long-running qualification shall not run on the Qt event thread.

### 4.6 Session catalog service

The GUI shall not scan directories or parse manifests itself.

The session catalog service shall provide:

- Session discovery beneath one selected parent directory
- Session catalog entries
- Session metadata summaries
- Completion state
- Start and end times
- Experiment and specimen identifiers
- Channel count
- Sample rate
- Duration requested and acquired
- Hardware profile and device identifiers
- Qualification report reference when present
- Warning and fault counts
- Processing-recipe provenance
- Session validity or corruption status

Reading a session summary shall not require loading the complete raw dataset into memory.

### 4.7 Start-eligibility query

Start eligibility shall be returned by an application service as a structured result:

```text
eligible: bool
blocking_reasons: [...]
warnings: [...]
configuration_fingerprint: ...
qualification_report_id: ...
qualification_fingerprint: ...
```

The GUI shall display this result. It shall not calculate eligibility from widget state.

## 5. Qualification provenance and invalidation

A qualification result is valid only for the hardware and acquisition context it actually tested.

The qualification report shall be bound to a stable fingerprint derived from the relevant configuration and hardware identity. At minimum, the fingerprint or equivalent provenance comparison shall cover:

- Hardware profile ID and schema version
- Chassis and module identity when available
- Physical channel order
- Terminal configuration
- Input range
- Requested or validated sample rate
- Chunk or throughput-relevant settings
- Channel enablement
- Calibration or conditioning identity where relevant to qualification
- DAQ adapter and driver versions when relevant

Changing a fingerprinted field after qualification shall immediately make the prior result stale for start-eligibility purposes.

Experiment notes, operator name, specimen name, and other non-hardware metadata should not invalidate qualification unless an existing backend policy explicitly requires it.

The GUI shall visibly distinguish:

- Not qualified
- Qualification running
- Qualified and current
- Qualified with warnings
- Failed
- Stale because configuration changed
- Stale because hardware changed
- Qualification interrupted

This prevents an old passing result from being presented as evidence for a different acquisition configuration.

## 6. GUI structure

The exact visual design may evolve, but the following functional areas are required.

### 6.1 Main application shell

Provide:

- Main window
- Application title and version
- Active DAQ mode indicator
- Global status area
- Navigation between setup, hardware tests, and sessions
- Nonmodal presentation of routine validation errors
- Modal handling only for destructive actions, unrecoverable faults, or explicit operator decisions
- Persistent window geometry and recent-location preferences through an injected preference adapter

Recommended primary pages:

1. **Experiment Setup**
2. **Hardware Tests**
3. **Sessions**

Do not add DAQ-M10 live plots or analysis pages during this milestone.

### 6.2 Experiment Setup page

The setup page shall contain the following sections.

#### Hardware and profile

- DAQ implementation indicator
- Hardware profile selector
- Discover or refresh action
- Discovered chassis summary
- Discovered module summary
- Profile compatibility result
- Driver or adapter status
- Clear simulated-mode identification

#### Experiment metadata

At minimum:

- Experiment name
- Description
- Operator
- Sample or specimen ID
- Notes

Required and optional fields shall be visually distinguishable.

#### Acquisition settings

At minimum:

- Sample rate
- Requested duration
- Chunk size
- Sampling mode where currently supported
- Terminal configuration
- Input range
- Trigger summary, even when the current supported mode is no trigger
- Read-only active baseline-recipe ID and version

Unsupported values shall not silently snap to a different value. The backend shall either normalize an allowed representation explicitly or return a validation issue.

#### Channel configuration table

Display one row per configured channel.

At minimum, columns shall include:

- Enabled state
- Logical channel name
- Channel role
- Physical channel
- Sensor type
- Sensor serial number
- Engineering unit
- Calibration identifier or source
- Calibration status
- Input range
- Validation status

The initial profile shall support:

- Three TE 830M1-0200 accelerometer channels
- One externally conditioned charge-converter channel

The table shall preserve physical channel order and logical channel identity.

#### Session destination

Provide:

- Parent-directory chooser
- Recent locations
- Sanitized session-name preview
- Full destination preview
- Collision warning
- Writable-path status
- Explicit overwrite prohibition

The GUI shall not create the final session folder during preview.

#### Readiness panel

Display:

- Configuration validity
- Hardware/profile compatibility
- Qualification state
- Qualification freshness
- Destination validity
- Current baseline-recipe version
- Start eligibility
- All blocking reasons
- Nonblocking warnings

The readiness panel shall update whenever a relevant field, hardware selection, destination, or qualification result changes.

### 6.3 Hardware Tests page

Provide:

- Current hardware and configuration summary
- Qualification test list
- Test purpose or short description
- Start qualification action
- Cancel action when supported by the application service
- Progress and active-test indication
- Per-test status
- Measurements and declared limits
- Warning and fault details
- Final qualification status
- Save or locate report action
- Report provenance
- Fingerprint match or stale-state indication

The GUI shall preserve and display a partial qualification report when the test is interrupted or faults.

Simulator qualification scenarios shall be selectable through test fixtures or developer configuration. The normal operator interface shall not expose arbitrary fault injection controls.

### 6.4 Sessions page

Provide a session browser that can:

- Select a session parent directory.
- Refresh the catalog.
- Display discovered sessions.
- Select one session.
- Display a metadata summary.
- Distinguish completed, stopped, incomplete, and faulted sessions.
- Display manifest or schema validation failures without crashing.
- Display warning and fault counts.
- Display channel and hardware provenance.
- Display the baseline processing-recipe version.
- Display qualification provenance when present.

Suggested catalog columns:

- Start time
- Experiment
- Specimen
- Completion state
- Duration acquired
- Sample rate
- Channel count
- Hardware profile
- Warning count
- Fault count

The session browser shall not add DAQ-M10 plotting, conditioning, FFT, PSD, or export functions.

## 7. Configuration validation and feedback

Application validation results shall use stable issue codes and field paths.

A validation issue should contain an equivalent of:

```text
code
field_path
severity
message
remediation
```

Examples of field paths:

```text
experiment.name
acquisition.sample_rate_hz
acquisition.duration_s
storage.parent_directory
channels[2].physical_channel
channels[3].calibration
hardware.profile_id
qualification.current
```

The GUI shall:

- Mark the associated field or table row.
- Display a concise inline message.
- Include the issue in a consolidated readiness summary.
- Move focus to the relevant field when the user activates the issue.
- Preserve the operator’s entered value unless normalization is explicitly returned by the backend.
- Clear an issue only after the backend no longer reports it.

The GUI shall not maintain a separate set of scientific or hardware validation rules.

## 8. Background work and responsiveness

Hardware discovery, qualification, session catalog scanning, and session summary parsing may involve blocking operations.

These operations shall:

- Execute outside the Qt event thread.
- Report structured progress or busy state.
- Prevent duplicate conflicting requests.
- Support cancellation where the underlying application service supports it.
- Return all results to the GUI through a controlled presentation boundary.
- Convert exceptions into structured application errors before display.
- Ignore superseded responses when a newer request has replaced them.

Qt thread objects shall not become the owners of acquisition, DAQ, or qualification domain state.

The background runner is an execution mechanism only.

## 9. Start-request boundary

DAQ-M09 shall establish the start-request boundary without implementing DAQ-M10 live operation.

The setup presenter may expose an action equivalent to:

```text
request_acquisition_start()
```

Behavior:

1. Requery backend start eligibility.
2. Refuse the request when eligibility is false.
3. Display all current blocking reasons.
4. Emit a typed navigation or launch intent when eligibility is true.
5. Do not directly start a DAQ task from a widget.
6. Do not implement the live run screen, stop workflow, or plot updates in this milestone.

DAQ-M10 will consume this launch intent and connect it to the acquisition workflow.

No placeholder control may imply that acquisition is running when it is not.

## 10. Error handling

The GUI shall present actionable handling for at least:

- NI-DAQmx not installed
- NI adapter unavailable
- No chassis discovered
- Expected module missing
- Hardware profile mismatch
- Physical channel unavailable
- Unsupported sample rate or range
- Invalid calibration reference
- Unwritable destination
- Existing destination collision
- Qualification failure
- Qualification becoming stale
- Qualification cancellation
- Hardware disconnect during testing
- Session manifest corruption
- Unsupported session schema
- Missing session files
- Incomplete or faulted session
- Unexpected application-service failure

Routine field validation shall not produce modal dialogs.

Unexpected errors shall include:

- Human-readable summary
- Stable error code
- Recommended operator action
- Access to diagnostic details
- Log correlation identifier when available

Raw NI exceptions and Python tracebacks shall not be presented as the primary operator message.

## 11. Resource lifecycle

The desktop application shall have an explicit shutdown path.

Closing the application during configuration or an idle state shall:

- Stop accepting new GUI commands.
- Cancel or conclude active discovery and qualification requests.
- Release any adapter-owned discovery or test resources.
- Shut down GUI worker infrastructure.
- Flush preference updates.
- Release NI tasks if any qualification operation created one.
- Leave existing recorded sessions unchanged.
- Avoid creating an empty acquisition session.
- Exit without background threads preventing process termination.

A forced close during qualification shall produce structured cancellation or interruption evidence where the existing qualification service supports it.

The main window shall not rely exclusively on object destructors for resource cleanup.

## 12. Required deliverables

### 12.1 GUI infrastructure

- PySide6 application entry point
- Desktop composition root
- Main window
- Navigation shell
- Presentation controller or view-model layer
- Background task runner
- Application shutdown integration
- User-preference port and adapter for GUI preferences
- GUI logging integration

### 12.2 Experiment setup

- Hardware/profile selector
- Discovery display
- Experiment metadata editor
- Acquisition settings controls
- Channel configuration table
- Calibration selection or status display
- Parent-folder chooser
- Session-name and destination preview
- Read-only baseline-recipe version
- Readiness and start-eligibility panel

### 12.3 Qualification interface

- Qualification launch and cancellation interface
- Test progress display
- Per-test result display
- Qualification report viewer
- Report provenance and stale-state display

### 12.4 Session browser

- Session-root selection
- Session catalog
- Session metadata summary
- Completion and fault-state display
- Corrupt or unsupported session handling

### 12.5 Documentation

- GUI architecture documentation
- Developer instructions for launching simulated and NI modes
- Operator description of setup and qualification
- Explanation of start eligibility
- Explanation of qualification staleness
- Session browser behavior
- Screenshots or diagrams where practical
- Updated roadmap status
- ADRs for major presentation architecture decisions

## 13. Testing requirements

### 13.1 Unit tests

Test at least:

- Presentation-state initialization
- Hardware-profile selection
- Configuration-draft updates
- Field-path to widget or table-row mapping
- Validation issue rendering
- Session-name preview rendering
- Collision handling
- Baseline-recipe version read-only behavior
- Start-eligibility rendering
- Rejection of an invalid start request
- Qualification status transitions
- Qualification fingerprint mismatch
- Qualification invalidation after configuration changes
- Session catalog sorting and selection
- Incomplete and corrupt session summaries
- Error-to-operator-message mapping
- Superseded background-result handling
- Shutdown-state behavior

### 13.2 GUI integration tests

Run PySide6 in an offscreen or otherwise CI-compatible mode.

Test at least:

1. Launch in simulated mode without NI-DAQmx installed.
2. Load the initial four-channel profile.
3. Edit experiment metadata.
4. Enter valid acquisition settings.
5. Select a temporary parent directory.
6. Receive a valid session-name preview.
7. Run a shortened simulator qualification.
8. Display the resulting report.
9. Become start-eligible after a current passing qualification.
10. Become ineligible when a fingerprinted configuration field changes.
11. Recover eligibility only after a matching qualification is restored or rerun.
12. Browse completed, incomplete, and faulted test sessions.
13. Display a corrupt session as invalid rather than crashing.
14. Close the application and verify that worker and adapter resources are released.

### 13.3 Architecture tests

Enforce at least:

- GUI widget modules do not import `nidaqmx`.
- GUI widget modules do not import TDMS libraries.
- GUI widget modules do not import SciPy signal-processing modules.
- GUI widget modules do not import concrete storage adapters.
- Only the desktop composition root imports concrete DAQ adapters.
- Core and application packages do not import PySide6.
- Simulated GUI mode imports successfully without NI dependencies installed.

### 13.4 Manual verification

Verify on the target Windows laboratory computer:

- Normal and high-DPI scaling
- Keyboard navigation
- Readable table layout
- Folder chooser operation
- Windows path handling
- Simulated-mode workflow
- NI discovery display
- Physical qualification launch
- Qualification-report display
- Closing during idle
- Closing during an active qualification test
- No reserved NI task after application exit

Physical checks that cannot be completed shall be reported explicitly rather than represented as passed.

## 14. Acceptance criteria

DAQ-M09 is complete only when all of the following are demonstrated.

### Architecture

- The GUI contains no `nidaqmx`, TDMS, SciPy filtering, FFT, PSD, calibration, or direct experiment-file serialization logic.
- Concrete adapters are selected in the composition root.
- Application and core packages remain independent of PySide6.
- Simulated mode works without NI software installed.

### Configuration

- The initial cDAQ-9171, NI-9239, and four-channel experiment profile can be loaded and edited.
- All invalid values produce actionable field-level feedback.
- Duplicate physical channels and unsupported acquisition settings are rejected by backend validation.
- Destination preview uses backend filename and collision rules.
- The baseline processing-recipe version is visible and read-only.

### Qualification and readiness

- The GUI can initiate and display the DAQ-M08 qualification workflow.
- Qualification results include machine-readable status and visible provenance.
- A missing, failed, interrupted, or stale qualification prevents start eligibility.
- A relevant hardware or acquisition configuration change invalidates the previous qualification.
- Start eligibility is obtained from an application service, not inferred by widgets.
- An invalid or unqualified configuration cannot emit an acquisition launch intent.

### Session browser

- Completed, stopped, incomplete, and faulted sessions can be listed and summarized.
- Browsing metadata does not require loading full raw arrays.
- Corrupt, incomplete, missing, or unsupported session records do not crash the application.
- Session provenance is sufficient to identify hardware, channels, rate, duration, recipe version, and completion state.

### Responsiveness and cleanup

- Hardware discovery, qualification, and catalog operations do not block the Qt event thread.
- Closing during configuration or idle releases all resources.
- Closing during qualification concludes or cancels the operation through the application shutdown path.
- Repeated GUI launches and closes leave no reserved NI tasks or nonterminating worker threads.

### Verification

- Focused GUI tests pass.
- Architecture tests pass.
- The complete existing test suite passes.
- Simulated end-to-end GUI evidence is recorded.
- Physical NI verification is recorded when the target hardware is available.
- Any acceptance criterion not physically verified is listed clearly in the implementation report.

## 15. Explicit non-goals

Do not implement the following as part of DAQ-M09:

- Live acquisition plots
- Acquisition progress screen
- Live metrics
- Live stop controls
- Display pause behavior
- Plot window configuration
- Post-run waveform viewing
- FFT or PSD controls
- Time-range selection
- CSV or figure export controls
- User-defined processing recipes
- Recipe editor
- Arbitrary hardware-profile editor
- Additional NI modules
- Vacuum gauge, power-supply, relay, or valve integration
- Installer or packaged laboratory release

Minimal interfaces required for DAQ-M10 may be introduced, but they shall not contain DAQ-M10 behavior.

## 16. Implementation guidance

Prefer a small number of explicit presentation states over direct widget-to-widget coordination.

Recommended presentation states include:

```text
ApplicationShellState
ExperimentSetupState
HardwareDiscoveryState
QualificationViewState
SessionBrowserState
BackgroundOperationState
```

Use immutable snapshots or controlled state updates where practical. This reduces stale UI state and makes headless presenter testing possible.

Treat configuration and qualification as two coupled but distinct records:

```text
configuration fingerprint
qualification fingerprint
```

Start eligibility requires the backend to establish that they match.

This is mathematically analogous to configuration-controlled metrology. A measurement certificate is valid only for the instrument state and calibration context under which it was produced.

## 17. Required implementation report

The Codex completion report shall include:

1. Files added and modified.
2. GUI architecture and presentation pattern.
3. Composition-root behavior.
4. Application services consumed or added.
5. Qualification fingerprint and invalidation behavior.
6. Session catalog behavior.
7. Threading and background-operation design.
8. Shutdown and resource-cleanup behavior.
9. Focused test results.
10. Full test-suite result.
11. Simulated GUI acceptance evidence.
12. Physical NI evidence, when performed.
13. Remaining assumptions.
14. Every acceptance criterion not verified.

## 18. Codex implementation prompt

> Implement DAQ-M09 only, using `DAQ-Software-Roadmap.md`, the repository architecture documents, completed milestone specifications, schemas, and ADRs as authoritative requirements. First inspect the current implementation, tests, package boundaries, and the application services delivered by DAQ-M00 through DAQ-M08.
>
> Add the PySide6 application shell, desktop composition root, presentation controllers or view models, experiment-configuration interface, hardware/profile selection, hardware-qualification interface, qualification-report viewer, session destination preview, readiness display, and existing-session browser described by DAQ-M09.
>
> Preserve the rule that the GUI is only a client of application services. GUI widgets must not import `nidaqmx`, directly read or write TDMS or JSON experiment records, calculate calibration or conditioning, apply SciPy filters, parse sessions, or determine start eligibility. Concrete adapters may be selected only in the desktop composition root.
>
> Bind qualification evidence to the relevant hardware and acquisition configuration. A hardware or acquisition change that alters the qualification context must mark the previous qualification stale and make the configuration ineligible to start. Obtain start eligibility from a backend application service. DAQ-M09 may emit a validated acquisition-launch intent, but it must not implement DAQ-M10 live acquisition, plotting, stop controls, offline analysis, or export behavior.
>
> All hardware discovery, qualification, session scanning, and session-summary operations that may block must execute outside the Qt event thread. Implement explicit application shutdown and verify that closing the GUI releases workers, qualification resources, and NI tasks.
>
> Add focused unit tests, offscreen GUI integration tests, import-boundary tests, documentation, and ADRs required by the implementation. Simulated mode must support the complete DAQ-M09 workflow without NI-DAQmx installed. Run the focused tests and the complete existing test suite.
>
> Report changed files, architecture decisions, qualification provenance and invalidation behavior, background-execution design, shutdown behavior, test results, simulated acceptance evidence, physical NI verification when available, remaining assumptions, and any acceptance criterion that could not be verified.
