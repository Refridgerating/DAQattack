# Calibration and Baseline Processing

DAQ-M06 turns immutable raw volts into reproducible engineering quantities. It
is headless and read-only with respect to recorded sessions. Install the
analysis and storage extras before processing a session:

```powershell
python -m pip install -e ".[analysis,storage]"
labdaq recipe show baseline-v1 --json
labdaq process-session path\to\session --summary --json
```

## Calibration records

Every enabled channel resolves one calibration containing channel and sensor
identity, input/output units, sensitivity, polarity, input offset, valid input
span, method/source/date, and optional range, frequency, and uncertainty data.
Sensitivities expressed in mV per output unit are normalized to volts per output
unit before hashing and use:

```text
engineering_value = polarity * (raw_voltage - input_offset) / sensitivity
```

Certificate calibration is labeled `traceable`; an externally validated chain
is `externally_validated`; automated fixtures are `synthetic`. The 830M1-0200
6.3 mV/g fallback is accepted only when the hardware profile explicitly permits
that exact model, value, unit, and accelerometer role. It emits
`NOMINAL_CALIBRATION_FALLBACK` and remains `nominal` in provenance.

The charge channel uses the same affine abstraction, but its output unit and
polarity come from the complete external-chain record. LabDAQ never assumes
that an unvalidated converter output represents charge, force, or pressure.

## `baseline-v1`

For accelerometer channels at the actual acquisition rate:

1. Apply affine calibration to g.
2. Apply a causal fourth-order Butterworth 1 Hz high-pass in SOS form.
3. Apply a causal sixth-order Butterworth 12 kHz low-pass in SOS form.

The charge channel receives only affine calibration. Filter state is independent
per channel and persists across contiguous chunks. The first sample initializes
each stage with scaled steady-state `sosfilt_zi`. Gaps, overlaps, channel reorder,
rate change, nonfinite input, or changed processing context fault before stale
state is used. An explicit reset starts a labeled new segment.

Replay selections use half-open sample intervals `[start, stop)`. When `start`
is nonzero, processing begins at source sample zero to reconstruct causal state;
only the selected output is returned. Zero-phase filtering is not part of
`baseline-v1`.

## `baseline-low-rate-v1`

Below 25 kHz, LabDAQ automatically selects the distinct
`baseline-low-rate-v1` recipe. It retains affine calibration for every
calibrated role and the causal fourth-order 1 Hz accelerometer high-pass, but
omits the 12 kHz low-pass because that cutoff is at or above Nyquist. At 25 and
50 kHz, `baseline-v1` remains selected. Existing recipe definitions and
recorded hashes are never rewritten.

Live acquisition resolves the experiment recipe; replay, review, processing,
and export resolve the recorded session recipe by default. `--recipe` remains
an explicit override for compatible forensic workflows. Recipe ID, version,
and canonical hash are displayed and stored.

## Metrics

Metrics are computed from the conditioned signal over an explicit sample range:

- mean, RMS, minimum, maximum, absolute peak, peak-to-peak, crest factor
- raw-voltage clipping fraction at or beyond the calibration span, using the
  recipe's zero-volt clipping margin

Zero RMS makes crest factor explicitly unavailable. Nonfinite analysis input is
unavailable rather than an unexplained NaN. Metric records retain signal stage,
unit, range, rate, recipe hash, and calibration hash.

## Review extrema and transient suggestions

Sessions / Review can calculate maximum, minimum, and the largest absolute excursion
over one explicit raw, calibrated, or conditioned range. Each result retains its
signed value, magnitude, earliest global sample index, relative time, unit, stage,
and half-open range. This bounded replay does not write a processed array or alter
the source session. A channel containing nonfinite values is shown as unavailable
without hiding valid channels.

Automatic transient suggestions are independent per checked channel. LabDAQ samples
at most 262,144 evenly distributed source positions to estimate the median and
`1.4826 * MAD`, then performs a second bounded replay to find threshold crossings.
The operator controls the MAD multiplier, pre/post padding, and merge gap; defaults
are 6 MAD, 0.05 seconds before and after, and a 0.02-second same-channel merge gap.
Selecting one suggestion applies the same synchronized range to every checked
channel, and draggable cursors remain the final operator choice. See
[ADR-0028](adr/0028-non-destructive-review-ranges-and-transient-detection.md).

## FFT amplitude

The one-sided amplitude spectrum removes the interval mean, applies a periodic
Hann window, performs a real FFT, and corrects for coherent gain. Interior bins
are doubled; DC and an even-length Nyquist bin are not. Frequencies come from
the exact sample count and rate. Review output units match the explicitly selected
raw, calibrated, or conditioned input.

All-product session summaries reject selections above 1,048,576 samples per
channel before allocating FFT arrays.

## Welch PSD

Welch PSD uses a periodic Hann window, constant detrending per segment, density
scaling, and 50 percent overlap. The target segment is one second, with sample
count rounded half-up from the actual rate. Shorter records use their complete
length; fewer than two samples are rejected. A bounded streaming accumulator
retains at most one segment plus accumulated density, so replay chunk boundaries
do not affect the result. Units are engineering-unit squared per hertz.

## Reproducibility and limitations

Provenance identifies the source session state and logical raw digest, selected
range, channel order, actual rate, calibration and recipe hashes, processing
mode, algorithm parameters, software/NumPy/SciPy versions, units, quality, and
warnings. Volatile processing time is excluded from its deterministic hash.

Software cannot remove or validate transverse sensitivity, sensor mounting,
analog-chain frequency response, calibration uncertainty, grounding, actual
hardware noise, or external-conditioner behavior. These remain inputs to
DAQ-M08 physical qualification. Simulator evidence is not physical calibration
evidence.
