# ADR-0004: Supported Platform and CI Acceptance

Status: Accepted

## Context

LabDAQ is a Windows-targeted laboratory application whose physical DAQ adapter
will use Windows NI-DAQmx drivers. DAQ-M00 originally treated hosted Windows and
Ubuntu CI as co-equal blocking acceptance criteria, even though the repository
did not yet have a GitHub remote and Linux was not a product requirement.

The complete M00 suite has passed locally on Windows with CPython 3.11.15 and
only core plus development dependencies. Making repository hosting or an
advisory platform block that evidence would not improve the Windows laboratory
product.

## Decision

Windows with CPython 3.11 is the supported v1 product platform. A complete
passing local Windows verification suite can satisfy a milestone before a
GitHub remote exists.

Hosted Windows CI is expected once a remote exists and is recorded as external
verification, but its prior absence does not block acceptance. The Ubuntu job
is retained as an advisory portability check with `continue-on-error`; it runs
the same workload but cannot fail the workflow or imply Linux product support.

Linux becomes supported only through a later project-charter revision and
corresponding acceptance criteria.

## Consequences

Milestone decisions follow the actual supported laboratory platform. Windows
regressions remain blocking when hosted CI becomes available. Ubuntu failures
remain visible for portability improvements but do not delay Windows work.
DAQ-M00 may be accepted from its recorded local evidence, and DAQ-M01 may
proceed.
