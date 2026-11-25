# ADR-0026: Raw diagnostic launch authorization

Status: Accepted

## Context

Qualified acquisition requires a current, eligible qualification report. During
commissioning, an operator may still need a durable raw-voltage observation to
diagnose wiring and signal levels before calibration and characterization are
approved. Treating that observation as a qualified experiment would erase an
important scientific and safety boundary.

## Decision

LabDAQ provides a separate `raw-diagnostic` session type and launch intent. The
intent requires a valid commissioning configuration, writable destination,
current accepted hardware validation, and explicit operator confirmation of the
measured sensor-board supply/polarity and ai3 connection state. Connected ai3
requires an independently observed range inside +/-8 V using a high-impedance
instrument.

The diagnostic launch does not carry a qualification attachment. It records all
configured rows as immutable volts through the normal single-task recording
path, but creates no live or offline calibrated, conditioned, metric,
acceleration, or charge product. The manifest retains the authorization and
hardware-validation identity, and review/export services enforce raw-only use.
[ADR-0032](0032-raw-voltage-spectra-in-session-review.md) permits offline voltage
FFT/PSD for these sessions without granting calibrated interpretation or qualification.

## Consequences

- The qualified `AcquisitionLaunchIntent` remains strict and unchanged.
- Diagnostic sessions cannot be reinterpreted as commissioning release or
  production evidence.
- Existing session manifests remain readable; absent session type and
  authorization fields retain existing experiment behavior.
- The GUI exposes qualified and diagnostic readiness separately.
