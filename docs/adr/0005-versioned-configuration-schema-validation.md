# ADR-0005: Versioned Configuration Schema Validation

Status: Accepted

## Context

DAQ-M01 requires installed CLI commands to validate versioned hardware,
experiment, and recipe JSON documents against JSON Schema Draft 2020-12 before
constructing domain records. It also requires semantic validation because
structurally valid documents can remain scientifically contradictory.

ADR-0002 established a dependency-free M00 core while isolating large,
platform-specific optional dependencies. Implementing a standards-compliant
schema engine inside LabDAQ would create a second, incomplete validator and a
larger maintenance risk than one small platform-independent dependency.

## Decision

Use `jsonschema` as a normal runtime dependency for configuration document
validation. Support schema version 1 only and reject missing, malformed,
nonpositive, and future versions before partial domain construction.

JSON Schema owns document structure and wire types. Frozen domain records and
pure cross-record validators own scientific and relational rules. The JSON
adapter owns file I/O and schema-library calls; domain and application modules
do not import `jsonschema`.

This decision supersedes only ADR-0002's statement that the core distribution
has no runtime dependencies. NI-DAQmx, Qt, PyQtGraph, TDMS, NumPy, SciPy, and
release tooling remain isolated optional dependencies.

## Consequences

The base installation can run every M01 validation command without an extra.
Schema behavior follows a maintained Draft 2020-12 implementation, while
application validation remains hardware-free and deterministic. The core
installation now includes `jsonschema` and its small transitive dependencies.
