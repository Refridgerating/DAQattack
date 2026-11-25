# DAQ-M03 Acceptance Evidence

Status: Implementation complete; physical acceptance pending channel mapping
Date: 2026-08-12
Platform: Windows, CPython 3.11.15, NumPy 2.4.6

## Implemented behavior

- Optional `nidaqmx` 1.6.0 backend behind the M02 DAQ port and composition root.
- Read-only discovery and aggregated profile validation with canonical and raw
  hardware identity.
- One ordered four-channel differential-voltage task with verified timing,
  exact requested/actual rate policy, preallocated reads, owned results, typed
  faults, structured warnings, and deterministic cleanup.
- NI-capable list, inspect, validate, and bounded acquire-test CLI workflows.
- Fake-driver, contract, integration, architecture, and explicitly marked
  hardware tests; simulator operation remains independent of NI installation.

## Physical hardware evidence

Discovered equipment:

```text
NI-DAQmx driver: 16.1.0
Python nidaqmx: 1.6.0
Chassis: cDAQ1, cDAQ-9171, serial 0x024D68A5, physical
Module: cDAQ1Mod1, NI 9239 (BNC), serial 0x024D471A
Physical slot: 1; logical profile slot: 0
Channels: cDAQ1Mod1/ai0, ai1, ai2, ai3
Capabilities used: differential, +/-10 V, 50,000 samples/s/channel
```

The baseline profile was accepted. An in-memory profile changed to NI-9238 was
rejected with `DAQ.HARDWARE.PROFILE_MISMATCH` while the adapter remained idle
and created no task.

The final two-second open-input CLI run reported:

```text
requested rate: 50000.0 Hz/channel
actual rate: 50000.0 Hz/channel
samples/channel: 100000
shape: [4, 100000]
dtype: float64
chunks: 20 x 5000
channel order: accel-x, accel-y, accel-z, charge-signal
physical order: cDAQ1Mod1/ai0, ai1, ai2, ai3
cleanup: succeeded
faults/warnings: none
```

All four opt-in safe hardware tests passed in 10.48 seconds. This includes 20
configure/start/read/stop/close cycles in one process without a reserved-task
failure. A forced timeout produced `DAQ.ACQUISITION.READ_TIMEOUT`, retained NI
code `-200284`, reported no cleanup error, and a fresh adapter subsequently
returned a `[4, 100]` array and stopped cleanly.

No physical reset was invoked. Open-input voltage values are not calibration,
signal-quality, clipping, wiring, or channel-mapping evidence.

The supervised active-task disconnect/reconnect check passed. With a continuous
four-channel task reading at 50,000 samples/s/channel, unplugging the chassis
produced `DaqDisconnectedError` and event code
`DAQ.HARDWARE.DEVICE_DISCONNECTED` from the `read` operation. NI status
`-88710` and the driver message were preserved; 690,000 samples/channel had
been returned before removal. `stop()` returned and final adapter close
succeeded without a device reset.

After reconnection, explicit rediscovery found the same chassis, module,
serials, and four physical channels on the first complete-topology poll. A new
adapter then acquired a `[4, 5000]` `float64` array at an exact requested and
actual rate of 50,000 samples/s/channel, stopped normally, and reported the
declared physical channel order.

## Automated verification

The implementation includes automated coverage for the M03 required failure
cases through the fake driver, including package/runtime absence, missing or
mismatched topology, range/mode/rate rejection, reservation, verify/start/read/
stop/close/reset failure, timeout, disconnect, partial/malformed reads,
lifecycle misuse, cleanup precedence, and later-read buffer independence.

Final local verification on 2026-08-12:

```text
ruff format --check .     90 files already formatted
ruff check .              passed
mypy src                  passed (36 source files)
lint-imports              4 contracts kept, 0 broken
pytest -q                 212 passed, 4 hardware tests skipped, 86.77% coverage
python -m build           source distribution and wheel built successfully
```

The wheel was installed with its `simulated` and `ni` extras into a clean
virtual environment. Diagnostics, simulator discovery, and physical NI
discovery all passed from the installed wheel.

## Pending physical acceptance

- Channel mapping with a distinguishable safe stimulus on each input.

The roadmap remains unaccepted until this check passes. This pending check does
not invalidate the completed implementation or safe physical evidence above.
