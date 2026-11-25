# ADR-0013: Common Live and Replay Chunk Contract

Status: Accepted

## Context

Downstream processing and future GUI consumers should not need separate array,
time, or channel-order semantics for live acquisition and recorded sessions.
Replay must not masquerade as physical DAQ discovery or mutate source events.

## Decision

Replay emits the immutable M04 `AcquisitionChunk` record with the recorded
session ID, actual sample rate, ordered logical channel IDs, raw units, and array
shape `[channels, samples]`. Sequence and start indices are deterministic and
source-relative; telemetry uses `stop_sample_index / sample_rate`. Callers choose
the replay chunk size without changing values or the verified extent.

Replay first performs full verification and accepts only a replayable terminal
session or an explicitly recovered verified prefix. It reads bounded channel
slices, supports cancellation, is single-use, and never appends replay events to
the source session.

## Consequences

- Live and offline consumers share one scientific sample contract.
- Rechunking is exact and deterministic but does not reproduce original wall
  pacing or acquisition read boundaries.
- Hardware discovery, task ownership, and physical retry semantics remain
  outside replay.

