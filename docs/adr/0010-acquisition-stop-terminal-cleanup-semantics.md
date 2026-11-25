# ADR-0010: Acquisition Stop, Terminal, and Cleanup Semantics

Status: Accepted

## Context

The DAQ port performs blocking reads, but operator and future GUI stop commands
must return promptly. Concurrent stop, duration completion, hardware failure,
queue pressure, and cleanup can otherwise create duplicate terminal events,
lost final samples, or an orphaned NI task.

## Decision

Application commands serialize state changes under one lock. The first valid
stop request moves `RUNNING` to `STOPPING`, records the sample cursor, and asks
the adapter to cancel its pending read. Repeated requests are idempotent. The
simulator uses an interruptible wait; the NI boundary maps cancellation to
`TaskMode.TASK_ABORT` without exposing NI types through the port.

A valid partial read that becomes available after the request remains eligible
as one final continuous chunk. A configured stop timeout supervises cooperative
cancellation. On expiry, the engine records `STOP_TIMEOUT` and forces adapter
close to release resources.

One worker owns terminalization. It attempts cleanup on every path, closes
streams only after final publication, emits exactly one terminal event, and
publishes one immutable completion record. Cleanup failures remain secondary
context when an acquisition fault already exists; they become the primary
fault only when no earlier failure exists. Terminal engines require reset
before reconfiguration.

## Consequences

Stop requests remain nonblocking and future GUI-safe. Forced close can preserve
resource safety but changes an otherwise manual stop into a typed fault.
Adapter cancellation is now part of the shared DAQ contract and must be tested
by later adapters. Physical NI cancellation behavior requires opt-in hardware
acceptance and is not inferred from simulator or fake-driver results.
