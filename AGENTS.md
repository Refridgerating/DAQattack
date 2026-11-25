# AGENTS.md

## 1. Purpose

This file governs automated coding work in the LabDAQ repository.

LabDAQ is a laboratory desktop data-acquisition application for synchronized multichannel measurements. The initial supported system is an NI cDAQ-9171 chassis with an NI-9239 module acquiring three TE 830M1-0200 accelerometer channels and one externally conditioned charge-converter channel at up to 50 kS/s per channel.

The software must be dependable enough for laboratory use, reproducible enough for scientific analysis, and modular enough to support later hardware, processing, and experiment plugins.

Unless a more specific nested `AGENTS.md` applies, these instructions govern the entire repository.

## 2. Order of authority

When requirements conflict, use this order:

1. The user's current implementation request
2. The milestone specification named in that request
3. `DAQ-Software-Roadmap.md`
4. Architecture documents and accepted ADRs
5. Existing tests that encode accepted behavior
6. Existing implementation details
7. This file

Do not silently resolve a real conflict. Report it and choose the narrowest interpretation that preserves accepted behavior and safety.

## 3. Mission boundaries

The application shall:

- Discover and identify supported DAQ hardware; offer qualification as an optional diagnostic
- Configure synchronized analog acquisition
- Display raw measurements and optionally record them without silent loss
- Preserve metadata and calibration provenance
- Reopen and replay recorded sessions without the original hardware
- Apply reproducible calibration and conditioning pipelines
- Produce responsive live views without compromising recording
- Export traceable scientific data products
- Operate in simulated mode without NI hardware or NI software installed

The first release is not a generic automation framework. Do not implement deferred hardware, experiment sequencing, cloud services, databases, or plugin systems before their interfaces are required by an active milestone.

## 4. Core architectural rules

These rules are mandatory.

### 4.1 Layer boundaries

The intended dependency direction is:

```text
GUI and CLI
    -> application services
        -> domain models and ports
            <- adapters
```

Preferred responsibilities:

- `domain`: immutable records, value objects, validation rules, state definitions, units, and errors that do not depend on infrastructure
- `application`: use cases, orchestration, state transitions, acquisition coordination, qualification workflows, replay, processing requests, and export requests
- `ports`: abstract contracts for DAQ access, recording, session loading, processing, plotting, clocks, and exporters
- `adapters`: NI-DAQmx, simulated DAQ, TDMS, JSON, filesystem, CSV, and figure implementations
- `processing`: calibration, filtering, metrics, spectra, decimation, and recipe execution that are independent of GUI widgets
- `gui`: PySide6 views, view models or presenters, user interaction, and dependency injection only
- `cli`: thin command surfaces over application services

Do not create a second competing architecture when an existing package or service already owns the responsibility.

### 4.2 Forbidden dependencies

- Domain and application code must not import `nidaqmx`, PySide6, PyQtGraph, TDMS libraries, Plotly, Matplotlib, or filesystem-specific storage implementations.
- GUI code must not import `nidaqmx`, write TDMS or JSON files directly, calculate filters, perform FFTs, or own acquisition state.
- Processing code must not depend on GUI widgets or NI task objects.
- Storage adapters must not own scientific processing decisions.
- NI-specific objects, constants, exceptions, and task handles must not leak into domain records or application interfaces.
- Tests for core behavior must not require physical hardware.

Use architecture tests where practical to enforce high-value import boundaries.

### 4.3 Dependency isolation

NI and GUI dependencies are optional at import time.

A computer without NI-DAQmx, NI hardware, PySide6, or PyQtGraph must still be able to:

- Import domain and application packages
- Run simulator-backed tests
- Parse and inspect compatible recorded sessions when the needed storage dependency is installed
- Use non-GUI CLI functions supported by its installed extras

Import optional dependencies inside their adapter or composition boundary. Translate missing dependencies into explicit application-level errors.

## 5. Scientific data invariants

### 5.1 Raw data

Raw measurement samples are immutable source records.

- Never overwrite raw samples with calibrated, filtered, resampled, or decimated values.
- Derived products must reference the source session and processing recipe.
- Reprocessing must create or replace a derived artifact only, according to an explicit overwrite policy.
- CSV files, plots, spectra, and processed arrays are not authoritative substitutes for the raw session.

### 5.2 Time and indexing

- Sample index is the authoritative relative-time coordinate.
- Session start wall-clock time identifies the acquisition epoch.
- Event timestamps must state their clock basis.
- Do not generate time vectors with ambiguous inclusive endpoints.
- For a sample rate `fs`, sample `n` occurs at relative time `n / fs` unless a documented timing model states otherwise.
- Preserve monotonic, gap-detectable sample indices across chunk boundaries.

### 5.3 Array conventions

Unless an accepted interface explicitly states otherwise:

- Multichannel sample arrays use shape `[channels, samples]`.
- Channel order is explicit and stable.
- Data types are explicit.
- Every chunk carries session identity, first sample index, sample rate, channel identities, engineering units or raw-voltage status, and sample count.
- Empty arrays, partial final chunks, and uneven channel lengths must be handled explicitly.

Do not infer channel identity from array position without retaining the corresponding ordered channel metadata.

### 5.4 Units and calibration

- Store units explicitly.
- Do not mix volts, g, acceleration SI units, charge, force, or derived units without an explicit conversion.
- Calibration must include provenance such as sensor identity, coefficients, source, date, and version where available.
- Use per-sensor certificate sensitivity when supplied.
- A nominal accelerometer sensitivity such as 6.3 mV/g may be used only as a clearly labeled fallback.
- The conditioned charge channel must use the externally validated chain sensitivity and polarity. Do not invent those values.
- Preserve the distinction between raw voltage, calibrated engineering values, and conditioned values.

### 5.5 Processing reproducibility

- The same raw session, processing recipe version, and software implementation must produce deterministic results within declared numeric tolerances.
- Live processing must be causal.
- Zero-phase and other noncausal processing are offline-only and must be labeled as such.
- Filtering, FFT, PSD, windowing, overlap, normalization, and resampling choices must be explicit in metadata.
- Do not expose a processing stage in the baseline recipe merely because an implementation exists.

## 6. Acquisition and recording invariants

### 6.1 One hardware stream

A single DAQ acquisition stream feeds separate consumers when recording is enabled:

- A lossless recording path that receives every sample in the selected recording interval
- A bounded live-display path that may discard only superseded display frames

Do not create a second NI task or a second raw acquisition stream to serve the GUI.

### 6.2 Recording has priority

- Slow plotting must never throttle or corrupt recording.
- Display drops and acquisition or storage loss are different conditions and must be counted separately.
- Backpressure that threatens recording must become an explicit fault.
- Sample loss, queue overflow, storage failure, task timeout, clipping, hardware removal, and configuration mismatch must never remain silent warnings.

### 6.3 Acquisition state

Acquisition must use an explicit state machine. Typical states include:

```text
idle -> configured -> armed -> running -> stopping -> completed
                                      \-> faulted
```

- Illegal transitions must fail explicitly.
- Stop and cancellation paths must be idempotent where practical.
- Partial final chunks must be preserved.
- Failure must leave enough state to finalize or recover the session.
- Hardware cleanup must occur on success, manual stop, timeout, cancellation, and exception paths.

### 6.4 NI adapter

For NI-DAQmx work:

- Validate the requested chassis, module, physical channels, terminal configuration, range, sampling mode, and supported rate before acquisition.
- Use the appropriate preallocated multichannel stream reader for sustained acquisition.
- Report requested and actual sample rate when the driver exposes both.
- Translate NI exceptions into application-level errors while preserving useful driver details for diagnostics.
- Explicitly close tasks and release reserved hardware.
- Repeated configure, start, stop, and failure cycles must not leave orphaned tasks.
- Physical acceptance evidence must distinguish measured results from unverified assumptions about wiring, sensor power, or external conditioning.

## 7. Session and storage rules

Each recording creates a self-contained session directory with this baseline structure. Monitoring without recording does not require a session:

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

### 7.1 Source record

`raw.h5` and the initial schema-v2 manifest form the source record for new
sessions. Existing schema-v1 `raw.tdms` sessions remain supported read-only.

The manifest must include enough information to interpret each raw channel without consulting the GUI. This includes session identity, completion state, software and schema versions, hardware identity, acquisition settings, channel order, channel and sensor identity, units, calibration provenance, timing, sample counts, warnings, faults, and processing provenance.

### 7.2 Durability

- Never overwrite an existing session implicitly.
- Sanitize user-provided path components for Windows.
- Use collision-resistant session naming.
- Write manifests and indexes atomically where practical.
- Interrupted and failed runs must remain identifiable as incomplete or faulted.
- Persist the actual acquired and persisted sample counts.
- Validate reopened sessions for schema compatibility, channel lengths, expected counts, and checksums where practical.
- Handle disk-space warnings and disk-full failures explicitly.
- Process large sessions in bounded memory.

### 7.3 Events and errors

Use structured events and typed errors.

An event should normally include:

- Event code and severity
- Session identifier
- Event time and clock basis
- Acquisition state
- Relevant channel, task, file, or sample-index context
- Human-readable message
- Machine-readable details

Do not rely on free-form log messages as the only record of a scientifically relevant fault.

## 8. Simulation rules

The simulator is a first-class adapter, not a toy mock.

It must support deterministic, synchronized multichannel generation and explicit fault injection.

Expected signal or fault families include:

- Sine
- Impulse
- Swept sine
- Noise
- Drift
- Clipping
- Dropout
- Delayed reads
- Disconnects

Rules:

- Random behavior requires an explicit seed.
- Identical configuration and seed must reconstruct identical sample arrays.
- Chunk boundaries must not alter the continuous generated signal.
- Simulator faults must emit the same application-level event vocabulary used by physical acquisition where applicable.
- Use analytical reference signals to test frequency, amplitude, phase, timing, channel alignment, metrics, filters, FFT, and PSD behavior.

## 9. GUI rules

The GUI is an operator interface over tested services.

- Keep all hardware, storage, processing, and export logic behind application services.
- Do not place long-running work on the Qt UI thread.
- Do not allow plot refresh, pause-display, autoscaling, or window changes to affect recording.
- Derive start eligibility from actionable device and channel configuration validation. Do not require qualification reports, certification, calibration metadata, or external-confirmation checklists for raw monitoring or recording.
- Display actionable configuration errors at the relevant field or control.
- Preserve partial sessions and diagnostics when a run faults.
- Simulated mode must support the complete GUI workflow.
- GUI shutdown must request orderly service shutdown and release resources.

PyQtGraph is preferred for responsive live plotting. Static export rendering remains a backend service and must not depend on a live widget.

## 10. Legacy and reference code

Files such as the original `ReadAllChannels.py` and text sample outputs are historical references for channel intent and initial hardware interaction. They are not architectural templates.

Do not preserve problematic legacy behavior merely for similarity. In particular, do not copy patterns that:

- Open one text file per channel as the authoritative recording format
- Convert arrays to strings for storage
- Read all data into memory after acquisition
- Couple acquisition, storage, plotting, and shutdown in one function
- Use GUI plotting as part of the acquisition path
- Depend on implicit channel ordering or filenames
- Produce a time vector with a sample-count mismatch
- Leave some files or tasks unclosed on exceptions

When replacing legacy behavior, retain scientifically meaningful intent and document the migration.

## 11. Milestone execution protocol

Use milestone specifications to organize work, not to block the user’s requested development. A task may span milestones when needed to deliver the requested workflow; avoid unrelated changes.

Before editing:

1. Read `AGENTS.md`.
2. Read `DAQ-Software-Roadmap.md`.
3. Read the active milestone specification and relevant ADRs.
4. Inspect the existing implementation, tests, and package boundaries.
5. Identify accepted behavior from earlier milestones that must remain unchanged.
6. Determine which acceptance criteria require physical hardware and which can be verified in simulation.

During implementation:

- Implement the active milestone only.
- Preserve completed milestone behavior.
- Prefer the smallest coherent change that satisfies the full milestone.
- Add minimal interfaces for later work only when required by the current milestone.
- Do not implement future GUI, plugin, trigger, analysis, hardware, or packaging features speculatively.
- Do not perform broad refactors unrelated to the milestone.
- Do not rename public interfaces without a requirement, migration, and updated tests.
- Keep commits and diffs reviewable.

After implementation:

1. Run focused tests for the changed behavior.
2. Run architecture, type, lint, and formatting checks configured by the repository.
3. Run the full test suite.
4. Run relevant CLI or end-to-end simulator workflows.
5. Record physical criteria as unverified unless actual hardware evidence was obtained.
6. Update the milestone document, roadmap status, ADRs, user documentation, and developer documentation where applicable.

A milestone is not complete because code exists. It is complete only when its acceptance criteria have evidence or are explicitly marked as requiring later physical verification.

## 12. Testing standards

Every functional change requires tests at the lowest useful layer and at the relevant integration boundary.

### 12.1 Required test qualities

Tests should be:

- Deterministic
- Independent of execution order
- Hardware-free by default
- Clear about numeric tolerances
- Explicit about units and array shape
- Focused on externally observable behavior
- Able to run from a clean checkout

### 12.2 Test categories

Use the repository's established structure. Typical categories include:

- Unit tests for domain validation, state transitions, processing, and serialization
- Contract tests shared by simulated and NI DAQ implementations where possible
- Integration tests for acquisition, recording, reopening, replay, and export
- Architecture tests for forbidden imports
- End-to-end simulator tests for operator workflows
- Hardware acceptance tests marked separately and skipped safely without hardware

Do not weaken a test to make an implementation pass. Correct the implementation or explain why an accepted requirement must change.

### 12.3 Numeric tests

- Use analytical vectors and declared tolerances.
- Test chunk-boundary invariance.
- Test channel alignment and phase.
- Test edge cases including empty input, one-sample input, partial chunks, clipping, NaN or invalid values where allowed, and very large sessions through bounded-memory paths.
- Avoid brittle equality for floating-point operations unless byte-equivalence is an explicit simulator requirement.

## 13. Code quality standards

- Target Python 3.11 or later.
- Use type annotations for public interfaces and nontrivial internal functions.
- Prefer immutable dataclasses or equivalent records for domain data.
- Keep functions narrow and explicit about side effects.
- Use dependency injection at composition roots.
- Use `pathlib.Path` for paths.
- Use timezone-aware datetimes for persisted wall-clock timestamps.
- Use NumPy arrays for numeric sample data.
- Avoid global mutable state.
- Avoid hidden singleton task, session, or configuration objects.
- Avoid catch-all exceptions unless cleanup is followed by typed translation or re-raising.
- Preserve the original exception as the cause when translating errors.
- Do not log full sample arrays.
- Do not use `print` in library code. CLI output is allowed through the CLI presentation layer.
- Keep comments focused on why a decision exists, not a restatement of the code.

Use the repository's configured formatter, linter, and type checker. Do not introduce a second toolchain without a documented reason.

## 14. Documentation and ADR rules

Update documentation in the same change as behavior.

Create or update an ADR when a change establishes or materially alters:

- A persistent data or schema format
- A public port or major application boundary
- Acquisition concurrency or buffering policy
- Error and event semantics
- Processing recipe semantics
- Hardware compatibility policy
- A significant dependency choice
- A deliberate tradeoff that later contributors might otherwise reverse

Documentation must distinguish:

- Implemented behavior
- Simulated verification
- Physical hardware verification
- Assumptions about external wiring or conditioners
- Deferred work

Do not claim physical validation from simulator results.

## 15. Safety and laboratory integrity

The software does not make an electrical connection safe merely because a DAQ configuration is valid.

- Do not infer safe voltage, grounding, sensor power, isolation, or conditioner compatibility without authoritative hardware information.
- Treat overrange, clipping, unexpected bias, hardware mismatch, and unstable acquisition as explicit qualification findings.
- Hardware qualification reports must separate pass, fail, warning, and not-tested states.
- Never suppress a safety-relevant condition to preserve a smooth GUI workflow.
- Do not automatically retry a physical acquisition after a fault unless the active milestone defines bounded and safe retry behavior.

## 16. Repository hygiene

- Do not commit generated sessions, large raw TDMS files, build artifacts, caches, virtual environments, credentials, local NI configuration, or machine-specific paths.
- Keep small deterministic fixtures under a clearly named test-data directory.
- Update `.gitignore` when a new generated artifact class is introduced.
- Do not delete user data or recorded sessions during tests.
- Use temporary directories for storage tests.
- Preserve backward compatibility with accepted session schemas or provide explicit migration or rejection behavior.

## 17. Completion report format

At the end of each Codex task, report:

### Changes

- Files added, changed, and removed
- User-visible and internal behavior implemented

### Architecture

- Layer ownership of the new behavior
- Ports or schemas added or changed
- Important tradeoffs or ADRs

### Verification

- Focused test commands and results
- Full-suite command and result
- CLI, simulator, packaging, or hardware checks performed

### Acceptance

- Criteria satisfied
- Criteria not satisfied
- Criteria requiring physical hardware or external equipment

### Assumptions and risks

- Remaining assumptions
- Known limitations
- Follow-on work that belongs to later milestones

Do not describe a milestone as complete when a blocking acceptance criterion failed or was not addressed.

## 18. Default Codex instruction

When the user requests a milestone without supplying a custom prompt, interpret the request as:

> Implement the named DAQ milestone only. Use `AGENTS.md`, `DAQ-Software-Roadmap.md`, the milestone document, architecture documents, and accepted ADRs as authoritative requirements. First inspect the repository and existing tests. Preserve completed behavior and architectural boundaries. Implement the milestone deliverables, tests, documentation, and acceptance evidence. Do not implement later features except for a minimal interface required by the active milestone. Run focused checks and the full test suite. Report changes, architecture decisions, verification results, unresolved assumptions, and any criterion that could not be physically verified.
