# ADR-0019: Qualification Threshold Provenance

Status: Accepted

## Context

Generic electrical sanity limits are useful, but laboratory bias, noise,
response, and crosstalk limits require authoritative sensor, conditioner, or
fixture evidence. Treating a missing limit as a permissive default would create
scientifically false passes.

## Decision

Every version-1 qualification limit records its operator, value or unresolved
state, unit, signal stage, consequence, source category, source reference, and
rationale. Limits with no authoritative value use source category `unresolved`
and a null value. Evaluating an unresolved limit produces `unevaluated`; the
owning test becomes `warning` or `not-tested` according to its declared
classification, never `pass` because of that limit.

The bundled engineering policy warns above 80 percent raw range utilization
and fails above 98 percent. Clipping, nonfinite samples, source-index defects,
recording overflow, and unequal channel counts have zero tolerance. The bundled
plans intentionally do not invent accelerometer bias/noise, response,
charge-chain noise, or crosstalk limits.

## Consequences

- Reports distinguish measured values from acceptance knowledge.
- Lab-specific limits can be reviewed and versioned without changing numerical
  implementations.
- A healthy simulator can demonstrate the software path but cannot establish a
  physical metrology baseline.
- Physical start eligibility stays pending until required evidence and limits
  are supplied.
