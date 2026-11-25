# ADR-0015: Causal Baseline Processing and Spectral Conventions

Status: Accepted

## Context

Live and replay conditioning must agree across arbitrary chunk boundaries, and
metrics or spectra must not depend on undocumented library defaults. Application
or storage code must not own numerical algorithms.

## Decision

Application services consume processing ports; `labdaq.processing` implements
them with the optional NumPy/SciPy analysis stack. The composition root imports
that implementation only when processing is requested.

The accelerometer baseline is affine calibration, fourth-order 1 Hz Butterworth
high-pass, then sixth-order 12 kHz Butterworth low-pass. Both filters use SOS,
causal `sosfilt`, independent channel/stage state, and `sosfilt_zi` scaled by the
stage's first input sample. The charge channel receives affine calibration only.
Continuity is checked before state is used. Selected replay ranges reconstruct
state from session sample zero and discard preceding output.

FFT amplitude uses mean removal, a periodic Hann window, coherent-gain
correction, and one-sided amplitude with DC and Nyquist not doubled. Welch PSD
uses periodic Hann, constant segment detrending, a deterministically rounded
one-second segment, 50 percent overlap, and density scaling. Full FFT selection
is limited to 1,048,576 samples per channel and rejected before allocation.

## Consequences

- Live and replay values are invariant to contiguous rechunking within declared
  floating-point tolerance.
- Arbitrary-range processing pays the cost of reading and filtering prior
  history; it does not silently invent initial state.
- NumPy/SciPy upgrades require updated numerical qualification evidence when
  accepted reference values change.
- M06 returns in-memory results and CLI summaries only; M07 owns persistent
  derived exports.
