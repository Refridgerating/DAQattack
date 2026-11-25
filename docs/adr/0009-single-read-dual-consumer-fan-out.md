# ADR-0009: Single-Read Dual-Consumer Fan-Out

Status: Accepted

## Context

The authoritative raw stream and the advisory live view have incompatible
latency requirements. Recording must receive every accepted synchronized
sample, while a future plot must remain current even when rendering is slower
than acquisition. Opening a second hardware task would create independent
timing and a competing source record.

## Decision

One application-owned worker performs the only ordered DAQ read loop. Each
validated immutable `AcquisitionChunk` is first offered to a bounded FIFO
recording queue and then shared by reference with a bounded display queue.

The recording queue never evicts, coalesces, reorders, or overwrites chunks. It
may apply bounded backpressure; exceeding that interval faults acquisition with
the acquired and accepted counts kept distinct. The display queue never blocks
the worker. When full, it replaces the oldest unpublished whole chunk and
increments a separate display-drop counter. Numerical decimation remains
deferred to DAQ-M07.

Consumers pull from close-aware streams. Stream closure is separate from queue
contents so terminal runs can still be drained without reserving capacity for
a sentinel.

## Consequences

Recording pressure can stop acquisition rather than conceal source-data loss.
Display latency stays bounded and cannot affect the hardware reader. Recording
and display consumers share read-only arrays, so mutation requires an explicit
consumer-local copy. DAQ-M05 can replace the test recording consumer without
changing acquisition sequencing.
