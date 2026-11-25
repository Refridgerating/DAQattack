# DAQ-M07 Acceptance Evidence

Status: Accepted locally on Windows  
Date: 2026-08-13  
Specification: [DAQ-M07 plotting and export services](../../Milestones/DAQ-M07-plotting-and-export-services.md)

## Changes and architecture

2026-09-08 review correction: raw-only diagnostic channels can produce offline
voltage FFT/PSD, including mixed-stage spectra and exports. The v1 sidecar schema
accepts optional per-channel `spectral_input_stage`; previous sidecars remain
readable. Analytical and export evidence is recorded in the
[M10 correction report](DAQ-M10-acceptance-evidence.md#2026-09-08-ai3-voltage-fftpsd-correction).

M07 adds NumPy-free immutable request/export records, NumPy-backed plot ports,
a reusable M06 recorded-signal stream, bounded min/max decimation, headless plot
orchestration, incremental CSV, static PNG/SVG rendering, schema-validated JSON
sidecars/manifests, collision-safe filesystem installation, CLI commands, and a
dedicated failure exit code.

Application services depend only on replay, processing, renderer, serializer,
CSV-writer, and artifact-store ports. NumPy arrays remain outside the domain.
Matplotlib is imported only inside the rendering adapter and is provided by the
bounded `export` extra. ADR-0016 fixes range/decimation semantics; ADR-0017 fixes
renderer, CSV, provenance, atomic-pair, and collision policy.

## Acceptance matrix

| Criterion | Evidence | Result |
|---|---|---|
| Raw, calibrated, filtered, FFT, and PSD plot data work without Qt/NI | Four-channel recorded simulator integration exercises every representation and raw/calibrated/conditioned spectral input | Satisfied |
| Range semantics are exact | Decimal-ceiling boundary tests cover index/seconds equivalence, half-open endpoints, empty/reversed/negative/mixed/out-of-bounds rejection | Satisfied |
| Plot data is bounded and preserves extrema | Deterministic min/max tests cover endpoints, impulses, extrema order, shared buckets, deduplication, rechunking, point bounds, and nonfinite rejection | Satisfied |
| CSV is exact, ordered, incremental, and undecimated | Selected channels/range are compared sample-for-sample with TDMS replay; a counting writer observes multiple chunks and `.17g` round-trips source `float64` values | Satisfied |
| Figures are headless PNG and SVG | Real noninteractive Matplotlib canvases render filtered overlays and conditioned PSD; byte/signature checks and sidecar schema checks pass | Satisfied |
| Provenance is complete and machine-readable | Version-1 request, sidecar, and manifest codecs round-trip; schemas validate source paths/hashes/versions, raw digest, identities/units, calibration, recipe/spectral settings, counts, warnings, checksums, progress, faults, and state | Satisfied |
| Outputs are atomic and never overwrite | Collision tests, same-directory exclusive installation, paired cleanup, cancellation, and partial-job fault tests pass | Satisfied |
| Incompatible spectral grids are not interpolated | Controlled plot double creates two grids; export produces two CSV/sidecar artifacts with their original channel grids | Satisfied |
| Memory limits remain explicit | Replay is chunked, decimator storage is target-bounded, CSV observes incremental chunks, Welch PSD bounded/rechunking tests pass, and oversized FFT is rejected before replay/allocation | Satisfied |
| Source record remains immutable | Integration tests byte-compare manifest, TDMS, events, and recipe before/after exports | Satisfied |
| Architecture and optional dependencies remain isolated | AST/import-linter/subprocess tests prohibit adapter/renderer imports and prove core/application plus renderer-module imports do not load Matplotlib, NumPy, SciPy, Qt, NI, or TDMS | Satisfied |

## Verification

Final local results:

| Command | Result |
|---|---|
| `ruff format --check .` | Passed; 149 files formatted |
| `ruff check .` | Passed |
| `mypy src` | Passed; 59 source files |
| `lint-imports` | Passed; 5 contracts kept, 0 broken |
| `pytest tests/unit/plotting tests/integration/test_plotting_and_export.py -q --no-cov` | Passed; 22 tests |
| Focused M05/M06/M07 unit, contract, and integration command | Passed; 73 tests |
| `pytest tests/architecture -q --no-cov` | Passed; 29 tests |
| `pytest -q` | Passed; 366 tests, 6 hardware skips, 85.67% coverage |
| `python -m build` | Passed; sdist and pure-Python wheel built from isolated environments |

Representative CLI workflows covered bounded raw JSON plot data, selected raw
CSV, filtered PNG overlay, and conditioned SVG PSD. Package schemas and codecs
were exercised from installed package resources.

## Physical and external acceptance

No physical hardware criterion is required for M07. All signal, replay,
processing, rendering, and export evidence is simulator-backed. No NI chassis,
module, sensor wiring, sensor power, accelerometer calibration certificate, or
external charge conditioner was physically tested. M03 through M05 physical
verification remains separately pending and is not implied by this acceptance.

## Assumptions and limits

- Sessions are addressed by path because no session-ID registry exists.
- V1 recorded sessions use synchronized channel grids. Controlled doubles prove
  the separate-artifact behavior for incompatible spectral grids.
- The destination must be an existing writable directory on a filesystem that
  supports exclusive hard links. Existing files are never replaced.
- FFT remains limited to 1,048,576 selected samples per channel; PSD, CSV,
  filtering, replay, and display decimation remain bounded.
- GUI, live plotting, hardware qualification, reports beyond M07 artifacts, and
  physical acquisition are deferred to later milestones.
