# ADR-0029: Detachable Open-Acquisition Recording

Status: Accepted

## Context

Commissioning work sometimes requires an indefinitely running raw-voltage view and short
operator-selected captures. The existing M10 workflow couples one DAQ task to one writer,
so stopping its recording correctly terminates the task. Reusing that terminal signal for
an open monitor would falsely claim hardware cleanup and would prevent repeated captures.

## Decision

`OpenAcquisitionCoordinator` owns one continuous M04 engine. It always drains the engine's
lossless source stream and separately drains its disposable display stream. Outside a
recording, source chunks are intentionally discarded after selection. The display retains
the ordered enabled channel set in a preallocated ten-second ring.

Record captures the current acquired cursor. Whole chunks at or beyond that boundary are
projected to the ordered enabled channel set and placed on a bounded lossless segment queue.
The projected chunks receive the segment session ID, sequence beginning at zero, and local
sample index beginning at zero. Stop captures a second cursor, drains the half-open source
range, and finalizes the writer without terminalizing the engine.

Schema-v2 manifests retain an optional `open_acquisition` object mapping local sample zero
to the monitor run and source range. `recorded_channel_ids` and
`recorded_channel_units` are authoritative for new multichannel segments;
`selected_channel_id` remains as a legacy primary-channel field so older single-channel
sessions and callers remain compatible. A separate
`RecordingSegmentCompletion` supplies writer finalization evidence and contains no DAQ
cleanup claim. Segments remain `raw-diagnostic`, carry the physical authorization, and
prohibit derived calibrated claims.

## Consequences

- No second DAQ task or hidden raw source is created.
- Monitor memory is bounded for arbitrarily long operation.
- Multiple collision-resistant source records can share one monitor run without appending
  discontinuous data to a session.
- Storage faults stop and fault the segment but may leave monitoring active; DAQ faults
  terminate both lifecycles.
- Post-run review remains disk-backed, and source/local index provenance is explicit.
