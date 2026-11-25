# DAQ-M04 Acceptance Evidence

Status: Implementation complete; physical NI engine verification pending  
Evidence date: 2026-08-12  
Normative platform: Windows, CPython 3.11.15  
Milestone: [DAQ-M04 acquisition engine and experiment state machine](../../Milestones/DAQ-M04-acquisition-engine-and-state-machine.md)

## Acceptance statement

The headless acquisition engine, immutable application contracts, one-reader
fan-out, DAQ cancellation extension, simulator integration, fake-NI contracts,
configuration extension, tests, ADRs, and documentation are implemented.
Acceptance criteria 1 through 10 have automated local evidence. Criterion 11,
the short physical NI engine contract, was not run because physical hardware
was deliberately excluded from this implementation task.

This milestone is therefore implementation complete, not fully accepted.
Neither simulator nor fake-driver results establish physical channel mapping,
wiring, sensor power, input safety, signal quality, or NI read-interruption
timing.

## State-transition evidence

| Initial state | Command or outcome | Result | Evidence |
|---|---|---|---|
| `IDLE` | `configure` succeeds | `CONFIGURED` and new session ID | Unit state/UUID tests |
| `CONFIGURED` | `arm` succeeds | `ARMED`, one configured task and two streams | Unit and integration tests |
| `ARMED` | `start` succeeds | `RUNNING`, adapter started before worker | Unit ordering tests |
| `RUNNING` | duration target reached | `COMPLETED` | Exact-target and integration tests |
| `RUNNING` | `request_stop` | `STOPPING` once | Concurrent/repeated stop tests |
| `STOPPING` | cooperative cleanup succeeds | `COMPLETED` / `manual_stop` | Partial-final-read test |
| active | acquisition/worker/queue fault | `FAULTED` | Fault-injection tests |
| cleanup | cleanup-only fault | `FAULTED` | Cleanup precedence tests |
| `CONFIGURED`, `ARMED`, `COMPLETED`, `FAULTED` | `reset` succeeds | `IDLE` | Reset and reconnectable-disconnect tests |
| any unsupported command/state pair | command rejected | State unchanged | Structured illegal-transition tests |

Commands serialize transitions under the engine lock. Repeated valid stop and
close requests are idempotent. Every tested path publishes one terminal event,
closes streams out of band, and leaves no live acquisition worker.

## Configuration and logical memory bounds

The version-1 experiment schema accepts the new `data_chunk` properties as
optional fields. Legacy version-1 documents missing them load with defaults.

| Property | Default |
|---|---:|
| `samples_per_channel` | 5,000 |
| `read_timeout_seconds` | 2.0 s |
| `stop_timeout_seconds` | 5.0 s |
| `recording_queue_capacity_chunks` | 32 |
| `recording_backpressure_timeout_seconds` | 2.0 s |
| `display_queue_capacity_chunks` | 2 |
| `progress_event_interval_seconds` | 0.5 s |

Four channels x 5,000 samples x 8 bytes per `float64` = 160,000 bytes per
chunk. The default logical raw-array capacity is 5,120,000 bytes (5.12 MB) for
recording and 320,000 bytes (0.32 MB) for display, excluding Python object and
queue overhead.

## Chunk and continuity rules verified

- Raw arrays are finite, C-contiguous, read-only `float64` with shape
  `[channels, samples]`.
- Ordered logical channel IDs and raw units accompany every shared array.
- Sequence numbers begin at zero and advance only after recording enqueue.
- `start_sample_index` begins at zero and equals the preceding index plus sample
  count; sample index remains the relative-time authority.
- Target count is derived from the adapter's actual sample rate and requested
  duration. The worker never requests beyond the remaining finite target.
- Valid partial final reads are retained. Premature zero-length reads,
  over-returns, wrong shapes, nonfinite data, rate changes, and channel-order
  changes fault before recording publication.
- Acquired count advances after a valid read. Recording-accepted count advances
  only after lossless enqueue, preserving the diagnostic difference if the
  recording queue saturates.

## Queue and stop evidence

The recording stream is a bounded FIFO and never evicts a chunk. Timed pressure
emits a warning; saturation becomes a terminal fault with capacity, occupancy,
acquired count, and recording-accepted count. Concurrent-consumer tests prove
FIFO order and bounded slowdown.

Display publication occurs only after recording acceptance. A full display
queue atomically replaces its oldest unpublished whole chunk, increments a
separate counter, and never blocks or faults acquisition. Tests cover absent and
slow display consumption.

The first stop request moves the engine to `STOPPING`, then cancellation occurs
outside the command lock. Simulator waits are interruptible. Fake NI tests prove
that cancellation invokes the narrow task-abort protocol and is safe when
repeated. Tests cover an already-returned partial read, delayed cancellation,
cancellation failure, watchdog timeout, forced close, and concurrent stop
requests.

## Fault and event vocabulary

| Condition | Stable code |
|---|---|
| Illegal command | `DAQ.ACQUISITION.ILLEGAL_STATE_TRANSITION` |
| Recording pressure / saturation | `DAQ.ACQUISITION.RECORDING_QUEUE_PRESSURE`, `DAQ.ACQUISITION.RECORDING_QUEUE_SATURATED` |
| Display supersession | `DAQ.ACQUISITION.DISPLAY_FRAME_SUPERSEDED` |
| Shape/value/continuity failure | `DAQ.ACQUISITION.MALFORMED_CHUNK`, `DAQ.ACQUISITION.SAMPLE_CONTINUITY_VIOLATION` |
| Channel/rate mismatch | `DAQ.ACQUISITION.CHANNEL_ORDER_CHANGED`, `DAQ.ACQUISITION.SAMPLE_CONTINUITY_VIOLATION` |
| Read timeout/failure/disconnect | Existing typed adapter code, including `DAQ.ACQUISITION.READ_TIMEOUT` and `DAQ.HARDWARE.DEVICE_DISCONNECTED` |
| Unexpected worker exception | `DAQ.ACQUISITION.WORKER_FAILURE` |
| Cooperative stop overdue | `DAQ.ACQUISITION.STOP_TIMEOUT` |
| Cleanup failure | `DAQ.ACQUISITION.CLEANUP_FAILED` |

Engine events add session ID, application state, event sequence, timezone-aware
wall time, monotonic time, and bounded context while preserving adapter codes.
When cleanup also fails, the primary acquisition fault remains authoritative
and cleanup details are attached as secondary context.

## Simulator and fake-NI results

The four-channel clean scenario ran three complete engine cycles at
50,000 samples/s/channel for 0.2 seconds per cycle. Each cycle produced exactly
10,000 samples per channel as ten 1,000-sample recording chunks with sequence
numbers 0 through 9 and start indices 0 through 9,000. Across all three cycles,
30,000 samples per channel entered the recording stream with no continuity gap
or live worker after terminal completion.

Deterministic tests additionally cover arbitrary/final chunk sizes, manual stop
races, queue isolation, queue saturation, delay/timeout, disconnect and reset,
malformed reads, rate/order changes, cancellation errors, stop timeout, cleanup
precedence, and exactly-one-terminal publication. Fake NI tests cover task abort,
expected cancellation translation, and repeated cleanup without importing NI
types into the port.

The preserved M03 simulator CLI smoke acquired the same four-channel scenario
as ten 1,000-sample chunks: shape `[4, 10000]`, actual rate 50,000 Hz, cleanup
successful, and canonical SHA-256
`c0a573b1ad18677184c2b33e5b84ef9e69ac2ee81b93b6a8da38b733ffafc622`.

## Verification commands

```text
ruff format --check .
ruff check .
mypy src
lint-imports
pytest tests/unit/application/test_acquisition_engine.py tests/unit/application/test_acquisition_records.py -q --no-cov
pytest tests/contract/test_daq_port_contract.py tests/integration/test_acquisition_engine_pipeline.py -q --no-cov
pytest tests/unit/adapters/ni/test_adapter.py -q --no-cov
pytest -q
python -m build
labdaq --version
labdaq diagnostics --json
python -m labdaq diagnostics --json
labdaq hardware acquire-test --backend simulated --experiment tests/fixtures/configuration/valid-four-channel-synthetic-v1.json --scenario src/labdaq/resources/config/simulator-scenarios/clean-four-channel-v1.json --duration 0.2 --chunk-size 1000 --json
```

Local result: 267 passed, 5 skipped with 87.22% branch-aware coverage; the five
skipped tests are guarded physical NI checks. Formatting, lint, mypy, import
contracts, package build, import isolation, and simulator CLI smoke checks
passed. No physical test was enabled.

## Acceptance criteria

| Criterion | Result |
|---|---|
| 1. Illegal transitions rejected without state change | Satisfied by unit tests |
| 2. Exact duration-based simulated target | Satisfied by unit and integration tests |
| 3. Manual stop and available partial final chunk | Satisfied by deterministic race test |
| 4. Recording sequence/index continuity | Satisfied across arbitrary chunking and repeated integration cycles |
| 5. Slow/absent plotting cannot lose recorded data | Satisfied by display-isolation tests |
| 6. Display supersession follows bounded policy | Satisfied with separate counters |
| 7. Recording chunks never silently discarded | Satisfied; saturation is a typed fault |
| 8. Required injected failures are typed terminal faults | Satisfied by unit and adapter-contract tests |
| 9. Cleanup and worker termination on all paths | Satisfied by cleanup, timeout, and no-leak assertions |
| 10. Primary deterministic-simulator verification | Satisfied |
| 11. Short physical NI engine contract | Pending; hardware not operated |

## Deferred scope and residual risk

- M05 owns durable recording, session folders, persisted counts, event files,
  replay, and storage-failure behavior. Recording enqueue is not persistence.
- Processing, display decimation, plots, GUI, qualification, and new CLI workflow
  remain in later milestones.
- Physical NI task-abort latency, partial data returned during abort, stop
  watchdog behavior, repeated physical engine cycles, and channel alignment are
  unverified until an authorized `LABDAQ_RUN_NI_HARDWARE=1` session.
- No physical safety, wiring, external-conditioner, calibration, or signal-
  quality claim is made.
