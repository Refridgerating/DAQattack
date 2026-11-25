# ADR-0027: HDF5 Source Record and On-Demand TDMS Export

Status: Accepted

## Context

ADR-0011 selected TDMS for schema-v1 sessions. LabDAQ now needs a raw container
that is broadly usable by scientific Python and other HDF5 tooling, supports
extensible chunked arrays, and can expose a crash-valid committed prefix while
recording. Writing HDF5 and TDMS in parallel would double the live storage path
and create two candidates for the authoritative record.

## Decision

This decision supersedes ADR-0011 for newly recorded sessions. Schema-v2
sessions use `raw.h5` as their sole authoritative raw container. The manifest,
JSONL events, immutable recipe, and derived-product directories remain
sidecars. Schema-v1 `raw.tdms` sessions remain read-only compatible and are not
migrated in place.

HDF5 layout v1 stores little-endian float64 voltage at `/raw/samples` with axes
`[channel, sample_index]`, an unlimited sample axis, acquisition-sized chunks,
Fletcher32, and no compression. All objects and fixed metadata are created
before SWMR writing is enabled. `/journal/commits` establishes the only trusted
readable prefix; data is flushed before its commit record. A terminal record is
stored in `/journal/finalization`. Checkpoints flush HDF5, request an OS-level
durable sync, and only then atomically advance `manifest.json`.

New recording is rejected on UNC and Windows remote-drive destinations. A
fully verified session may be exported afterward to an unconditioned,
full-extent TDMS interoperability artifact. That export has no channel, range,
calibration, or filtering options, never modifies its source, rejects
collisions, and installs both TDMS and provenance sidecar atomically from
temporary files.

`h5py` is the storage dependency. `npTDMS` remains optional for schema-v1 reads
and TDMS export; laboratory release builds include both.

## Consequences

- Live recording has one source of truth and one persistence receipt path.
- A nonterminal file may contain an uncommitted physical tail; recovery reads
  the contiguous journal prefix without truncating or rewriting `raw.h5`.
- The same tail in an unrecovered terminal session is corruption.
- Logical SHA-256 remains chunk-independent and format-independent; terminal
  file checksums now use the `raw.h5` key for schema-v2 sessions.
- Format dispatch remains in adapters, while application and port contracts
  stay independent of both `h5py` and `npTDMS`.
