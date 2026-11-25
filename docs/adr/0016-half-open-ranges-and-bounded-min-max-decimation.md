# ADR-0016: Half-Open Ranges and Bounded Min/Max Decimation

Status: Accepted

## Context

Plot requests may be entered as sample indices or decimal seconds, while large
recordings cannot be copied into memory merely to draw a viewport. A display
reduction must retain isolated peaks, preserve channel alignment during bucket
selection, and never become an input to scientific processing or export.

## Decision

M07 resolves a range exactly once to `[start_index, stop_index)`. Decimal-second
boundaries are multiplied by the authoritative sample rate with decimal
arithmetic and both are rounded upward. Requests outside the verified replay
prefix are rejected rather than clipped.

`min-max-v1` retains the first and last sample and partitions the interior into
`floor((M - 2) / 2)` shared source-index buckets. Each channel emits its minimum
and maximum in source order and deduplicates coincident extrema. Reduction is
streaming, rejects nonfinite input, and uses memory proportional to the channel
count, target point count, and replay chunk. A reduction that is needed requires
at least four target points.

Decimation runs only after calibration, causal conditioning, or completed
spectral calculation. Raw records, CSV values, filter input, FFT input, and PSD
input remain undecimated.

## Consequences

- Sample-index meaning is stable across CLI, plots, figures, CSV, and metadata.
- Peaks and endpoints survive without fabricating interpolated values.
- Different channels share buckets but may retain different extrema indices.
- Invalid gaps are explicit; the renderer never visually bridges nonfinite data.
- FFT retains M06's 1,048,576-sample preallocation guard and PSD remains bounded.
