# ADR-0001: Package and Layer Boundaries

Status: Accepted

## Context

The legacy prototype couples NI access, acquisition, text conversion, storage,
bookkeeping, and plotting. LabDAQ must evolve without moving those concerns
into a new monolith.

## Decision

Use ports and adapters with dependency direction from presentation to
application, then domain and ports, with adapters implementing ports.
`labdaq.bootstrap` is the only normal production composition root. Root package
imports expose metadata only. Import Linter and subprocess architecture tests
enforce the highest-value boundaries.

## Consequences

Later milestones must introduce contracts only when required. Concrete
dependencies remain replaceable and headless services remain testable. There is
additional boundary-test maintenance, accepted in exchange for preventing
architectural drift.
