# ADR-0017: Headless Rendering and Atomic Derived Artifacts

Status: Accepted

## Context

M07 must create shareable files without Qt, NI access, source mutation, partial
artifact pairs, implicit overwrite, or hidden provenance. CSV data may be much
larger than memory and figure dependencies must remain optional.

## Decision

Application services depend on replay, scientific-processing, renderer,
serializer, and artifact-storage ports. The Matplotlib adapter imports lazily
and uses only noninteractive PNG and SVG canvases. Matplotlib belongs to the
bounded `export` extra; absence becomes `DAQ.DEPENDENCY.UNAVAILABLE`.

CSV is written incrementally as UTF-8 comma-separated rows with `.17g` floats
and lowercase `nan`. Time data uses sample index and relative seconds; spectral
data uses frequency. Incompatible frequency grids create separately named
artifacts without interpolation.

Each primary file has a version-1 JSON metadata sidecar. Each job has a
version-1 manifest containing completed pairs, checksums, requests, progress,
warnings, faults, timestamps, and terminal state. Documents are schema-validated
before installation. Same-directory temporary files are flushed and synced,
checksummed, and installed through an exclusive hard-link operation. Existing
paths are rejected, and no overwrite option exists in version 1.

## Consequences

- The source TDMS, manifest, event log, and recipe snapshot remain unchanged.
- A primary and sidecar are one artifact; current-pair failure removes both.
- Completed earlier requests survive later failure and remain listed in a
  faulted or cancelled job manifest.
- Destination filesystems must support exclusive hard-link installation.
- Static export works headlessly; GUI and live plotting remain later milestones.
