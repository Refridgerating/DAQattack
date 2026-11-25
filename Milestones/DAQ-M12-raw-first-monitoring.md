# DAQ-M12: Raw-First Monitoring and Optional Recording

Status: software/simulator accepted; physical NI verification pending  
Target: simulator and the supported NI cDAQ-9171 / NI-9239 configuration

## Goal

Make the normal workflow straightforward: discover a DAQ, select and configure
available voltage input channels, start viewing their raw signals, optionally
record, and stop. Qualification and certification are not prerequisites.

## Scope and behavior

- Present discovered supported channels and allow the operator to select one or
  more, with explicit physical channel identities, input range, and sample rate.
  Start requires a usable backend and a technically valid configuration; report
  driver, device, channel, and configuration errors clearly.
- Show raw voltage against sample-index-derived time, with channel identity,
  units, and rate visible. Use one synchronized acquisition stream and bounded
  live history. Display may discard superseded frames without hiding acquisition
  errors.
- Start monitoring without creating a recording or requesting a storage path,
  calibration certificate, sensor identity, qualification report, report
  fingerprint, external confirmation checklist, or fixed commissioning mode.
  Unknown sensor and conditioning details remain unknown; do not invent them or
  label raw volts as calibrated measurements.
- Let the operator start and stop recording while monitoring continues. Save all
  selected channels from the recording interval, with sample rate, ordered
  physical channels, units, timestamps, counts, and available hardware metadata.
  Keep existing session format and replay compatibility. Recording failures are
  explicit and preserve recoverable data; they need not stop monitoring if the
  hardware stream remains healthy.
- Stop or close releases the DAQ task and finalizes any active recording. Surface
  disconnects, invalid configuration, overrange/clipping where detectable, and
  acquisition or recording loss. Electrical wiring, power, grounding, and input
  compatibility remain physical responsibilities and are not certified by the
  software. Do not introduce mandatory confirmation dialogs as a substitute.
- Keep existing qualification and processing features accessible as optional
  tools. Preserve existing recordings and report readability. Remove their role as
  a prerequisite for ordinary raw monitoring and recording, including any GUI,
  application-service, or CLI gate on that path.

## Acceptance and evidence

1. In simulation, select one and multiple channels, start and view raw voltage
   without qualification, calibration metadata, storage selection, or a session;
   channel labels, units, rates, and sample-index time are correct.
2. Start and stop multiple recordings without restarting monitoring. Reopen each
   and verify channel ordering, metadata, samples, and recording boundaries.
3. Invalid device/channel/rate settings and backend failures are actionable;
   disconnect and loss are visible, and closing releases resources. Recording
   failure does not silently lose data or falsely claim successful completion.
4. Existing optional qualification reports and supported historical sessions
   remain readable; simulator and core imports work without NI software.
5. Automated focused and integration tests exercise the public service and GUI
   workflow, plus appropriate architecture and full-suite checks. Record actual
   results and any failures.
6. When hardware is available, manually verify selected physical channels,
   voltage display, optional recording/replay, stop, and task cleanup on the target
   device. Until performed, mark physical behavior unverified; it does not block
   development or simulator use and must not be represented as certified.

Use existing acquisition and recording services where practical. Update the
operator instructions to describe the actual resulting behavior. This milestone
does not require a qualification campaign, a 600-second soak, 100 starts, or a
packaged release. Add features outside this workflow only when requested or
strictly necessary to support it.


Evidence and outstanding checks are recorded in [DAQ-M12 acceptance evidence](../docs/milestones/DAQ-M12-acceptance-evidence.md).
