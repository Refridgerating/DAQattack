# ADR-0024: Explicit accelerometer commissioning mode

Status: Accepted

## Decision

LabDAQ supports an explicit `accelerometer_commissioning` experiment mode for
the three TE 830M1 voltage channels. The mode records and calibrates ai0–ai2, preserves
raw volts, and applies the supplied per-axis sensitivities as operator-
transcribed, unverified calibration until characterization and provenance are
approved. The conditioned-charge role remains mandatory for `production`
mode.

Existing schema-v1 configurations decode as production. Schema-v2
commissioning configurations are persisted in session metadata so reopening a
session cannot silently present it as production evidence.

## Consequences

- Commissioning enables useful three-axis measurements while the charge chain
  is blocked.
- Commissioning reports and sessions cannot authorize the four-channel
  production workflow or the final live-NI default.
- The NI profile accepts three through four configured channels, while domain
  validation enforces the role set appropriate to the selected mode.
- Calibration records may omit an effective date only for operator-transcribed
  values; the transcription timestamp and missing provenance are retained.
- ADR-0025 permits an additional ai3 row only as an unpowered, raw-voltage
  diagnostic; it does not expand commissioning qualification beyond X/Y/Z.
