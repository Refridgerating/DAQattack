# LabDAQ Project Charter

Status: revised raw-first direction; implementation pending

## Mission

Build a dependable, reusable, offline-capable desktop data-acquisition
application for laboratory users. LabDAQ opens configured supported DAQ channels
and displays raw voltages. The operator may optionally record synchronized raw
measurements with enough metadata to interpret them. Qualification, calibration,
and derived processing are optional capabilities, not prerequisites for raw use.

## Initial system

- NI cDAQ-9171 chassis
- NI-9239 analog input module
- Three TE 830M1-0200 accelerometer channels
- One externally conditioned charge-converter channel
- Four simultaneous channels at up to 50 kS/s per channel

## Supported platform

The v1 supported product platform is Windows with CPython 3.11 and Windows
NI-DAQmx drivers. Hosted Windows CI is expected once the repository has a
GitHub remote, but a complete passing local Windows acceptance suite is
sufficient for a milestone whose criteria do not require external hardware.

Ubuntu CI is retained as an inexpensive advisory portability check. Linux is
not a supported product platform and its CI result is non-blocking unless a
later charter revision explicitly adds Linux support.

External wiring, grounding, sensor power, and charge-converter sensitivity or
polarity require authoritative physical validation. Software configuration
does not establish electrical safety.

## Priority order

1. Raw-data integrity
2. Correct sample identity and ordering
3. Explicit faults and recoverable state
4. Measurement provenance
5. Deterministic reproducibility
6. Hardware and storage isolation
7. Operator usability
8. Performance optimization
9. Visual polish

## Non-negotiable rules

- The GUI is a client of application services.
- Core packages do not import NI or Qt libraries.
- Raw measurements are immutable.
- Sample index is authoritative relative time.
- Hardware access occurs through ports implemented by adapters.
- One acquisition stream is authoritative.
- Recording loss faults explicitly; display loss is separate and disposable.
- Interrupted runs remain identifiable and derived artifacts retain provenance.
- Live processing is causal.
- Imports do not access hardware, create files, configure logging, or launch UI.
- Simulator evidence is never represented as physical validation.
- Essential laboratory workflows do not require internet access.

## Milestone discipline

Milestones organize work without preventing requested development across boundaries.
A milestone is complete only
when its acceptance evidence is recorded. Pending external verification and
physical criteria remain visible, but block completion only when the active
milestone explicitly makes them acceptance requirements.
