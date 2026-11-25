# ADR-0032: Raw-voltage spectra in session review

Status: Accepted

## Context

A checked raw-only ai3 was silently removed from FFT/PSD selection. Its recorded
voltage is sufficient for voltage-domain spectral analysis, without calibration.

## Decision

Offline FFT/PSD may analyze raw-only diagnostic channels as volts. In experiment
sessions, calibrated channels use the requested stage and raw-only channels always
use raw voltage. Raw-diagnostic sessions, including saved open-acquisition segments,
accept spectral requests only at the raw stage.

The application plotting service partitions channels by actual stage, reuses the
existing numerical routines and recorded recipe selection, and restores requested
channel order. Groups share the source range. Existing FFT memory limits and
streaming Welch conventions remain in force. Missing recipe provenance is an
explicit error; no recipe is invented for old sessions.

PlotSeries adds optional `spectral_input_stage`, serialized in bundles and export
sidecars. Bundle `spectral_details.input_stage` is `mixed` when stages differ;
otherwise it retains `raw`, `calibrated`, or `conditioned`. Per-channel metadata
records the actual stage. The v1 sidecar schema accepts this optional field;
existing sidecars still validate. Source-session schemas remain unchanged.

Diagnostic spectra retain raw-only disposition, null calibration and engineering
interpretation, and the diagnostic warning. Raw-diagnostic session spectra also
have null engineering interpretation. FFT uses V; PSD uses V^2/Hz. GUI and static
figures separate incompatible units through existing axis grouping.

GUI analysis selects channels per product. FFT/PSD include every checked channel;
time plots, extrema, and detection retain stage eligibility with a visible
explanation of exclusions. Failed spectra clear obsolete curves in the relevant panel.

## Consequences

- This supersedes only the offline voltage FFT/PSD restrictions in ADRs 0025/0026.
- Voltage spectra do not authorize charge calibration, conditioning, live processing,
  qualification, or hardware operation.
- CSV, PNG, SVG, CLI, and GUI share application policy and provenance.
- Separate stage groups require bounded replay passes; raw samples, manifests,
  and recorded recipes remain unchanged.
- This repository change does not update an installed preview or build an installer.
