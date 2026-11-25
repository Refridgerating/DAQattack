# ADR-0023: Live Operator Buffering and Disk-Backed Review

Status: Accepted

## Context

DAQ-M10 connects the desktop to sustained acquisition, durable recording, causal
processing, replay, plotting, and export. A slow or paused Qt renderer must not
become backpressure on the recording path, and the live display must not become
an undocumented source of post-run data. The existing M04 engine already owns
the single DAQ read and its lossless-recording/best-effort-display fan-out.

## Decision

An application-owned `LiveAcquisitionCoordinator` consumes the byte-exact M08
qualification attachment and creates one fresh `RecordingSessionService` per
run. It attaches one display consumer to the M04 engine's existing bounded
display stream before starting acquisition. It never creates another DAQ adapter
read, NI task, recording path, or raw dataset.

The display worker drains every available source chunk outside Qt and maintains
preallocated channel-major NumPy rings. Both raw and conditioned rings retain at
most ten seconds; five seconds is the default viewport. Causal `baseline-v1`
processing advances once, in source order. A source discontinuity resets the
filter, clears discontinuous conditioned history, and publishes a
`DAQ.DISPLAY.PROCESSING_SOURCE_GAP` degradation without changing recording
truth. M04 source-frame replacement remains the authoritative display-drop
counter.

Qt requests current plot frames on demand at a target ten hertz. The application
copies only the selected retained suffix and applies `min-max-v1` to at most
2,000 points per channel. Rendered frames are not queued. Display pause suppresses
requests only; acquisition, recording, display draining, metrics, and status
continue. Rolling metrics use the M06 processing port, a one-second conditioned
window, and a 500 ms target update interval.

Acquired/persisted lag is queue progress while a run is active. It becomes
persistence loss only after terminal evidence. Stop and close are idempotent;
normal GUI close during a run requests stop and waits for recorder drain,
finalization, and cleanup before closing.

All post-run review is reopened through M05 session inspection/replay and M06/M07
plot services. JSONL event parsing and export-manifest discovery live in adapters.
The same review surface is used after a run and from the session browser. Existing
session, event, processing-recipe, export-sidecar, and export-manifest schema
versions remain unchanged.

## Consequences

- Recording priority and one-reader ownership remain the M04/M05 contracts.
- An arbitrarily long run consumes fixed live-history memory and no rendered-frame
  backlog.
- Pause, visibility, scale, and viewport changes cannot reconfigure hardware or
  alter stored samples.
- Live filter state is deliberately discarded after a display-source gap; disk
  replay remains authoritative and continuous where the source record is intact.
- Display degradation, source gaps, and terminal persistence loss remain separate
  operator concepts.
- Review proves that finalized or recoverable session artifacts are sufficient
  without hidden GUI state.
