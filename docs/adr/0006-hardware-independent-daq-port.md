# ADR-0006: Hardware-Independent DAQ Port

Status: Accepted

## Context

The simulator and the later NI-DAQmx implementation must support the same
application workflows without exposing driver objects or creating competing
acquisition APIs. M02 also needs a local adapter lifecycle without prematurely
implementing the M04 experiment state machine.

## Decision

Define one `DaqPort` in the ports layer for discovery, validation,
configuration, start, bounded reads, stop, health, reset, and deterministic
close. Runtime records contain primitive/domain values and owned NumPy arrays,
never NI types. Application services depend on this port and concrete adapters
are selected only in `labdaq.bootstrap`.

The adapter lifecycle is limited to idle, configured, running, stopped,
faulted, and closed. Session identity, authoritative chunk sequencing,
continuity accounting, armed/stopping/completed states, queues, and fan-out
remain deferred to M04.

## Consequences

M03 must implement and reuse the applicable DAQ-port contract tests rather than
introducing an NI-specific public API. NumPy is loaded only when DAQ runtime
records or simulation are requested. Adapter cleanup can be tested before
acquisition orchestration exists.
