# Session Verification and Recovery

Inspection is read-only. Recovery is explicit and mutates metadata only.

Fast inspection reads bounded metadata: required artifacts, manifest and recipe
schemas, lock ownership, format-specific layout/properties, counts, and the
common readable extent. Full verification additionally streams all raw data
to recompute the logical digest and file checksums, validates every event and its
sequence, and compares duplicated manifest/TDMS fields.

Integrity is `PASS`, `WARNING`, or `FAIL`. Replay requires full verification and
either `REPLAYABLE` or `REPLAYABLE_PREFIX`. A terminal checksum, count, schema,
metadata, or channel-length failure is fail-closed and reported as `CORRUPT`.

## Interrupted session procedure

1. Stop the application that created the session. Never remove a lock belonging
   to a live process.
2. Inspect the session. A stale lock plus a nonterminal manifest and at least one
   contiguous HDF5 journal commit (or complete synchronized legacy TDMS extent)
   is a recovery candidate.
3. Call the application `SessionRecoveryService.recover(path)` API. There is no
   implicit CLI repair command in M05.
4. Recovery verifies the committed prefix, scans events, records the old
   state/checkpoint and new digest/checksums, atomically writes `INTERRUPTED`, and
   removes the stale lock only after that write succeeds.
5. Run full summary verification before replay.

Raw HDF5/TDMS and event files are never padded, interpolated, reordered,
truncated, or overwritten. An HDF5 dataset tail beyond the contiguous commit
journal is ignored only for nonterminal recovery; the same disagreement in an
unrecovered terminal session is corruption. Only an incomplete final JSONL line
may be ignored, and that produces a warning. An empty session, active owner,
unknown schema, malformed intermediate event, or untrustworthy synchronized
extent is refused.

## Troubleshooting

- `ACTIVE_WRITER_DETECTED`: stop the owning application; do not recover.
- `RUNTIME_SPACE_THRESHOLD` or `DISK_FULL`: free space, preserve the directory,
  and inspect the last acknowledged count.
- `CHECKSUM_MISMATCH`, `CHANNEL_LENGTH_MISMATCH`, or `METADATA_MISMATCH`: treat
  the session as corrupt; do not copy values into a replacement source record.
- `EVENT_FINAL_LINE_TRUNCATED`: recovery may retain the synchronized raw prefix;
  the missing final event remains an explicit warning.
- `HDF5_UNCOMMITTED_TAIL`: recover a nonterminal session explicitly; do not
  truncate or edit `raw.h5`. A terminal occurrence is not replayable.
- `HDF5_COMMIT_INVALID`: preserve the session as corrupt; journal continuity is
  the authority and must not be guessed or repaired.
