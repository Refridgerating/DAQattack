# DAQ-M08: Hardware Qualification and Diagnostics

> This is a historical specification. Qualification plans and reports are optional diagnostics; their completion, fingerprint, age, and external confirmations must not gate ordinary raw monitoring or recording under [DAQ-M12](DAQ-M12-raw-first-monitoring.md). Current implementation may still enforce historical gates until M12 is implemented. Existing reports and evidence remain valid for their stated tests.

Status: software implementation complete; physical qualification pending  
Depends on: DAQ-M00 through DAQ-M07  
Primary hardware: NI cDAQ-9171, NI-9239, three TE 830M1-0200 accelerometer channels, one externally conditioned charge-converter channel  
Primary acquisition target: four simultaneous channels at 50 kS/s/channel

## 1. Goal

Determine, before a real experiment begins, whether the configured acquisition chain is present, compatible, electrically plausible, synchronized, stable, and able to sustain the required acquisition and recording load without losing samples.

This milestone treats qualification as three equivalent engineering problems:

1. A metrology problem, where each signal must be traceable to a known sensor, conditioner, channel, calibration, and expected operating envelope.
2. A system-identification problem, where known inputs or controlled idle conditions are used to distinguish a responsive channel from a miswired, saturated, frozen, noisy, or coupled channel.
3. A real-time pipeline problem, where the acquisition, queueing, storage, and event paths must sustain 200,000 samples per second without discontinuity.

DAQ-M08 does not certify the sensor or conditioner independently. It determines whether the assembled chain is suitable for the configured experiment under the tests actually performed.

## 2. Scope boundary

DAQ-M08 shall add application-level qualification orchestration and diagnostic evaluation. It shall reuse the existing DAQ, acquisition, storage, replay, processing, plotting, and export interfaces from DAQ-M02 through DAQ-M07.

The qualification service shall not:

- Place NI-DAQmx calls outside the NI adapter.
- Implement a second acquisition engine or a second recording path.
- Let the GUI own qualification logic.
- Treat an NI device self-test as proof that sensors, wiring, conditioning, calibration, storage, or signal quality are correct.
- Infer that an external stimulus occurred unless the operator or an automated fixture explicitly declares it.
- mark a skipped external test as passed.
- overwrite calibration data or modify raw qualification samples.
- implement the DAQ-M09 qualification GUI beyond interfaces needed by the application layer.

## 3. Hardware constraints that must be represented

The initial profile shall explicitly represent the following facts and assumptions.

### 3.1 NI acquisition hardware

- The NI-9239 is a four-channel, simultaneous, differential, 24-bit voltage-input module.
- The supported input range for the initial profile is +/-10 V.
- The target rate is 50 kS/s per channel.
- Channel data shall remain ordered and shaped as `[channels, samples]` throughout qualification.
- Device, chassis, module, channel, serial-number, driver-version, and capability information shall be reported when available through NI-DAQmx.

### 3.2 TE 830M1-0200 accelerometers

The initial sensor profile shall represent each installed 830M1-0200 as a powered, three-wire voltage-output accelerometer with:

- +/-200 g measurement range.
- Nominal sensitivity of 6.3 mV/g, used only when channel-specific calibration data is unavailable and clearly labeled as nominal.
- Amplified output span of approximately +/-1.25 V around its output bias.
- Output bias expected near one-half of the applied excitation voltage.
- Nominal frequency response beginning above DC, so static gravity orientation shall not be used as the sole axis-response test.
- Sensor-specific serial number, axis identity, sensitivity, polarity, excitation description, calibration source, and calibration date.

The powered-idle test shall allow a configurable warm-up and settling interval before measurements are evaluated.

### 3.3 Conditioned charge-converter channel

The charge channel shall be modeled as the output of a documented external conditioning chain, not merely as a generic fourth voltage input.

For the initial PCB 422E03-based chain, the profile shall record at least:

- Charge-converter model and serial number when available.
- Nominal charge sensitivity and output polarity.
- Excitation source and current.
- The conditioning stage that removes, offsets, AC-couples, or scales the converter output before the NI-9239 input.
- Expected NI-terminal bias, dynamic range, and engineering-unit conversion.

A profile that claims a direct PCB 422E03 output connection to an NI-9239 voltage input without documented bias removal or other compatible conditioning shall be rejected. The converter's normal powered bias is above the NI-9239 +/-10 V input range, even though its dynamic component may be smaller.

## 4. Qualification domain model

Add immutable, serializable records equivalent to the following concepts. Exact names may follow existing repository conventions.

### 4.1 QualificationPlan

A versioned plan defining:

- Plan ID and schema version.
- Hardware-profile ID and revision.
- Experiment-configuration fingerprint.
- Required and optional tests.
- Test parameters, durations, settling times, stimulus descriptions, and thresholds.
- Whether each test is blocking or advisory.
- Required operator confirmations.
- Maximum accepted report age when the profile elects to use a time limit.
- Evidence-retention policy.

Provide at least these initial plans:

- `preflight-v1`: discovery, identity, capability, configuration, self-test, and short powered-idle checks.
- `full-v1`: all preflight tests, response and alignment tests, and sustained end-to-end throughput.
- `throughput-v1`: production acquisition and recording path at four channels by 50 kS/s for a configurable duration, default 600 seconds.

### 4.2 QualificationTestDefinition

Each test definition shall include:

- Stable test ID and version.
- Display name and technical purpose.
- Required inputs and prerequisites.
- Blocking or advisory classification.
- Automated, operator-assisted, or external-fixture execution mode.
- Declared metrics and limits.
- Explicit reasons that can produce `pass`, `fail`, `warning`, or `not-tested`.

### 4.3 QualificationMetric

Each metric shall record:

- Metric name.
- Numeric, Boolean, categorical, or textual value.
- Unit.
- Limit or expected range when applicable.
- Evaluation result.
- Sample-index interval and channel identity.
- Calculation method and software version.

### 4.4 QualificationTestResult

Each test result shall record:

- Test ID and version.
- Start and stop timestamps.
- Status: `pass`, `fail`, `warning`, or `not-tested`.
- Blocking classification.
- Human-readable summary.
- Structured reason codes.
- Metrics.
- Related events and evidence artifact paths.
- Operator confirmation when required.
- Any assumptions that were not externally verified.

### 4.5 QualificationReport

The machine-readable report shall include:

- Report UUID, schema version, creation time, operator, application version, operating system, and NI-DAQmx version.
- Exact qualification-plan ID and hash.
- Hardware-profile ID, revision, and hash.
- Experiment-configuration hash.
- Chassis, module, device alias, serial number, and physical channel identities when available.
- Sensor, conditioner, calibration, and wiring metadata.
- Ordered test results.
- Overall status.
- Blocking failures, warnings, skipped tests, and recommended actions.
- Acquired, persisted, missing, duplicated, dropped-display, and invalid sample counts.
- Links to the qualification session, raw TDMS data, event log, plots, and exported summaries.
- A deterministic qualification fingerprint used to decide whether the report still matches a later experiment configuration.

## 5. Overall status and start-eligibility rules

The report evaluator shall apply these rules deterministically:

1. `fail` if any required blocking test fails.
2. `not-tested` if one or more required blocking tests were not performed and no blocking test failed.
3. `warning` if all required blocking tests pass but at least one advisory test warns or a declared nonblocking assumption remains unverified.
4. `pass` only when every required blocking test passes and no test produces a warning that the plan classifies as start-blocking.

A report is stale or nonmatching when any qualification-fingerprint input changes, including hardware identity, channel order, sample rate, input range, calibration identity, conditioning identity, profile revision, or qualification-plan revision.

Time alone shall not invalidate a report unless the active profile defines a maximum report age. The application shall expose a start-eligibility decision with structured reasons rather than a single Boolean.

## 6. Required qualification tests

### 6.1 Discovery and identity

Verify that the configured chassis, module, and four physical channels are present and uniquely mapped.

Required checks:

- Expected chassis and module models are discovered.
- Serial numbers match the profile when the profile pins them.
- Exactly the configured physical channels are available.
- No physical channel appears more than once.
- Channel ordering matches the experiment configuration.
- NI-DAQmx and application versions are recorded.

Blocking failures include missing hardware, wrong module model, unavailable channel, duplicate physical channel, and pinned serial mismatch.

### 6.2 Capability and configuration comparison

Compare requested acquisition settings with discovered capabilities before creating a run.

Required checks:

- Four simultaneous differential voltage channels are supported.
- Requested rate is supported for all channels.
- Requested input range and terminal configuration are supported.
- Chunk size and buffer settings are valid.
- Sensor and conditioning metadata are complete enough to interpret every channel.
- Estimated storage requirement fits available disk space with the existing safety margin.

The comparison shall return field-level mismatches. It shall not rely on a later NI exception as normal validation.

### 6.3 NI self-test and task lifecycle

Run the NI self-test when supported by the installed driver and device.

Then create, reserve, start, read, stop, clear, and recreate a short task using the production NI adapter.

Required evidence:

- Self-test supported status and outcome.
- NI error codes translated to application errors.
- Successful cleanup after pass, timeout, cancellation, and injected exception paths.

A self-test result shall be `not-tested`, not `pass`, when the installed hardware or driver does not expose it.

### 6.4 Powered-idle acquisition

Acquire all four channels after configured warm-up and settling periods using the normal acquisition engine and recorder.

For each channel calculate:

- Mean and median DC level.
- Standard deviation and RMS after the baseline-v1 conditioning stages appropriate to the metric.
- Robust noise estimate based on median absolute deviation.
- Minimum, maximum, peak-to-peak, and configured-range utilization.
- Clipping and near-clipping fractions.
- Non-finite sample count.
- Repeated-value or frozen-signal indicators.
- Dropout and discontinuity indicators.
- PSD or spectral-noise summary in configured bands.
- Mains-frequency and selected harmonic line amplitudes when requested by the profile.

Default voltage-range limits may warn above 80 percent utilization and fail above 98 percent utilization. Hardware overrange or driver-detected overflow is always a blocking failure. Sensor-specific limits shall take precedence over generic defaults.

### 6.5 Accelerometer bias plausibility

For each TE 830M1 channel, compare the measured powered-idle output with the profile's expected bias and headroom.

The test shall:

- Use the actual excitation voltage when measured or configured.
- Evaluate bias against profile-defined tolerance around the expected midpoint.
- Check that the expected dynamic span remains inside both the sensor supply rails and NI input range.
- Warn on unexpected axis-to-axis bias differences.
- Fail on saturation, open-circuit-like output, short-circuit-like output, impossible excitation, or insufficient headroom.

The test shall not claim that a correct bias proves correct sensitivity, axis orientation, mounting, or dynamic response.

### 6.6 Accelerometer axis-response test

Provide an operator-assisted or fixture-driven response test.

The initial operator-assisted procedure shall:

1. Record a baseline interval.
2. Ask the operator to excite one declared physical axis using a repeatable tap or other controlled stimulus with significant energy above the sensor's low-frequency cutoff.
3. Record the stimulus interval.
4. Compare response energy, peak magnitude, and dominant-band energy across all three axes.
5. Repeat for each declared axis when the plan requires full axis mapping.

The result shall distinguish:

- Responsive expected axis.
- No response.
- Wrong channel or swapped axis.
- Similar response on all axes, which may indicate structural coupling or mapping ambiguity.
- Clipping during stimulus.

A manual tap can establish continuity and gross axis mapping. It cannot establish calibrated sensitivity. A calibrated shaker or traceable reference input is required for sensitivity verification.

### 6.7 Conditioned charge-channel plausibility

Evaluate the signal at the NI-9239 terminal, after the external conditioning chain.

Required checks:

- Measured bias matches the profile's conditioned-output expectation.
- Signal remains inside the NI range with declared headroom.
- Noise is within the profile limit.
- Output is not frozen, saturated, open, or short-like.
- Polarity and scale metadata are present.
- Conditioner excitation and external configuration are operator-confirmed or read from an available interface.

The report shall explicitly state which parts of the chain were measured by software and which were accepted from operator-entered or externally validated information.

### 6.8 Cross-channel alignment

Use a known common input, splitter, shaker, or other controlled fixture to excite two or more channels.

Calculate:

- Cross-correlation lag in samples.
- Phase difference at the declared stimulus frequency.
- Coherence or normalized correlation when already available through the processing interfaces.
- Start and stop sample-index agreement.

For channels on the same NI-9239, the default expectation is zero relative sample offset. Any allowed tolerance shall be profile-defined and justified. Processing stages with unequal group delay shall not be used to judge hardware alignment.

If no known common input is applied, the result is `not-tested`.

### 6.9 Optional crosstalk test

Stimulate one channel while the remaining channels are terminated or left in a documented idle state.

Report:

- Stimulated-channel amplitude or band power.
- Unstimulated-channel amplitude or band power.
- Coupling ratio in dB.
- Whether observed coupling exceeds the profile limit.

The report shall not automatically attribute failed crosstalk to the NI module. Mechanical coupling, sensor mounting, cable routing, grounding, and external conditioners remain alternative causes.

### 6.10 Sustained end-to-end throughput

Run the production acquisition engine, recording queue, TDMS writer, manifest writer, and event writer at four channels by 50 kS/s.

The default duration is 600 seconds. The test shall record:

- Requested and actual sample rate.
- Expected, acquired, enqueued, persisted, and replay-validated sample counts.
- First and last sample indices for every chunk and channel.
- Missing, duplicated, reordered, and non-finite samples.
- Read durations, writer durations, queue depth, queue high-water mark, and buffer health.
- Display-frame drops separately from raw acquisition or storage loss.
- Disk free space before and after the run.
- Stop latency and task cleanup result.

Acceptance for the default physical run requires:

- Zero missing, duplicated, or reordered raw sample indices.
- Equal persisted sample count for all four channels.
- Parsed TDMS sample counts equal manifest counts.
- No acquisition-queue or recorder overflow.
- No hardware disconnect, driver overflow, write failure, or unhandled exception.
- Display drops, when they occur, are reported and do not alter the raw record.

The throughput test shall fail if any channel is shorter than another. The historical pattern of separately written channel files with unequal lengths shall be represented as a regression fixture and must be detected as invalid.

## 7. Qualification thresholds and profile design

Thresholds shall be data, not hard-coded control flow. Extend the versioned hardware or qualification-plan schema with limits such as:

- Expected bias and tolerance by channel.
- Allowed RMS, robust-noise, and spectral-band noise.
- Warning and failure headroom fractions.
- Maximum clipping fraction.
- Frozen-value duration.
- Maximum non-finite samples.
- Maximum cross-channel lag.
- Minimum expected response ratio for the excited axis.
- Maximum crosstalk ratio.
- Throughput duration and maximum queue utilization.

Each limit shall include units, comparison operator, source, and rationale. Sources may be manufacturer specifications, sensor calibration certificates, measured laboratory baselines, or engineering policy. Manufacturer specifications and empirical laboratory thresholds shall not be conflated.

When no defensible limit exists, report the metric and return `warning` or `not-tested` according to the plan. Do not invent a passing threshold merely to complete the report.

## 8. Application services and execution flow

Implement application services equivalent to:

- `BuildQualificationPlan`
- `EvaluateHardwareCompatibility`
- `RunQualification`
- `EvaluateQualificationReport`
- `LoadQualificationReport`
- `CheckQualificationEligibility`
- `AttachQualificationToSession`

Expected execution flow:

```text
load profile and experiment configuration
    -> discover hardware
    -> build exact qualification fingerprint
    -> evaluate capability and metadata completeness
    -> create qualification session
    -> run selected tests through existing ports
    -> persist raw evidence and structured events
    -> evaluate metrics and limits
    -> finalize qualification report
    -> return start-eligibility decision
```

Cancellation shall finalize the report with completed tests preserved and unexecuted tests marked `not-tested`.

## 9. Ports and adapters

Add only the smallest interfaces needed beyond completed milestones.

Potential ports include:

- Hardware self-test port, implemented by NI and simulated adapters.
- Operator-prompt or stimulus-confirmation port with headless and future-GUI implementations.
- Qualification-report repository port.
- Clock and environment-information ports if not already present.

The simulator shall implement the same qualification-facing contracts. No qualification application service may import `nidaqmx`, Qt, TDMS libraries, or plotting widgets.

## 10. Storage and session integration

A qualification run shall use the DAQ-M05 session mechanism with a session type such as `hardware-qualification`.

The qualification session shall contain:

```text
YYYY-MM-DD_HHMMSS_hardware-qualification/
|-- manifest.json
|-- raw.h5
|-- events.jsonl
|-- qualification-plan.json
|-- qualification-report.json
|-- processed/
|-- plots/
`-- exports/
```

The report shall be finalizable after interruption or failure. Large throughput evidence shall follow an explicit retention policy, but the report must retain hashes, counts, time spans, and artifact disposition.

A later experiment session shall store either:

- A copy of the exact report used for start eligibility, or
- A stable relative reference plus report hash when both sessions share a managed parent store.

The experiment manifest shall record the qualification report UUID, overall status, fingerprint, age, and any operator override. DAQ-M08 shall define the data needed for an override but shall not add the DAQ-M09 GUI.

## 11. Structured events and reason codes

Add documented codes for at least:

- `qualification_started`
- `qualification_completed`
- `qualification_cancelled`
- `qualification_failed`
- `hardware_identity_mismatch`
- `hardware_capability_mismatch`
- `hardware_self_test_failed`
- `hardware_self_test_not_supported`
- `channel_bias_out_of_range`
- `channel_noise_above_limit`
- `channel_near_clipping`
- `channel_clipping_detected`
- `channel_frozen`
- `channel_dropout_detected`
- `channel_nonfinite_sample`
- `sensor_response_missing`
- `sensor_axis_mapping_ambiguous`
- `charge_conditioning_incompatible`
- `cross_channel_misalignment`
- `crosstalk_above_limit`
- `throughput_gap_detected`
- `throughput_channel_length_mismatch`
- `throughput_storage_backpressure`
- `qualification_report_stale`
- `qualification_required_test_not_run`

Every failure shall identify the affected test, channel, sample interval when applicable, observed value, expected limit, and recommended next action.

## 12. CLI surface

Add headless commands consistent with the existing CLI conventions. The following behavior is required even if exact command spelling differs:

```text
labdaq qualify hardware --profile PROFILE --config CONFIG --plan preflight-v1
labdaq qualify hardware --profile PROFILE --config CONFIG --plan full-v1
labdaq qualify hardware --profile PROFILE --config CONFIG --plan throughput-v1 --duration 600
labdaq qualify hardware --simulate SCENARIO --plan full-v1
labdaq qualification show REPORT
labdaq qualification check REPORT --profile PROFILE --config CONFIG
```

CLI output shall provide a concise table of tests, status, key metrics, blocking reasons, report path, and start eligibility. JSON output shall be available for automated qualification.

## 13. Simulator scenarios and automated tests

The simulator shall support deterministic qualification scenarios for:

- Fully healthy four-channel chain.
- Missing chassis or module.
- Wrong module model or pinned serial.
- Missing and duplicate channel mappings.
- Unsupported sample rate or range.
- Self-test failure and self-test not supported.
- Accelerometer bias high, low, and rail-saturated.
- Charge-channel incompatible bias.
- Excess broadband noise.
- Strong 50 Hz or 60 Hz interference.
- Clipping and near-clipping.
- Frozen constant output.
- Repeated stale chunks.
- NaN or infinite samples.
- Dropped, duplicated, and reordered chunks.
- One-channel sample offset.
- Swapped accelerometer axes.
- Missing stimulus response.
- Excess simulated crosstalk.
- Slow reader, slow writer, queue growth, disk-full, and disconnect during throughput.
- Unequal final channel lengths based on the legacy text-file failure pattern.

Unit tests shall cover metric calculations, threshold boundaries, status aggregation, fingerprint determinism, stale-report detection, and report serialization.

Integration tests shall cover:

- Healthy simulated preflight.
- Healthy simulated full qualification.
- Every simulator fault mapping to the correct reason code.
- Qualification through the real acquisition, recording, parsing, replay, processing, plotting, and export services.
- Cancellation and injected failure producing a recoverable report.
- Exact sample equality after recording and replay.
- CLI JSON output and exit codes.

Architecture tests shall verify that GUI and NI dependencies do not enter domain or application qualification modules.

## 14. Physical verification protocol

Physical acceptance cannot be completed by Codex. The implementation shall provide a documented operator protocol and leave unexecuted physical criteria open.

Minimum physical sequence:

1. Record chassis, module, sensor, conditioner, and calibration identifiers.
2. Inspect wiring and confirm the external charge-channel conditioning path.
3. Run discovery, capability comparison, and NI self-test.
4. Allow configured sensor and conditioner warm-up.
5. Run powered-idle acquisition and review bias, noise, headroom, clipping, and spectral summaries.
6. Perform an operator-assisted response check for each accelerometer axis.
7. Perform charge-channel response and polarity verification using an appropriate safe input or fixture.
8. Apply a common known input for alignment testing when a fixture is available.
9. Perform crosstalk testing when required by the plan.
10. Run the 10-minute four-channel, 50 kS/s end-to-end throughput test on the target laboratory computer and selected storage location.
11. Reopen and replay the qualification session.
12. Save the final report and associate it with the next experiment session.

The protocol shall include wiring boundaries, safe input limits, required external equipment, and explicit stop conditions. Software shall never instruct the operator to apply a signal that may exceed sensor, conditioner, or NI input limits.

## 15. Acceptance criteria

DAQ-M08 is complete when all software-verifiable criteria below pass and the physical-only criteria are clearly reported as pending or completed.

### 15.1 Software acceptance

- A deterministic simulator qualification report is produced for the healthy chain.
- Every declared simulator fault is detected and mapped to the intended structured reason code.
- Required tests distinguish `fail`, `warning`, and `not-tested` correctly.
- External wiring and stimulus assumptions are never silently converted into passes.
- Qualification uses the production acquisition and recording paths.
- Raw qualification samples remain immutable.
- Reports round-trip through JSON without information loss.
- Report fingerprints are deterministic and invalidate on relevant configuration changes.
- Unsafe, saturated, overrange, incompatible, or discontinuous inputs produce a blocking start decision.
- A cancelled or failed qualification remains reopenable with completed evidence intact.
- The historical unequal-channel-length pattern is rejected.
- Existing tests continue to pass.

### 15.2 Physical acceptance

- The cDAQ-9171 and NI-9239 are discovered and match the active profile.
- The NI task can be repeatedly created, started, stopped, cleared, and recreated.
- All four channels produce electrically plausible powered-idle signals.
- Each accelerometer channel responds to a declared above-cutoff stimulus and the mapping is documented.
- The externally conditioned charge channel remains inside the NI input range and its bias, polarity, and response are plausible.
- A known common input demonstrates acceptable relative channel alignment.
- Optional crosstalk results are recorded or explicitly marked `not-tested`.
- A 600-second acquisition at four channels by 50 kS/s records zero missing, duplicated, or reordered sample indices.
- All four recorded channels have exactly equal lengths.
- Reopened TDMS data, manifest counts, and report counts agree.
- The physical qualification report can be attached to an experiment session.

## 16. Documentation deliverables

Update or add:

- DAQ-M08 milestone document.
- Qualification-plan schema and examples.
- Qualification-report schema and example reports.
- Hardware profile limits for the initial four-channel chain.
- Operator hardware-qualification procedure.
- Charge-channel conditioning boundary and incompatibility warning.
- Diagnostic reason-code reference.
- CLI usage.
- Architecture decision records for report fingerprinting, threshold provenance, external stimulus handling, and qualification-session storage.
- Roadmap status and acceptance evidence after implementation.

## 17. Explicitly out of scope

- PySide6 qualification screens and report widgets, deferred to DAQ-M09.
- General user-authored qualification-plan editor.
- Automatic calibration-certificate ingestion.
- Traceable accelerometer sensitivity calibration without a calibrated shaker or reference standard.
- Automated diagnosis that uniquely assigns every noise or crosstalk failure to one physical component.
- Additional C-Series modules or non-NI hardware.
- Automated vacuum, PSU, relay, or valve sequencing.
- Multi-device synchronization.

## 18. Implementation order

Implement in this order:

1. Domain records, schemas, status aggregation, and fingerprinting.
2. Qualification-plan loading and validation.
3. Discovery, compatibility, and self-test services.
4. Powered-idle metrics and channel-specific plausibility evaluators.
5. Operator-assisted response, alignment, and crosstalk workflows.
6. Sustained throughput orchestration through the existing engine and recorder.
7. Report persistence, recovery, and experiment-session attachment.
8. Simulator fault scenarios and contract tests.
9. CLI commands and JSON output.
10. Documentation and physical operator protocol.

Do not begin DAQ-M09 GUI work as part of this milestone.

## 19. Codex implementation prompt

> Implement DAQ-M08 only, using `DAQ-Software-Roadmap.md`, the repository architecture documents, completed DAQ-M00 through DAQ-M07 behavior, and `Milestones/DAQ-M08-hardware-qualification-and-diagnostics.md` as authoritative requirements. First inspect the existing ports, domain records, acquisition engine, session storage, replay, processing, plotting, CLI conventions, tests, and hardware profile schemas. Add hardware qualification and diagnostics without creating a second acquisition or storage path. Qualification must run headlessly against both simulated and NI adapters, produce a versioned machine-readable report, preserve raw evidence, distinguish automated verification from external wiring and stimulus assumptions, and return structured start-eligibility reasons. Add deterministic simulator faults for every required diagnostic, including unequal channel lengths, clipping, bias errors, noise, frozen data, dropouts, alignment errors, crosstalk, storage backpressure, and disconnects. Run focused tests and the full suite. Report changed files, architecture decisions, CLI examples, verification results, remaining assumptions, and every physical acceptance criterion that was not executed on real hardware. Do not implement DAQ-M09 GUI work.

## 20. Reference constraints

- NI-9239: four simultaneous differential voltage channels, 24-bit conversion, +/-10 V range, and 50 kS/s/channel.
- TE 830M1-0200: +/-200 g, nominal 6.3 mV/g, amplified approximately +/-1.25 V output about a powered bias, with nominal response above DC.
- PCB 422E03: 1 mV/pC charge conversion, inverted polarity, approximately +/-2.5 V dynamic output, and a powered output bias above the NI-9239 direct input range. The initial system therefore requires a documented compatible conditioning stage before the NI input.
