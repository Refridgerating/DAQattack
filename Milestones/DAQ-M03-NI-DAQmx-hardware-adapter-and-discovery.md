# DAQ-M03: NI-DAQmx Hardware Adapter and Discovery

Status: Implementation complete; physical acceptance pending channel mapping  
Depends on: DAQ-M00, DAQ-M01, DAQ-M02  
Target hardware: NI cDAQ-9171 with one NI-9239 module  
Primary configuration: Four simultaneous differential voltage channels at 50 kS/s/channel

## 1. Goal

Provide the smallest trustworthy NI hardware boundary for LabDAQ.

This milestone shall discover the installed NI hardware, compare it with a versioned hardware profile, configure one synchronized four-channel voltage-input task, read bounded sample chunks into NumPy arrays, translate NI failures into application errors, and release every reserved NI resource deterministically.

DAQ-M03 is the first milestone that requires acceptance against physical hardware. Simulator tests remain mandatory, but simulator-only evidence cannot complete this milestone.

## 2. Engineering interpretation

This milestone is an impedance-matching layer between two state machines:

- The vendor-controlled NI-DAQmx task and device state
- The application-controlled DAQ port defined in DAQ-M02

The adapter shall reduce NI-specific behavior to stable application records and errors. Higher layers shall not need to know NI task names, DAQmx constants, device handles, error classes, or stream-reader objects.

The accepted first-principles requirement is not that NI-DAQmx is internally correct. The requirement is that every externally observable assumption made about the driver is checked, recorded, or converted into an explicit fault.

## 3. Exit condition

DAQ-M03 is complete when the application can perform all of the following through the DAQ port:

1. Load without NI-DAQmx installed when simulated mode is selected.
2. Discover a connected cDAQ-9171 and its installed NI-9239.
3. Report hardware identity and capabilities using NI-independent records.
4. Reject an incompatible or incomplete hardware configuration before acquisition.
5. Configure one task containing four differential voltage channels in declared channel order.
6. Acquire a short physical run at the requested 50 kS/s/channel target.
7. Return data as owned arrays shaped `[channels, samples]`.
8. Report requested rate, actual rate, channel order, and samples read.
9. Translate timeout, disconnect, resource-reservation, configuration, and generic DAQ failures into structured application errors.
10. Repeatedly configure, start, read, stop, and close without leaving reserved tasks or requiring an application restart.

## 4. Scope

### 4.1 Included

- Optional loading of the `nidaqmx` Python package
- NI driver and runtime availability checks
- Chassis, module, and analog-input-channel discovery
- cDAQ chassis to installed-module relationship reporting
- Hardware-profile comparison
- NI-9239 voltage range, channel count, terminal mode, and sample-rate validation
- One multi-channel analog-input task
- Finite and continuous timing configuration only where required by the existing DAQ port
- Preallocated NumPy reads through the NI multi-channel stream reader
- Task start, bounded read, stop, close, reset, and health operations required by the DAQ port
- NI error and warning translation
- Headless CLI commands for discovery, validation, and short acquisition
- Unit, integration, contract, and hardware acceptance tests
- Documentation and ADR updates

### 4.2 Excluded

- Acquisition threads, queues, worker supervision, and the full experiment state machine
- Recording to TDMS or any session folder
- GUI controls or plots
- Calibration into acceleration, charge, or other engineering units
- Filtering, FFT, PSD, metrics, or processing recipes
- Trigger configuration beyond preserving the existing DAQ-port contract
- Multiple chassis synchronization
- Dynamic module hot-swap recovery during a continuing run
- Automatic hardware-profile rewriting based on discovered hardware
- NI-MAX automation

These belong to later milestones unless a minimal interface change is required to preserve the architecture.

## 5. Architectural constraints

1. `nidaqmx` imports shall remain inside the NI adapter package.
2. Core, domain, application, simulator, storage, processing, CLI command models, and GUI packages shall not import `nidaqmx`.
3. NI classes, enums, task handles, channel objects, and exceptions shall not cross the adapter boundary.
4. The adapter shall implement the DAQ port established in DAQ-M02 rather than create a parallel hardware API.
5. One adapter instance shall own at most one active NI task unless the existing port explicitly requires otherwise.
6. Task ownership shall be unambiguous. The object that creates a task is responsible for closing it.
7. Discovery shall not reserve acquisition resources.
8. Configuration shall not begin recording, processing, plotting, or session creation.
9. Channel order shall come from the validated experiment configuration, not from lexical sorting of NI physical-channel names.
10. Hardware aliases such as `cDAQ1` shall be treated as local identifiers, not permanent hardware identity.
11. Serial numbers and model identifiers shall be recorded when the driver exposes them. Missing optional metadata shall be represented explicitly rather than fabricated.
12. A successful read shall return an array that is safe after the next adapter read. Callers shall never receive a mutable view into a reusable internal buffer.

An architecture test shall prohibit NI imports outside the adapter boundary.

## 6. Required records and contracts

DAQ-M03 should reuse the domain records and DAQ port from DAQ-M01 and DAQ-M02. Extend those contracts only when physical evidence proves that a required concept is missing.

At minimum, the NI adapter must be able to produce NI-independent equivalents of the following information:

### 6.1 Driver information

- Driver available or unavailable
- NI-DAQmx driver version when available
- Python `nidaqmx` package version when available
- Discovery timestamp
- Hostname and operating-system information if already part of the existing diagnostics contract

### 6.2 Device information

- Local NI device name
- Product type or model
- Product category
- Serial number when exposed
- Simulated-device flag when exposed
- Chassis relationship
- Installed module slot
- Health or accessibility state

### 6.3 Module and channel information

- Module local name
- Product type
- Serial number when exposed
- Slot number
- Available analog-input physical channels
- Supported measurement type
- Supported terminal configuration
- Supported or effective input range
- Supported maximum sample rate when exposed
- Simultaneous-sampling capability as profile metadata

### 6.4 Configured-task information

- Ordered physical channels
- Ordered logical channels
- Requested sample rate
- Actual sample rate after NI task verification or commit
- Requested input minimum and maximum
- Effective terminal configuration
- Acquisition mode
- Requested samples per channel or buffer target
- Task lifecycle state visible through the DAQ port

No record shall contain a live NI object.

## 7. Optional dependency and import behavior

The repository must remain usable on development computers that do not have NI software installed.

Required behavior:

- Importing the top-level LabDAQ package shall succeed without `nidaqmx`.
- Importing domain, application, simulator, processing, storage, and non-NI CLI code shall succeed without `nidaqmx`.
- Selecting or constructing the NI backend without the package or driver shall raise a typed `DriverUnavailable` application error with installation guidance.
- Simulated CLI commands and tests shall remain functional when the NI dependency is absent.
- The NI adapter shall not silently fall back to the simulator.
- Driver absence, package absence, and no connected hardware shall be reported as distinct conditions.

The preferred implementation is a lazy import inside the NI adapter factory or constructor. An equivalent isolated optional-dependency mechanism is acceptable if architecture tests prove the same boundary.

## 8. Discovery behavior

### 8.1 Discovery sequence

The NI discovery service shall:

1. Verify that the Python package can load.
2. Verify that the NI-DAQmx runtime can answer a basic system query.
3. Enumerate local NI devices.
4. Identify the expected cDAQ chassis.
5. Enumerate installed modules and map them to chassis slots.
6. Identify the expected NI-9239 module.
7. Enumerate available analog-input channels.
8. Normalize discovered information into domain records.
9. Return a structured discovery report containing matches, mismatches, warnings, and missing metadata.

### 8.2 Discovery rules

- Discovery shall be read-only and shall not create a long-lived acquisition task.
- Device order returned by the driver shall not determine experiment channel order.
- Duplicate local aliases shall be treated as invalid input even if the driver normally prevents them.
- A simulated NI device reported by NI-DAQmx shall not satisfy physical-hardware acceptance unless the user explicitly runs a simulation-only diagnostic.
- An unexpected extra NI device shall not fail discovery. It shall be reported and ignored unless selected by the profile.
- A missing chassis, missing module, wrong module model, wrong slot, or missing channel shall fail profile validation.
- Serial-number mismatch shall fail only when the profile declares a required serial number.
- Model comparison shall use normalized identifiers and preserve the raw driver-reported value for diagnostics.

## 9. Hardware-profile validation

The initial hardware profile shall declare at least:

- Profile ID and schema version
- Expected chassis model: cDAQ-9171
- Expected module model: NI-9239
- Expected module slot
- Four ordered physical channels
- Four ordered logical channel identifiers
- Differential voltage measurement
- Requested input range compatible with the NI-9239 ±10 V range
- Requested sample rate of 50,000 samples per second per channel
- Channel units of volts at the adapter boundary
- Whether chassis and module serial numbers are required, preferred, or unconstrained

Validation shall occur before acquisition starts.

The validator shall reject:

- Missing expected chassis or module
- Wrong module model or slot
- Fewer than four required physical channels
- Duplicate physical channels
- A configured physical channel belonging to another device or module
- Unsupported measurement type
- Unsupported terminal configuration
- Requested voltage limits outside the available range
- Nonpositive or unsupported sample rate
- Contradictory finite and continuous settings
- A hardware profile with an unsupported schema version
- A profile that cannot preserve declared channel order

The validator shall return all independently detectable mismatches in one report where practical. It shall not stop after the first harmless mismatch if the remaining checks are safe to perform.

## 10. NI task configuration

### 10.1 Task construction

The adapter shall create one analog-input task and add channels in the exact order declared by the validated experiment configuration.

For the baseline hardware, the task shall use:

- Four voltage input channels
- Differential terminal behavior supported by the NI-9239
- Voltage units at the adapter boundary
- Configured minimum and maximum values within the supported range
- One shared sample clock for all four channels
- The requested 50 kS/s/channel target

The adapter shall not create one task per channel.

### 10.2 Early verification

Configuration errors should be discovered before the first experiment read. The adapter should use the NI task state model to verify or commit the task when supported by the Python API and consistent with the existing DAQ port.

After verification or commit, the adapter shall query and record the effective sample rate. The driver-reported actual rate is authoritative for the configured task.

Rate policy:

- Never silently replace the requested rate.
- Record requested and actual rates separately.
- Accept exact agreement.
- Accept a coerced rate only when it is within an explicitly configured tolerance and the caller receives a structured warning.
- Reject a coerced rate outside the allowed tolerance.
- Use the actual rate in every returned acquisition record.

For the baseline 50 kS/s/channel profile, physical acceptance should require 50,000 samples per second per channel unless the hardware or driver proves a documented reason for a different effective rate and the milestone specification is revised.

### 10.3 Buffering

The adapter shall configure enough NI-side buffering for the bounded read size required by the DAQ port. It shall not implement M04 queueing or backpressure.

Chunk size is a software read boundary. It shall not be interpreted as a second sample clock or a second acquisition stream.

## 11. Reading samples

### 11.1 Reader

Use the NI analog multi-channel stream reader appropriate for floating-point voltage samples.

The adapter shall:

- Preallocate a two-dimensional NumPy `float64` array
- Use shape `[number_of_channels, requested_samples_per_channel]`
- Reuse the private buffer where practical
- Request a bounded number of samples per channel
- Apply a finite timeout supplied through the DAQ port or configuration
- Read and preserve the actual number of samples returned
- Return only valid sample columns
- Return an owned array or immutable application object that cannot be changed by a later read

### 11.2 Array invariants

Every successful four-channel read shall satisfy:

- `data.ndim == 2`
- `data.shape[0] == 4`
- `data.shape[1] == samples_read_per_channel`
- Channel row order equals configured channel order
- All rows contain the same number of samples
- Values are finite unless the driver explicitly returns a detectable invalid value that is converted into a fault
- Units are volts
- The array does not alias the adapter's next reusable read buffer

The adapter shall not calibrate volts into acceleration or charge in this milestone.

### 11.3 Sample timing

The adapter shall report the actual sample rate and samples read. The M04 acquisition engine will own global sample indexing, duration accounting, queues, and experiment lifecycle.

DAQ-M03 shall not derive authoritative relative time from repeated wall-clock calls. Sample index divided by actual sample rate remains the later authoritative relative-time model.

## 12. Task lifecycle and cleanup

### 12.1 Lifecycle

The adapter shall support the DAQ-port lifecycle established in M02, including:

- Configure
- Start
- Read
- Stop
- Close or reset
- Health query where already required

Illegal local lifecycle operations shall become typed application errors. Examples include reading before start, starting twice, configuring while a task is active, and reading after close.

### 12.2 Deterministic cleanup

Every task path shall end in deterministic cleanup.

Required paths include:

- Normal completion
- Manual stop
- Read timeout
- Configuration failure after partial task construction
- Start failure
- Read failure
- Hardware disconnect
- Caller cancellation
- Unexpected Python exception
- Adapter destruction after explicit misuse

Use context management or `try` and `finally` ownership patterns. Garbage collection is not an acceptable primary cleanup mechanism.

Cleanup behavior shall:

1. Attempt to stop an active task.
2. Close the task and release NI resources.
3. Clear adapter references to the closed task.
4. Preserve the primary error.
5. Attach cleanup failure as structured secondary context rather than masking the primary error.
6. Permit a later fresh adapter instance to configure the device without restarting Python.

Repeated-cycle acceptance shall specifically detect NI resource-reservation failures.

## 13. Error and warning translation

Raw `nidaqmx` exceptions shall not cross the adapter boundary.

The adapter shall map driver conditions into the existing application error hierarchy. Add the minimum missing subclasses or reason codes needed to distinguish at least:

- NI package unavailable
- NI runtime or driver unavailable
- No matching device
- Hardware-profile mismatch
- Unsupported hardware configuration
- Resource already reserved
- Task verification or commit failure
- Task start failure
- Read timeout
- Device disconnected or removed
- Read failure
- Stop failure
- Cleanup failure
- Driver-reported warning

Each translated error shall preserve diagnostic context where available:

- Stable application error code
- Human-readable action message
- NI numeric error code
- NI message text
- Operation being attempted
- Device, module, and channel identifiers
- Task state
- Requested rate and channel count where relevant
- Original exception through exception chaining

Messages shall identify likely corrective actions such as closing another NI application, checking NI-MAX, reconnecting the chassis, verifying the module slot, or correcting the hardware profile.

Warnings shall not be logged and ignored. They shall be returned or emitted through the structured result mechanism already used by the application.

## 14. Health and reset behavior

The DAQ-port health operation shall report only evidence the adapter can support.

Potential health states include:

- Driver unavailable
- Driver available with no devices
- Expected hardware absent
- Expected hardware present but profile invalid
- Hardware valid and idle
- Hardware configured
- Hardware running
- Hardware faulted
- Hardware state unknown after disconnect or cleanup failure

Reset behavior shall be conservative.

- Closing the owned task is always allowed.
- Resetting a physical NI device shall occur only through an explicit reset operation.
- The adapter shall not reset every detected NI device.
- A reset shall identify the selected device and return a structured result.
- Automatic reset after every error is prohibited because it can hide lifecycle defects and disrupt other NI applications.

## 15. CLI surface

Provide headless commands consistent with the repository's existing CLI naming. Equivalent command names are acceptable when repository conventions differ.

Recommended commands:

```text
labdaq hardware list --backend ni
labdaq hardware inspect --backend ni --device cDAQ1
labdaq hardware validate --backend ni --profile profiles/cdaq9171-ni9239-four-channel.json
labdaq hardware acquire-test --backend ni --profile profiles/cdaq9171-ni9239-four-channel.json --duration 2
```

### 15.1 `hardware list`

Report:

- Driver availability and version
- Detected NI devices
- Product type
- Serial number when available
- Simulated-device state
- Installed modules and slots

### 15.2 `hardware inspect`

Report:

- Selected chassis and module relationship
- Physical analog-input channels
- Relevant voltage-input capabilities
- Driver-reported metadata used by validation

### 15.3 `hardware validate`

Report:

- Profile ID and schema version
- Match or rejection
- Every detected mismatch
- Ordered selected channels
- Requested rate and range
- Warnings for optional missing metadata

### 15.4 `hardware acquire-test`

Perform one short, non-recorded acquisition and report:

- Requested and actual rate
- Ordered physical and logical channels
- Samples requested per channel
- Samples returned per channel
- Array shape and dtype
- Per-channel minimum, maximum, and mean voltage for diagnostics
- Start, stop, timeout, and completion status
- Cleanup result

The command shall not write a DAQ-M05 session or imply calibrated engineering units.

## 16. Implementation layout

Follow the repository structure established by DAQ-M00. A likely layout is:

```text
src/labdaq/
|-- adapters/
|   `-- ni/
|       |-- __init__.py
|       |-- adapter.py
|       |-- discovery.py
|       |-- task.py
|       `-- error_translation.py
|-- application/
|   `-- hardware_qualification.py
|-- ports/
|   `-- daq.py
`-- cli/
    `-- hardware.py
```

Tests may be organized as:

```text
tests/
|-- architecture/test_ni_import_boundaries.py
|-- unit/adapters/ni/
|-- contract/test_daq_port_contract.py
|-- integration/test_ni_cli_with_fake_driver.py
`-- hardware/test_cdaq9171_ni9239.py
```

Exact filenames may follow current repository conventions. Layer ownership and behavior are mandatory.

## 17. Test strategy

### 17.1 Unit tests without NI hardware

Use fakes or narrowly mocked NI boundaries to test:

- Optional dependency behavior
- Driver unavailable versus no hardware
- Discovery normalization
- Chassis, module, slot, and channel mapping
- Hardware-profile acceptance and rejection
- Channel insertion order
- Voltage range and terminal-mode validation
- Requested and actual rate handling
- Coerced-rate warning and rejection policy
- Preallocated buffer shape
- Partial reads
- Buffer ownership across sequential reads
- Lifecycle misuse
- Every error translation branch
- Cleanup after each injected failure point
- Preservation of the primary error when cleanup also fails

Do not mock the domain or DAQ port itself.

### 17.2 Architecture tests

Prove that:

- Only the NI adapter package imports `nidaqmx`
- Simulated tests collect and run when `nidaqmx` is not installed
- Application services reference the DAQ port, not the NI adapter implementation
- No NI object appears in public type annotations outside the adapter package

### 17.3 DAQ-port contract tests

Run the same relevant behavior contract against:

- The deterministic simulator
- A fake NI driver adapter
- The physical NI adapter under the hardware test marker

The contract shall cover discovery, validation, configure, start, bounded read, stop, close, lifecycle errors, shape, channel order, and cleanup.

### 17.4 Hardware tests

Hardware tests shall be marked and skipped by default. They shall require explicit selection and shall never run accidentally in general CI.

Example marker:

```text
pytest -m hardware
```

Hardware-test configuration shall come from a local profile or environment variable. Do not commit laboratory-specific NI aliases or serial numbers as universal defaults.

## 18. Physical acceptance procedure

Physical acceptance is mandatory for DAQ-M03.

### 18.1 Preconditions

- NI-DAQmx is installed and its version is recorded.
- The Python `nidaqmx` package is installed and its version is recorded.
- NI-MAX or an equivalent NI diagnostic confirms that the cDAQ-9171 is visible.
- The NI-9239 is installed in the declared slot.
- All four input channels are connected to safe voltage sources within the module range.
- The connected sensor or conditioner outputs are electrically compatible with the NI-9239.
- No other program owns the device.

NI-MAX is a setup and troubleshooting aid. It is not the LabDAQ runtime API.

### 18.2 Required checks

#### Check A: Discovery

Run hardware listing and inspection.

Pass conditions:

- The chassis is found.
- The NI-9239 is found in the expected slot.
- Four analog-input channels are found.
- Available model and serial metadata are reported.
- The device is not accepted as physical evidence if NI marks it simulated.

#### Check B: Profile validation

Validate the baseline four-channel profile.

Pass conditions:

- Chassis, module, slot, channels, differential voltage mode, range, and rate are accepted.
- Ordered channels match the profile.
- The report contains no unexplained warning.

Then intentionally change one profile field, such as module model, slot, or channel name.

Pass condition:

- The mismatch is rejected before a task starts.

#### Check C: Short physical acquisition

Acquire at least two seconds at 50,000 samples per second per channel.

Expected minimum result:

- 100,000 samples per channel
- Four aligned rows
- Array shape `[4, 100000]` when acquired as one bounded result, or an exactly reconstructable equivalent across bounded reads
- Actual rate reported as 50,000 samples per second per channel
- Channel order equal to the profile order
- No timeout, dropout, clipping claim, or silent partial read
- Successful stop and close

This milestone does not need to interpret the physical signal. It must prove reliable voltage acquisition and identity.

#### Check D: Channel mapping

Apply a distinguishable safe stimulus to each input one at a time, or use another controlled method that produces a unique response on each channel.

Pass conditions:

- The responding array row matches the declared logical and physical channel.
- No channel is swapped.
- Unstimulated channels do not show a comparable response caused by software ordering mistakes.

This is a wiring and mapping check, not a complete crosstalk qualification.

#### Check E: Repeated lifecycle

Run at least 20 configure, start, bounded-read, stop, and close cycles in one Python process.

Pass conditions:

- Every cycle succeeds.
- No resource-reserved error occurs.
- No task-leak warning occurs.
- The device remains usable without restarting Python or reconnecting hardware.

#### Check F: Timeout and cleanup

Create a controlled timeout or insufficient-sample condition.

Pass conditions:

- A typed application timeout is returned.
- The NI code and context are retained.
- The task is closed.
- The next clean acquisition succeeds.

#### Check G: Controlled disconnect

During a dedicated test, disconnect the chassis or otherwise make it unavailable while a task is active.

Pass conditions:

- The adapter reports a typed hardware-disconnect or device-unavailable fault.
- The process does not hang indefinitely.
- Cleanup is attempted and its result is recorded.
- After reconnection and explicit rediscovery, a new adapter instance can acquire again.

Do not make automatic continuation after reconnection part of this milestone.

## 19. Acceptance criteria

DAQ-M03 shall not be accepted until every criterion below is supported by automated or physical evidence.

### 19.1 Architecture

- NI imports are confined to the adapter package.
- Simulated mode works without NI software.
- No NI object crosses the adapter boundary.
- The adapter implements the established DAQ port.
- No M04 acquisition-engine behavior is moved into the adapter.

### 19.2 Discovery and validation

- The cDAQ-9171, NI-9239, installed slot, and four physical channels are discoverable.
- Hardware identity is represented through NI-independent records.
- A valid baseline profile is accepted.
- Wrong chassis, module, slot, channel, range, mode, or rate is rejected before acquisition.
- Driver absence, no hardware, and profile mismatch are distinguishable.

### 19.3 Acquisition

- One four-channel task is used.
- Channels are added and returned in declared order.
- A short physical acquisition succeeds at the 50 kS/s/channel target.
- Requested and actual rates are both reported.
- Returned arrays use `[channels, samples]` shape and `float64` values.
- Partial reads are explicit.
- Returned arrays remain valid after later reads.

### 19.4 Reliability

- Timeout, disconnect, reserved-resource, configuration, start, read, stop, and cleanup failures are translated.
- No raw NI exception escapes the adapter boundary.
- Every success and failure path attempts deterministic cleanup.
- Twenty repeated physical cycles leave no reserved tasks.
- A fresh acquisition succeeds after a controlled timeout and cleanup.

### 19.5 Documentation and evidence

- The hardware profile and local setup procedure are documented.
- CLI usage is documented.
- Unit, contract, integration, architecture, and hardware tests are documented.
- The full existing test suite passes.
- Physical acceptance output records hardware identity, software versions, exact commands, requested and actual rate, channel order, sample count, and cleanup result.
- The roadmap and milestone status are updated only after physical evidence passes.

## 20. Required failure cases

At minimum, tests shall cover:

1. `nidaqmx` package missing
2. NI runtime unavailable
3. No NI devices detected
4. Expected chassis missing
5. Expected module missing
6. Module in wrong slot
7. Wrong module model
8. Missing physical channel
9. Duplicate configured channel
10. Unsupported range
11. Unsupported terminal mode
12. Unsupported or coerced rate outside tolerance
13. Resource already reserved
14. Task verification failure
15. Task start failure
16. Read timeout
17. Partial read
18. Hardware disconnect during read
19. Stop failure
20. Close or cleanup failure
21. Primary read failure plus secondary cleanup failure
22. Attempted read before start
23. Attempted second start
24. Attempted reconfiguration while active
25. Returned-buffer mutation caused by a later read

## 21. Documentation changes

Update at least:

- Main README with NI optional-dependency installation and hardware diagnostic commands
- Architecture document with the NI adapter boundary
- Hardware-profile documentation with local aliases versus stable identity
- Developer documentation with hardware-test markers and setup
- Error-code or fault-code documentation
- ADR index and new decisions
- Roadmap status after acceptance

Recommended ADRs:

1. NI-DAQmx remains an optional adapter dependency.
2. One multi-channel NI task preserves hardware synchronization and channel order.
3. Driver-reported actual sample rate is recorded separately from requested rate.
4. The adapter owns deterministic task cleanup.
5. Returned sample arrays never alias the reusable NI read buffer.

## 22. Implementation constraints for Codex

When implementing DAQ-M03:

- Read the charter, architecture, roadmap, AGENTS.md, DAQ-M01, DAQ-M02, and this milestone before changing code.
- Inspect the existing DAQ port and domain records before adding interfaces.
- Implement only the smallest contract changes required by physical NI behavior.
- Keep NI-specific code in the adapter layer.
- Do not add GUI, storage, processing, replay, or M04 queueing.
- Preserve simulator behavior and existing tests.
- Use fake-driver tests for error paths that are impractical or unsafe to reproduce physically.
- Mark physical tests so normal CI skips them.
- Do not mark the milestone complete until the physical acceptance procedure passes.
- If hardware is unavailable, complete the code and automated tests, then report the milestone as implementation-complete but acceptance-blocked on hardware.

## 23. Required implementation report

Codex shall return:

### Changes

- Files added and modified
- Public contracts changed
- CLI commands added
- Documentation and ADRs added

### Architecture

- Location of the NI dependency boundary
- Task ownership and cleanup model
- Discovery and profile-validation flow
- Requested versus actual rate policy
- Buffer ownership model

### Automated verification

- Exact commands run
- Unit-test result
- Contract-test result
- Architecture-test result
- Full-suite result
- Any skipped hardware tests

### Physical verification

- Chassis and module identity
- Driver and Python package versions
- Profile used
- Exact acquisition command
- Requested and actual rate
- Channel order
- Samples per channel
- Array shape and dtype
- Twenty-cycle result
- Timeout recovery result
- Disconnect result
- Cleanup result

### Assumptions and risks

- Metadata not exposed by the driver
- Local NI aliases used during testing
- Untested driver or operating-system combinations
- Any rate coercion or warning
- Any physical check not completed

## 24. Completion statement

DAQ-M03 may be marked accepted only when the NI adapter passes the automated suite and the physical cDAQ-9171 plus NI-9239 acceptance procedure.

Passing fake-driver tests without a physical acquisition is not sufficient. A physically successful read without mismatch tests, error translation, and repeated cleanup is also not sufficient.
