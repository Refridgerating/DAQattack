# HDF5 Raw Layout Version 1

Schema-v2 sessions store unconditioned voltage in `raw.h5`. The file is opened
with `libver="latest"`; every group, dataset, datatype, and fixed metadata value
is created before SWMR writer mode is enabled.

## Datasets

| Path | Shape | Purpose |
|---|---|---|
| `/raw/samples` | `[channels, samples]`, sample axis unlimited | little-endian float64 source samples |
| `/raw/channel_ids` | `[channels]` | ordered UTF-8 logical IDs |
| `/raw/channel_units` | `[channels]` | ordered raw units, normally `V` |
| `/journal/commits` | unlimited records | sequence, start, count, committed stop |
| `/journal/finalization` | at most one record | terminal state, reason, acquired and persisted counts |

`/raw/samples` is chunked as `[channel_count, configured_acquisition_chunk]`,
uses Fletcher32, and is not compressed. Its dimension labels are `channel` and
`sample_index`. Sample index is authoritative; no redundant time vector is
stored. Root attributes and `/channels/000`, `/channels/001`, ... duplicate the
identity, rate, hardware, sensor, calibration, units, and ordered-channel
metadata needed to interpret the file independently. Full verification compares
this duplication with the manifest.

## Commit order

For each acquisition chunk the writer:

1. resizes and writes `/raw/samples`;
2. flushes that dataset;
3. appends the next contiguous commit record;
4. flushes the commit dataset; and
5. returns the persistence receipt.

Periodic checkpoints additionally flush the HDF5 file, perform an OS durable
sync, and atomically advance the manifest. The logical digest is unchanged from
schema v1: sample-frame-major little-endian float64 bytes prefixed with
`LABDAQ-RAW-F64LE-INTERLEAVED-V1\0`.
