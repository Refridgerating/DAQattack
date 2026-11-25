# DAQ-M06: Calibration and Signal-Conditioning Pipeline

Status: Complete; accepted locally on Windows  
Depends on: DAQ-M00 through DAQ-M05  
Unlocks: DAQ-M07 plotting/export, DAQ-M08 hardware qualification, and DAQ-M10 live and post-run analysis

## 1. Goal

Turn immutable raw voltage samples into traceable engineering quantities through one versioned and reproducible baseline processing recipe.

DAQ-M06 establishes the scientific interpretation boundary of LabDAQ. The same raw session, calibration records, recipe version, sample range, and software version shall produce the same calibrated signal, conditioned signal, metrics, FFT amplitude spectrum, and power spectral density.

This milestone shall work headlessly. It shall not add GUI processing controls or a general-purpose filter editor.

## 2. Milestone outcome

At completion, LabDAQ shall be able to:

1. Read a recorded or live four-channel voltage stream.
2. Resolve the calibration assigned to each logical channel.
3. Convert the three 830M1 accelerometer axes from volts to acceleration.
4. Convert the externally conditioned charge channel using its validated chain calibration.
5. Apply the causal `baseline-v1` conditioning pipeline while preserving filter state across chunks.
6. Calculate declared time-domain metrics.
7. Generate deterministic FFT amplitude and Welch PSD products.
8. Serialize the exact recipe and complete processing provenance.
9. Reprocess a stored session without modifying its raw samples or source manifest.
10. Reject scientifically ambiguous processing requests instead of guessing missing calibration or filter parameters.

## 3. Scientific and architectural invariants

The following rules are mandatory:

- Raw voltage arrays are immutable.
- Calibration is explicit data, not hidden code or a GUI setting.
- Sample index remains the authoritative relative-time coordinate.
- Processing never invents a missing sensitivity, polarity, offset, unit, sample rate, or channel identity.
- A nominal sensitivity is not equivalent to a certificate sensitivity.
- Live processing is causal and stateful across chunk boundaries.
- Offline products may operate over a selected range, but the baseline conditioned waveform shall use the same causal recipe used during live processing.
- A gap, overlap, reordered chunk, or sample-rate change breaks processing continuity and becomes a structured fault.
- Metrics and spectra always identify the signal stage and units from which they were calculated.
- Windowing is an analysis operation. It shall not overwrite or masquerade as the conditioned time history.
- Processing code shall not import Qt, NI-DAQmx, or TDMS implementation details.
- Storage and application services may serialize processing results, but numerical algorithms remain outside storage and GUI adapters.

## 4. Hardware interpretation for the initial profile

The initial profile uses three axes from the TE 830M1-0200 accelerometer.

The product datasheet supports the following profile facts:

- Measurement range: plus or minus 200 g.
- Nominal sensitivity: 6.3 mV/g.
- Full-scale output: plus or minus 1.25 V around the zero-g bias.
- Zero-g output: nominally one-half of the applied supply voltage.
- Frequency response: 6 Hz to 10 kHz within plus or minus 1 dB.
- Extended frequency response: 2 Hz to 15 kHz within plus or minus 3 dB.
- Resonant frequency: 30 kHz typical.
- Transverse sensitivity: up to 8 percent.
- Each device is shipped with NIST-traceable amplitude calibration data at 80 Hz.

These are profile and provenance inputs. They do not authorize the software to fabricate the actual axis sensitivities from the nominal value when certificate data are available.

The externally conditioned charge channel shall use the measured sensitivity, polarity, valid input span, output unit, and calibration source for the complete sensor and conditioner chain. DAQ-M06 shall not infer these values from the converter model number.

## 5. Scope

### 5.1 Calibration records

Define immutable, versioned calibration records sufficient to reproduce the conversion from raw volts to engineering units.

Each channel calibration shall include at least:

- Calibration ID and schema version.
- Logical channel ID and sensor ID.
- Sensor model and serial number when available.
- Input unit and output engineering unit.
- Conversion convention, preferably volts per engineering unit.
- Sensitivity value.
- Fixed input offset or zero-bias voltage.
- Polarity, restricted to plus one or minus one.
- Valid input minimum and maximum.
- Valid output minimum and maximum when known.
- Calibration method such as certificate, laboratory calibration, nominal fallback, or externally validated chain.
- Calibration source, certificate or record identifier, date, and notes.
- Declared validated and extended frequency bands when known.
- Optional uncertainty statement when supplied by the source calibration.
- Canonical content hash.

The accelerometer conversion shall be defined as:

```text
acceleration_g = polarity * (raw_voltage_v - zero_bias_v) / sensitivity_v_per_g
```

Certificate sensitivities may differ for X, Y, and Z. The pipeline shall apply the calibration assigned to each channel independently.

For the 830M1-0200, 6.3 mV/g may be used only when the hardware profile explicitly permits nominal fallback. Use of the fallback shall emit a warning and mark the result quality as `nominal`, not `traceable`.

The charge-channel conversion shall use the same affine-calibration abstraction. Its output unit shall come from the calibration record. No hard-coded assumption shall be made that the output represents charge, force, pressure, or another derived quantity.

### 5.2 Processing records

Add immutable records for at least:

- Processing recipe identity and schema version.
- Ordered stage definitions.
- Stage applicability by channel type.
- Streaming processor state metadata.
- Processed data chunk.
- Processing range.
- Time-domain metric set.
- FFT amplitude result.
- PSD result.
- Processing provenance.
- Per-channel processing status and quality.

A processed chunk shall preserve:

- Session UUID.
- Channel identity and order.
- Start sample index.
- Number of samples.
- Sample rate.
- Output unit.
- Recipe ID and recipe hash.
- Calibration ID and calibration hash.
- Continuity status.

### 5.3 `baseline-v1` recipe

Implement one checked-in, versioned recipe named `baseline-v1`. It is the only supported recipe in this milestone.

The target sampling rate for the initial profile is 50,000 samples per second per channel.

#### Accelerometer live pipeline

Apply these stages in order:

1. Fixed affine calibration from volts to g using the assigned per-axis calibration.
2. Fourth-order Butterworth high-pass filter at 1 Hz.
3. Sixth-order Butterworth low-pass filter at 12 kHz.

Both filters shall:

- Use second-order-section representation.
- Execute causally with persistent state.
- Be designed using the actual sample rate.
- Reject configurations where the sample rate cannot support the declared cutoff.
- Initialize deterministically.
- Record their order, cutoff, design family, implementation form, and phase behavior in provenance.

The 1 Hz high-pass suppresses residual offset while preserving the sensor's specified 2 Hz extended response and 6 Hz primary response. The 12 kHz low-pass preserves the primary response through 10 kHz while reducing content near the upper edge of the sensor response and the 25 kHz Nyquist frequency.

At 50 kS/s, the declared digital response targets are:

- High-pass attenuation at 1 Hz: minus 3.01 dB within plus or minus 0.15 dB.
- High-pass attenuation at 2 Hz: less than 0.05 dB.
- Low-pass attenuation at 10 kHz: less than 0.30 dB.
- Low-pass attenuation at 12 kHz: minus 3.01 dB within plus or minus 0.15 dB.
- Low-pass attenuation at 15 kHz: at least 18 dB.

#### Conditioned charge-channel live pipeline

Apply the validated affine calibration and polarity conversion.

Do not apply a default digital high-pass, low-pass, notch, or band-pass stage until the complete external chain bandwidth has been documented and accepted. The channel remains eligible for metrics and spectral products after calibration.

#### Filter startup and continuity

The streaming processor shall maintain independent filter state per channel.

The first chunk shall use a deterministic initial-state policy based on the first calibrated sample. The policy shall be recorded in the recipe.

A chunk is contiguous only when its start sample index equals the prior end sample index and its sample rate, channel identity, calibration hash, and recipe hash are unchanged.

On discontinuity, the processor shall not silently continue with stale state. It shall emit a continuity fault and require an explicit reset or new processing segment.

Offline reprocessing of a selected range shall process sufficient preceding session history to reproduce the same causal state, then return only the requested range. Starting a filter directly at an arbitrary selected sample is not equivalent and is prohibited unless the result is explicitly marked as a new segment.

### 5.4 Time-domain metrics

Provide deterministic metrics over an explicit sample interval:

- Mean.
- Root mean square.
- Minimum.
- Maximum.
- Absolute peak.
- Peak-to-peak.
- Crest factor.
- Clipping fraction.

Definitions shall be fixed and documented:

```text
rms = sqrt(mean(x^2))
absolute_peak = max(abs(x))
peak_to_peak = max(x) - min(x)
crest_factor = absolute_peak / rms
```

Crest factor is undefined when RMS is zero and shall return a typed unavailable value rather than infinity or NaN without explanation.

Clipping fraction shall be calculated from the raw voltage and the calibration record's valid input span. The configured clipping margin shall be included in the recipe or metric configuration. A missing valid input span shall produce `not_evaluated`, not zero clipping.

Every metric result shall record:

- Source signal stage.
- Channel and units.
- Start and end sample indices.
- Sample count and sample rate.
- Recipe and calibration hashes.
- Availability and warning state.

### 5.5 FFT amplitude spectrum

Provide a one-sided FFT amplitude product for a finite selected interval.

The baseline convention shall be:

- Input: `baseline-v1` conditioned signal.
- Detrending: remove the interval mean.
- Window: Hann.
- Transform: real FFT.
- Amplitude normalization: coherent-gain corrected, one-sided amplitude.
- DC and Nyquist bins: not doubled.
- Frequency coordinates: derived exactly from sample count and sample rate.
- Output unit: same engineering unit as the input signal.

A bin-centered sine of known amplitude shall be recovered within the declared numerical tolerance after window correction.

### 5.6 Power spectral density

Provide a one-sided Welch PSD product.

The baseline convention shall be:

- Input: `baseline-v1` conditioned signal.
- Segment duration: 1.0 second where enough data exist.
- Segment sample count: rounded deterministically from sample rate.
- Window: Hann.
- Overlap: 50 percent.
- Detrending: constant per segment.
- Scaling: density.
- Output units: engineering-unit squared per hertz.
- Short records: use the largest valid deterministic segment and report the effective parameters.

The integrated PSD over frequency shall agree with the mean-square value of appropriate analytical reference signals within the declared tolerance.

### 5.7 Recipe serialization and provenance

The checked-in `baseline-v1` JSON shall be validated by a versioned schema and shall round-trip without information loss.

Canonical serialization shall produce a stable recipe hash independent of whitespace or dictionary insertion order.

Each processing result shall identify at least:

- Source session UUID and completion state.
- Source raw-data identity or checksum supplied by DAQ-M05.
- Source sample range.
- Channel identities and order.
- Actual sample rate.
- Calibration IDs and hashes.
- Recipe ID, version, schema version, and hash.
- Processing mode such as live or replay.
- Algorithm and stage parameters.
- Application version.
- NumPy and SciPy versions.
- Output units.
- Warnings, nominal fallbacks, unavailable metrics, and continuity faults.

Volatile fields such as processing time shall not be included in the deterministic content hash.

### 5.8 Application services and CLI

Add headless application services for:

- Resolving and validating channel calibrations.
- Creating a streaming processor for live chunks.
- Processing a recorded session or sample range through replay.
- Computing metrics.
- Computing FFT and PSD products.
- Returning processing provenance.

Add CLI coverage sufficient to exercise the milestone without Qt. Recommended commands are:

```text
labdaq recipe show baseline-v1
labdaq recipe validate path/to/recipe.json
labdaq process-session path/to/session --recipe baseline-v1 --summary
```

`process-session` may emit a machine-readable summary under the session's `processed/` directory, but it shall not implement DAQ-M07 CSV or figure export.

## 6. Layer ownership

### Domain

Owns calibration, recipe, metric, spectrum, provenance, and processed-chunk records plus validation rules. Domain code shall not import NumPy, SciPy, Qt, NI-DAQmx, or TDMS adapters unless the existing repository charter explicitly permits NumPy arrays in domain records.

### Processing

Owns affine calibration, filter design, streaming filter state, metrics, FFT, PSD, and deterministic numerical conventions.

### Application

Owns calibration resolution, live processing orchestration, replay processing, range selection, continuity handling, and structured status reporting.

### Ports

Owns any required processing-result sink or recipe repository contracts. Do not couple the numerical pipeline directly to filesystem paths.

### Adapters

Own JSON recipe and provenance serialization plus any minimal derived-result persistence used by the CLI.

### GUI

No GUI behavior is added in DAQ-M06.

## 7. Structured warnings and faults

Add stable codes for at least:

- `CALIBRATION_MISSING`
- `CALIBRATION_INVALID`
- `CALIBRATION_CHANNEL_MISMATCH`
- `NOMINAL_CALIBRATION_FALLBACK`
- `CALIBRATION_INPUT_OUT_OF_RANGE`
- `RECIPE_SCHEMA_UNSUPPORTED`
- `RECIPE_HASH_MISMATCH`
- `RECIPE_CHANNEL_INCOMPATIBLE`
- `RECIPE_SAMPLE_RATE_INCOMPATIBLE`
- `NONCAUSAL_STAGE_IN_LIVE_PIPELINE`
- `PROCESSING_CHUNK_GAP`
- `PROCESSING_CHUNK_OVERLAP`
- `PROCESSING_CHANNEL_ORDER_CHANGED`
- `PROCESSING_NONFINITE_INPUT`
- `FILTER_STATE_RESET`
- `METRIC_NOT_EVALUATED`
- `SPECTRAL_RANGE_TOO_SHORT`

Warnings and faults shall include channel identity, session identity, sample range where relevant, and an actionable message.

## 8. Deliverables

- Versioned calibration domain records and JSON schema.
- Versioned processing-recipe records and JSON schema.
- Checked-in `baseline-v1` recipe.
- Per-channel affine calibration implementation.
- Stateful causal SOS filtering implementation.
- Time-domain metric implementation.
- FFT amplitude implementation with explicit normalization.
- Welch PSD implementation with explicit normalization and units.
- Streaming and replay processing application services.
- Recipe and provenance serialization.
- Headless CLI commands.
- Structured warnings and faults.
- Unit, contract, integration, and regression tests.
- Updated architecture, processing, calibration, CLI, and user documentation.
- Acceptance evidence showing focused and full-suite results.

## 9. Acceptance criteria

DAQ-M06 is accepted only when all software-verifiable criteria pass.

### Calibration

- A 6.3 mV change above the configured zero bias converts to plus 1 g when using the nominal 830M1-0200 sensitivity and positive polarity.
- A 6.3 mV change below the configured zero bias converts to minus 1 g.
- Per-axis certificate sensitivities produce different correct results when assigned different values.
- Polarity reversal is exact.
- Charge-channel affine reference vectors pass using a synthetic or validated chain calibration.
- Missing calibration never silently falls back.
- Nominal fallback requires explicit profile permission, emits a warning, and is preserved in provenance.
- Calibration records round-trip through JSON without information loss.

### Filters and streaming

- Digital frequency response meets every declared `baseline-v1` tolerance at 50 kS/s.
- Filtering one contiguous array and filtering the same data in arbitrary contiguous chunks produce equivalent output within a strict floating-point tolerance.
- Filter state is isolated by channel.
- A gap, overlap, channel reorder, sample-rate change, recipe change, or calibration change is detected before stale state is used.
- Live pipeline validation rejects all noncausal stages.
- Input arrays are not modified in place.

### Metrics

- Constant, sine, impulse, zero, clipped, and nonfinite analytical vectors produce the documented metric values or typed unavailability states.
- Crest factor does not return an unexplained infinity or NaN for zero input.
- Clipping fraction is evaluated from the declared raw valid-input span and margin.
- Metric results carry stage, units, sample range, recipe hash, and calibration hash.

### Spectral products

- Frequency-bin coordinates are correct for multiple sample counts and rates.
- A bin-centered sine's FFT amplitude is recovered within 0.5 percent.
- PSD peak frequency is recovered within one frequency bin.
- Integrated PSD agrees with analytical mean square within 2 percent for accepted reference vectors.
- FFT and PSD units are correct for both acceleration and a synthetic conditioned-charge unit.
- Short and empty ranges return explicit results or faults according to the documented policy.

### Reproducibility and integration

- The same deterministic simulated session processed twice with the same recipe produces equivalent numerical arrays and identical deterministic provenance hashes.
- Live chunk processing and M05 replay processing produce equivalent conditioned samples for the same source samples.
- Processing a recorded session does not change its raw container, source sample arrays, or source-record checksums.
- An incomplete but readable session may be processed only over its verified recorded prefix and retains its incomplete source state in provenance.
- A recipe with changed stage parameters produces a different recipe hash and derived-result identity.
- Simulated mode and the complete test suite run without NI hardware, NI-DAQmx, or Qt.

## 10. Required tests

### Unit tests

- Calibration formula, units, polarity, offset, range validation, and quality status.
- Canonical JSON and hash stability.
- Filter design response.
- Streaming state and chunk-boundary invariance.
- Metrics against hand-calculated vectors.
- FFT amplitude normalization.
- Welch PSD normalization and integrated power.
- Fault and warning payloads.

### Contract tests

- Every processing implementation accepts and returns the declared records.
- Recipe repositories and serializers preserve all required fields.
- Processing results preserve sample-index and channel-order contracts.

### Integration tests

- M02 deterministic simulator to M04 acquisition to M05 recording and replay to M06 processing.
- Four simultaneous channels with independent calibrations.
- Live versus replayed output equivalence.
- Nominal accelerometer fallback plus validated charge-chain calibration.
- Missing charge calibration with explicit per-channel failure.
- Incomplete-session verified-prefix processing.

### Regression tests

- Chunk size does not change the conditioned waveform.
- Dictionary ordering does not change recipe or calibration hashes.
- Raw arrays and raw session checksums remain unchanged.
- A future SciPy or NumPy upgrade cannot silently alter accepted reference outputs without updating qualification evidence and provenance.

## 11. Documentation requirements

Add or update:

- Calibration record format and examples.
- `baseline-v1` recipe and rationale.
- Causal versus noncausal processing explanation.
- Filter response and supported sample-rate constraints.
- Metric definitions.
- FFT and PSD normalization and units.
- Nominal versus traceable calibration labels.
- Charge-chain calibration requirements.
- Replay and selected-range state reconstruction behavior.
- CLI examples.
- Known scientific limitations.

The known-limitations section shall state that transverse sensitivity, sensor mounting, analog-chain response, calibration uncertainty, and actual hardware noise are physical effects that software processing cannot remove or validate by itself. These are inputs to DAQ-M08 hardware qualification.

## 12. Non-goals

DAQ-M06 shall not implement:

- GUI filter controls or a recipe editor.
- User-defined recipe libraries.
- Arbitrary drag-and-drop filter chains.
- Zero-phase filtering in `baseline-v1`.
- Adaptive filtering.
- Order tracking.
- Coherence, transfer functions, or modal analysis.
- Cross-axis rotation or coordinate-frame correction.
- Sensor mounting compensation.
- Automatic calibration from unknown experimental data.
- CSV, figure, or vector-graphic export.
- Hardware qualification or pass/fail judgments.

## 13. Definition of done

DAQ-M06 is complete when:

1. The `baseline-v1` recipe is explicit, versioned, schema-valid, and checked into the repository.
2. Calibrated and conditioned outputs are reproducible from M05 sessions.
3. Causal live and replay processing agree for identical samples.
4. Metrics, FFT, and PSD pass analytical reference tests.
5. Calibration quality and provenance remain visible in every derived result.
6. Raw data and source records remain unchanged.
7. Focused tests and the full test suite pass.
8. Documentation and acceptance evidence are updated.
9. Any unavailable real certificate or charge-chain coefficient is reported as an external calibration dependency, not replaced with a fabricated value.

## 14. Codex implementation prompt

> Implement DAQ-M06 only, using `DAQ-Software-Roadmap.md`, the repository architecture documents, the existing M00 through M05 implementation, and `DAQ-M06-calibration-signal-conditioning-pipeline.md` as authoritative requirements. First inspect the current domain records, configuration schemas, acquisition chunks, session parser, replay API, error hierarchy, CLI composition, and test conventions. Preserve all completed behavior and layer boundaries. Implement versioned calibration and processing records, the checked-in `baseline-v1` recipe, per-channel affine calibration, causal stateful SOS filters, metrics, FFT amplitude, Welch PSD, processing provenance, replay and streaming application services, minimal CLI coverage, structured warnings and faults, tests, and documentation. Do not implement GUI controls, arbitrary recipe editing, plotting, export, hardware qualification, or later analysis features. Do not invent actual per-axis certificate sensitivities or an actual charge-chain coefficient. Use synthetic calibration values in automated tests and require explicit profile data for real sessions. Run focused tests and the full test suite. Report changed files, architecture decisions, numerical conventions, verification results, remaining external calibration dependencies, and any acceptance criterion that could not be physically verified.
