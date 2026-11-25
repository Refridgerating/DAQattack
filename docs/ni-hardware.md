# NI cDAQ Hardware Setup and M03/M04 Acceptance

## Safety boundary

Software validation does not establish safe voltage, grounding, sensor power,
isolation, or external-conditioner compatibility. Confirm every source is
compatible with the NI-9239 differential ±10 V input before creating a task.
Open inputs may be used for software acquisition checks, but their values are
not signal-quality, clipping, calibration, crosstalk, or wiring evidence.

Do not disconnect or reset a physical chassis while another application owns
it. LabDAQ never automatically resets a device or continues after reconnection.

## Installation

Install the NI-DAQmx Windows driver first and confirm the chassis and module in
NI-MAX. Then install the optional Python integration:

```powershell
python -m pip install -e ".[dev,ni]"
labdaq diagnostics --json
labdaq hardware list --backend ni --json
```

The M08 NI adapter also exposes the driver's device self-test through the
backend-neutral DAQ contract. Unsupported driver/device combinations are
reported as `not-tested`; a driver self-test is never treated as evidence for
wiring, sensors, conditioning, calibration, or storage.

The bundled profile uses stable logical identifiers. NI aliases such as
`cDAQ1` and `cDAQ1Mod1` are local addresses and may differ between computers.
The profile's `logical_slot: 0` maps to NI-DAQmx physical slot 1; reports retain
both values. `NI 9239 (BNC)` normalizes to `NI-9239`, while the exact reported
product string and serial number remain in discovery output.

## Internal sample rates

Profile revision 2 recognizes every NI-9239 internal-clock rate derived from
the 12.8 MHz master timebase: `12.8 MHz / 256 / n = 50,000 Hz / n` for integer
`n = 1..31`, from 50,000 Hz through approximately
1,612.903 Hz. These are discrete choices, not an arbitrary continuous range.
LabDAQ rejects an off-grid request such as 20 kHz and rejects NI-DAQmx coercion
to another discrete rate. External-clock operation is not supported.
See the [NI-9239 datasheet](https://download.ni.com/support/manuals/375939b_02.pdf).

Changing rate resets Samples/read/channel to the nearest whole sample for a
100 ms target using decimal half-up rounding. Examples are 5,000 samples at
50 kHz (100 ms), 1,667 at 16.667 kHz (about 100.020 ms), and 161 at 1.613 kHz
(about 99.820 ms). Operators may override the batch until the next rate change.
Read size controls transfer cadence, not the sample clock or requested duration.

Physical qualification is rate-specific. A rate or read-size change makes
prior evidence stale. Simulator and fake-driver coverage of all 31 rates is not
physical evidence; non-50-kHz rates remain unverified on the target hardware.

For the optional commissioning campaign, use a schema-v2 configuration with
`experiment_mode: accelerometer_commissioning`. In that campaign ai0-ai2 use
the supplied X/Y/Z sensitivities, and ai3 uses role `diagnostic_raw_voltage` and disposition
`raw_only_diagnostic`, permanently labeled `UNPOWERED / UNQUALIFIED / NOT
CHARGE`. The NI-9239 is a differential voltage-input module; its +/-10 V input
range does not supply the 422E03 constant-current excitation or remove powered
bias.

Before connecting the unpowered 422E03 output, disconnect ai3 and measure the
output with a high-impedance scope or meter. DC and observed excursion must
remain within the campaign's conservative +/-8 V guard band. This check does
not make the output charge data. Stop for overrange, clipping, nonfinite input,
or an unstable value. A later powered chain must supply documented 18–28 VDC
constant-current excitation at 2.2–20 mA and present a bias-removed signal
inside the NI input range.

The commissioning qualification plan is supervised: verify the measured 5 V
board supply and polarity, capture three powered-idle repetitions, then apply
one controlled manual X, Y, and Z stimulus at a time. Review bias, noise,
headroom, dominance, polarity, and clipping before approving offsets and
guard-banded limits. This is a mapping/plausibility check, not an absolute
sensitivity certificate. Do not rewire an active NI task; common-input and
crosstalk fixture sub-runs remain a separate task-stopped production gate.

## Validation and bounded acquisition

Use a locally validated experiment configuration. The synthetic fixture may be
used only for raw-voltage adapter acceptance and is explicitly not physical
sensor, conditioner, or calibration evidence.

```powershell
labdaq hardware validate --backend ni `
  --profile src\labdaq\resources\config\hardware-profiles\cdaq-9171-ni-9239-four-channel-v2.json `
  --experiment tests\fixtures\configuration\valid-four-channel-synthetic-v1.json `
  --json

labdaq hardware acquire-test --backend ni `
  --profile src\labdaq\resources\config\hardware-profiles\cdaq-9171-ni-9239-four-channel-v2.json `
  --experiment tests\fixtures\configuration\valid-four-channel-synthetic-v1.json `
  --duration 2 --chunk-size 5000 --timeout 2 --json
```

The command writes no session and prints no raw arrays. It reports requested
and actual rate, channel order, counts, shape, dtype, bounded voltage
statistics, events, and cleanup state.

## Hardware tests

Hardware tests are skipped unless explicitly enabled:

```powershell
$env:LABDAQ_RUN_NI_HARDWARE = "1"
$env:LABDAQ_HARDWARE_PROFILE = "C:\path\to\local-profile.json"       # optional
$env:LABDAQ_EXPERIMENT_CONFIG = "C:\path\to\experiment.json"       # optional
pytest tests\hardware\test_cdaq9171_ni9239.py -m hardware -q
```

The suite covers discovery, profile validation, a two-second acquisition,
twenty lifecycle cycles, timeout cleanup, fresh-instance recovery, and a short
M04 state/stream contract. It does not reset or disconnect the device.

The NI Python wrapper exposes task abort through its narrow internal task
protocol, and fake-driver tests verify that cancellation requests invoke it and
remain safe when repeated. Physical interruption timing for an in-flight read,
manual-stop partial-read behavior, and stop-watchdog behavior remain pending.
Do not treat fake-driver or simulator cancellation evidence as a physical
driver/device result.

## Qualification

After completing the safety and wiring review, run the M08 commands in the
[hardware qualification guide](hardware-qualification.md). The default physical
throughput acceptance is 600 seconds at four channels by 50 kS/s on the target
computer and selected storage location. Short `--duration` runs are development
checks only. Keep `LABDAQ_RUN_NI_HARDWARE` opt-in semantics for pytest; ordinary
test runs never reserve hardware.

## Supervised checks

Channel mapping requires a distinguishable, known-safe stimulus applied to one
input at a time. Confirm that each response appears only in its declared
logical and physical row. Do not infer this result from open-input noise.

The controlled-disconnect check must be supervised. Start a bounded continuous
read only after agreeing on the cable action, disconnect the chassis while the
read is active, confirm a typed disconnect and bounded cleanup, reconnect it,
explicitly rediscover, and run a fresh acquisition. Never automate the cable
action or reset every detected NI device.
