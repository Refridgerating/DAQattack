# ADR-0011: Version-1 Session Format and Synchronized Writer

Status: Accepted

## Context

LabDAQ must preserve one authoritative, synchronized four-channel source record.
Independent per-channel files cannot prove equal extent or common sample identity,
and neither a TDMS file alone nor free-form logs carry the complete operator,
configuration, calibration, and fault provenance required to interpret a run.

## Decision

Version 1 uses a self-contained directory containing `raw.tdms`, `manifest.json`,
`events.jsonl`, an immutable `processing-recipe.json`, and stable empty directories
for later derived products. The TDMS file has one `/raw` group whose channels are
named and ordered by stable logical channel IDs.

One writer owns the directory and appends every acquisition chunk as one
all-channel TDMS segment. It rejects session, sequence, sample-index, channel
order, sample-rate, shape, and dtype changes before writing. Persisted counts
advance only after `TdmsWriter.write_segment` returns successfully. Raw values
remain uncalibrated float64 voltage samples.

The manifest and TDMS deliberately duplicate critical identity, channel order,
rate, units, sensor, calibration, and hardware properties. Full verification
compares the duplicated fields. npTDMS remains an optional, adapter-local
dependency.

## Consequences

- Every accepted append has one synchronized extent and receipt.
- Sessions remain interpretable without the GUI or connected hardware.
- File overhead is higher than independent binary arrays, and verification must
  maintain explicit redundancy checks.
- V1 readers reject unknown schemas or layouts instead of guessing.

