# DAQ-M11: System Qualification, Packaging, and Laboratory Release

> Historical release specification: the qualification campaigns, 600-second soak, 100-cycle test, independent-operator checklist, and comprehensive release evidence below are optional validation activities, not gates for ordinary use or further development. Packaging and physical evidence can be pursued when needed. See [DAQ-M12](DAQ-M12-raw-first-monitoring.md).

**Goal:** Convert the completed LabDAQ application into a versioned, supportable laboratory release whose simulator workflow, packaged Windows build, physical NI workflow, failure behavior, documentation, and data compatibility have been verified with retained evidence.

## 1. Scope

DAQ-M11 qualifies and releases the behavior implemented in DAQ-M00 through DAQ-M10. It shall not add new acquisition modes, user-defined processing recipes, hardware families, triggers, instrument integrations, or analysis features.

This milestone covers five distinct evidence domains:

1. Headless end-to-end qualification using the deterministic simulator
2. Packaged-build qualification on a clean Windows computer
3. Physical acceptance using the target cDAQ-9171, NI-9239, and four-channel experiment
4. Recovery and fault-behavior qualification under interruption and resource failures
5. Release packaging, documentation, compatibility policy, and retained release evidence

A criterion requiring NI hardware, a clean test computer, an independent operator, or a long-duration run shall remain explicitly `NOT VERIFIED` until that activity is physically completed. Simulator evidence shall not be reported as physical acceptance.

## 2. Required architecture

- Qualification orchestration shall be callable headlessly and shall use existing application services and public ports.
- Qualification code shall not duplicate acquisition, recording, replay, processing, plotting, or export logic.
- The GUI shall remain a client of application services and shall not acquire special release-only behavior.
- Packaging configuration shall remain outside the domain and application layers.
- NI prerequisite detection shall be isolated from simulator startup so the packaged application can run in simulated mode without NI-DAQmx installed.
- Qualification results shall be emitted as structured records and rendered into human-readable reports by an adapter.
- Release verification shall preserve raw-session immutability. Compatibility checks or migrations shall not modify an original session in place.

## 3. Deliverables

### 3.1 System qualification framework

Add a release-qualification command or service that can execute named qualification suites and produce a machine-readable report plus a concise human-readable summary.

Each check shall record at least:

- Check identifier and qualification-suite version
- Application version, build identifier, source revision, and schema versions
- Host name, operating-system version, Python/runtime version where applicable, and available storage
- NI-DAQmx version and detected hardware identity when applicable
- Hardware profile, experiment configuration, and baseline processing-recipe version
- Start and end time, duration, result, measurements, thresholds, warnings, faults, and retained evidence paths
- Result state of `PASS`, `FAIL`, `WARNING`, `NOT_TESTED`, or `NOT_APPLICABLE`

The qualification runner shall support selective execution so simulator, package, destructive fault-injection, and physical hardware suites can be run independently.

### 3.2 Automated end-to-end simulator suite

Exercise the complete supported workflow through public application interfaces:

- Discover the simulated device
- Load and validate the initial hardware profile and experiment configuration
- Run simulated hardware qualification
- Create a session in a user-selected parent directory
- Acquire four synchronized channels at 50 kS/s/channel
- Stop by requested duration and by manual cancellation
- Persist and finalize the session
- Reopen, validate, and replay the session
- Apply `baseline-v1`
- Generate time-domain metrics, FFT, PSD, plot artifacts, CSV exports, and export provenance
- Verify raw-data immutability and deterministic derivatives

The suite shall include nominal, clipping, dropout, delayed-read, disconnect, storage-failure, and interrupted-finalization scenarios already supported by completed milestones.

### 3.3 Soak and repeated-run qualification

Provide scripted qualification procedures for:

- A sustained target-rate run with continuous sample-index, persisted-count, queue-health, clipping, warning, and fault monitoring
- At least 100 consecutive short configure, start, stop, close, and reopen cycles
- Repeated manual-stop timing at different chunk boundaries
- Repeated session-folder creation to verify collision resistance and handle cleanup
- Process memory, queue depth, file growth, and open-resource observations sufficient to identify unbounded growth

The test shall fail when any raw sample gap, unreported count mismatch, unreleased NI task, unhandled exception, unrecoverable session, or storage corruption is detected.

### 3.4 Physical hardware acceptance protocol

Create a controlled protocol for the target system:

- NI cDAQ-9171 chassis
- NI-9239 module
- Three TE 830M1-0200 accelerometer channels
- One externally conditioned charge-converter channel
- Four simultaneous channels at 50 kS/s/channel

The protocol shall require recording:

- Chassis and module identity, serial numbers when available, driver version, physical channel order, terminal configuration, input range, actual sample rate, and selected hardware profile
- Sensor identity, calibration source, sensitivity, polarity, units, and conditioning-chain description
- Wiring inspection and confirmation that external sensor power and the charge-conditioning chain are outside the software-controlled safety boundary
- NI discovery, profile validation, self-test where supported, powered-idle test, channel plausibility checks, headroom, noise, alignment, and throughput results
- A target-rate acquisition that is saved, reopened, replayed, processed, plotted, and exported using the packaged build
- Comparison of acquired, persisted, parsed, and replayed sample counts
- Evidence that task resources are released after normal completion, manual stop, fault, and application close

The protocol shall distinguish software-observed facts from externally asserted wiring, sensor calibration, charge-converter performance, and mechanical mounting conditions.

### 3.5 Failure and recovery qualification

Add automated or controlled tests for:

- Hardware disconnect before start and during acquisition
- NI read timeout or task failure
- Insufficient disk space before start
- Disk-full or injected write failure during acquisition
- Permission loss or invalid destination path
- Forced application close while idle, configured, running, stopping, finalizing, and reviewing a session
- Abrupt process termination during acquisition and manifest finalization
- Missing, truncated, malformed, or unsupported manifest files
- Truncated or corrupted TDMS data
- Missing, malformed, or partially written event logs
- Unsupported future schema versions and supported older schema fixtures

Expected behavior shall be defined for each case. No failure may silently report a completed session. Recoverable sessions shall reopen with their actual `INCOMPLETE` or `FAULTED` state, retained events, available raw data, and an actionable explanation of unavailable operations.

### 3.6 Windows packaging and prerequisite checks

The dev7 internal feedback-preview slice uses an administrator-elevated shared
installation, defaulting to Windows' 64-bit Program Files directory with an
editable destination page. It creates all-user shortcuts and HKLM installation
records. Upgrades reuse the registered shared folder; old per-user installations
remain untouched. Owned-file inventory cleanup preserves unrelated files and
per-user preferences. See ADR-0030 and the feedback-preview evidence for the
implemented subset and outstanding installed/physical acceptance gates.

Produce a versioned Windows laboratory distribution using the repository-approved packaging toolchain.

The distribution shall include:

- Application executable and required runtime dependencies
- Version metadata visible in the application, CLI diagnostics, and file properties where supported
- Packaging configuration tracked in the repository
- Installation or deployment instructions
- A release manifest containing application version, source revision, build date, supported schema versions, baseline recipe version, supported hardware profile revisions, dependency inventory, and SHA-256 hashes of release artifacts
- Example profiles, calibration templates, and demonstration sessions

The packaged application shall:

- Start in simulated mode on a clean supported Windows computer without a Python development environment
- Detect whether NI-DAQmx is unavailable, incompatible, or usable
- Provide an actionable prerequisite message without preventing simulated-mode use
- Discover and acquire from the supported NI system when the approved NI-DAQmx version is installed
- Write user data only to user-selected or documented writable locations, never into the installed application directory
- Preserve logs and diagnostic information needed for support

NI drivers shall be treated as an external prerequisite unless their redistribution is explicitly permitted and documented.

### 3.7 Compatibility and upgrade policy

Document and test a version policy for:

- Session manifest schema
- Hardware-profile schema
- Experiment-configuration schema
- Processing-recipe schema
- Event-log schema if independently versioned
- Packaged application version

The policy shall state:

- Which prior schema versions the release opens directly
- Which versions require a migration adapter
- Which versions are rejected
- How unsupported future versions are reported
- Whether downgraded applications may open newer sessions
- How migrations preserve the original source record
- How compatibility fixtures are retained in the test suite

At minimum, current-version fixtures, the oldest supported prior version, malformed files, and unsupported future-version fixtures shall be tested.

### 3.8 Documentation and release evidence

Create or update:

- Operator quick-start guide
- Installation and NI-DAQmx prerequisite guide
- Supported-hardware and hardware-wiring-boundary guide
- Physical hardware acceptance procedure and checklist
- Calibration-template instructions
- Session recovery guide
- Troubleshooting guide organized by observable symptom
- Developer build, package, test, and release guide
- Versioning, compatibility, migration, and upgrade policy
- Known limitations and deferred features
- Release notes for the laboratory release

Retain a release-evidence directory or generated bundle containing:

- Qualification reports
- Test logs
- Package hashes and release manifest
- Clean-computer simulator evidence
- Physical hardware acceptance evidence
- Soak and repeated-run results
- Independent-operator usability checklist
- Explicit list of criteria not tested and why

Generated evidence and large datasets shall not be committed unless repository policy explicitly permits it. Templates, schemas, report examples, and small deterministic fixtures shall be version controlled.

## 4. Required tests

### 4.1 Unit tests

- Qualification result-state and threshold evaluation
- Release-manifest generation and hashing
- Version and compatibility policy evaluation
- NI prerequisite-state translation
- Recovery-state classification
- Report serialization and schema validation

### 4.2 Integration tests

- Full simulated configure-to-export workflow through public services
- Packaged-path and writable-data-directory behavior
- Incomplete-session reopening after injected interruption
- Corrupted and unsupported fixture handling
- Deterministic reprocessing and artifact provenance
- Repeated start/stop resource cleanup using the simulator and NI contract doubles

### 4.3 Packaging tests

- Build succeeds from a documented clean checkout
- Packaged CLI environment diagnostics run without the source tree
- Packaged simulated workflow completes without system Python
- Required Qt plugins, numeric libraries, TDMS support, schemas, profiles, and templates are present
- Missing NI-DAQmx produces the intended actionable state

### 4.4 Physical and manual tests

- Ten-minute minimum target-rate acquisition on the target computer
- Long-duration target-rate soak defined by the acceptance protocol
- 100 consecutive physical start/stop cycles
- Physical disconnect and application-close behavior
- Packaged-build discovery and acquisition
- Independent operator completes the documented workflow

Manual and physical tests shall use controlled checklists and retained evidence. They shall not be represented as automated pytest results.

## 5. Acceptance criteria

DAQ-M11 is complete only when all applicable release gates below pass or are explicitly accepted as documented exceptions by the project owner.

1. The full existing unit, integration, architecture, and GUI test suite passes from a clean checkout.
2. The automated end-to-end simulator qualification suite completes the full configure, qualify, acquire, record, reopen, replay, process, plot, and export workflow.
3. A packaged build completes the full simulated workflow on a clean supported Windows computer with no source checkout and no separately installed Python.
4. Missing or incompatible NI-DAQmx is detected and reported actionably while simulated mode remains usable.
5. The packaged build discovers, validates, and records from the physical cDAQ-9171 and NI-9239 system.
6. A minimum ten-minute, four-channel, 50 kS/s/channel physical run completes on the target computer with zero detected raw-sample gaps and equal acquired, persisted, parsed, and replayed sample counts.
7. The defined long-duration soak completes without unbounded memory or queue growth, sample gaps, silent write loss, or unrecoverable output.
8. At least 100 consecutive short physical start/stop runs release the NI task cleanly and allow immediate reconfiguration.
9. Normal stop, manual stop, hardware disconnect, disk-full behavior, write failure, and forced close produce the documented session state and retained diagnostic evidence.
10. Interrupted sessions reopen with correct `INCOMPLETE` or `FAULTED` status and never appear as successfully completed.
11. Current and supported prior session/configuration fixtures open correctly. Malformed and unsupported future versions are rejected with actionable messages.
12. Release artifacts have version metadata, a release manifest, and verified SHA-256 hashes.
13. The operator, installation, hardware-boundary, recovery, troubleshooting, compatibility, and developer-release documents match the packaged application.
14. An unfamiliar lab user can install or launch the release, select the supplied profile, run hardware qualification, acquire a session, stop safely, reopen it, inspect plots, and export data without modifying code.
15. Every hardware-dependent or human-dependent criterion has retained evidence. Any unperformed criterion remains `NOT_VERIFIED` and prevents an unconditional release claim.

## 6. Non-goals

Do not implement during DAQ-M11:

- New trigger modes or pre-trigger buffering
- Additional NI modules or non-NI hardware
- User-authored processing recipes or recipe editor
- Vacuum gauge, power-supply, relay, or valve integration
- Automated experiment sequencing
- New analysis families such as coherence, transfer functions, modal analysis, or order tracking
- Cloud services, databases, remote control, or role-based authorization
- Changes to scientific processing merely to make qualification pass, unless a verified defect in an earlier milestone is documented and corrected with regression coverage

## 7. Required completion report

The implementation report shall include:

- Changed files grouped by qualification, packaging, compatibility, documentation, and tests
- Architecture decisions and any new ADRs
- Focused and full automated test results
- Package build command and produced artifacts
- Simulator qualification summary
- Physical hardware qualification summary
- Soak, repeated-run, fault-injection, and independent-user results
- Release manifest and artifact hashes
- Remaining assumptions and known limitations
- Every acceptance criterion marked `PASS`, `FAIL`, `NOT_VERIFIED`, or `NOT_APPLICABLE` with evidence

## 8. Codex implementation prompt

> Implement DAQ-M11 only, using `DAQ-Software-Roadmap.md`, the repository architecture documents, and all completed milestone contracts as authoritative requirements. First inspect the existing implementation, tests, packaging configuration, documentation, and public application interfaces. DAQ-M11 is a qualification, packaging, compatibility, recovery, and laboratory-release milestone. Do not add deferred acquisition, hardware, analysis, recipe-editor, or instrument-integration features.
>
> Add a headless release-qualification framework with structured `PASS`, `FAIL`, `WARNING`, `NOT_TESTED`, and `NOT_APPLICABLE` results. Build an automated end-to-end simulator suite that exercises discovery, configuration, hardware qualification, four-channel acquisition at 50 kS/s/channel, manual and duration stop, durable recording, session reopening, replay, `baseline-v1` processing, metrics, FFT/PSD, plotting, CSV export, provenance, deterministic reprocessing, and raw-data immutability. Add controlled fault and recovery tests for disconnect, timeout, disk-space failure, write failure, forced close, abrupt termination, incomplete finalization, corrupted files, and unsupported schemas.
>
> Add repository-tracked Windows packaging using the approved toolchain, version metadata, NI-DAQmx prerequisite and compatibility detection, clean writable-data paths, release-manifest generation, SHA-256 artifact hashes, example profiles, calibration templates, and demonstration sessions. The packaged application must remain usable in simulated mode when NI-DAQmx is absent and must provide actionable diagnostics.
>
> Define and test the schema compatibility and upgrade policy for sessions, hardware profiles, experiment configurations, processing recipes, and event logs. Preserve original raw sessions and never perform an in-place destructive migration.
>
> Create the operator quick-start, installation, NI prerequisite, supported-hardware boundary, physical acceptance, recovery, troubleshooting, compatibility, developer release, known-limitations, and release-notes documentation. Add evidence templates for the target cDAQ-9171, NI-9239, three TE 830M1-0200 accelerometers, and one externally conditioned charge channel.
>
> Implement all checks that can be automated. Do not claim physical NI acceptance, clean-computer package acceptance, long-duration soak acceptance, 100 physical start/stop cycles, or independent-user acceptance unless those activities are actually performed and evidence is available. Mark such criteria `NOT_VERIFIED` with exact commands and checklists for the project owner to execute.
>
> Preserve all completed milestone behavior and architectural boundaries. Qualification must call public application services rather than duplicate acquisition, storage, replay, processing, plotting, or export logic. Packaging code must not enter domain or application layers. Run focused tests, the full suite, and package smoke tests. Report changed files, architecture decisions, commands, results, produced artifacts, hashes, remaining assumptions, and every DAQ-M11 acceptance criterion with its evidence state.
