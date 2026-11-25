# ADR-0025: Raw-only ai3 commissioning diagnostic

Status: Accepted

## Decision

Accelerometer commissioning may acquire one optional fourth channel with role
`diagnostic_raw_voltage` and disposition `raw_only_diagnostic`. The channel is
stored as immutable raw volts in the same synchronized NI task as X/Y/Z, but
has no calibration, conditioning-chain reference, or engineering
interpretation. Calibrated/conditioned processing receives only channels whose disposition is
`calibrated`.

The commissioning template identifies ai3 as the unpowered PCB 422E03 output
and permanently labels it `UNPOWERED / UNQUALIFIED / NOT CHARGE`. Raw-only ai3
may appear in raw time history and raw-voltage CSV products. Calibration,
conditioning, metrics, charge, force, and production qualification reject it.
[ADR-0032](0032-raw-voltage-spectra-in-session-review.md) also permits offline
voltage FFT/PSD, including alongside acceleration spectra, with raw-only provenance.

Schema-v1 channels decode with the `calibrated` disposition. Schema-v2
channels declare the disposition explicitly. Production remains exactly X/Y/Z
plus `conditioned_charge`, so a diagnostic session cannot become production
evidence through reinterpretation.

## Consequences

- One acquisition and lossless recording path retains all four raw rows.
- Live and calibrated/conditioned replay products contain X/Y/Z only; offline
  spectra may also include diagnostic ai3 in volts under ADR-0032.
- Session, TDMS, GUI, and export provenance preserve the raw-only disposition,
  null engineering interpretation, and warning.
- Powered-idle qualification still evaluates ai3 for clipping, nonfinite
  values, frozen samples, noise, and input-range utilization, but never treats
  response as charge evidence.
- Installing a compatible conditioner requires a new production configuration
  and a new qualification campaign.
