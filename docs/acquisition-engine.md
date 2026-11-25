# Acquisition engine

Status: Implemented in DAQ-M04; simulator verified. Physical NI engine verification is pending.

The headless `AcquisitionEngine` owns the acquisition state machine, the only
blocking read worker, and the fan-out from each validated hardware read. It does
not persist data, process signals, display a GUI, or add a CLI command. Those
consumers attach through pull-based streams.

## State model

```text
IDLE -> CONFIGURED -> ARMED -> RUNNING -> STOPPING -> COMPLETED
  ^                                |             \-> FAULTED
  |                                +----------------> FAULTED
  +--------- explicit reset from CONFIGURED, ARMED, COMPLETED, or FAULTED
```

`configure()` validates the hardware profile and experiment and assigns a new
session UUID. `arm()` reserves and configures the one adapter task. `start()`
starts the adapter before creating the worker. Illegal commands raise a
structured error, emit `DAQ.ACQUISITION.ILLEGAL_STATE_TRANSITION`, and do not
change state.

After `arm()`, `daq_configuration()` exposes the immutable effective
`DaqConfigurationResult` for M05 provenance, including requested/actual rate,
ordered logical/physical channels, effective ranges, and samples per read. It is
unavailable before the adapter has been configured by `arm()`.

`reset()` is explicit. Resetting an armed engine releases the configured task.
After a disconnect fault, reset also asks the adapter to recover an unavailable
device; if adapter recovery fails, the engine stays faulted. `close()` is
idempotent and owns final adapter closure.

## Producer and consumers

```text
one DaqPort.read()
        |
        v
validate shape, values, rate, channel order, and continuity
        |
        +--> bounded recording FIFO (lossless, blocking with timeout) --> M05 receipts
        |
        `--> bounded display FIFO (replace oldest whole chunk) --------> later live view
```

The worker increments the acquired-sample count after a read is valid. It
increments the recording count and publishes a sequence number only after the
recording FIFO accepts the chunk. Display publication happens last. Therefore a
recording saturation fault can report more acquired samples than recording
samples, while display supersession never changes recording continuity.

Chunk arrays are C-contiguous, read-only `float64` arrays with shape
`[channels, samples]`. Every chunk includes session identity, zero-based sequence
number, first sample index, actual sample rate, ordered logical channel IDs,
ordered raw units, sample count, and read-completion monotonic time. Consumers
may retain the shared immutable array; the engine does not copy it separately for
recording and display.

Finite duration is converted once to a target sample count with decimal
half-up rounding: `samples = ROUND_HALF_UP(requested_seconds * requested_rate)`.
Targets below one sample are rejected. A final read may therefore be smaller
than Samples/read/channel and is preserved. Requested duration remains in
configuration; actual duration is acquired samples divided by actual rate.

Streams close independently of queue capacity. A consumer may drain queued
chunks after terminal completion. Once a closed stream is empty, `get()` raises
`AcquisitionStreamClosedError`; no sentinel consumes queue capacity.

## Runtime configuration

The fields below live in `experiment.data_chunk` and remain optional in
version-1 JSON documents.

| Field | Default | Meaning |
|---|---:|---|
| `samples_per_channel` | 5,000 | Maximum samples requested by one read |
| `read_timeout_seconds` | 2.0 s | Adapter read timeout |
| `stop_timeout_seconds` | 5.0 s | Cooperative-stop watchdog |
| `recording_queue_capacity_chunks` | 32 | Lossless recording FIFO capacity |
| `recording_backpressure_timeout_seconds` | 2.0 s | Maximum recording enqueue wait |
| `display_queue_capacity_chunks` | 2 | Latest-data display capacity |
| `progress_event_interval_seconds` | 0.5 s | Minimum progress-event interval |

At four channels and 5,000 samples per channel, a raw `float64` chunk is
160,000 bytes. The default logical payload capacities are therefore 5.12 MB for
recording and 0.32 MB for display. Python and queue object overhead is additional.

The desktop labels this value **Samples / read / channel**. With profile
revision 2, changing rate replaces it with the half-up rounded 100 ms default.
A manual override changes latency and chunking only and persists until the next
sample-rate change.

## Headless use

```python
from pathlib import Path

from labdaq.bootstrap import create_acquisition_engine, load_daq_configuration
from labdaq.ports.acquisition import AcquisitionStreamClosedError

profile, experiment = load_daq_configuration(
    experiment_path=Path("tests/fixtures/configuration/valid-four-channel-synthetic-v1.json")
)
engine = create_acquisition_engine(backend="simulated")
events = engine.subscribe_events()

try:
    engine.configure(profile, experiment)
    engine.arm()
    recording = engine.recording_stream()
    display = engine.display_stream()
    engine.start()

    try:
        while True:
            chunk = recording.get()
            consume_without_mutating(chunk)  # application-supplied consumer
    except AcquisitionStreamClosedError:
        pass
    completion = engine.wait_for_terminal(timeout=30.0)
finally:
    engine.close()
```

Attach and run a recording consumer before a run can fill its configured FIFO;
the example drains on its calling thread while the engine worker runs in the
background. Calling `wait_for_terminal()` alone does not drain recording data.
A stop caller uses `request_stop()` and then `wait_for_terminal()`; stop
requests do not turn the caller into an acquisition worker.

## Stop and terminal semantics

The first valid stop request changes `RUNNING` to `STOPPING` and starts adapter
cancellation. Repeated stop requests are idempotent. A valid partial read that
has already returned is still recorded. If cancellation does not finish before
`stop_timeout_seconds`, the engine records `DAQ.ACQUISITION.STOP_TIMEOUT` and
forces adapter closure.

Success, manual stop, timeout, adapter error, worker error, and queue saturation
all converge on one cleanup path. A primary acquisition fault remains primary if
cleanup also fails; cleanup failure is attached as secondary context. Exactly one
terminal acquisition event is published, then all streams are closed after
worker cleanup.

See [ADR-0009](adr/0009-single-read-dual-consumer-fan-out.md),
[ADR-0010](adr/0010-acquisition-stop-terminal-cleanup-semantics.md), and the
[M04 acceptance evidence](milestones/DAQ-M04-acceptance-evidence.md).

## Detachable open-acquisition recording

M10A composes the unchanged engine through `OpenAcquisitionCoordinator`. One
continuous task acquires every configured channel. The coordinator always drains
the lossless source queue: outside a recording it discards each source chunk
and during recording forwards all selected channels as immutable chunks. The
display path retains the selected raw-voltage channels in the fixed ten-second ring.

Record and stop boundaries are half-open engine cursor positions. A segment
starts with the first complete chunk at or beyond the captured start cursor and
includes already-acquired chunks below the captured stop cursor. Saved chunks
use a new session ID, sequence from zero, and local sample index from zero while
the optional manifest provenance preserves the source interval. A bounded queue
bridges session-writer initialization. Segment storage failures terminate that
segment without stopping the DAQ stream; DAQ faults remain terminal to the
monitor. See [ADR-0029](adr/0029-detachable-open-acquisition-recording.md).
