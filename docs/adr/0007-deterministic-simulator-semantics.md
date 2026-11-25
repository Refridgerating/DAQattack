# ADR-0007: Deterministic Simulator Semantics

Status: Accepted

## Context

A stateful random generator or chunk-local oscillator can change results when
read sizes, channel ordering, or unrelated components change. Such behavior
would make simulator-backed acceptance evidence irreproducible.

## Decision

Simulator values are functions of absolute sample index. Generator `sim-v1`
uses analytical signal equations and keyed counter-derived Gaussian noise. A
noise stream is keyed by generator version, unsigned seed, channel ID,
component ID, and absolute Box–Muller pair index.

Canonical output is an ordered, channel-major, little-endian, C-contiguous
`float64` byte stream. Its reference digest is SHA-256. Fault boundaries use
absolute sample indices or explicit read sequence numbers; wall time affects
only real-time pacing and injected delays.

Simulated model-compatible identities always carry `simulated=true` and
synthetic serials. Simulator validation is software evidence and never
physical qualification.

## Consequences

Changing numerical output requires a new generator version. Exact checksums
are supported within the locked Python/NumPy environment; other environments
use declared numerical tolerances in addition to checksum reporting. Chunk
partitioning cannot alter the reconstructed stream.
