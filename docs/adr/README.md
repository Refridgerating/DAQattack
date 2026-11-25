# Architectural Decision Records

ADRs capture durable decisions that later contributors might otherwise reverse.

- [ADR-0033: Raw-First Monitoring and Optional Diagnostics](0033-raw-first-monitoring-and-optional-diagnostics.md) - Accepted direction; implementation pending
- [ADR-0032: Raw-voltage spectra in session review](0032-raw-voltage-spectra-in-session-review.md) — Accepted

## Naming and status

Files use four-digit sequence numbers:

```text
0001-short-decision-name.md
```

Each ADR begins with a title and exactly one status: `Proposed`, `Accepted`,
`Superseded`, or `Rejected`. Accepted records are immutable except for typo and
link corrections. A new ADR supersedes an accepted decision.

## Required sections

- Context
- Decision
- Consequences

## Index

- [ADR-0031: NI-9239 Variable Internal-Clock Rates](0031-ni-9239-variable-internal-rates.md) - Accepted
- [ADR-0030: Internal Feedback-Preview Packaging](0030-internal-feedback-preview-packaging.md) - Accepted
- [ADR-0029: Detachable Open-Acquisition Recording and Source-Index Rebasing](0029-detachable-open-acquisition-recording.md) - Accepted
- [ADR-0028: Non-Destructive Review Ranges and Transient Detection](0028-non-destructive-review-ranges-and-transient-detection.md) - Accepted
- [ADR-0027: HDF5 Source Record and On-Demand TDMS Export](0027-hdf5-source-record-and-tdms-export.md) - Accepted
- [ADR-0026: Raw Diagnostic Launch Authorization](0026-raw-diagnostic-launch-authorization.md) - Accepted
- [ADR-0024: Accelerometer Commissioning Mode and Production Cutover](0024-accelerometer-commissioning-mode.md) - Accepted
- [ADR-0025: Raw-only ai3 Commissioning Diagnostic](0025-raw-only-ai3-diagnostic.md) - Accepted
- [ADR-0023: Live Operator Buffering and Disk-Backed Review](0023-live-operator-buffering-and-disk-review.md) - Accepted
- [ADR-0022: Desktop Presentation, Background Work, and Preferences](0022-desktop-presentation-background-preferences.md) - Accepted
- [ADR-0021: Qualification Session and Report Storage](0021-qualification-session-and-report-storage.md) - Accepted
- [ADR-0020: External Qualification Confirmations](0020-external-qualification-confirmations.md) - Accepted
- [ADR-0019: Qualification Threshold Provenance](0019-qualification-threshold-provenance.md) - Accepted
- [ADR-0018: Qualification Fingerprint and Start Eligibility](0018-qualification-fingerprint-and-start-eligibility.md) - Accepted
- [ADR-0017: Headless Rendering and Atomic Derived Artifacts](0017-headless-rendering-and-atomic-derived-artifacts.md) - Accepted
- [ADR-0016: Half-Open Ranges and Bounded Min/Max Decimation](0016-half-open-ranges-and-bounded-min-max-decimation.md) - Accepted
- [ADR-0015: Causal Baseline Processing and Spectral Conventions](0015-causal-baseline-processing.md) - Accepted
- [ADR-0014: Versioned Calibration, Recipes, and Canonical Hashes](0014-versioned-calibration-recipe-hashes.md) - Accepted
- [ADR-0013: Common Live and Replay Chunk Contract](0013-common-live-replay-chunk-contract.md) - Accepted
- [ADR-0012: Atomic Checkpoints, Digests, and Explicit Recovery](0012-atomic-checkpoint-digest-recovery.md) - Accepted
- [ADR-0011: Version-1 Session Format and Synchronized Writer](0011-v1-session-format-synchronized-writer.md) - Accepted
- [ADR-0010: Acquisition Stop, Terminal State, and Cleanup Semantics](0010-acquisition-stop-terminal-cleanup-semantics.md) - Accepted
- [ADR-0009: Single-Read, Dual-Consumer Fan-Out](0009-single-read-dual-consumer-fan-out.md) - Accepted

- [ADR-0008: NI-DAQmx Task and Buffer Ownership](0008-ni-daqmx-task-and-buffer-ownership.md) — Accepted

- [ADR-0006: Hardware-Independent DAQ Port](0006-hardware-independent-daq-port.md) — Accepted
- [ADR-0007: Deterministic Simulator Semantics](0007-deterministic-simulator-semantics.md) — Accepted

- [ADR-0001: Package and Layer Boundaries](0001-package-and-layer-boundaries.md) — Accepted
- [ADR-0002: Optional Dependency Isolation](0002-optional-dependency-isolation.md) — Accepted
- [ADR-0003: Structured Errors and Logging](0003-structured-errors-and-logging.md) — Accepted
- [ADR-0004: Supported Platform and CI Acceptance](0004-supported-platform-and-ci-acceptance.md) — Accepted
- [ADR-0005: Versioned Configuration Schema Validation](0005-versioned-configuration-schema-validation.md) — Accepted
