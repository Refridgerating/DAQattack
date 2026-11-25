# DAQ-M06 Acceptance Evidence

Status: Accepted locally on Windows  
Evidence date: 2026-08-13  
Specification: [DAQ-M06 calibration and signal-conditioning pipeline](../../Milestones/DAQ-M06-calibration-signal-conditioning-pipeline.md)

## Scope delivered

M06 implements a headless, read-only scientific-processing slice over live
`AcquisitionChunk` records and fully verified M05 replay. It resolves explicit
calibrations, runs the causal `baseline-v1` pipeline, calculates metrics, FFT
amplitude, and Welch PSD, and returns deterministic provenance. It adds no GUI,
plot/export persistence, recipe editor, or hardware qualification.

New recordings snapshot active `baseline-v1` while retaining raw TDMS values in
volts. Existing `baseline-v1-draft` snapshots remain readable. Processing writes
nothing under `processed/` and does not modify any source record.

## Automated acceptance

| Criterion | Evidence | Result |
|---|---|---|
| Affine calibration is explicit and correct | Unit vectors cover 6.3 mV/g, independent sensitivities, offset, polarity reversal, synthetic charge, quality, span metadata, and JSON round trips | Satisfied |
| Nominal fallback is controlled and visible | Exact profile model/value/unit/role matching is required; unit and recorded-session tests assert `nominal` quality and `DAQ.PROCESSING.NOMINAL_CALIBRATION_FALLBACK` | Satisfied |
| Missing calibration fails closed | Aggregated per-channel resolution reports identify a missing charge offset; no substitute array is produced | Satisfied |
| Baseline filters meet targets | SOS response tests check the 1 Hz fourth-order high-pass and 12 kHz sixth-order low-pass at the actual 50 kS/s rate | Satisfied |
| Causal state is deterministic | Whole-array versus irregular rechunking agrees within `2e-12`; state is isolated by channel and continuity/nonfinite faults occur before advancement | Satisfied |
| Metrics follow declared policies | Constant, sine, impulse, zero, clipped, empty, and nonfinite vectors check RMS/peaks/crest/clipping and typed unavailability | Satisfied |
| Spectral conventions are qualified | Bin-centered sine FFT amplitude is within 0.5%; Welch peak and integrated power are within 2%; acceleration and charge units are asserted | Satisfied |
| Numerical upgrades are visible | Fixed calibrated, conditioned, FFT, and PSD SHA-256 vectors plus realized SOS coefficients pin the accepted NumPy/SciPy behavior; versions enter provenance | Satisfied |
| Replay reproduces live causal output | M02/M04/M05 simulator recording is processed in live and replay modes under different chunking and compared within `2e-12` | Satisfied |
| Reprocessing identity is deterministic | Different replay chunk sizes retain the same provenance hash; changing a filter cutoff changes recipe and provenance hashes | Satisfied |
| Recovered prefixes are bounded and labeled | Explicit M05 recovery processes only the 25-sample verified prefix and retains `interrupted` source state | Satisfied |
| Source records remain immutable | Integration tests byte-compare TDMS, manifest, event log, and recipe snapshots and assert that `processed/` remains empty | Satisfied |
| Dependency boundaries remain intact | Import contracts and subprocess tests keep application/core/CLI imports independent of SciPy, TDMS, NI, and Qt until composition requests them | Satisfied |

## Verification record

The final local commands and exact results were:

```text
ruff format --check .                       133 files already formatted
ruff check .                                all checks passed
mypy src                                    success: 50 source files
lint-imports                                5 contracts kept, 0 broken
pytest tests/unit/processing \
  tests/contract/test_processing_port_contract.py \
  tests/integration/test_durable_sessions.py \
  tests/integration/configuration \
  tests/architecture -q --no-cov            84 passed
pytest -q                                   340 passed, 6 skipped;
                                              86.25% branch coverage
python -m build                             sdist and wheel built successfully
labdaq recipe show baseline-v1 --json       succeeded; canonical recipe hash shown
```

The durable-session integration test drives the deterministic simulator through
M04 acquisition, M05 recording/full verification/replay, and both Python and
JSON CLI M06 processing. It checks live/replay equivalence, nominal fallback,
changed-recipe identity, recovered-prefix behavior, source-byte immutability,
the dedicated exit code 8, and an empty `processed/` directory.

## Physical acceptance

No physical hardware criterion is required for M06 software acceptance. All
automated calibration values are explicitly synthetic or nominal-policy test
data and are not physical evidence. This milestone makes no claim about sensor
wiring, mounting, transverse response, analog-chain behavior, hardware noise,
or actual charge-converter calibration.

## External dependencies and limitations

- Real per-axis certificate sensitivities, offsets, uncertainty, frequency
  response, and the validated charge-chain coefficient/polarity remain operator
  inputs. The operator template stays intentionally unusable without them.
- The active baseline is causal only. Zero-phase processing, arbitrary recipes,
  persistent derived artifacts, plots, and exports are deferred.
- All-product summaries cap FFT selections at 1,048,576 samples per channel.
  Selected replay begins at sample zero internally to reconstruct causal state.
- Software cannot compensate for or validate mounting, transverse sensitivity,
  grounding, conditioner response, or physical noise; DAQ-M08 owns that work.
