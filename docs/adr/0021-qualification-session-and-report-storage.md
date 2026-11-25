# ADR-0021: Qualification Session and Report Storage

Status: Accepted

## Context

Qualification must exercise the production acquisition and storage path, retain
raw evidence, survive interruption, and be attachable to a later experiment
without mutating either source record.

## Decision

Every data-bearing run uses one `AcquisitionEngine`, one
`RecordingSessionService`, and one synchronized TDMS writer. Its manifest uses
`session_type: hardware-qualification`. `qualification-plan.json` is created
exclusively; `qualification-report.json` is atomically replaced after each test.
The report links to retained `raw.tdms` and `events.jsonl` evidence.

Explicit recovery first applies the M05 recovered-session rules, then combines
that readable prefix with the last valid report checkpoint. Completed results
are preserved and unfinished tests become `not-tested`; source raw and event
bytes are not rewritten.

A later experiment copies the exact validated report bytes during exclusive
session creation. Its manifest records report UUID, SHA-256, fingerprint,
status, age, eligibility, and advisory override. A completed source session is
never edited in place.

## Consequences

- Qualification load is representative of production recording.
- A report remains connected to immutable raw evidence and exact sample counts.
- Recovery is explicit and conservative rather than an implicit pass.
- Experiment attachment is self-contained and byte-verifiable.
