# DAQ-M02: Simulated DAQ and Deterministic Signal Generation

Status: Complete  
Depends on: DAQ-M00, DAQ-M01  
Blocks: DAQ-M03 through DAQ-M11  
Primary configuration: Four synchronized voltage channels at 50 kS/s/channel

Acceptance evidence: [DAQ-M02 acceptance evidence](../docs/milestones/DAQ-M02-acceptance-evidence.md)

## 1. Goal

Create the hardware-independent DAQ contract and a deterministic simulated implementation that can support all backend development before physical NI hardware is required.

At the end of this milestone, LabDAQ shall be able to discover a profile-compatible simulated device, validate the existing four-channel experiment configuration, configure and start one synchronized acquisition stream, read bounded NumPy arrays shaped `[channels, samples]`, stop and reset the adapter, report health, and inject repeatable signal and fault scenarios.

The same configuration, simulator scenario, seed, generator version, and supported numeric environment shall reproduce byte-equivalent sample arrays regardless of read chunk boundaries.

## 2. Engineering interpretation

This milestone defines a virtual instrument, not a demonstration script.

The problem has several equivalent forms:

- In sampled-data language, each channel is a deterministic function of absolute sample index.
- In distributed-systems language, read chunks are transport packets and must not alter the underlying ordered stream.
- In numerical-methods language, signal generation must be invariant under partitioning of the evaluation domain.
- In fault-injection language, abnormal behavior is scheduled against sample position or read operation and becomes structured evidence.
- In adapter architecture, the simulator and the future NI implementation are interchangeable implementations of one port.

A common but unnecessary assumption is that a seeded stateful random-number generator automatically guarantees reproducibility. It does not guarantee chunk invariance when channels, components, or read sizes change the order in which random numbers are consumed. M02 shall therefore make generated values addressable by absolute sample index.

## 3. Exit condition

DAQ-M02 is complete when the application can perform all of the following without NI-DAQmx or physical hardware:

1. Import the domain, application, ports, simulator, and CLI packages.
2. Discover one explicit simulated chassis, module, and four-channel device topology.
3. Validate the DAQ-M01 baseline hardware profile and four-channel experiment against the simulator.
4. Configure one ordered four-channel voltage stream at 50 kS/s/channel.
5. Start the stream and read owned `float64` arrays shaped `[4, samples_read]`.
6. Reconstruct the same complete sample stream from multiple arbitrary chunk-size sequences.
7. Reproduce the same canonical byte stream and SHA-256 checksum from the same scenario and seed.
8. Generate sine, impulse, linear swept-sine, white-noise, drift, and composite signals.
9. Inject clipping, sample dropout, delayed read, read timeout, and device disconnect behavior.
10. Surface injected abnormal behavior through typed application events, warnings, or errors.
11. Stop, close, reset, and rerun without stale state or changed output.
12. List simulated hardware and run a bounded non-recorded acquisition through the CLI.

## 4. Scope

### 4.1 Included

- The hardware-independent DAQ port
- NI-independent discovery, validation, configuration, read-result, lifecycle, event, and health records required by the port
- A simulated DAQ adapter
- A deterministic signal-generation library
- Versioned simulator-scenario records, JSON schema, codec, and fixtures
- Four synchronized raw-voltage channels
- Absolute-sample-index signal generation
- Deterministic white Gaussian noise
- Sine, impulse, linear chirp, linear drift, bias, and additive composition
- Clipping, dropout, delayed-read, timeout, and disconnect injection
- Immediate and optionally real-time-paced simulator operation
- Headless CLI discovery and bounded stream-test commands
- Unit, integration, architecture, and reusable DAQ-port contract tests
- Documentation, event codes, and ADR updates

### 4.2 Excluded

- NI-DAQmx imports or physical device access
- NI task objects, stream readers, or driver error translation
- Acquisition worker threads, queues, fan-out, or experiment state machine
- Authoritative session UUID creation or global acquisition chunk sequencing
- TDMS, manifest, event-log, CSV, or any other session storage
- Replay of stored sessions
- Calibration from volts into acceleration, charge, or other engineering units
- Filters, metrics, FFT, PSD, decimation, or processing recipes
- Plotting or GUI code
- Hardware qualification
- Automatic retries after faults
- Trigger prehistory or pre-trigger buffering
- Modeling the complete mechanical dynamics of the sensor, specimen, or fixture

M02 may generate physically plausible raw voltages. It shall not claim that the simulator is a calibrated model of the real experiment.

## 5. Architectural constraints

1. The DAQ port belongs in the ports layer and shall not import any concrete adapter.
2. The simulator belongs in the adapter layer and shall implement the DAQ port directly.
3. Application services shall depend on the DAQ port, not on the simulator class.
4. The port shall expose no NI-DAQmx class, enum, task, channel, exception, or driver-specific string requirement.
5. The simulator shall not import PySide6, PyQtGraph, TDMS libraries, SciPy signal-processing functions, or GUI modules.
6. NumPy is permitted in the DAQ port runtime result module and simulator numeric code. The DAQ-M01 domain package and immutable configuration records shall remain free of NumPy.
7. The simulator shall produce raw voltage samples only. Engineering-unit conversion remains in DAQ-M06.
8. Channel order comes from the validated experiment configuration and shall never be inferred from a dictionary, lexical sort, or discovery order.
9. Signal values shall derive from absolute sample indices. Chunk size shall only select which indices are returned.
10. The simulator shall generate data in bounded memory. It shall not precompute an entire long-duration or continuous run.
11. No simulator command may silently substitute for a requested NI backend.
12. Simulated hardware shall be clearly marked as simulated and shall never satisfy a physical-hardware acceptance or qualification result.
13. The DAQ adapter lifecycle is not the DAQ-M04 experiment state machine. M02 shall implement only the local adapter states needed to make calls legal and resources unambiguous.

## 6. DAQ port

The exact Python names may follow repository conventions. The public behavior shall remain equivalent.

The simulator scenario shall be supplied through the simulated-adapter factory or constructor. It shall not add a simulator-only parameter to the shared DAQ port.

A recommended protocol is:

```python
class DaqPort(Protocol):
    def discover(self) -> DaqDiscoveryReport: ...
    def validate(
        self,
        hardware_profile: HardwareProfile,
        experiment: ExperimentConfig,
    ) -> DaqValidationReport: ...
    def configure(self, experiment: ExperimentConfig) -> DaqConfigurationResult: ...
    def start(self) -> DaqStartResult: ...
    def read(
        self,
        samples_per_channel: int,
        timeout_seconds: float,
    ) -> DaqReadResult: ...
    def stop(self) -> DaqStopResult: ...
    def health(self) -> DaqHealthSnapshot: ...
    def reset(self) -> DaqResetResult: ...
    def close(self) -> None: ...
```

Equivalent method names are acceptable where M00 established different conventions.

### 6.1 Port semantics

- `discover` is read-only and does not configure or reserve acquisition resources.
- `validate` compares discovered capabilities, the hardware profile, and the experiment configuration without starting acquisition.
- `configure` prepares one ordered multi-channel stream but does not start it.
- `start` begins sample availability.
- `read` returns at most the requested samples per channel and applies a finite timeout.
- `stop` ends active acquisition and releases active stream state where appropriate.
- `health` reports evidence available from the backend.
- `reset` clears recoverable adapter fault and simulator state according to explicit backend rules.
- `close` deterministically releases adapter-owned resources and is safe to call from cleanup paths.

The port shall support context management or an equivalent `try` and `finally` cleanup pattern.

### 6.2 Local adapter lifecycle

At minimum, expose or internally enforce these local states:

- `idle`
- `configured`
- `running`
- `stopped`
- `faulted`
- `closed`

Required behavior:

- `configure` is legal from `idle` and from a fully released `stopped` state.
- `start` is legal only from `configured`.
- `read` is legal only from `running`.
- `stop` is legal from `configured` and `running` and shall be safe during cleanup.
- `reset` is legal from `stopped` or `faulted` and returns the simulator to `idle`.
- `close` is idempotent.
- Reading before start, starting twice, configuring while running, and reading after close shall raise typed lifecycle errors.

This state set shall not add `armed`, `stopping`, `completed`, or experiment completion reasons. Those belong to DAQ-M04.

## 7. Required public records

Use immutable records where practical. Public records shall contain only domain values, primitive values, NumPy arrays where required, and structured context.

### 7.1 `DaqDiscoveryReport`

Minimum content:

- Backend identifier
- Discovery time
- Driver or adapter version
- Detected devices
- Detected modules
- Available physical channels
- Capability summaries
- Warnings
- Whether each device is simulated

### 7.2 `DaqDeviceDescriptor`

Minimum content:

- Stable backend-local device identifier
- Vendor
- Model
- Serial number when available
- Human-readable alias
- Simulated flag
- Accessibility or health state
- Installed modules

### 7.3 `DaqModuleDescriptor`

Minimum content:

- Stable backend-local module identifier
- Parent device identifier
- Vendor
- Model
- Slot
- Serial number when available
- Available channels
- Simultaneous-sampling capability
- Supported measurement quantities
- Supported terminal configurations
- Supported input range
- Supported application sample rates

### 7.4 `DaqValidationReport`

Minimum content:

- Accepted or rejected result
- Hardware profile ID and revision
- Experiment configuration ID and revision
- Backend ID
- Selected device and module
- Ordered logical and physical channels
- Requested rate
- Supported or effective range
- Errors
- Warnings

Validation shall report all independently detectable mismatches where safe rather than stopping after the first error.

### 7.5 `DaqConfigurationResult`

Minimum content:

- Backend ID
- Device and module identity
- Ordered logical channels
- Ordered backend-local physical channels
- Requested sample rate
- Effective sample rate
- Sampling mode
- Requested input ranges
- Effective input ranges
- Configured samples-per-read target where applicable
- Structured warnings

### 7.6 `DaqReadResult`

Minimum content:

- Owned NumPy sample array
- Samples read per channel
- Ordered logical channel IDs
- Actual sample rate
- End-of-stream flag where finite acquisition reaches its configured end
- Structured nonterminal events associated with the read
- Optional adapter-local read sequence for diagnostics

Array invariants:

- `samples.dtype == numpy.float64`
- `samples.ndim == 2`
- `samples.shape == (len(channel_order), samples_read_per_channel)`
- Channel order equals the configured order
- All rows contain the same sample count
- Returned data is C-contiguous or explicitly normalized
- Returned data does not alias a private buffer that will be reused by the next read
- Values are volts at the DAQ boundary

M02 shall not attach an authoritative session ID, application chunk sequence, or global `start_sample_index` to this record. The simulator may track its internal next sample index, but DAQ-M04 owns the application acquisition chunk and continuity ledger.

### 7.7 `DaqHealthSnapshot`

Minimum content:

- Backend ID
- Local adapter lifecycle state
- Device availability
- Configured state
- Running state
- Last successful operation
- Last event or fault code
- Last error summary
- Effective sample rate when configured
- Ordered channel count
- Optional backend details

The simulator backend details may include scenario ID, seed, generator version, and internal next sample index.

### 7.8 Events and errors

Events shall include:

- Stable event code
- Severity
- Backend ID
- Operation
- Message
- Structured context
- Event timestamp
- Optional internal sample index or read sequence

Every `DaqError` shall include or reference an equivalent structured event so a failed operation does not lose machine-readable evidence.

## 8. Error hierarchy and event codes

Reuse the M00 application error hierarchy. Add the minimum DAQ-specific subclasses or reason codes needed to distinguish:

- DAQ lifecycle misuse
- DAQ profile mismatch
- Unsupported DAQ configuration
- DAQ start failure
- DAQ read timeout
- DAQ sample loss
- DAQ device disconnect
- DAQ stop failure
- DAQ reset failure
- DAQ cleanup failure
- Malformed DAQ read result
- Simulator scenario validation failure
- Simulator internal failure

Recommended stable event or fault codes:

```text
DAQ_LIFECYCLE_INVALID
DAQ_PROFILE_MISMATCH
DAQ_CONFIGURATION_UNSUPPORTED
DAQ_STARTED
DAQ_STOPPED
DAQ_INPUT_CLIPPED
DAQ_SAMPLE_LOSS
DAQ_READ_DELAYED
DAQ_READ_TIMEOUT
DAQ_DEVICE_DISCONNECTED
DAQ_RESET_COMPLETED
DAQ_CLEANUP_FAILED
SIM_SCENARIO_INVALID
SIM_GENERATOR_FAILURE
```

Fault and warning codes shall be backend-neutral where the same condition can occur on physical hardware. Event context shall identify `source: simulated` when injected by the simulator.

## 9. Simulated hardware topology

The simulator shall discover one explicit virtual topology compatible with the baseline application profile:

```text
Simulated cDAQ-class chassis
`-- Simulated four-channel simultaneous voltage module
    |-- ai0
    |-- ai1
    |-- ai2
    `-- ai3
```

Required reported capabilities:

- Four simultaneous analog voltage inputs
- Differential input behavior for profile validation
- Raw units of volts
- Application-supported rate of 50,000 samples per second per channel
- Input range compatible with the baseline NI-9239 profile
- Immediate start behavior
- Simulated identity clearly set to true

The simulator may use model-compatible names for profile testing, but it shall preserve an explicit simulated flag and synthetic serial identifiers. Selecting the simulator must be an explicit backend choice.

## 10. Simulator scenario model

Add a versioned simulator-scenario document and schema.

Recommended top-level fields:

- `schema_version`
- `scenario_id`
- `scenario_revision`
- `name`
- `description`
- `generator_version`
- `seed`
- `pacing_mode`
- `channel_signals`
- `fault_schedule`
- Optional expected reference values
- Optional notes

### 10.1 Seed

The seed shall be an explicitly validated unsigned integer. The accepted range shall be documented and stable.

The same seed may not rely on Python's process-randomized `hash()` function.

### 10.2 Pacing mode

Initial modes:

- `immediate`, where reads synthesize data as fast as practical
- `real_time`, where the simulator may delay reads to approximate configured sample timing

Automated tests shall use `immediate` unless testing pacing or timeout behavior. Sleep and clock functions shall be injectable so unit tests do not depend on real elapsed time.

### 10.3 Channel signal definition

Each configured logical channel shall have one signal definition containing:

- `channel_id`
- Raw unit, fixed to `V` for M02
- Baseline or bias voltage
- Ordered component list
- Optional clipping transform

The channel set shall match the enabled experiment channels exactly. Missing, duplicate, or unknown channel IDs shall be rejected.

### 10.4 Component identity

Every stochastic component shall have an explicit stable `component_id` or `stream_id`.

Random streams shall be derived from:

- Scenario seed
- Stable channel ID
- Stable component ID
- Absolute sample index
- Generator version

This prevents adding a noise component to one channel from changing the random sequence of another channel.

## 11. Signal generation

Signal generation shall be a pure evaluation of absolute sample indices wherever practical.

For a sample rate `fs`, the time coordinate for absolute sample index `n` is:

```text
t[n] = n / fs
```

### 11.1 Bias

A constant baseline voltage may be added to each channel.

The initial accelerometer-like fixture should represent the three 830M1-0200 outputs as biased voltage channels. At 3.3 V excitation, the supplied sensor datasheet identifies the nominal zero-g output as one-half of the excitation voltage, which is 1.65 V. The supplied datasheet also lists a nominal 6.3 mV/g sensitivity for the +/-200 g model and an amplified full-scale signal of approximately +/-1.25 V around bias.

These values belong in clearly labeled scenario fixtures. They shall not become universal hardcoded assumptions for all simulated accelerometers.

The fourth conditioned charge-converter channel shall use a separately declared baseline suited to its synthetic fixture. The repository shall not invent a physical charge-chain calibration.

### 11.2 Sine component

Required fields:

- Amplitude in volts
- Frequency in hertz
- Phase in radians or degrees with one canonical serialized form
- Optional component offset
- Optional start and stop sample indices

Reference formula:

```text
x[n] = A sin(2 pi f n / fs + phi)
```

Validation shall reject non-finite values and frequencies at or above Nyquist unless a future scenario explicitly supports intentional aliasing.

### 11.3 Impulse component

Required fields:

- Start sample index
- Amplitude in volts
- Width in samples
- Optional shape, initially `rectangular`

A one-sample impulse shall be supported. Its location shall be exact and independent of read boundaries.

### 11.4 Linear swept-sine component

Required fields:

- Start frequency
- End frequency
- Start sample index
- Duration in samples
- Amplitude
- Initial phase

Use an analytically defined phase for a linear chirp. Do not generate a chirp through a chunk-local oscillator whose phase resets at each read.

For elapsed chirp time `tau` and chirp rate `k`:

```text
phase(tau) = 2 pi (f0 tau + 0.5 k tau^2) + phi0
```

### 11.5 White-noise component

Required fields:

- RMS amplitude in volts
- Distribution, initially `gaussian`
- Stable component or stream ID

Requirements:

- Mean is nominally zero.
- RMS converges statistically to the requested value over a sufficiently large vector.
- A value is reproducible from its absolute sample index.
- Read partitioning does not change generated values.
- Channel generation order does not change generated values.
- Adding an unrelated stochastic component does not alter an existing component's stream.

A mutable sequential `numpy.random.Generator` is acceptable only if the implementation proves random access and partition invariance for every supported operation. A counter-based or stable hash-derived generator is preferred.

If a Box-Muller or similar transform is used, pair selection shall be based on absolute sample index so odd chunk lengths do not alter later samples.

### 11.6 Drift component

Initial drift type:

- Linear drift in volts per second

Required fields:

- Initial offset
- Slope
- Reference sample index
- Optional active interval

Drift shall derive from absolute time and shall not accumulate through repeated chunk-local addition.

### 11.7 Composite signal

For each channel:

1. Evaluate baseline.
2. Evaluate each active component at the requested absolute indices.
3. Sum components in documented stable order.
4. Apply channel transforms such as clipping.
5. Return raw volts.

Component ordering shall be deterministic. Validation shall reject duplicate component IDs.

## 12. Determinism and chunk invariance

This is the central acceptance requirement.

### 12.1 Canonical stream

For a fixed configuration, scenario, seed, generator version, supported Python version, and locked numeric dependency set, the canonical sample stream shall be byte-equivalent.

Canonical byte representation:

- NumPy `float64`
- C-contiguous array
- Channel-major shape `[channels, samples]`
- Little-endian representation for checksum calculation
- Channel order from the experiment configuration

The CLI and tests shall calculate SHA-256 over the canonical byte representation.

### 12.2 Partition invariance

The following acquisitions shall reconstruct identical canonical bytes for the same total sample range:

- One read containing all samples
- Equal fixed-size chunks
- Unequal predetermined chunk sizes
- Prime-number chunk sizes
- One-sample reads
- A final partial chunk

The test shall use `numpy.array_equal` and a matching canonical SHA-256 checksum, not only approximate equality.

### 12.3 Reproducibility boundary

Exact cross-platform floating-point identity is not guaranteed by first principles for every transcendental function and numeric library implementation.

M02 acceptance requires byte equivalence inside the supported and dependency-locked release environment. Cross-platform verification shall additionally use declared numerical tolerances and report any checksum difference. The generator version shall change if a deliberate algorithm change alters canonical output.

## 13. Fault injection

Fault schedules shall use absolute sample indices or explicit read sequence numbers. User-facing time values may be accepted only if validation converts them unambiguously to integral sample indices before execution.

A fault shall not depend on variable wall-clock scheduling except where delayed-read behavior is the subject of the test.

### 13.1 Clipping

Configuration:

- Target channels
- Lower voltage limit
- Upper voltage limit
- Active sample interval

Behavior:

- Values outside the interval are clamped after signal composition.
- The first affected read emits `DAQ_INPUT_CLIPPED`.
- Event context identifies channels, limits, first affected sample index, and clipped sample count for that read.
- Repeated events shall follow a documented deduplication rule so every sample does not create an event.
- Acquisition may continue.

### 13.2 Sample dropout

Configuration:

- Fault start sample index
- Missing samples per channel
- Optional target read or channel set, with all-channel simultaneous loss as the baseline scenario

Behavior:

- Clean samples before the exact fault boundary may be returned as a final partial successful read.
- The next affected read raises `DaqSampleLossError` and emits `DAQ_SAMPLE_LOSS`.
- Context includes missing sample count, fault boundary, expected channel count, and scenario ID.
- The simulator shall not insert zeros, repeat prior samples, compress time, or silently skip the missing interval.
- The adapter enters `faulted` unless a future contract explicitly permits continued degraded acquisition.

### 13.3 Delayed read and timeout

Configuration:

- Active sample or read interval
- Simulated read latency

Behavior:

- If simulated latency does not exceed the caller timeout, the read succeeds and emits `DAQ_READ_DELAYED` when the delay crosses a documented warning threshold.
- If simulated latency exceeds the caller timeout, the read raises `DaqReadTimeoutError` and emits `DAQ_READ_TIMEOUT`.
- Unit tests shall use an injected virtual clock or sleeper and shall not wait for long real-time delays.
- Timeout behavior shall not advance the signal cursor unless the declared hardware-like policy explicitly says samples were acquired and lost. The initial policy shall not advance the cursor on a pure simulated timeout.

### 13.4 Device disconnect

Configuration:

- Disconnect sample index or read sequence

Behavior:

- Clean data before the disconnect boundary may be returned as a final partial successful read.
- The next affected operation raises `DaqDisconnectedError` and emits `DAQ_DEVICE_DISCONNECTED`.
- Health reports the device unavailable.
- Further reads fail without advancing the sample cursor.
- `stop` and `close` still attempt local cleanup.
- `reset` may restore the simulated device only when the scenario declares reconnectability or a new adapter instance is created.

### 13.5 Fault composition

Multiple faults may coexist only when their ordering and interaction are deterministic.

At minimum, define and document precedence for:

1. Lifecycle legality
2. Device availability
3. Read timeout or delayed read
4. Sample-loss boundary
5. Signal generation
6. Clipping transform
7. Read-result publication

Contradictory or ambiguous fault schedules shall be rejected during scenario validation.

## 14. Finite and continuous acquisition behavior

### 14.1 Finite acquisition

For a finite DAQ-M01 configuration:

- The target sample count is derived from validated duration and sample rate.
- Reads return no more than the remaining sample count.
- The final read may be partial.
- The final successful read sets `end_of_stream` true.
- A read after end-of-stream returns an explicit end condition or raises a dedicated lifecycle or end-of-stream result according to one documented port convention.
- It shall not restart the signal or return duplicate data.

### 14.2 Continuous acquisition

For continuous acquisition:

- The simulator can generate samples until stopped or faulted.
- Memory use remains bounded by read size.
- The CLI shall always impose a sample or duration bound so a test command cannot run forever by accident.

## 15. Required scenario fixtures

Provide versioned fixtures that cover the baseline and every required behavior.

Recommended fixtures:

```text
config/simulator-scenarios/
|-- clean-four-channel-v1.json
|-- analytical-sine-reference-v1.json
|-- impulse-alignment-v1.json
|-- swept-sine-v1.json
|-- deterministic-noise-drift-v1.json
|-- clipping-v1.json
|-- dropout-v1.json
|-- delayed-read-v1.json
`-- disconnect-v1.json
```

### 15.1 Clean four-channel fixture

- Four channels at 50 kS/s/channel
- Three accelerometer-like voltage channels with declared bias
- One conditioned charge-like voltage channel with separately declared bias
- Distinct amplitudes, frequencies, and phases
- No faults

### 15.2 Analytical sine reference fixture

Use a coherent, noise-free reference suitable for objective recovery testing.

Recommended baseline:

- Sample rate: 50,000 Hz
- Duration: 1.0 s
- Frequency: 1,000 Hz on all channels
- Distinct nonzero amplitudes on all channels
- Phases: 0, +90, +180, and -90 degrees
- Declared DC biases
- No clipping, noise, drift, or fault injection

Expected values and the generator version shall be included in fixture metadata or a neighboring reference document.

### 15.3 Impulse alignment fixture

- One common impulse sample index on all four channels
- Distinct amplitudes or polarities
- No noise

This proves exact interchannel sample alignment.

## 16. Analytical reference acceptance

For the `analytical-sine-reference-v1` fixture, use a documented independent recovery method in tests. A least-squares sinusoid fit or coherent DFT projection is preferred.

Required tolerances:

- Frequency error no greater than 0.5 Hz
- Amplitude error no greater than the larger of 1 microvolt or 0.1 percent of configured amplitude
- Phase error no greater than 0.1 degree after applying one documented phase convention
- Interchannel sample alignment exactly zero samples
- Recovered channel order exactly matches configuration order

For the impulse fixture:

- Peak sample index shall equal the configured impulse index on every channel.
- Relative channel alignment shall be exactly zero samples.
- Chunk boundaries shall not split, shift, duplicate, or remove the impulse.

These tolerances test the simulator and test method. They do not qualify the physical sensor, NI hardware, or later processing pipeline.

## 17. Validation rules

At minimum, reject:

- Missing or unsupported scenario schema version
- Missing or unsupported generator version
- Duplicate scenario, channel, component, or fault IDs
- Scenario channel set that differs from the experiment channel set
- Unknown channel IDs
- Non-voltage raw units in M02
- Non-finite amplitude, bias, phase, frequency, drift, latency, or clipping values
- Negative sample indices
- Zero or negative impulse width
- Zero or negative chirp duration
- Negative noise RMS
- Frequency at or above Nyquist
- Chirp endpoints at or above Nyquist
- Clipping minimum greater than or equal to clipping maximum
- Zero or negative dropout count
- Negative read delay
- Fault intervals with invalid ordering
- Contradictory fault schedules
- Unsupported pacing mode
- Seed outside the documented range
- Scenario request incompatible with the selected acquisition mode

Validation errors shall identify the scenario ID, field path, rejected value where safe, rule, and corrective action.

## 18. CLI surface

Follow the CLI naming established in M00 and M01. The following surface aligns with the planned M03 hardware commands:

```text
labdaq hardware list --backend simulated
labdaq hardware inspect --backend simulated
labdaq hardware validate --backend simulated \
  --profile config/hardware-profiles/cdaq-9171-ni-9239-four-channel-v1.json \
  --experiment config/experiments/four-channel-vibration-template-v1.json
labdaq hardware acquire-test --backend simulated \
  --experiment config/experiments/four-channel-vibration-template-v1.json \
  --scenario config/simulator-scenarios/clean-four-channel-v1.json \
  --duration 2
```

Equivalent names are acceptable where repository conventions differ.

### 18.1 List and inspect

Report:

- Backend identity
- Simulated flag
- Device and module identifiers
- Module slot
- Available channels
- Supported rate and range
- Simultaneous-sampling capability
- Adapter and generator versions

### 18.2 Validate

Report:

- Hardware profile
- Experiment configuration
- Scenario ID and schema version
- Generator version and seed
- Selected channel order
- Requested sample rate
- Acceptance, warnings, and all detected errors

### 18.3 Bounded acquisition test

Perform one non-recorded bounded acquisition and report:

- Backend, device, module, and scenario
- Seed and generator version
- Requested and actual sample rate
- Requested duration or sample limit
- Read chunk size
- Chunk count
- Total samples per channel
- Array shape and dtype
- Ordered channel IDs
- Per-channel minimum, maximum, mean, and RMS voltage
- Canonical SHA-256 checksum
- Structured events and faults
- Final health and cleanup result

The command shall not print all raw samples by default. It shall not write a DAQ-M05 session. Optional JSON output is recommended for automated acceptance evidence.

A continuous configuration shall require an explicit `--duration`, `--samples`, or equivalent bound.

## 19. Suggested implementation layout

Follow M00 repository conventions. A likely layout is:

```text
src/labdaq/
|-- ports/
|   `-- daq.py
|-- application/
|   `-- daq_validation.py
|-- adapters/
|   `-- simulated_daq/
|       |-- __init__.py
|       |-- adapter.py
|       |-- discovery.py
|       |-- scenario.py
|       |-- generators.py
|       |-- deterministic_noise.py
|       |-- faults.py
|       `-- clock.py
`-- cli/
    `-- hardware.py

schemas/
`-- v1/
    `-- simulator-scenario.schema.json

config/
`-- simulator-scenarios/
    `-- ...

tests/
|-- architecture/
|   `-- test_daq_boundaries.py
|-- contract/
|   `-- test_daq_port_contract.py
|-- unit/adapters/simulated_daq/
|-- unit/simulator/
|-- integration/
|   |-- test_simulated_daq.py
|   `-- test_simulated_daq_cli.py
`-- fixtures/simulator/
```

Runtime records containing NumPy arrays shall live in the port or another runtime-facing module outside the DAQ-M01 domain package. Pure metadata records may follow the architecture established in M00. The boundary and import rules are mandatory.

## 20. Required test strategy

### 20.1 DAQ-port contract tests

Create a reusable contract suite that later adapters can run where behavior is applicable.

Contract coverage:

- Discovery returns NI-independent records
- Validation accepts the baseline profile and rejects mismatches
- Configure preserves declared channel order
- Start before configure fails
- Read before start fails
- Start twice fails
- Successful reads return owned `float64` arrays shaped `[channels, samples]`
- Consecutive reads do not alias one another
- Stop and close are safe through normal and fault paths
- Close is idempotent
- Health reflects local state transitions
- Reset clears recoverable adapter state
- No concrete adapter object leaks through public records

M03 shall reuse this contract rather than inventing a separate NI API.

### 20.2 Signal-generator unit tests

For every component:

- Validate exact values at selected known sample indices
- Validate active interval boundaries
- Validate chunk partition invariance
- Validate channel independence
- Validate stable component ordering
- Validate finite numeric output
- Validate rejection of malformed parameters

For noise:

- Exact byte equality for the same seed and sample indices
- Different seeds produce different streams
- Different channel or component IDs produce different streams
- One-sample and arbitrary chunk reads reconstruct the same bytes
- Statistical mean and RMS fall within declared confidence tolerances for a large vector

### 20.3 Determinism tests

For one fixed scenario, generate the same total sample range with chunk sizes including:

```text
1, 7, 64, 257, 1000, 4096, 8191, and the full vector
```

Also test one irregular sequence whose chunk sizes sum to the same total.

Assertions:

- `numpy.array_equal` for reconstructed arrays
- Equal canonical byte strings
- Equal SHA-256 checksums
- Equal event sequence for the same fault schedule
- Equal final internal sample index

### 20.4 Analytical reference tests

- Recover frequency, amplitude, and phase from the analytical sine fixture within Section 16 tolerances.
- Recover the exact impulse index and zero interchannel sample skew.
- Confirm a chirp begins and ends at its declared frequencies within the limits of the independent test method.

### 20.5 Fault tests

For each fault scenario, assert:

- Fault onset is tied to the declared absolute sample or read position.
- Clean data before onset is unchanged.
- The correct typed event or error is produced.
- The correct event code and structured context are present.
- No silent zero filling, repetition, compression, or cursor advance occurs.
- Health reflects the resulting state.
- Stop and close still execute.
- Reset behavior matches the declared policy.

Required faults:

- Clipping
- Sample dropout
- Delayed read below timeout
- Delayed read above timeout
- Disconnect

### 20.6 Finite and continuous integration tests

Finite mode:

- Exact target sample count
- Correct final partial read
- Explicit end-of-stream behavior
- No duplicate read after completion

Continuous mode:

- Bounded CLI termination
- Bounded memory across many reads
- Clean manual stop at the adapter boundary

### 20.7 CLI integration tests

- Simulated list and inspect commands succeed without NI software.
- Validation reports selected channels and accepted configuration.
- Bounded acquisition prints or returns the expected totals and checksum.
- Fault scenarios return nonzero exit status where appropriate.
- JSON output is machine-readable when implemented.
- Continuous acquisition without a bound is rejected.

### 20.8 Architecture tests

Prove that:

- `labdaq.ports` does not import the simulator or NI adapter.
- The simulator does not import NI, GUI, storage, plotting, or processing packages.
- Application code depends on the DAQ port.
- No `nidaqmx` import is required to collect or run M02 tests.
- No NI type appears in public annotations or runtime records.
- The simulator cannot be selected through silent NI fallback.

### 20.9 Performance and memory tests

- Signal generation shall use vectorized or bounded-block operations rather than Python loops over every sample.
- A multi-second four-channel 50 kS/s immediate-mode acquisition shall complete without whole-run preallocation.
- Peak simulator working memory shall scale with channel count and read size, not requested total duration.

Performance tests shall avoid fragile machine-specific timing thresholds. Report throughput as evidence, but fail only on clear regression criteria established in the repository.

## 21. Acceptance scenarios

### Scenario A: Baseline clean acquisition

Given the DAQ-M01 four-channel experiment, the profile-compatible simulator, and the clean fixture, validation succeeds. A two-second run at 50 kS/s/channel returns exactly 100,000 samples per channel in declared order with no faults.

### Scenario B: Byte-equivalent rerun

Given the same experiment, scenario, seed, generator version, and chunk plan, two independent adapter instances produce identical arrays and matching canonical SHA-256 checksums.

### Scenario C: Chunk-invariant reconstruction

Given one total sample range, a single read and several irregular read partitions reconstruct byte-identical four-channel arrays.

### Scenario D: Analytical sine recovery

Given the analytical sine fixture, an independent recovery test estimates frequency, amplitude, and phase within Section 16 tolerances and confirms zero-sample interchannel skew.

### Scenario E: Exact impulse alignment

Given the impulse fixture, all channel peaks occur at the declared absolute sample index regardless of whether a chunk boundary falls immediately before, at, or after the impulse.

### Scenario F: Clipping

Given a signal exceeding configured limits, returned samples are clamped, `DAQ_INPUT_CLIPPED` is emitted with affected channels and sample counts, and acquisition continues.

### Scenario G: Sample dropout

Given a dropout at a declared sample index, clean prefix samples remain exact. The following affected read raises `DaqSampleLossError`, emits `DAQ_SAMPLE_LOSS`, enters faulted state, and does not silently fill or compress the gap.

### Scenario H: Delayed read and timeout

Given latency below the timeout, the read succeeds with a delayed-read event. Given latency above the timeout, the read fails with `DaqReadTimeoutError` without unintentional sample-cursor advance.

### Scenario I: Disconnect

Given a disconnect boundary, health becomes unavailable and reads fail with `DaqDisconnectedError`. Cleanup remains callable and no further samples are generated.

### Scenario J: Lifecycle misuse

Reading before start, starting twice, configuring while running, and reading after close each produce `DAQ_LIFECYCLE_INVALID` without corrupting adapter state.

### Scenario K: Repeatable reset

After a recoverable simulator fault, reset and reconfiguration with the same inputs reproduce the original stream from sample zero.

## 22. Acceptance criteria

DAQ-M02 is accepted only when all of the following are demonstrated:

1. The DAQ port defines discovery, validation, configure, start, read, stop, health, reset, and deterministic close behavior.
2. The port contains no NI-specific types or dependencies.
3. The simulator discovers one explicit profile-compatible four-channel simultaneous voltage device.
4. The DAQ-M01 baseline configuration validates against the simulator.
5. Successful reads return owned C-contiguous NumPy `float64` arrays shaped `[4, samples_read]` in declared channel order.
6. The simulator generates sine, impulse, swept-sine, noise, drift, and composite signals.
7. The same scenario, seed, generator version, and supported environment produce byte-equivalent canonical sample arrays.
8. Arbitrary chunk boundaries do not change the reconstructed continuous stream.
9. Noise is deterministic by absolute sample index and independent of channel generation order.
10. Clipping, dropout, delayed-read, timeout, and disconnect scenarios produce explicit typed events or errors.
11. Fault behavior does not silently fill, repeat, compress, or discard samples.
12. Known sine frequency, amplitude, and phase are recovered within Section 16 tolerances.
13. Impulse alignment proves zero-sample skew across all four channels.
14. Finite acquisition returns the exact target count and supports a final partial read.
15. Continuous acquisition remains bounded by caller stop or explicit CLI limits.
16. Stop, reset, close, and failure cleanup leave no stale simulator state.
17. The CLI lists simulated hardware and completes a bounded non-recorded acquisition.
18. Simulated mode works when NI-DAQmx is absent.
19. Contract, unit, integration, architecture, and full repository tests pass.
20. Documentation and acceptance evidence are updated.

No physical hardware verification is required for M02. Physical DAQ acceptance begins in DAQ-M03.

## 23. Required documentation and ADR updates

Update at least:

- Main README with simulated discovery and bounded acquisition commands
- Architecture documentation with the DAQ port and simulator boundary
- Developer documentation for scenario creation and generator versioning
- Simulator scenario schema documentation
- Event and fault-code documentation
- Roadmap milestone status and acceptance evidence
- Test documentation for deterministic checksums and contract reuse

Recommended ADRs:

1. DAQ access is defined by one hardware-independent port.
2. Simulated values are functions of absolute sample index, not read history.
3. Canonical simulator output uses ordered channel-major `float64` arrays.
4. Random streams are keyed by seed, channel, component, and sample index.
5. Fault schedules use sample position rather than wall-clock timing.
6. Simulated hardware may validate software configuration but cannot satisfy physical qualification.
7. Global acquisition chunk sequencing and session identity remain owned by DAQ-M04.

## 24. Completion evidence expected from Codex

The implementation report shall include:

- Files added and modified
- Final DAQ-port method signatures
- Public runtime records and error codes
- Simulator lifecycle table
- Scenario schema and fixture list
- Deterministic generator algorithm and generator version
- Canonical byte-order and checksum rule
- Chunk sizes and irregular partition used for invariance testing
- Reference scenario amplitudes, frequencies, phases, and recovery errors
- Fault scenarios and observed event codes
- Focused test commands and results
- Full test-suite command and result
- CLI command and sample output summary
- Generated sample count, array shape, throughput, and SHA-256 checksum
- Remaining assumptions and deferred work

Codex shall not claim physical validation in this milestone.

## 25. Implementation prompt

> Implement DAQ-M02 only, using the repository charter, architecture documents, AGENTS.md, `DAQ-Software-Roadmap.md`, DAQ-M00, DAQ-M01, and `Milestones/DAQ-M02-simulated-daq-deterministic-signal-generation.md` as authoritative requirements. First inspect the existing package layout, configuration records, error hierarchy, CLI, tests, and import-boundary rules. Define one hardware-independent DAQ port and implement a deterministic simulated adapter that produces synchronized four-channel raw-voltage reads. Generate signals from absolute sample indices so identical configuration, scenario, seed, generator version, and supported numeric environment produce byte-equivalent arrays independent of chunk boundaries. Implement the required signal components, fault scenarios, scenario schema and fixtures, CLI commands, reusable DAQ-port contract tests, unit tests, integration tests, architecture tests, documentation, event codes, and ADRs. Do not implement NI-DAQmx access, acquisition queues or state-machine orchestration, storage, replay, calibration execution, processing, plotting, or GUI work. Preserve all completed M00 and M01 behavior. Run focused tests and the full suite. Report changed files, port signatures, lifecycle semantics, generator algorithm and version, deterministic checksums, fault evidence, verification results, remaining assumptions, and any criterion not completed.
