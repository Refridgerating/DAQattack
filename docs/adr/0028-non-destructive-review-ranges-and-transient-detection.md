# ADR-0028: Non-Destructive Review Ranges and Transient Detection

Status: Accepted

## Context

Post-run operators need to isolate transient waveform events, compare synchronized
channels over the same interval, inspect extrema, and run spectral analysis without
creating another authoritative recording. Long sessions also make whole-array event
detection inappropriate, while opaque hard-coded thresholds would be difficult to
reproduce or defend.

## Decision

A review trim is an in-memory, half-open sample range over the verified source
prefix. It never changes `raw.h5`, a legacy `raw.tdms`, the manifest, or the baseline
recipe, and it does not create a child session. The selected range is shared by all
checked synchronized channels and is used by time plots, FFT, PSD, located extrema,
and the existing selected-range exports. Full-extent TDMS interoperability export is
unchanged.

Automatic suggestions are detected independently for each checked channel at one
explicit raw, calibrated, or conditioned signal stage. A deterministic first replay
collects at most 262,144 evenly distributed samples per channel and estimates the
median and robust scale `1.4826 * MAD`. A second bounded replay finds contiguous
absolute deviations at or above an operator-selected scale multiple. A zero-MAD
constant channel has no events; nonzero deviations from a zero-MAD baseline are
crossings. Same-channel crossings may be merged, padded outward using ceiling sample
conversion, clamped to the readable extent, and merged again when padded intervals
overlap. Channels are never merged with one another.

Detected events retain their source channel, crossing range, padded analysis range,
strongest deviation, global sample index, relative time, and normalized score.
Selecting any suggestion moves editable cursors, but analysis remains an explicit
operator action. The initial controls are 6 MAD, 50 ms pre-padding, 50 ms
post-padding, and a 20 ms merge gap; they are review controls rather than additions
to `baseline-v1`.

Located extrema are accumulated in bounded memory. Maximum, minimum, and largest
absolute excursion retain the signed value, magnitude, earliest matching global
sample index, relative time, unit, stage, and range. A nonfinite channel is explicitly
unavailable without suppressing valid results from other channels.

## Consequences

- Raw-session and recipe schemas remain unchanged.
- Detection and extrema work through the common format-dispatching replay boundary,
  so HDF5 and supported legacy TDMS sessions share the same behavior.
- Detector suggestions are reproducible and chunk-boundary invariant but are not a
  physical event classifier; the operator can tune or override every range.
- The two-pass detector trades additional bounded I/O for fixed analysis memory.
- Cursor and detection state are intentionally ephemeral. Persisted exports retain
  the exact source range through existing provenance.
