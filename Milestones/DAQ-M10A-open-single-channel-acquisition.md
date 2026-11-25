# DAQ-M10A: Open Multichannel Acquisition

> Historical specification: the qualification and physical-authorization prerequisites below are superseded by [DAQ-M12](DAQ-M12-raw-first-monitoring.md). The current raw-first workflow removes these prerequisites.

Status: software/simulator implemented; physical NI verification pending  
Depends on: DAQ-M04, DAQ-M05A, DAQ-M09, and DAQ-M10  
Enables: repeated diagnostic capture without restarting the DAQ task

## Goal

Provide a raw-voltage diagnostic mode in which one continuous configured DAQ task
feeds a bounded monitor for every enabled channel. The operator may create multiple
synchronized multichannel HDF5 sessions by starting and stopping recording without
stopping the hardware stream.

## Required behavior

- Open acquisition is available only through the existing commissioning diagnostic
  readiness and physical-authorization workflow.
- The DAQ task acquires every enabled configured channel through one M04 read loop.
  The same ordered channel set is retained for live display and detachable recording.
- Live history is bounded to ten seconds by default and overwrites its oldest values.
  It is never replayed into a new recording.
- Record begins at the first whole source chunk after the command cursor. Stop ends at
  the captured whole-chunk cursor. Each segment rebases sequence and sample index to zero.
- Each Record/Stop cycle creates a distinct schema-v2 raw-diagnostic session containing
  every enabled channel. Its manifest maps local indices back to the monitor run and
  source range and records the authoritative ordered channel set.
- A storage fault faults only the active segment after preserving its readable prefix;
  monitoring continues. A DAQ fault terminates monitoring and faults any active segment.
- End monitoring and supported GUI close finalize an active segment before releasing
  the DAQ task.

## Acceptance

- Simulator tests prove bounded monitoring, exact multichannel replay, multiple
  distinct segments, local/source index mapping, and continued monitoring after Stop.
- Injected storage failure remains explicit and does not stop the DAQ stream.
- Existing qualified and one-shot diagnostic acquisition behavior remains unchanged.
- GUI controls call application services only and remain responsive during monitor,
  recording, finalization, and shutdown work.
- Historical single-channel M10A sessions remain readable through the legacy primary-channel
  compatibility field.
- Physical NI monitoring, repeated recording, continuity, and cleanup remain explicitly
  pending until executed on the target cDAQ-9171/NI-9239 system.
