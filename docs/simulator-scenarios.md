# Simulator Scenarios

M02 scenarios are closed version-1 JSON documents loaded by the simulated DAQ
adapter. Install `labdaq[simulated]` to use them.

Each document declares a stable scenario ID/revision, `sim-v1`, an unsigned
64-bit seed, pacing mode, exactly one signal definition per enabled experiment
channel, and a fault schedule. Raw units are fixed to volts.

Supported signal components are sine, rectangular impulse, analytical linear
chirp, indexed Gaussian noise, and linear drift. Components are evaluated in
serialized order from absolute sample index `n`, where `t = n / fs`. Phase is
serialized in radians. Frequencies must remain below Nyquist.

Supported injected conditions are clipping, simultaneous dropout, read delay
or timeout, disconnect, nonfinite samples, frozen intervals, stale reads,
source-index gaps/duplicates/reordering, channel-relative delay, and crosstalk.
Fault intervals are half-open. Delay warnings begin at 50 ms. Dropout and
disconnect return any clean prefix before faulting and never silently insert,
repeat, or compress samples.

Optional `self_test_supported` and `self_test_passed` fields control the
backend-neutral self-test result. Optional `topology` fields control chassis and
module presence, models, serials, maximum sample rate, and input range. This lets
M08 distinguish discovery, pinned-identity, and capability faults without
editing the hardware profile under test.

Canonical checksums use SHA-256 over the complete little-endian,
channel-major, C-contiguous `float64` array. The seed, generator version,
scenario, experiment, channel order, and supported numeric environment are
part of reproducibility provenance.

Bundled scenarios cover clean acquisition, analytical sine recovery, impulse
alignment, swept sine, deterministic noise/drift, clipping, dropout, delayed
read/timeout, reconnectable disconnect, and a phase-addressed healthy full-M08
fixture. All are synthetic software fixtures, not sensor, conditioner, wiring,
or physical-hardware evidence.

Qualification tests may derive additional scenarios immutably from these
bundled documents. Bias, noise, and mains-line conditions use ordinary signal
components; topology, self-test, continuity, frozen/nonfinite, delay, crosstalk,
slow-read, and disconnect behavior use the explicit controls above.
