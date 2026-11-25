# LabDAQ Software Roadmap

> **Current direction (2026-11-10):** Raw channel monitoring with optional recording is the primary workflow. Qualification, certification, calibration details, report fingerprints, and long acceptance campaigns are optional diagnostics, not prerequisites to start. The historical milestone descriptions and evidence below describe what was planned or implemented at the time; conflicting gates are superseded by [DAQ-M12](Milestones/DAQ-M12-raw-first-monitoring.md) and [ADR-0033](docs/adr/0033-raw-first-monitoring-and-optional-diagnostics.md). Physical verification remains separate from simulator results.

Status: raw-first software/simulator accepted; physical NI verification pending  
Target hardware: NI cDAQ-9171 chassis with NI-9239 module  
Initial experiment: three TE 830M1-0200 accelerometer channels plus one externally conditioned charge-converter channel  
Initial acquisition target: four simultaneous channels at 50 kS/s/channel  

Implemented extension: profile revision 2 exposes all 31 NI-9239 internal-clock
rates `50,000 / n` (`n = 1..31`) with rate-specific hardware validation as currently implemented. Non-50-kHz
physical behavior remains unverified until exercised on the target hardware.

Milestone status:

| Milestone | Status | Specification |
|---|---|---|
| DAQ-M00 | Complete | [Specification](docs/milestones/DAQ-M00-design-charter-repository-foundation.md) · [Evidence](docs/milestones/DAQ-M00-acceptance-evidence.md) |
| DAQ-M01 | Complete | [Specification](docs/milestones/DAQ-M01-domain-records-validated-configuration.md) · [Evidence](docs/milestones/DAQ-M01-acceptance-evidence.md) |
| DAQ-M02 | Complete | [Specification](Milestones/DAQ-M02-simulated-daq-deterministic-signal-generation.md) · [Evidence](docs/milestones/DAQ-M02-acceptance-evidence.md) |
| DAQ-M03 | Implementation complete; physical channel mapping pending | [Specification](Milestones/DAQ-M03-NI-DAQmx-hardware-adapter-and-discovery.md) · [Evidence](docs/milestones/DAQ-M03-acceptance-evidence.md) |
| DAQ-M04 | Implementation complete; physical NI engine verification pending | [Specification](Milestones/DAQ-M04-acquisition-engine-and-state-machine.md) · [Evidence](docs/milestones/DAQ-M04-acceptance-evidence.md) |
| DAQ-M05 | Implementation complete; physical NI recording verification pending | [Specification](Milestones/DAQ-M05-durable-recording-metadata-parsing-replay.md) · [Evidence](docs/milestones/DAQ-M05-acceptance-evidence.md) |
| DAQ-M05A | Implementation and target-disk simulator soak complete; physical NI verification pending | [Specification](Milestones/DAQ-M05A-HDF5-source-record-migration.md) · [Evidence](docs/milestones/DAQ-M05A-acceptance-evidence.md) |
| DAQ-M06 | Complete; accepted locally on Windows | [Specification](Milestones/DAQ-M06-calibration-signal-conditioning-pipeline.md) · [Evidence](docs/milestones/DAQ-M06-acceptance-evidence.md) |
| DAQ-M07 | Complete; accepted locally on Windows | [Specification](Milestones/DAQ-M07-plotting-and-export-services.md) · [Evidence](docs/milestones/DAQ-M07-acceptance-evidence.md) |
| DAQ-M08 | Implementation complete; physical qualification pending | [Specification](Milestones/DAQ-M08-hardware-qualification-and-diagnostics.md) · [Evidence](docs/milestones/DAQ-M08-acceptance-evidence.md) |
| DAQ-M09 | Software/simulator accepted; physical NI GUI verification pending | [Specification](Milestones/DAQ-M09-GUI-Shell-Configuration-Session-Browser.md) · [Evidence](docs/milestones/DAQ-M09-acceptance-evidence.md) |
| DAQ-M10 | Software/simulator accepted; physical NI workflow pending | [Specification](Milestones/DAQ-M10-live-acquisition-plots-stop-control-post-run-analysis.md) · [Evidence](docs/milestones/DAQ-M10-acceptance-evidence.md) |
| DAQ-M10A | Software/simulator accepted; physical NI verification pending | [Specification](Milestones/DAQ-M10A-open-single-channel-acquisition.md) · [Evidence](docs/milestones/DAQ-M10A-acceptance-evidence.md) |
| DAQ-M11 | Internal feedback preview packaged; historical release gates superseded | [Preview evidence](docs/milestones/DAQ-M11-feedback-preview-evidence.md) |
| DAQ-M12 | Software/simulator accepted; physical NI verification pending | [Specification](Milestones/DAQ-M12-raw-first-monitoring.md) · [Evidence](docs/milestones/DAQ-M12-acceptance-evidence.md) |

## 1. Mission

Build a desktop DAQ interface that discovers supported hardware, opens selected valid analog input channels, and displays their raw voltage signals. Recording is an explicit operator choice; when enabled, save the raw samples and enough metadata to interpret and reopen them. Simulator mode supports development without NI hardware.

The initial target is the NI cDAQ-9171 and NI-9239. Calibration, processing, exports, and detailed qualification remain available or can be developed separately, but are not prerequisites for observing raw voltage. The software does not certify wiring or external equipment.

## 2. Architectural rules

1. The GUI is a client of application services. It must not import `nidaqmx`, write acquisition files, calculate filters, or own experiment state.
2. Raw measurements are immutable. Conditioning and plotting create reproducible derivatives and never overwrite raw samples.
3. Every chunk carries its sample rate, ordered channel identities, raw units, sample indices, and acquisition identity; retain calibration provenance when known and applicable.
4. Sample index is the authoritative relative-time coordinate. Wall-clock time identifies the session start and noteworthy events.
5. Hardware access is behind a DAQ interface with NI and simulated implementations.
6. One hardware acquisition stream feeds the live display and, when enabled, a recording consumer. The recorder receives every sample in its recording interval. The display receives a bounded, decimated view and may discard superseded display frames. This does not create a second NI task or a second raw dataset.
7. Loss of samples, storage failure, hardware removal, clipping, or configuration mismatch must become explicit faults, not silent warnings.
8. A stopped, interrupted, or failed recording must leave an identifiable session with its actual completion state where possible. Monitoring alone does not require a session.
9. Live conditioning must be causal. Offline processing may use zero-phase or noncausal methods, but must label them as offline-only.
10. V1 provides one versioned baseline processing recipe. It must reproduce the same processed output from the same raw session. User-defined recipe libraries are deferred.

## 3. Proposed system boundary

```text
PySide6 GUI
    |
Application services
    |-- experiment configuration
    |-- hardware qualification
    |-- acquisition/session orchestration
    |-- replay, processing, plotting, export
    |
Core contracts and domain models
    |-- DAQ port
    |-- data sink/source ports
    |-- processing and plotting ports
    |
Adapters
    |-- NI-DAQmx
    |-- simulated DAQ
    |-- HDF5/session-folder storage and legacy TDMS reader
    |-- CSV and figure exporters
```

Recommended initial stack:

- Python 3.11 or later
- `nidaqmx` for NI hardware
- NumPy and SciPy for numeric processing
- PySide6 for the desktop interface
- PyQtGraph for responsive live plots
- HDF5 as the authoritative raw measurement container for new sessions; TDMS remains readable and exportable
- JSON for the human-readable session manifest, configurations, and the versioned baseline processing recipe
- pytest for automated tests
- PyInstaller for the Windows laboratory release

## 4. Session format

Each recording creates one self-contained session directory; monitoring without recording creates none:

```text
YYYY-MM-DD_HHMMSS_experiment-name/
|-- manifest.json
|-- raw.h5
|-- events.jsonl
|-- processing-recipe.json
|-- processed/
|-- plots/
`-- exports/
```

The manifest should include at least:

- Session UUID, experiment name, description, operator, sample/specimen ID, and notes
- Start time, timezone, duration requested, duration acquired, and completion state
- Computer, operating-system, application, and schema versions
- Chassis/module names, serial numbers when available, driver version, and device aliases
- Sampling mode, actual sample rate, channel order, terminal configuration, and input range
- Per-channel logical name, physical channel, sensor type, sensor serial number, engineering units, calibration coefficients, and calibration date/source
- Sensor power or conditioning description, including the externally validated charge-converter chain
- Baseline processing-recipe version and software version used for each derived artifact
- Counts for acquired samples, persisted samples, detected gaps, clipping, warnings, and faults

`raw.h5` and the initial manifest are the source record for schema-v2 sessions. Existing schema-v1 `raw.tdms` sessions remain source records in place. CSV, TDMS exports, processed arrays, spectra, and plot images are derived products.

## 5. Cross-cutting acceptance rules

Every milestone shall:

- Preserve the layer boundaries above.
- Include unit tests and integration tests appropriate to the change.
- Work headlessly before its GUI surface is added.
- Update architecture, user, and developer documentation.
- Emit structured errors with actionable messages.
- Avoid implementing future-milestone behavior unless it is required by the current interface.
- Run the existing full test suite before being considered complete.

## 6. Milestones

### DAQ-M00: Design charter and repository foundation

**Status:** Complete  
**Specification:** [DAQ-M00 design charter and repository foundation](docs/milestones/DAQ-M00-design-charter-repository-foundation.md)
**Evidence:** [DAQ-M00 acceptance evidence](docs/milestones/DAQ-M00-acceptance-evidence.md)

**Goal:** Establish scope, terminology, dependencies, coding standards, and the initial package structure.

**Deliverables**

- Project charter, architecture document, roadmap, and ADR process
- `src/` package layout separating domain, application, ports, adapters, processing, storage, GUI, and CLI
- Dependency and development-environment configuration
- Logging configuration and structured application error hierarchy
- CI-ready formatting, linting, type checking, and pytest setup
- Minimal CLI that reports application version and environment diagnostics

**Acceptance**

- Clean installation in a fresh virtual environment
- Tests run without NI hardware or NI-DAQmx installed
- Importing core/application packages does not import GUI or NI dependencies
- Architecture tests enforce the highest-value import boundaries

### DAQ-M01: Domain records and validated configuration

**Status:** Complete  
**Specification:** [DAQ-M01 domain records and validated configuration](docs/milestones/DAQ-M01-domain-records-validated-configuration.md)
**Evidence:** [DAQ-M01 acceptance evidence](docs/milestones/DAQ-M01-acceptance-evidence.md)

**Goal:** Define the stable language shared by all later layers.

**Deliverables**

- Immutable records for device, module, channel, sensor, calibration, acquisition, trigger, session, and data-chunk configuration
- Channel types for the three accelerometer axes and conditioned charge signal
- Validation for unique physical channels, valid units, allowed sampling rates, duration, chunk size, paths, and safe filenames
- Versioned JSON schemas for hardware profiles, experiment configurations, and the baseline processing recipe
- Initial profile for cDAQ-9171 + NI-9239 + four-channel experiment

**Acceptance**

- Valid configurations round-trip through JSON without information loss
- Invalid duplicate channels, unsupported rates, missing calibration fields, unsafe filenames, and contradictory duration/trigger settings are rejected
- No NI-specific object leaks into domain records
- Schema migrations or explicit schema-version rejection are tested

### DAQ-M02: Simulated DAQ and deterministic signal generation

**Status:** Complete  
**Specification:** [DAQ-M02 simulated DAQ and deterministic signal generation](Milestones/DAQ-M02-simulated-daq-deterministic-signal-generation.md)  
**Evidence:** [DAQ-M02 acceptance evidence](docs/milestones/DAQ-M02-acceptance-evidence.md)

**Goal:** Make the complete backend and future GUI developable without laboratory hardware.

**Deliverables**

- DAQ port defining discovery, validation, configure, start, read, stop, reset, and health operations
- Simulated implementation producing synchronized four-channel chunks
- Configurable sine, impulse, swept-sine, noise, drift, clipping, dropout, delayed-read, and disconnect scenarios
- Deterministic random seeds and known analytical reference signals
- CLI commands to list simulated devices and stream a bounded test acquisition

**Acceptance**

- Identical configuration and seed produce byte-equivalent sample arrays
- Chunk boundaries do not change the reconstructed continuous signal
- Fault scenarios produce explicit typed events
- Known sine frequency, amplitude, phase, and channel alignment are recovered within stated tolerances

### DAQ-M03: NI-DAQmx hardware adapter and discovery

**Status:** Implementation complete; physical channel mapping pending  
**Specification:** [DAQ-M03 NI-DAQmx hardware adapter and discovery](Milestones/DAQ-M03-NI-DAQmx-hardware-adapter-and-discovery.md)  
**Evidence:** [DAQ-M03 acceptance evidence](docs/milestones/DAQ-M03-acceptance-evidence.md)

**Goal:** Provide the smallest trustworthy interface to the cDAQ-9171 and NI-9239.

**Deliverables**

- NI adapter implementing the DAQ port
- Device/module/channel discovery with model, serial, and capability reporting when exposed by the driver
- Validation of four simultaneous differential voltage channels
- NI-9239 rate and range validation
- Preallocated NumPy reads using the appropriate multi-channel stream reader
- NI error translation into application-level errors
- Explicit task cleanup through success, stop, timeout, and exception paths

**Acceptance**

- Adapter imports are isolated so simulated mode works on computers without NI software
- Hardware profile mismatch is rejected before acquisition
- Repeated configure/start/stop cycles leave no reserved NI tasks
- Four channels return aligned arrays shaped consistently as `[channels, samples]`
- A short physical acquisition reports the requested/actual rate, channel ordering, and sample count

### DAQ-M04: Acquisition engine and experiment state machine

**Status:** Implementation complete; physical NI engine verification pending  
**Specification:** [DAQ-M04 acquisition engine and experiment state machine](Milestones/DAQ-M04-acquisition-engine-and-state-machine.md)  
**Evidence:** [DAQ-M04 acceptance evidence](docs/milestones/DAQ-M04-acceptance-evidence.md)

**Goal:** Orchestrate acquisition independently from storage, analysis, and GUI timing.

**Deliverables**

- Explicit states such as idle, configured, armed, running, stopping, completed, and faulted
- Acquisition worker with bounded chunks, monotonic sample indices, cancellation, and clean shutdown
- One acquisition read path feeding a lossless recording queue and a bounded live-display queue
- Backpressure policy that protects recording and may discard only superseded display data
- Structured status, progress, warning, and fault events
- Duration-based and manual-stop operation

**Acceptance**

- Illegal transitions are rejected
- Manual stop produces an orderly final chunk and completion event
- Slow plotting cannot slow or lose recorded acquisition
- Queue overflow, reader timeout, task failure, and forced disconnect are detected
- Sample counts remain continuous across arbitrary chunk sizes
- Engine behavior is verified primarily against the simulator and contract-tested against NI hardware

### DAQ-M05: Durable recording, metadata, parsing, and replay

**Status:** Implementation complete; physical NI recording verification pending  
**Specification:** [DAQ-M05 durable recording, metadata, parsing, and replay](Milestones/DAQ-M05-durable-recording-metadata-parsing-replay.md)  
**Evidence:** [DAQ-M05 acceptance evidence](docs/milestones/DAQ-M05-acceptance-evidence.md)

**Goal:** Create a trustworthy source record that can be reopened without the original hardware.

**Deliverables**

- Session-folder creation with user-selected parent directory and sanitized experiment name
- Collision-resistant naming with preview and explicit overwrite prohibition
- Streaming TDMS recorder plus manifest and event-log writers
- Atomic/finalized manifest updates and recoverable incomplete sessions
- Session parser that validates schema, channel lengths, sample counts, timestamps, and checksums where practical
- Chunked replay API presenting recorded data through the same consumer-facing form as live acquisition
- Session summary CLI

**Acceptance**

- A simulated and a physical run can be recorded, closed, reopened, and replayed
- Parsed samples equal recorded samples exactly
- User-selected folders and filenames behave correctly on Windows
- Abrupt-stop and injected write-failure sessions remain identifiable and recoverable
- Disk-space checks and disk-full behavior are explicit
- Metadata is sufficient to interpret every raw channel without consulting the GUI

### DAQ-M05A: HDF5 source-record migration

**Status:** Implementation and target-disk simulator soak complete; physical NI verification pending  
**Specification:** [DAQ-M05A HDF5 source-record migration](Milestones/DAQ-M05A-HDF5-source-record-migration.md)  
**Evidence:** [DAQ-M05A acceptance evidence](docs/milestones/DAQ-M05A-acceptance-evidence.md)

**Goal:** Use HDF5 as the sole live raw container for new sessions without abandoning existing TDMS source records or NI interoperability.

**Deliverables**

- Manifest schema v2 and SWMR HDF5 layout v1 with an explicit commit journal
- Format-dispatching inspection, replay, processing, plotting, and export
- Read-only schema-v1 TDMS compatibility
- Verified, full-extent, on-demand TDMS export through CLI and GUI
- Local-disk live-recording policy and typed HDF5 durability failures

**Acceptance**

- Exact simulator HDF5 round trip and committed-prefix crash recovery
- Schema-v1 sessions retain the complete review and export workflow
- TDMS export is sample-exact, collision-safe, cancellable, and source-immutable
- Target-disk ten-minute soak and physical NI round trip remain separately evidenced

### DAQ-M06: Calibration and signal-conditioning pipeline

**Status:** Complete; accepted locally on Windows  
**Specification:** [DAQ-M06 calibration and signal-conditioning pipeline](Milestones/DAQ-M06-calibration-signal-conditioning-pipeline.md)  
**Evidence:** [DAQ-M06 acceptance evidence](docs/milestones/DAQ-M06-acceptance-evidence.md)

**Goal:** Turn raw volts into traceable engineering quantities through one validated, reproducible baseline recipe.

**Deliverables**

- Channel calibration primitives including gain, offset, sensitivity, polarity, and unit conversion
- Accelerometer conversion using per-axis certificate sensitivity when available, with nominal 6.3 mV/g only as a labeled fallback
- Charge-channel conversion using the validated chain sensitivity and polarity
- One `baseline-v1` processing recipe containing only the calibration and conditioning stages selected and validated during this milestone
- Candidate stages may include mean/bias removal, detrending, high-pass, low-pass, band-pass, notch, resampling, and windowing, but unused stages shall not be exposed merely because they are implemented
- Time-domain metrics such as RMS, peak, peak-to-peak, crest factor, and clipping fraction
- Spectral products including FFT amplitude and PSD with explicit window, overlap, normalization, and units
- Serializable baseline recipe and processing provenance, structured so later recipes can be added without changing stored raw data

**Acceptance**

- Calibration and filters pass analytical reference-vector tests
- Filter cutoff and attenuation meet declared tolerances
- The baseline live recipe rejects noncausal stages
- Any offline-only operation is clearly distinguished from causal live output
- Reprocessing the same session with the same recipe is deterministic
- Raw data remains unchanged

### DAQ-M07: Plotting and export services

**Status:** Complete; accepted locally on Windows  
**Specification:** [DAQ-M07 plotting and export services](Milestones/DAQ-M07-plotting-and-export-services.md)  
**Evidence:** [DAQ-M07 acceptance evidence](docs/milestones/DAQ-M07-acceptance-evidence.md)

**Goal:** Generate displays and shareable outputs without depending on GUI widgets.

**Deliverables**

- Backend plot-data service for raw, calibrated, filtered, and spectral views
- Viewport-aware decimation that preserves peaks and does not alter recorded data
- Static figure generation for time histories, overlays, FFT, and PSD
- CSV export with metadata sidecar
- Figure export to PNG and vector format
- Selected-time-range and selected-channel export
- Export manifest linking each artifact to its source session and baseline-recipe version

**Acceptance**

- Plot services are callable from CLI/tests without Qt
- Exported CSV time and samples agree with the source session
- Large sessions are processed and exported in bounded memory
- Decimated plots preserve extrema and time span
- Every conditioned export records its calibration and baseline-recipe version

### DAQ-M08: Hardware qualification and diagnostics

Historical qualification criteria below are optional diagnostics, not start gates.

**Status:** Implementation complete; supervised physical qualification and the
default 600-second target-hardware throughput run remain pending.

**Goal:** Determine whether the acquisition chain is ready before a real experiment.

**Deliverables**

- Discovery and NI self-test where supported
- Profile/capability comparison for chassis, module, and channels
- Powered-idle acquisition test
- Per-channel DC level, RMS noise, clipping/headroom, dropout, and spectral-noise summary
- Accelerometer bias plausibility and axis-response checks
- Conditioned charge-channel bias/range plausibility check
- Cross-channel alignment and optional crosstalk test using a known input
- Sustained throughput test at four channels × 50 kS/s
- Machine-readable qualification report with pass, fail, warning, and not-tested states

**Acceptance**

- Tests distinguish software verification from external wiring/conditioner assumptions
- Unsafe or overrange inputs block acquisition when appropriate
- A 10-minute default throughput run reports zero missing sample indices
- Known simulator faults are all detected
- Physical results can be saved with the experiment session

### DAQ-M09: GUI shell, configuration, and session browser

**Status:** Software/simulator accepted; physical NI GUI verification pending  
**Specification:** [DAQ-M09 GUI shell](Milestones/DAQ-M09-GUI-Shell-Configuration-Session-Browser.md)  
**Evidence:** [DAQ-M09 acceptance evidence](docs/milestones/DAQ-M09-acceptance-evidence.md)

**Goal:** Provide a usable desktop interface without relocating backend logic.

**Deliverables**

- PySide6 application shell and dependency injection/composition root
- Hardware/profile selector and channel configuration table
- Experiment metadata editor
- Parent-folder chooser, filename preview, collision warning, and recent locations
- Sample-rate, duration, chunk, and calibration controls plus a read-only display of the active baseline-recipe version
- Hardware-test interface and qualification-report viewer
- Existing-session browser and metadata summary
- Start eligibility derived from backend validation

**Acceptance**

- GUI contains no `nidaqmx`, TDMS, SciPy filtering, or direct file-serialization logic
- All configuration errors appear with actionable field-level feedback
- Invalid or unqualified configuration cannot start accidentally
- Simulated mode supports complete GUI testing without hardware
- Closing the GUI during configuration or an idle state releases all resources

### DAQ-M10: Live acquisition, plots, stop control, and post-run analysis

**Status:** Software/simulator accepted; physical NI workflow pending  
**Specification:** [DAQ-M10 live operator workflow](Milestones/DAQ-M10-live-acquisition-plots-stop-control-post-run-analysis.md)  
**Evidence:** [DAQ-M10 acceptance evidence](docs/milestones/DAQ-M10-acceptance-evidence.md)

The ai3 review correction permits offline voltage FFT/PSD alongside acceleration
spectra and in saved raw-diagnostic/open-acquisition sessions (ADR-0032). This is a
repository correction; installed-preview packaging and physical acceptance remain separate.

Session-review panels also support in-workspace minimize/maximize/restore and
reset controls, with application-session-only layout state. Verification is
recorded in the M10 acceptance evidence; installed-preview packaging remains separate.

**Goal:** Complete the operator workflow.

**Deliverables**

- Start, stop, elapsed time, acquired samples, persisted samples, queue health, and fault status
- Live raw-voltage plots and calibrated/conditioned plots
- Configurable time window, channel visibility, axis scaling, and pause-display behavior
- Live metrics with clearly stated update interval and bandwidth
- End-of-run summary and transition into session review
- Offline conditioned plots, FFT/PSD generation, time-range selection, and export controls
- Fault dialog that preserves the partial session and diagnostic report

**Acceptance**

- Start/stop commands call application services only
- GUI remains responsive during sustained 50 kS/s/channel acquisition
- Pausing or changing a plot never pauses recording
- Display drops, if any, are counted separately from acquisition/storage loss
- Live and replayed views agree when given the baseline causal recipe and the same samples
- A user can configure, test, acquire, inspect, condition, plot, and export without using the CLI

### DAQ-M10A: Open multichannel acquisition

**Status:** Software/simulator accepted; physical NI verification pending  
**Specification:** [DAQ-M10A open multichannel acquisition](Milestones/DAQ-M10A-open-single-channel-acquisition.md)  
**Evidence:** [DAQ-M10A acceptance evidence](docs/milestones/DAQ-M10A-acceptance-evidence.md)

**Goal:** Monitor all enabled raw-voltage channels continuously and create multiple
independent recording segments without restarting the configured DAQ task.

**Deliverables**

- Separate monitoring and recording lifecycles under one M04 engine
- Fixed-memory multichannel display with no pre-roll recording
- Multiple synchronized multichannel raw-diagnostic HDF5 sessions per monitor run
- Explicit local-to-monitor source-index provenance and segment finalization
- GUI Start monitoring, Record/Stop recording, and End monitoring controls

**Acceptance**

- Stopping a recording leaves monitored sample counts advancing
- Recorded segments reopen and replay exactly with indices rebased from zero
- Storage failure faults the segment visibly without silently stopping monitoring
- Ending or closing finalizes active recording work and releases the DAQ task

### DAQ-M11: System qualification, packaging, and laboratory release

The release campaign and acceptance criteria below are historical and optional for ordinary use and development.

**Status:** Internal 0.1.0.dev7 shared NI functional feedback-preview packaging implemented; M11
qualification and unconditional release remain incomplete  
**Preview evidence:** [DAQ-M11 feedback-preview packaging evidence](docs/milestones/DAQ-M11-feedback-preview-evidence.md)

**Goal:** Produce a supportable application that lab mates can install and use safely.

**Deliverables**

- Automated end-to-end simulator qualification suite
- Physical hardware acceptance protocol
- Long-duration soak, repeated-run, disconnect, disk-full, corrupted-file, and forced-close tests
- Windows executable/installer with version information
- NI-DAQmx prerequisite and compatibility check
- Example profiles, calibration templates, and demonstration datasets
- Operator quick-start guide, hardware wiring boundary, troubleshooting guide, and developer guide
- Versioned release and upgrade policy for session/configuration schemas

**Acceptance**

- Ten-minute four-channel 50 kS/s run completes with zero recorded sample gaps on the target computer
- At least 100 consecutive short start/stop runs release the NI task cleanly
- A packaged build completes the full simulated workflow on a clean test computer
- A packaged build discovers and records from the physical NI system
- Interrupted sessions reopen with correct incomplete/fault status
- An unfamiliar lab user can complete the documented workflow without modifying code

## 7. Deferred extensions

These should not delay the first dependable release, but the interfaces should leave room for them:

- Analog, digital, and software triggers with pre-trigger buffering
- User-defined processing recipes and a recipe editor
- Additional NI C-Series modules and non-NI DAQs
- Experiment templates and plugin-discovered channel/analysis packages
- Vacuum gauge, power-supply, relay, and valve integration
- Automated IV/vibration experiment sequencing
- MATLAB and database exporters
- Order tracking, coherence, transfer functions, and modal analysis
- Multi-device synchronization
- Role-based configuration locking and signed calibration records

## 8. Recommended implementation order

Implement one milestone per Codex task. Do not parallelize milestones that depend on unfinished contracts.

[DAQ-M12](Milestones/DAQ-M12-raw-first-monitoring.md) makes raw monitoring and optional recording the default workflow without qualification gates. Existing services and historical evidence remain useful; M08 qualification and M11 release campaigns do not block development or ordinary use. Later tasks may improve diagnostics, processing, packaging, or hardware coverage based on actual needs.
## 9. Codex milestone prompt template

Use the following structure for each implementation request:

> Implement DAQ-MXX only, using `DAQ-Software-Roadmap.md` and the repository architecture documents as authoritative requirements. First inspect the existing implementation and tests. Preserve all completed milestone behavior and architectural boundaries. Implement the milestone deliverables, tests, documentation, and acceptance evidence. Do not implement later GUI or feature work except where a minimal interface is required by DAQ-MXX. Run the focused tests and full test suite. Report changed files, architecture decisions, verification results, remaining assumptions, and any acceptance criterion that could not be physically verified.

## 10. Hardware references

- [NI-9239 specifications](https://www.ni.com/docs/en-US/bundle/ni-9239-specs/page/specs.html)
- [TE 830M1-0200 product specifications](https://www.te.com/en/product-20011425-00.html)
- [PCB 422E03 product specifications](https://www.pcb.com/products?m=422e03)
