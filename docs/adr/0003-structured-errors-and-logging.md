# ADR-0003: Structured Errors and Logging

Status: Accepted

## Context

Laboratory faults must be actionable and machine-readable. Free-form console
messages and import-time global logging would make failures hard to diagnose and
would interfere with embedding and GUI behavior.

## Decision

All application errors derive from `LabDAQError`, use
`DAQ.<SUBSYSTEM>.<CONDITION>` codes, preserve immutable safe context, and
serialize stable fields. Causes remain available for debugging but are excluded
from normal serialization. Entry points explicitly configure the `labdaq`
logger through an idempotent function; package imports never configure logging.
Operational context is bounded and sensitive fields are redacted.

## Consequences

CLI and future GUI surfaces can render the same failures consistently.
Callers must provide stable event codes and remediation where known. Logs remain
operational evidence and never replace the scientific measurement record.
