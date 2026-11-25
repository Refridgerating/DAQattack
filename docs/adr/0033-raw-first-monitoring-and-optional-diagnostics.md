# ADR-0033: Raw-First Monitoring and Optional Diagnostics

Status: Accepted

## Context

Qualification reports, sensor metadata, and commissioning confirmations became
start gates for observing raw input. This makes routine DAQ interface development
and exploratory monitoring depend on extensive experiment-specific evidence.
The primary product is a raw DAQ interface with optional recording.

## Decision

Offer selected valid channels as raw voltage in a bounded live monitor, using one
hardware stream. Recording is operator-selected and uses the existing durable
session path. A usable backend and valid device, channel, rate, and input settings
are required; qualification, certification, calibration information, report
eligibility, and external confirmation checklists are not start prerequisites.
Diagnostic reports remain optional evidence and do not establish electrical safety.
No sensor calibration or conditioner properties are inferred from raw input.

Keep acquisition and storage faults visible, distinguish display drops from raw
loss, and preserve existing session/report compatibility. Actual wiring and input
compatibility remain the operator's responsibility. Simulator results do not
establish physical performance.

This supersedes the ordinary-acquisition gating portions of ADR-0018 and the
commissioning/authorization prerequisites in ADRs 0024, 0025, and 0026.
DAQ-M12 implements this direction for raw monitoring and recording. Other decisions, including single-stream acquisition, raw data
integrity, and recording durability, remain applicable.

## Consequences

The common workflow has fewer prerequisites and supports observation before
sensor characterization. Optional qualification evidence remains meaningful
only for the conditions actually tested. The application must avoid presenting
raw measurements as calibrated or physically certified. Hardware validation
and release campaigns may be performed when useful without blocking development.

## Raw channel provenance

A raw-only channel may have no known sensor identity, calibration, or conditioning
chain. Its schema-v2 manifest snapshot stores `sensor: null`; existing snapshots
with sensor records remain readable. Export metadata carries unknown sensor values
as null, and TDMS exports omit unavailable sensor properties. This is an additive
extension of the existing manifest and export-sidecar schemas, not a new raw data
container. A sensorless channel remains raw voltage and cannot claim an engineering
interpretation.
