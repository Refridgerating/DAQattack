# Durable Session Format

Status: schema-v2 HDF5 writes and schema-v1 TDMS reads implemented

Every acquisition owns one collision-resistant directory:

```text
YYYY-MM-DD_HHMMSS_experiment-slug_1234abcd/
|-- manifest.json
|-- raw.h5
|-- events.jsonl
|-- processing-recipe.json
|-- processed/
|-- plots/
`-- exports/
```

The timestamp is UTC and the final component is the first eight hexadecimal
characters of a preallocated session UUID. Preview and creation use that same
UUID. The selected parent must already exist and be writable; creation is
exclusive and no collision is renamed, reused, or overwritten. A planned path
at least 240 characters long carries a warning.

During recording, `.recording.lock` identifies the owning host and process,
`.metadata-reserve` reserves 1 MiB by default for fault metadata, and
`manifest.json.tmp` may briefly exist during atomic replacement. Normal
finalization removes all three. The source record is `raw.h5` plus the initial
and checkpointed manifest. Events and the raw-only recipe are immutable
provenance; `processed`, `plots`, and `exports` are empty M05 targets for later
milestones.

M08 adds an optional `session.session_type`. Existing documents default
semantically to `experiment`; qualification runs write
`hardware-qualification`. Such sessions additionally contain the exclusively
created `qualification-plan.json` and atomically checkpointed
`qualification-report.json`.

An experiment created with validated qualification evidence contains an exact
byte copy of `qualification-report.json`. Its manifest has a top-level optional
`qualification` object recording report UUID, SHA-256, fingerprint, status,
age, eligibility, and advisory override. This is performed during exclusive
session creation; a completed source session is never rewritten.

M10A open-acquisition Record/Stop cycles each create an independent
`raw-diagnostic` schema-v2 directory containing the ordered enabled raw-voltage
channels. The optional `open_acquisition` manifest section maps the segment's
local zero-based indices back to the continuous full-task source stream and stores
the authoritative `recorded_channel_ids` and aligned `recorded_channel_units`.
The legacy `selected_channel_id` is the first/primary channel for compatibility
with historical single-channel segments.
Stopping a segment finalizes its source record without fabricating DAQ cleanup
metadata, because the shared monitor task remains active.

## Lifecycle and counts

The stored states are `INITIALIZING`, `RECORDING`, `FINALIZING`, `COMPLETED`,
`STOPPED`, `FAULTED`, `INTERRUPTED`, and `CORRUPT`. State and reason are separate:
duration completion is `COMPLETED/DURATION_REACHED`, a clean operator stop is
`STOPPED/MANUAL_STOP`, and acquisition or storage failure is `FAULTED` unless
synchronization is untrustworthy, in which case it is `CORRUPT`.

Persisted count changes only after one complete all-channel HDF5 data append and
commit-journal append return. Recording-accepted and acquired counts remain
distinct. Sample index is authoritative: sample `n` occurs at
`n / actual_sample_rate_hz`. No per-sample wall timestamps are synthesized.

## Durability and capacity

The default policy uses a format-neutral 10% HDF5 overhead estimate, a 16 MiB metadata/event allowance, a
1 GiB warning threshold, a 512 MiB hard-stop reserve, and one-second capacity and
checkpoint intervals. Fixed runs must pass the data estimate plus reserves;
continuous runs must start above the warning reserve. `ENOSPC` releases the
metadata reserve and attempts a fault manifest. A successful append receipt is
not a power-loss guarantee: sudden-loss durability extends through the latest
stream flush, OS sync, and atomic manifest checkpoint.

The raw logical digest is version 1 SHA-256 over the fixed prefix
`LABDAQ-RAW-F64LE-INTERLEAVED-V1\0` and then sample-frame-major little-endian
float64 bytes. The definition is independent of write and replay chunk sizes.
Finalization also computes bounded-memory SHA-256 checksums for `raw.h5`,
`events.jsonl`, and `processing-recipe.json` unless disabled by policy.

See the [manifest](manifest-schema.md), [HDF5 layout](hdf5-layout.md), legacy
[TDMS layout](tdms-layout.md), and [recovery guide](recovery.md).

Schema-v1 directories retain `raw.tdms` as their source record and are never
rewritten during inspection, replay, processing, plotting, or export. The
manifest selects the adapter. A TDMS interoperability artifact for either
source format is created only on demand under `exports/`.
