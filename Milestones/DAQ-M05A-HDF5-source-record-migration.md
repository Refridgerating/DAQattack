# DAQ-M05A: HDF5 Source-Record Migration

Status: Implementation and target-disk simulator soak complete; physical NI verification pending

## Goal

Make schema-v2 `raw.h5` the sole source container for new recordings while
preserving all schema-v1 TDMS review workflows and providing verified,
on-demand TDMS interoperability export.

## Implemented scope

- HDF5 layout v1 and manifest schema v2 with format-neutral `raw_container`
- SWMR writer, contiguous commit journal, terminal journal record, durable
  checkpoints, exact logical digest, terminal file checksums, and prefix recovery
- Local-disk-only live recording and format-dispatching summary/replay/processing
- Read-only schema-v1 TDMS support without source rewriting
- Format-neutral `RawContainerSummary` and HDF5 overhead policy
- CLI and background GUI TDMS export with provenance, cancellation, progress,
  collision rejection, atomic installation, and temporary cleanup
- Optional-dependency isolation for `h5py` and `npTDMS`

## Acceptance state

Automated simulator round trip, rechunking, partial chunks, continuity,
lifecycle, checksums, metadata, bounded replay, disk policy, Unicode paths,
dependency isolation, phase faults, abrupt restart recovery, legacy TDMS
summary/replay/processing/plotting/export, and exact TDMS export are covered by
the repository test suites.

The target-disk four-channel 50 kS/s ten-minute real-time soak passed on the
workspace Windows fixed disk. A physical cDAQ-9171/NI-9239 HDF5
record/reopen/replay is not verified because no hardware evidence was available.
Existing TDMS sessions are preserved in place; bulk migration is outside this
milestone.
