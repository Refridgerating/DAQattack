# ADR-0018: Qualification Fingerprint and Start Eligibility

> The requirement to use report eligibility as a prerequisite for raw acquisition is superseded by [ADR-0033](0033-raw-first-monitoring-and-optional-diagnostics.md). The fingerprint and eligibility semantics may continue to describe optional qualification reports; existing code may still enforce them until DAQ-M12 is implemented.

Status: Accepted

## Context

A qualification result is meaningful only for the plan, scientific
configuration, hardware identity, acquisition settings, and storage target that
were actually tested. Operator-facing labels and notes must not invalidate
otherwise identical scientific evidence. A warning may be acceptable, but a
stale identity or a missing blocking test must never be hidden by an override.

## Decision

Version-1 reports store both the canonical fingerprint input and its SHA-256
hash. The input contains the exact plan and profile hashes, acquisition rate and
duration, read and recording-buffer sizes, normalized storage location, ordered
logical and physical channels, input ranges, sensor/calibration/conditioning
identities, and normalized live chassis/module identity. Experiment session
presentation fields are excluded.

Eligibility is a separate immutable decision evaluated at a stated time. A
report is eligible only when it is terminal, its fingerprint equals the current
fingerprint, its age is within the plan policy, every required blocking test ran,
and its result is `pass` or a nonblocking `warning`. An override may acknowledge
only an advisory reason code declared by that plan; it cannot bypass identity,
age, required tests, safety, clipping, continuity, equal-length, backpressure,
or storage findings.

Offline checking without current hardware identity can verify document and
configuration compatibility, but it remains ineligible.

## Consequences

- Changing any scientifically relevant input invalidates earlier evidence.
- Operator notes, display names, and experiment descriptions do not create
  spurious invalidation.
- Reports remain independently auditable because fingerprint inputs are stored,
  not merely the resulting digest.
- DAQ-M09 can consume one backend-owned decision instead of recreating policy in
  the GUI.
