# DAQ-M01: Domain Records and Validated Configuration

Status: Complete  
Depends on: DAQ-M00  
Blocks: DAQ-M02 through DAQ-M15

Acceptance evidence: [DAQ-M01 acceptance evidence](DAQ-M01-acceptance-evidence.md)

## 1. Goal

Define the stable, hardware-independent language used by acquisition, recording, replay, processing, plotting, export, CLI, and GUI code.

At the end of this milestone, a complete four-channel experiment can be represented as immutable domain records, validated without NI hardware or NI-DAQmx, serialized to versioned JSON, read back without information loss, and rejected predictably when invalid.

This milestone establishes configuration truth. It does not acquire data, create NI tasks, write TDMS files, or perform signal processing.

## 2. Engineering interpretation

In software terms, this milestone defines the type system and legal state space of the application.

In metrology terms, it establishes the measurement chain and calibration provenance required to interpret every voltage channel.

In protocol terms, it defines versioned messages that later adapters and user interfaces must obey.

In control-system terms, it fixes the parameters and coordinate definitions before any dynamic behavior is implemented.

## 3. Required outcome

The repository shall contain:

- Immutable domain records for hardware, channels, sensors, calibrations, acquisition, triggers, sessions, chunks, hardware profiles, experiment configurations, and processing-recipe metadata
- Pure validation rules that do not import NI, GUI, storage, plotting, or processing packages
- Versioned JSON schemas and codecs
- An initial cDAQ-9171 plus NI-9239 four-channel hardware profile
- A four-channel experiment configuration template for accelerometer X, Y, Z, and conditioned charge input
- Headless configuration validation through tests and a small CLI command
- Explicit rejection of unsupported schema versions

## 4. Scope

### 4.1 In scope

- Domain identifiers and enums
- Immutable configuration records
- Cross-record validation
- JSON serialization and deserialization
- JSON Schema validation
- Safe experiment and session naming
- Initial hardware and experiment profile files
- Calibration provenance and conditioning-chain metadata
- Requested acquisition timing and chunk configuration
- Coordinate-frame and sensor-axis identity
- Tests, fixtures, and documentation

### 4.2 Out of scope

- NI-DAQmx imports or NI task creation
- Hardware discovery
- Simulated signal generation
- Acquisition workers, threads, queues, or state machines
- TDMS recording
- Runtime sample arrays
- Calibration execution
- Filtering, FFT, PSD, or metrics
- Plotting
- GUI forms or widgets
- Automatic schema migration
- A qualified processing recipe

M01 may define a processing-recipe envelope and a draft baseline recipe document. It shall not claim that any processing stage is implemented or qualified.

## 5. Architectural constraints

1. Domain modules shall not import `nidaqmx`, PySide6, PyQtGraph, TDMS libraries, NumPy, SciPy, or storage adapters.
2. Domain records shall be immutable. Standard-library frozen dataclasses with slots are preferred.
3. JSON file I/O belongs outside the domain package. The domain may expose pure conversion helpers for primitive values, but it shall not open files.
4. JSON Schema validation does not replace domain validation. Both are required because schema-valid documents can still be semantically contradictory.
5. Physical channel references shall be plain domain values, not NI-DAQmx channel or task objects.
6. Channel order shall be explicit and preserved. It shall never depend on dictionary ordering or hardware discovery order.
7. Requested acquisition values belong in configuration. Actual sample rate, acquired sample count, runtime faults, and completion state belong to later runtime and session records.
8. No loader may silently repair, infer, or discard invalid fields.

## 6. Suggested package and file layout

```text
src/labdaq/
|-- domain/
|   |-- identifiers.py
|   |-- units.py
|   |-- hardware.py
|   |-- sensors.py
|   |-- calibration.py
|   |-- acquisition.py
|   |-- sessions.py
|   |-- recipes.py
|   `-- validation.py
|-- application/
|   `-- configuration_service.py
|-- adapters/
|   `-- configuration_json.py
`-- cli/
    `-- config_commands.py

schemas/
`-- v1/
    |-- hardware-profile.schema.json
    |-- experiment-config.schema.json
    `-- processing-recipe.schema.json

config/
|-- hardware-profiles/
|   `-- cdaq-9171-ni-9239-four-channel-v1.json
|-- experiments/
|   `-- four-channel-vibration-template-v1.json
`-- processing-recipes/
    `-- baseline-v1-draft.json

tests/
|-- unit/domain/
|-- unit/configuration/
|-- integration/configuration/
`-- fixtures/configuration/
```

Names may follow the package name established in M00. Layer ownership and behavior shall remain equivalent.

## 7. Domain model

The following records are the minimum required model. Field names may vary only where the replacement remains equally explicit.

### 7.1 Identifiers

Use dedicated immutable string value objects or strictly validated string fields for:

- `hardware_profile_id`
- `experiment_config_id`
- `device_id`
- `module_id`
- `channel_id`
- `sensor_id`
- `calibration_id`
- `conditioning_chain_id`
- `coordinate_frame_id`
- `recipe_id`

Identifiers shall:

- Be non-empty
- Use lowercase ASCII letters, digits, and hyphens
- Begin with a letter or digit
- Reject whitespace, path separators, control characters, and `..`
- Remain stable across serialization

Display names are separate fields and may contain normal human-readable text.

### 7.2 Schema version

Every top-level JSON document shall contain an explicit integer `schema_version`.

M01 supports only schema version `1`.

Behavior for missing, zero, negative, malformed, or future versions shall be explicit rejection through `UnsupportedSchemaVersionError`. No best-effort loading is permitted.

### 7.3 Device configuration

`DeviceConfig` represents the chassis-level requirement without creating a driver object.

Minimum fields:

- `device_id`
- `vendor`
- `model`
- `role`
- `required`
- Optional expected serial number
- Optional operator-facing alias

The initial profile shall describe one required National Instruments cDAQ-9171 chassis.

### 7.4 Module configuration

`ModuleConfig` represents a module installed in a logical chassis slot.

Minimum fields:

- `module_id`
- `device_id`
- `vendor`
- `model`
- `logical_slot`
- `required`
- `channel_count`
- Optional expected serial number

The initial profile shall describe one required NI-9239 module with four simultaneously acquired analog input channels.

### 7.5 Physical channel reference

`PhysicalChannelRef` identifies a channel without embedding a machine-specific NI device alias.

Minimum fields:

- `module_id`
- `channel_index`
- `terminal_configuration`

The initial profile uses channel indices 0 through 3 and differential voltage acquisition.

The NI adapter in M03 will translate this logical reference into the host-specific NI-DAQmx physical-channel string.

### 7.6 Sensor record

`SensorConfig` describes the physical sensor or transducer that produces one or more signals.

Minimum fields:

- `sensor_id`
- `manufacturer`
- `model`
- `serial_number`
- `sensor_type`
- `output_quantity`
- `output_count`
- `coordinate_frame_id`
- Optional asset tag
- Optional mounting description
- Optional certificate reference

The triaxial accelerometer shall be modeled as one physical sensor with three axis outputs, not as three unrelated sensors.

The initial sensor type shall support the TE Connectivity 830M1-0200 triaxial accelerometer. Its three channels shall identify the sensor X, Y, and Z outputs separately while retaining the common sensor identity and serial number.

### 7.7 Coordinate frame and axis identity

Each vector measurement channel shall state:

- The sensor output axis, such as `x`, `y`, or `z`
- The coordinate frame in which that axis is defined
- An optional experiment-axis label
- Optional mounting notes

M01 does not perform coordinate transformation. It records enough information to avoid silently treating sensor-body axes as specimen or laboratory axes.

A full orientation matrix may be added later without changing raw samples. If the physical orientation is already known, the configuration may include a validated 3 by 3 direction-cosine matrix. The matrix must be finite and approximately orthonormal within a declared tolerance.

### 7.8 Conditioning-chain record

`ConditioningChainConfig` represents external electronics between a transducer and the NI-9239 input.

Minimum fields:

- `conditioning_chain_id`
- Human-readable description
- Ordered component identifiers or descriptions
- Input quantity and unit
- Output quantity and unit
- Supply or excitation description when relevant
- Chain calibration reference
- Polarity

The fourth channel shall represent the complete externally conditioned charge-converter path. Its calibration belongs to the complete chain, not only to the upstream charge-producing transducer.

The repository shall not invent the chain sensitivity or engineering unit. A production experiment configuration is invalid until the validated chain sensitivity, polarity, output unit, and source reference are supplied. Test fixtures may use clearly labeled synthetic values.

### 7.9 Calibration record

`CalibrationConfig` shall preserve both executable coefficients and provenance.

Minimum fields:

- `calibration_id`
- `calibration_model`
- `input_quantity`
- `input_unit`
- `output_quantity`
- `output_unit`
- `sensitivity_value`
- `sensitivity_unit`
- `polarity`
- `source_kind`
- `source_reference`
- `effective_date`
- Optional expiration date
- Optional certificate identifier
- Optional environmental or frequency conditions
- Optional notes

Initial supported `source_kind` values:

- `certificate`
- `manufacturer_nominal`
- `external_validation`
- `test_fixture`

Initial supported calibration model:

- `linear_sensitivity`

M01 stores calibration data but does not apply it.

For an 830M1-0200 axis, the manufacturer nominal sensitivity may be represented as 6.3 mV/g only when it is labeled `manufacturer_nominal`. Per-axis certificate values shall supersede the nominal value when available. The attached sensor datasheet states that the 830M1 family is shipped with calibration data and specifies NIST-traceable amplitude calibration at 80 Hz.

Missing sensitivity, unit, polarity, source, source reference, or effective date shall invalidate the channel configuration.

### 7.10 Channel configuration

`ChannelConfig` binds one logical measurement to one physical input.

Minimum fields:

- `channel_id`
- `logical_name`
- `channel_role`
- `physical_channel`
- `channel_order`
- `raw_quantity`
- `raw_unit`
- `engineering_quantity`
- `engineering_unit`
- `sensor_id`
- Optional sensor output axis
- `calibration_id`
- Optional `conditioning_chain_id`
- Requested input minimum and maximum
- Enabled state

Required initial channel roles:

- `accelerometer_x`
- `accelerometer_y`
- `accelerometer_z`
- `conditioned_charge`

All four initial raw channels are voltage inputs. Channel order shall be exactly 0, 1, 2, and 3 with no gaps or duplicates.

### 7.11 Trigger configuration

`TriggerConfig` shall support an explicit start mode without implementing trigger behavior.

Initial trigger modes:

- `immediate`
- `digital_edge`

Minimum fields:

- `mode`
- Optional source identifier
- Optional edge, `rising` or `falling`
- Optional pretrigger sample count

Validation:

- `immediate` forbids a source, edge, and nonzero pretrigger count
- `digital_edge` requires source and edge
- Pretrigger samples shall be non-negative
- The initial four-channel hardware profile may permit only `immediate`

Defining a record does not mean the adapter implements the trigger. Unsupported trigger modes shall be rejected by profile validation before acquisition.

### 7.12 Acquisition configuration

`AcquisitionConfig` describes the requested sample stream.

Minimum fields:

- `sample_rate_hz`
- `sampling_mode`
- Optional requested duration
- `channel_ids` in authoritative acquisition order
- `trigger`
- `chunk_config`

Initial sampling modes:

- `finite`
- `continuous`

Validation:

- Sample rate shall be finite and positive
- Sample rate shall be accepted by the selected hardware profile
- The initial application-supported profile shall accept 50,000 samples per second per channel
- `finite` requires a positive duration
- `continuous` forbids a duration
- Duration multiplied by sample rate shall resolve to an integral requested sample count, or the configuration shall be rejected with an actionable error
- Every referenced channel shall exist and be enabled
- The channel list shall contain exactly four unique channels for the initial profile

M01 validates the application-supported rate set, not every theoretical rate the NI-9239 may support. Additional rates shall be added only after authoritative capability confirmation.

### 7.13 Data-chunk configuration

`DataChunkConfig` describes acquisition read granularity, not an acquired data array.

Minimum fields:

- `samples_per_channel`
- `read_timeout_seconds`

Validation:

- Samples per channel shall be a positive integer
- Read timeout shall be finite and positive
- The configuration shall permit a final partial chunk for finite acquisition
- No queue, thread, display-decimation, or storage policy belongs in this record until M04

### 7.14 Session configuration

`SessionConfig` describes user intent before a session exists.

Minimum fields:

- `experiment_name`
- `experiment_slug`
- `description`
- `operator`
- Optional sample or specimen ID
- Optional notes
- Parent output directory

The session UUID, actual start time, actual duration, completion state, and runtime counts are not user configuration. They are created by later session orchestration.

### 7.15 Hardware profile

`HardwareProfile` aggregates:

- Schema version
- Profile ID and profile revision
- Required device and module records
- Supported physical channels
- Required terminal configuration
- Application-supported sample rates
- Supported trigger modes
- Channel-count constraints
- Optional hardware notes

The initial profile shall describe:

- One cDAQ-9171 chassis
- One NI-9239 module
- Four simultaneous differential voltage channels
- Logical channel indices 0 through 3
- An application-supported acquisition rate of 50,000 samples per second per channel
- Immediate start triggering

The profile shall not contain a `nidaqmx` device object, task object, channel object, or driver exception.

### 7.16 Experiment configuration

`ExperimentConfig` aggregates:

- Schema version
- Configuration ID and revision
- Hardware profile ID and compatible revision
- Session configuration
- Sensors
- Conditioning chains
- Calibrations
- Ordered channels
- Acquisition configuration
- Optional draft processing-recipe ID

All references shall resolve exactly once. Orphaned records, duplicate identifiers, missing references, and cyclic references shall be rejected.

### 7.17 Processing-recipe envelope

M01 shall define only the versioned recipe envelope.

Minimum fields:

- `schema_version`
- `recipe_id`
- `recipe_version`
- `name`
- `status`
- `execution_class`
- `stages`

The M01 `baseline-v1-draft.json` shall be marked `draft` and shall contain no active processing stages. M01 shall not expose candidate filters merely because they may be implemented later. M06 owns the qualified baseline recipe and stage-specific validation.

## 8. Unit policy

M01 shall use a controlled unit vocabulary rather than unrestricted free text.

The initial vocabulary shall include at least:

- `V`
- `mV`
- `g`
- `m/s^2`
- `pC`
- `nC`

Sensitivity units shall be validated as a supported input-unit to output-unit ratio, such as `mV/g`.

Unit labels do not by themselves provide dimensional analysis. M01 shall still verify that the calibration input unit matches the channel raw unit and that the calibration output unit matches the channel engineering unit.

## 9. Filename and path safety

`experiment_slug` and any user-controlled filename component shall reject:

- `/` and `\\`
- `..`
- Control characters
- Leading or trailing whitespace
- Trailing periods or spaces
- Empty names
- Windows reserved base names such as `CON`, `PRN`, `AUX`, `NUL`, `COM1` through `COM9`, and `LPT1` through `LPT9`
- Names exceeding the documented application limit

The safe slug format shall be lowercase ASCII letters, digits, and single hyphens. Repeated separators shall be normalized only through an explicit slug-construction function. Loaders shall not silently alter a supplied slug.

M01 validates path syntax and naming. M05 will validate directory existence, permissions, collisions, disk space, and actual session-folder creation.

## 10. JSON and schema requirements

1. Schemas shall use JSON Schema Draft 2020-12 or a clearly documented alternative.
2. Top-level schemas shall reject unknown properties through `additionalProperties: false` unless a specific extension object is intentionally defined.
3. Required fields shall be explicit.
4. Enums shall serialize as lowercase snake-case strings.
5. Dates shall use ISO 8601 calendar-date form.
6. Paths shall serialize as strings and be reconstructed as platform-aware path values by the codec.
7. Serialization shall preserve authoritative channel order.
8. Round-trip equality shall compare reconstructed domain records, not only raw dictionaries.
9. Unknown schema versions shall be rejected before constructing partial domain records.
10. Validation failures shall identify the document, field path, invalid value where safe, rule violated, and corrective action.

## 11. Validation rules

At minimum, reject:

- Duplicate physical channels
- Duplicate channel IDs, sensor IDs, calibration IDs, device IDs, or module IDs
- Missing channel references
- Missing calibration fields
- Calibration unit mismatch
- Missing conditioning-chain calibration for the fourth channel
- Unsupported sample rate
- Non-finite numeric values
- Zero or negative duration
- Zero or negative chunk size
- Unsafe experiment slug or filename
- Missing or unsupported schema version
- Contradictory duration and sampling-mode settings
- Contradictory trigger fields
- Channel-order gaps or duplicates
- Sensor-axis duplication for a triaxial sensor
- Unknown hardware profile
- Hardware profile revision mismatch
- Channel count other than four for the initial profile
- Disabled channels referenced by acquisition
- Input minimum greater than or equal to input maximum

Validation shall collect independent errors when practical, but shall not continue after a schema-version failure or structurally unsafe document.

## 12. Headless interface

Add a minimal CLI validation surface:

```text
labdaq config validate <experiment-config.json>
labdaq profile validate <hardware-profile.json>
labdaq recipe validate <processing-recipe.json>
```

Successful validation shall print the document type, ID, schema version, revision, hardware profile, channel count, sample rate, and a concise success result.

Failure shall return a nonzero exit code and structured actionable messages. The command shall work on a computer without NI software installed.

## 13. Required tests

### 13.1 Unit tests

- Every value object and enum accepts valid values and rejects malformed values
- All domain records are immutable
- Record equality is value-based
- Channel ordering is explicit and stable
- Filename and slug validation covers Windows reserved names and path traversal
- Unit compatibility validation works
- Trigger contradiction rules work
- Finite and continuous duration rules work
- Calibration conditional requirements work
- Coordinate-frame and axis rules work

### 13.2 Serialization tests

- Valid hardware profile round-trips JSON to domain to JSON without information loss
- Valid experiment configuration round-trips without information loss
- Draft processing-recipe envelope round-trips
- Dates, paths, enums, optional fields, and ordered channel lists survive round-trip
- Canonical fixture documents remain stable

### 13.3 Invalid fixture tests

Provide one fixture for each major rejection category, including:

- Duplicate physical channel
- Duplicate logical channel ID
- Unsupported rate
- Missing calibration sensitivity
- Missing calibration source
- Calibration unit mismatch
- Unsafe filename
- Missing schema version
- Future schema version
- Finite acquisition without duration
- Continuous acquisition with duration
- Immediate trigger with a source
- Digital trigger without an edge
- Missing sensor reference
- Missing conditioning chain
- Non-contiguous channel order

### 13.4 Integration tests

- Load the initial hardware profile and valid synthetic experiment fixture through the real JSON adapter and application service
- Validate the result through the CLI
- Confirm no NI or GUI package is imported
- Confirm the same file is accepted on Windows path syntax and in the test environment
- Confirm unknown schema versions fail before any partial configuration is returned

### 13.5 Architecture tests

- Importing `labdaq.domain` shall not import NI, GUI, TDMS, plotting, or processing dependencies
- `labdaq.application.configuration_service` may depend on domain and configuration ports only
- The JSON adapter may depend on domain records and schema-validation libraries
- No domain record may contain a field annotated with an NI-DAQmx type

## 14. Acceptance scenarios

### Scenario A: Valid four-channel configuration

Given the initial cDAQ-9171 plus NI-9239 hardware profile and a complete experiment configuration containing three calibrated 830M1-0200 axis channels plus one externally validated conditioned-charge channel, validation succeeds and round-trip equality holds.

### Scenario B: Duplicate physical channel

Given two logical channels assigned to the same module channel index, validation fails before acquisition with both channel IDs named in the error.

### Scenario C: Missing charge-chain calibration

Given a fourth conditioned-charge channel without a validated chain sensitivity or source reference, validation fails. The software shall not substitute an arbitrary identity conversion.

### Scenario D: Unsupported acquisition rate

Given an experiment requesting a rate not listed by the selected hardware profile, validation fails with the requested rate and allowed application-supported rates.

### Scenario E: Unsafe session name

Given an experiment slug containing path traversal, a separator, or a Windows reserved name, validation fails and no path is created.

### Scenario F: Unsupported schema version

Given a document with a future schema version, loading fails with `UnsupportedSchemaVersionError`. No defaults are applied and no partial domain record is returned.

### Scenario G: Contradictory run termination

Given continuous acquisition with a duration, or finite acquisition without a duration, validation fails with a correction message.

### Scenario H: Contradictory trigger

Given immediate triggering with a digital source or edge, validation fails. Given a digital edge trigger without a source or edge, validation fails.

## 15. Completion criteria

DAQ-M01 is complete only when:

- All required records exist and are immutable
- The initial hardware profile loads and validates
- A complete synthetic four-channel experiment configuration loads and validates
- Production configuration cannot validate without real charge-chain calibration data
- Valid documents round-trip without information loss
- All required invalid configurations are rejected
- Unsupported schema versions are rejected explicitly
- The CLI validator works without NI software
- Architecture tests confirm domain independence
- The full repository test suite passes
- Architecture, user, and developer documentation are updated

## 16. Required implementation report

The implementation response shall include:

- Files added and changed
- Final domain-record inventory
- Schema files and supported schema version
- Initial profile and fixture locations
- Validation rules implemented
- Exact test commands and results
- Confirmation that NI and GUI packages are not imported by domain or configuration validation
- Any unresolved hardware values or calibration data that remain intentionally absent
- Any deviations from this milestone and the reason for each deviation

## 17. Critical assumptions and risks

### Assumptions commonly made but not guaranteed by first principles

- JSON Schema validity is not equivalent to scientific validity. Cross-record semantic validation remains necessary.
- Sensor X, Y, and Z labels are not automatically laboratory or specimen coordinates.
- A DAQ physical-channel alias is not a durable asset identifier.
- A nominal sensitivity is not interchangeable with a certificate value.
- A charge converter's calibration cannot be separated from its complete signal-conditioning chain without evidence.
- Requested sample rate is not necessarily the exact rate achieved by hardware. M03 and later session metadata must record the actual rate.

### Main risks

1. **Overfitting domain records to NI-DAQmx**  
   Mitigation: retain generic device, module, and physical-channel values and isolate NI address translation in M03.

2. **Inventing missing charge-chain calibration**  
   Mitigation: reject production configurations until the actual sensitivity, polarity, unit, and source are supplied.

3. **Treating axis labels as orientation**  
   Mitigation: require coordinate-frame identity and preserve mounting information.

4. **Premature processing schema design**  
   Mitigation: define only a draft recipe envelope in M01. M06 owns qualified stages.

5. **Silent configuration repair**  
   Mitigation: reject invalid documents with field-specific errors and require explicit user correction.

## 18. Design hypotheses to verify

Ranked by plausibility and testability:

1. **Separating hardware profiles from experiment configurations will reduce setup errors.**  
   Plausibility: high. Testability: high. Reuse one profile across multiple experiment fixtures and measure duplicated configuration fields and validation failures.

2. **Modeling the charge converter as a complete conditioning chain will improve calibration traceability.**  
   Plausibility: high. Testability: high. Verify that every derived charge-channel value can be traced to the exact chain configuration.

3. **Recording coordinate frames in M01 will prevent later axis-sign and orientation ambiguity.**  
   Plausibility: high. Testability: medium. Reprocess a known orientation change and confirm that configuration alone explains the mapping.

4. **Using explicit schema rejection before migration is safer for the first laboratory release than permissive loading.**  
   Plausibility: high. Testability: high. Inject future and malformed versions and verify deterministic rejection.

5. **Restricting the initial application-supported rate set to 50 kS/s/channel will shorten qualification without materially limiting the first experiment.**  
   Plausibility: medium to high. Testability: high. Qualify the full acquisition chain at that rate before exposing additional device-supported rates.
