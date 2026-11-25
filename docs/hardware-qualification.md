# Hardware Qualification

> This guide describes optional qualification. [DAQ-M12](../Milestones/DAQ-M12-raw-first-monitoring.md) does not require qualification or confirmation checklists for ordinary raw monitoring and recording. Physical electrical setup remains the operator's responsibility.

Status: DAQ-M08 software implementation complete; physical qualification pending

LabDAQ qualification is a headless application workflow over the production DAQ,
acquisition, TDMS, inspection, and replay services. A data-bearing run creates
one `hardware-qualification` session and retains raw samples. It does not certify
a sensor or conditioner independently.

## Plans

- `preflight-v1` performs live discovery, profile/capability comparison,
  backend self-test, lifecycle checks, and a 10-second powered-idle capture.
- `full-v1` adds charge-conditioning, three axis-response confirmations,
  alignment, optional crosstalk, and a 600-second throughput interval.
- `throughput-v1` exercises four synchronized channels at 50 kS/s for 600
  seconds. `--duration` changes only that interval and is intended for tests or
  a deliberately reviewed shorter run.

For the current campaign, launch the explicit commissioning mode with
`labdaq-gui --daq ni --mode accelerometer_commissioning`. It validates and
records ai0/ai1/ai2 (X/Y/Z), uses the operator-transcribed 6.914/6.791/
7.398 mV/g values as unverified derived calibration, and permanently labels
the session as partial production qualification. ai3 is retained only as
unpowered raw voltage with a permanent `UNPOWERED / UNQUALIFIED / NOT CHARGE`
warning. It is excluded from calibration, conditioning, metrics,
charge units, and production readiness until the approved constant-current conditioner,
bias-removal range, complete-chain sensitivity, and polarity have passed
external review. Offline voltage FFT/PSD is permitted for observation only and
does not change qualification status (ADR-0032).

All plans carry exact threshold provenance. Generic raw range utilization warns
above 80 percent and fails above 98 percent. Clipping, nonfinite input,
source-index loss/duplication/reordering, queue saturation, storage failure, and
unequal channel lengths are blocking. Missing lab values for bias, noise,
response, charge conditioning, or crosstalk stay unresolved and cannot produce a
fabricated physical pass.

Commissioning-plan binding retains discovery, capability, self-test,
powered-idle, accelerometer-bias, and any selected X/Y/Z response definitions.
Charge and production-only definitions are excluded. Missing runner-required
definitions or broken prerequisite references fail before a DAQ task is
created with `DAQ.QUALIFICATION.PLAN_INVALID`.

Raw monitoring and optional recordings can be used without a qualification
report or confirmation checklist. Their voltage products do not substitute for
qualification or establish calibration or electrical safety.

## Physical setup boundary

Before selecting the NI backend, independently verify:

- the NI-9239 differential input remains within +/-10 V;
- sensor excitation, grounding, shielding, and mounting are safe and documented;
- each accelerometer serial, axis, sensitivity source, polarity, and power
  evidence matches the configuration;
- the PCB 422E03 or other charge converter has a documented compatible stage
  that removes or shifts its powered bias before the NI input; and
- every applied stimulus and common-input fixture is bounded for the complete
  sensor/conditioner/DAQ chain.

A claimed direct PCB 422E03-to-NI connection without documented compatible bias
removal is blocking. Stop immediately for overrange, unstable bias, clipping,
unexpected heating, loss of sensor power, hardware removal, storage faults, or
any condition outside the reviewed wiring procedure.

## Operator procedure

1. Create a local experiment configuration with actual serials, certificates,
   sensor-power evidence, charge-converter evidence, and NI-terminal
   conditioning evidence.
2. Confirm the target storage directory has enough free space for the raw run.
3. Inspect the wiring and external conditioner independently of software.
4. Run `preflight-v1`; review identity, self-test, raw DC, robust noise,
   headroom, clipping, frozen-value, continuity, and count evidence.
5. For commissioning, verify the measured 5 V supply/polarity, capture three
   powered-idle repetitions, and apply controlled X/Y/Z manual stimuli. Review
   bias, noise, headroom, dominance, polarity, clipping, and repeatability
   before approving offsets and guard-banded limits. For `full-v1`, provide
   only confirmations actually performed with a reviewed safe fixture. An
   absent confirmation remains `not-tested`.
6. With ai3 initially disconnected, verify the unpowered 422E03 output on a
   high-impedance meter or scope and require the observed DC and excursion to
   stay within +/-8 V. Then run a short four-row preflight, the three idle
   repetitions, controlled X/Y/Z stimuli, and a two-minute 50 kS/s/channel GUI
   recording. Stop for ai3 clipping/overrange/nonfinite input, unstable
   accelerometer bias, sample gaps, storage loss, or cleanup failure.
7. Run the default 600-second throughput plan on the target computer and target
   storage device. Do not use a shortened run as physical acceptance evidence.
8. Reopen the report with `qualification show`, then check it against live
   hardware with `qualification check --backend ni`.
9. Attach the exact eligible report when creating the subsequent experiment
   session. Never edit a completed source session to add evidence later.

The minimum external equipment is the reviewed sensor power supply, documented
charge-conditioning chain, safe above-cutoff per-axis stimulus, and—when
alignment/crosstalk are required—a common-input or single-channel fixture whose
output is known to remain within every component's limit.

## Reports, recovery, and eligibility

`qualification-plan.json` is installed exclusively and
`qualification-report.json` is atomically checkpointed after every result. The
report contains lifecycle, pass/fail/warning/not-tested results, exact metrics
and sample ranges, confirmations, assumptions, live identity, counts, bounded
read/write/queue telemetry, evidence links, hashes, and the qualification
fingerprint.

Eligibility requires a terminal `pass` or nonblocking `warning`, a current
matching fingerprint, acceptable age, and all required blocking tests. Advisory
overrides can acknowledge only advisory plan codes. They never bypass identity,
staleness, missing required tests, unsafe input, clipping, continuity, unequal
lengths, backpressure, or storage faults.

Recovery is explicit:

```powershell
labdaq qualification recover path\to\hardware-qualification-session
```

It preserves raw/event bytes, uses the M05 verified recoverable prefix, retains
completed results, and marks unfinished results `not-tested`.

## Simulator controls

Version-1 scenarios can control topology and self-test plus clipping, dropout,
delayed reads, disconnect, nonfinite values, frozen intervals, stale reads,
source-index gaps/duplicates/reordering, per-channel delay, and crosstalk.
Signal definitions provide deterministic bias, broadband noise, drift, mains
lines, impulses, and sweeps. Scenario identity includes a seed; chunk boundaries
do not change the generated signal.

Simulator evidence validates software behavior only. The target cDAQ-9171,
NI-9239, sensors, wiring, conditioner, target storage, and 600-second physical
throughput run have not been exercised unless a separately retained physical
report says so.
