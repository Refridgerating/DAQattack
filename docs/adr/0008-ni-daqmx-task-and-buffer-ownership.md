# ADR-0008: NI-DAQmx Task and Buffer Ownership

Status: Accepted

## Context

DAQ-M03 must use a platform-specific NI driver without exposing its objects,
silently coercing timing, leaking reserved tasks, or returning arrays that a
later read can mutate.

## Decision

Keep `nidaqmx` in the optional `ni` extra and load it only after explicit NI
backend selection. The NI adapter exclusively owns one multi-channel task,
adds channels in declared experiment order, verifies and commits timing, and
records requested and driver-reported rates separately. The baseline rate
tolerance is exactly zero.

The adapter uses a private, writable, C-contiguous `float64` buffer whose exact
shape matches each requested read. Every successful result is copied into an
owned, read-only array. Stop, timeout, failure, context exit, and close all
attempt deterministic task release. Cleanup failures remain secondary context
when another operation already failed. Driver warnings become structured DAQ
events.

Local aliases address devices but do not define identity. Discovery preserves
raw model, serial, category, and physical slot while exposing canonical model
and zero-based logical slot values to the hardware-independent port.

## Consequences

Core and simulator workflows remain usable without NI software. NI objects and
exceptions cannot cross the adapter boundary. Buffer resizing can occur when
callers vary read size, accepted in exchange for guaranteed C-contiguous driver
input and non-aliasing public results. Physical reset remains an explicit call
and is never part of normal cleanup.

