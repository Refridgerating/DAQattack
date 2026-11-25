# ADR-0012: Atomic Checkpoints, Digests, and Explicit Recovery

Status: Accepted

## Context

Acquisition, storage, or process failure may occur between synchronized appends.
LabDAQ must identify the last trustworthy extent without rewriting raw data or
claiming sudden-power-loss durability beyond what was actually synchronized.

## Decision

Manifest checkpoints are validated, written to `manifest.json.tmp`, flushed and
synced, then installed with `os.replace`. TDMS and JSONL streams are flushed and
synced at one-second checkpoints and terminal transitions. An exclusive
`.recording.lock` records owner host and process ID; a configurable 1 MiB
`.metadata-reserve` is released after `ENOSPC` so a fault manifest can be
attempted.

Logical digest version 1 is SHA-256 over the fixed prefix
`LABDAQ-RAW-F64LE-INTERLEAVED-V1\0` followed by sample-frame-major,
little-endian float64 bytes. It is independent of acquisition or replay chunk
boundaries. Terminal sessions additionally store bounded-memory SHA-256 checksums
for TDMS, events, and recipe files.

Recovery is a separate mutating API. It refuses a live writer, accepts only a
readable complete synchronized prefix, records the prior state and checkpoint,
preserves raw and event bytes unchanged, atomically marks the session
`INTERRUPTED`, and only then removes the stale lock. A malformed intermediate
event is corruption; only a truncated final JSONL line may be ignored with a
warning. Untrustworthy synchronization is `CORRUPT` and is never replayed.

## Consequences

- Append receipts mean that the writer accepted a complete segment; survival of
  power loss is guaranteed only through the latest synced checkpoint.
- Interrupted sessions are never repaired through padding, truncation,
  interpolation, or raw-file replacement.
- Recovery may reject damaged data even when some values appear readable; this
  fail-closed behavior protects scientific traceability.

