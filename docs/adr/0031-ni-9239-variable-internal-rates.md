# ADR-0031: NI-9239 Variable Internal-Clock Rates

Status: Accepted

## Context

The NI-9239 derives 31 discrete internal-clock rates from its 12.8 MHz master
timebase: `12.8 MHz / 256 / n = 50,000 Hz / n` for integer divisors
`n = 1..31`. LabDAQ previously exposed a
free-text rate while its bundled profile accepted only 50 kHz. This invited
unsupported inputs, driver coercion, inconsistent read latency, stale
qualification, and use of a 12 kHz low-pass where it is invalid.

Finite durations also need one deterministic conversion to whole samples.
Binary floating-point and ties-to-even rounding are unsuitable as a shared
scientific configuration policy.

## Decision

- Hardware-profile revision 2 lists exactly `50,000 / n`, `n = 1..31`, in
  descending order. Revision 1 remains available for legacy configurations.
- Operators choose from a non-editable, profile-driven selector. Arbitrary
  rates and NI-DAQmx coercion to another discrete rate are rejected; only
  binary representation noise is equivalent.
- A rate change atomically selects the rate, sets Samples/read/channel to the
  decimal-half-up value of `rate * 0.1 s`, and selects its bundled recipe.
  Manual read-size edits remain until the next rate change.
- `baseline-v1` applies at 50 and 25 kHz. `baseline-low-rate-v1` applies below
  25 kHz and retains affine calibration plus the causal 1 Hz accelerometer
  high-pass without the incompatible 12 kHz low-pass.
- Finite duration resolves to `ROUND_HALF_UP(duration * requested_rate)` and
  must produce at least one sample. Actual duration is acquired samples divided
  by actual rate.
- Qualification plans bind the selected rate, while the experiment fingerprint
  covers rate and read size. Each physical selection requires its own evidence.
- Acquisition, open acquisition, qualification, replay, review, and export
  resolve the recipe named by the experiment or recorded session.
- External clocks remain out of scope.

## Consequences

New templates and default composition use profile revision 2. External
revision-1 configurations continue to require their revision-1 profile. The
nominal 100 ms batch has small rate-dependent rounding error; for example,
1,667 samples at 16,666.667 Hz is about 100.020 ms. Simulator and fake-NI
checks establish software behavior only. Every non-50-kHz physical selection
remains unverified until separately exercised on the target hardware.
