# ADR-0020: External Qualification Confirmations

Status: Accepted

## Context

Software cannot observe wiring inspection, a safe applied stimulus, charge
conditioning, or fixture setup. Interactive and automated operation need the
same explicit, auditable boundary without allowing silence to mean success.

## Decision

Operator-assisted and external-fixture tests declare stable confirmation IDs.
Human CLI mode prompts through an operator-prompt port. JSON mode never prompts
and consumes a schema-versioned confirmation file. A confirmation records its
ID, Boolean assertion, aware timestamp, operator, optional note, and optional
source-relative sample index.

Missing or false confirmation makes the affected test `not-tested`. A
confirmation proves only that the operator or fixture made the declared
assertion; it does not replace numeric evidence or resolve a missing limit.
Simulator confirmations are labeled synthetic and never count as physical
hardware verification.

## Consequences

- Automated jobs are deterministic and cannot block on stdin.
- Report readers can separate measured results from human or fixture claims.
- External assumptions remain visible after copying a report to an experiment.
- Future GUI prompts can implement the same port without owning qualification
  policy.
