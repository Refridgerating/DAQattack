# DAQ-M02 Acceptance Evidence

Status: Accepted locally on Windows  
Date: 2026-07-28  
Platform: Windows, CPython 3.11.15, NumPy 2.4.6  
Generator: `sim-v1`

## Implemented behavior

- One hardware-independent DAQ port covers discovery, aggregated validation,
  configuration, start, bounded reads, stop, health, reset, context-managed
  cleanup, and idempotent close.
- The explicit simulated topology reports a synthetic cDAQ-9171, NI-9239,
  differential `ai0` through `ai3`, ±10 V input range, simultaneous sampling,
  and 50,000 samples/s/channel.
- Owned read-only C-contiguous arrays use shape `[channels, samples]`,
  `float64`, configured channel order, and raw volts.
- `sim-v1` evaluates sine, impulse, linear chirp, drift, bias, keyed Gaussian
  noise, and additive composition from absolute sample index.
- Clipping, dropout, delay, timeout, and disconnect produce structured events
  or typed errors with deterministic onset and cleanup behavior.
- The CLI lists/inspects simulated hardware, validates profile/experiment/
  scenario compatibility, and performs bounded non-recorded acquisitions.

## Determinism and analytical evidence

The contract and integration suite reconstructs identical arrays from one
complete read, one-sample reads, fixed chunks of 7, 64, 257, 1,000, 4,096, and
8,191 samples, plus an irregular partition. Assertions use `array_equal`,
canonical bytes, SHA-256, event codes, and final simulator cursor.

The analytical fixture contains one coherent second at 50 kS/s with 1,000 Hz
sines, amplitudes 0.0063, 0.0126, 0.0189, and 0.05 V, and phases 0°, 90°, 180°,
and -90°. Independent coherent projection and DFT tests satisfy the required
frequency, amplitude, and phase tolerances. The impulse fixture places all
four peaks at absolute sample 12,345 with zero-sample skew across boundaries.

A two-second clean CLI acquisition produced:

```text
samples/channel: 100000
shape: 4 x 100000
dtype: float64
chunks: 25 at a requested chunk size of 4096
observed throughput: 11,756,131 samples/s/channel
SHA-256: e0602db669263b64e799c684ba7e32195a1092e1e7280855b86bf16f2b0c4494
cleanup: succeeded
```

## Automated verification

The initial complete M02 local run produced:

```text
ruff format --check .       passed
ruff check .                passed
mypy src                    passed
lint-imports                4 contracts kept
pytest -q                   166 passed, 88.16% coverage
```

The final verification run is recorded in the task completion report. Package
build and installed-wheel resource/CLI smoke checks are required there as well.

## Acceptance mapping

- DAQ port, simulator topology, lifecycle, validation, read invariants: automated
- Byte-equivalent rerun and arbitrary chunk invariance: automated
- Analytical sine, chirp, noise statistics, and exact impulse alignment: automated
- Clipping, dropout, delayed read, timeout, disconnect, reset, cleanup: automated
- Finite final chunk, continuous bound requirement, CLI and JSON output: automated
- NI/GUI/storage import isolation and explicit backend selection: automated
- Physical NI hardware, wiring, sensors, and charge conditioning: not tested and
  not part of M02
