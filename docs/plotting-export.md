# Plotting and Export Services

DAQ-M07 is a headless, read-only application layer over verified M05 replay and
M06 processing. It produces bounded plot bundles, exact CSV data, static PNG or
SVG figures, metadata sidecars, and export-job manifests. It never writes into
the source record or `processed/`.

## Plot data

`PlotDataService.build_plot_data()` supports raw voltage, calibrated engineering
units, baseline-filtered values, FFT amplitude, and Welch PSD. Spectra always
name the recipe that supplies FFT or PSD settings and explicitly identify raw,
calibrated, or conditioned input. Raw views do not resolve calibration;
calibrated views resolve only selected channels; conditioned views reconstruct
causal filter state by replaying from sample zero.

Ranges are half-open. Sample `n` occurs at `n / sample_rate_hz`. A seconds-based
range is converted once with decimal arithmetic and ceiling at both boundaries,
selecting exactly the samples whose timestamps fall in the requested interval.
Empty, negative, reversed, mixed index/seconds, and out-of-bounds requests fail.

Display reduction uses `min-max-v1`. It preserves endpoints and bucket extrema,
uses shared source-index buckets, never interpolates, and returns at most the
requested point count per series. Reduction applies after conditioning or
spectral calculation and never affects CSV, filtering, FFT input, PSD input, or
the source record. See [ADR-0016](adr/0016-half-open-ranges-and-bounded-min-max-decimation.md).

## CSV and figures

Time CSV headers are:

```text
sample_index,relative_time_s,<logical-channel-id>...
```

Spectral CSV headers are:

```text
frequency_hz,<logical-channel-id>...
```

Rows are streamed as UTF-8 comma-separated data. Floating values use `.17g`;
the reserved missing-value spelling is lowercase `nan`, and infinities are
rejected. Requested channel order is retained. Channels on incompatible
frequency grids are split into separately checksummed artifacts without
interpolation.

The optional Matplotlib adapter uses noninteractive PNG and SVG canvases. It
renders a single-channel time history, synchronized same-unit overlays, FFT,
and PSD. Different units receive separate shared-x subplots. Captions identify
the source session, sample range, and recipe where applicable.

Install figure support with:

```powershell
python -m pip install -e ".[export,analysis,storage]"
```

## Durable derived records

CSV defaults to the source `exports/` directory and figures to `plots/`.
Operators may select another existing writable directory, but every request in
one job must share it. Base names are lowercase kebab case. Primary, sidecar,
and manifest paths are checked for collisions before output is installed.

Every primary receives `<primary>.metadata.json`. The sidecar records source
paths and hashes, source schema/application versions, raw digest, requested and
resolved range, logical/physical/sensor identities, raw/output units,
calibration coefficients and hashes, complete recipe settings and hash,
spectral settings, counts, warnings, and the primary checksum.

The job writes `export-manifest-<uuid>.json` with requests, completed artifacts,
sidecar checksums, progress, warnings, faults, timestamps, and `completed`,
`cancelled`, or `faulted` state. Version-1 request, sidecar, and manifest schemas
are packaged under `labdaq.resources/schemas/v1`; `JsonPlotExportCodec` validates
and round-trips the durable document families.

Temporary files are created beside their targets, flushed, synced, checksummed,
and installed exclusively. Existing files are never replaced. Failure removes
the current primary/sidecar pair, preserves completed earlier requests, and
writes a truthful terminal manifest. See
[ADR-0017](adr/0017-headless-rendering-and-atomic-derived-artifacts.md).

## Limits

- Full FFT input remains limited to 1,048,576 samples per channel and is
  rejected before allocation.
- Welch PSD, time CSV, causal filtering, and plot decimation use bounded chunks.
- Static figures contain bounded display data; they are not authoritative data.
- M07 performs no acquisition, NI access, GUI work, live plotting, qualification,
  or physical validation.

## M10 session review and artifact history

The desktop uses `SessionReviewService` to reopen a completed, stopped, faulted,
or explicitly recoverable session through full M05 verification before asking
M07 for plot data. Relative-time controls resolve once to the same authoritative
half-open range used by every selected channel. The final live buffer is never a
review source.

The filesystem export-history adapter scans only `exports/` and `plots/` for
version-1 `export-manifest-*.json` records and decodes them with
`JsonPlotExportCodec`. Malformed manifests remain visible as invalid diagnostic
entries. It does not infer provenance from filenames or primary artifacts.

DAQ-M10 does not change the plot request, export request, sidecar, or manifest
schema version. UI history is a view of existing M07 records; all source hashes,
calibration/recipe identity, spectral settings, collision policy, progress, and
terminal state retain M07 semantics.

The ai3 review correction adds optional per-channel `spectral_input_stage` to the
v1 sidecar schema and `PlotSeries`. Older sidecars without the field still validate.
FFT/PSD resolve raw-only channels to raw volts while calibrated channels retain the
requested stage. Bundle `spectral_details.input_stage` reports `mixed` when needed;
every spectral series records its actual stage. Diagnostic spectra have null
calibration and engineering interpretation and retain their warning/disposition.
Raw-diagnostic sessions allow spectra only with raw input. Source schemas, window
and normalization conventions, memory limits, and recipe selection are unchanged.
