# DAQ-M04: Acquisition Engine and Experiment State Machine

Status: Implementation complete; physical NI engine verification pending  
Depends on: DAQ-M00 through DAQ-M03  
Primary verification: deterministic simulated DAQ  
Secondary verification: NI adapter contract tests and a short physical smoke test

## 1. Goal

Implement a headless acquisition runtime that owns experiment state, performs exactly one ordered DAQ read stream, distributes each acquired chunk to recording and live-display consumers, and terminates runs without silent sample loss.

This milestone establishes orchestration only. It does not write TDMS files, process signals, calculate spectra, or expose GUI behavior. DAQ-M05 will attach durable storage to the lossless recording path.

## 2. Why this milestone exists

The acquisition loop is the temporal authority for the application. It must prevent GUI timing, plotting load, file-system latency, or operator actions from corrupting the ordered sample stream.

The problem can be expressed in three equivalent forms:

- As a sampled-data system, sample index is the conserved coordinate and chunk boundaries are implementation details.
- As a producer-consumer pipeline, the DAQ reader is the sole producer and recording is the lossless consumer.
- As a network-capture system, display frames may be coalesced or discarded, but source packets destined for the authoritative record may not be silently dropped.

## 3. Scope

DAQ-M04 shall provide:

- An explicit experiment state machine
- A blocking acquisition worker isolated from callers and future GUI threads
- Monotonic chunk sequencing and sample-index accounting
- Duration-based and operator-requested stopping
- One fan-out path to a lossless recording queue and a bounded live-display queue
- Structured progress, warning, state-transition, completion, and fault events
- Explicit behavior for queue pressure, read timeout, malformed data, task failure, disconnect, cancellation, and cleanup failure
- Headless tests using the simulated DAQ
- Contract tests proving that the NI adapter can participate without changing engine behavior

## 4. Non-goals

The milestone shall not implement:

- TDMS, CSV, JSON manifest, or event-file persistence
- Calibration, filtering, decimation algorithms, metrics, FFT, or PSD
- PySide6, PyQtGraph, or any GUI component
- Hardware qualification workflows
- Trigger prehistory or pre-trigger buffering
- Multiple DAQ tasks for one experiment
- Automatic retry after a hardware or continuity fault
- Recovery of interrupted sessions from disk

Minimal in-memory consumers, null consumers, and injected test consumers are permitted only to verify the queue contracts that DAQ-M05 will use.

## 5. Architectural boundaries

### 5.1 Application ownership

The acquisition engine belongs in the application layer. It may depend on domain records, ports, standard-library concurrency primitives, and logging. It shall not import:

- `nidaqmx`
- PySide6 or other GUI packages
- TDMS or file-format libraries
- SciPy processing functions
- Concrete storage adapters

### 5.2 Adapter ownership

The NI and simulated adapters continue to implement the DAQ port defined in DAQ-M02 and DAQ-M03. NI task creation, stream readers, driver exceptions, and task cleanup remain inside the NI adapter.

### 5.3 Single-read invariant

One experiment run shall create one configured DAQ task and one ordered read loop. Recording and display must consume data derived from that same read stream. They shall not open independent DAQ tasks or reread hardware channels.

## 6. Required domain and application records

The exact module names may follow the repository conventions, but the implementation shall provide equivalent immutable records.

### 6.1 `AcquisitionChunk`

Required fields:

- `session_id`
- `sequence_number`, starting at zero and incrementing by one
- `start_sample_index`, starting at zero
- `sample_count`
- `sample_rate_hz`
- `channel_order`
- `samples`, shaped `[channels, samples]`
- Optional monotonic read-completion timestamp for runtime telemetry

Required invariants:

- `sample_count > 0`
- `samples.shape == (len(channel_order), sample_count)`
- `start_sample_index` is nonnegative
- Sample arrays are contiguous or explicitly normalized before publication
- Published sample arrays shall not be mutated by downstream consumers
- No timestamps are generated per sample when the relative time can be reconstructed from sample index and sample rate

Relative time is:

```text
t_seconds = sample_index / sample_rate_hz
```

### 6.2 `AcquisitionStatus`

At minimum:

- Current state
- Session identity
- Requested and actual sample rate
- Requested duration when present
- Target sample count when duration-based
- Acquired sample count per channel
- Enqueued recording sample count
- Published display sample count
- Dropped or superseded display-frame count
- Recording-queue occupancy and capacity
- Display-queue occupancy and capacity
- Stop-request status
- Last warning or fault code when present

### 6.3 `AcquisitionCompletion`

At minimum:

- Terminal state
- Completion reason
- Start and stop wall-clock times
- Total acquired samples per channel
- Total recording samples accepted by the lossless queue
- Final sequence number
- Display frames published and dropped
- Warnings and fault summary
- Cleanup outcome

The completion record describes acquisition orchestration. DAQ-M05 will later add persisted-sample counts and storage finalization results.

### 6.4 Events

Events shall be immutable, structured, and suitable for later serialization. Each event shall include:

- Event type or code
- Session identity
- State
- Wall-clock timestamp
- Monotonic timestamp when ordering precision is useful
- Human-readable message
- Structured context values

## 7. Experiment state machine

### 7.1 States

Required states:

- `IDLE`
- `CONFIGURED`
- `ARMED`
- `RUNNING`
- `STOPPING`
- `COMPLETED`
- `FAULTED`

### 7.2 State meaning

**IDLE**  
No active experiment configuration or reserved DAQ task exists.

**CONFIGURED**  
The experiment configuration has passed domain and hardware-profile validation. No acquisition task is running.

**ARMED**  
The adapter has successfully configured and reserved the DAQ task. Queues and runtime counters are initialized. No samples have been accepted.

**RUNNING**  
The task has started and the acquisition worker may publish chunks.

**STOPPING**  
No new normal-duration work is scheduled. The worker is completing a bounded final read or drain operation, stopping the adapter, and finalizing runtime counters.

**COMPLETED**  
Acquisition ended normally due to requested duration or manual stop. Resources have been released.

**FAULTED**  
Acquisition ended because continuity, hardware, queue, timeout, cancellation, or cleanup requirements could not be maintained. Resources have been released as far as possible.

### 7.3 Required transitions

```text
IDLE -> CONFIGURED       configure(validated_configuration)
CONFIGURED -> ARMED      arm()
ARMED -> RUNNING         start()
RUNNING -> STOPPING      request_stop() or target sample count reached
STOPPING -> COMPLETED    final chunk handled and cleanup succeeds
ARMED -> FAULTED         start/configuration failure after reservation
RUNNING -> FAULTED       read, continuity, queue, disconnect, or task failure
STOPPING -> FAULTED      bounded stop or cleanup failure
CONFIGURED -> IDLE       reset()
ARMED -> IDLE            disarm/reset with successful cleanup
COMPLETED -> IDLE        reset()
FAULTED -> IDLE          reset() after fault information is retained externally
```

Any transition not explicitly permitted shall be rejected with a typed illegal-transition error and shall not change state.

### 7.4 Command semantics

- `configure`, `arm`, and `start` are not interchangeable.
- `request_stop` is idempotent. Repeated stop requests must not create repeated adapter stop calls or duplicate terminal events.
- `reset` is forbidden while the worker is running.
- A completed or faulted engine cannot be restarted until reset and reconfigured.
- State transitions must be serialized so concurrent callers cannot produce contradictory states.

## 8. Acquisition worker

### 8.1 Execution model

The reference implementation should use one standard-library worker thread for the blocking DAQ read loop, plus thread-safe queues and cancellation primitives. The public application service shall remain independent of GUI event loops.

The implementation may choose a different internal concurrency mechanism only if it preserves the same observable contract and remains compatible with a blocking NI adapter.

### 8.2 Read loop

For each read:

1. Determine the requested number of samples from configured chunk size and remaining duration target.
2. Invoke the DAQ port once.
3. Validate channel count, sample count, shape, numeric type, and finite index progression.
4. Construct one immutable `AcquisitionChunk`.
5. Publish the same logical chunk to the recording path.
6. Publish a bounded display representation or reference without blocking acquisition on display behavior.
7. Advance the acquired-sample counter after a valid DAQ read. Advance the recording-accepted counter only after successful lossless-queue enqueue.
8. If enqueue fails, preserve the difference between acquired and recording-accepted counts in the fault context.
9. Emit progress and queue-health events at a bounded cadence.

The engine shall never infer acquired samples from elapsed wall time. It shall count acquired samples from validated adapter returns and track recording acceptance separately. A chunk that cannot enter the recording queue must produce an explicit fault and must never be reported as recorded.

### 8.3 Duration-based runs

Duration shall be converted to a target sample count using the actual configured sample rate before entering `RUNNING`.

The engine shall request no more than the remaining target sample count. The final duration chunk may be shorter than the normal chunk size. The engine shall not pad, duplicate, or silently trim adapter data.

If the adapter returns more samples than requested, the engine shall treat this as an adapter-contract violation unless the DAQ port explicitly defines a safe bounded behavior.

### 8.4 Manual stop

A manual stop shall:

1. Atomically record the stop request.
2. Transition from `RUNNING` to `STOPPING` once.
3. Interrupt or bound the active read according to the DAQ-port cancellation contract.
4. Accept one final synchronized partial chunk when valid samples are available.
5. Publish no zero-length chunk.
6. Stop and release the adapter task.
7. Emit exactly one terminal completion or fault event.

A final chunk may be shorter than the configured chunk size. It must contain only valid samples returned by the adapter and must continue the sequence and sample indices without a gap.

### 8.5 Cleanup

Cleanup shall be attempted in all success and failure paths. Adapter stop and close operations shall be safe to call through guarded idempotent logic. Cleanup failures must not replace the original fault, but must be added to the completion or fault context.

## 9. Fan-out and queue policy

### 9.1 Lossless recording queue

The recording queue is the authoritative downstream path.

Required behavior:

- It is bounded by configuration or an application default.
- Chunks are never evicted, overwritten, coalesced, or reordered.
- Enqueue order equals acquisition sequence order.
- The queue may apply bounded backpressure for a configured interval.
- If capacity cannot be recovered within that interval, acquisition shall fault explicitly rather than silently discard samples.
- The fault context shall report queue capacity, occupancy, blocked duration, last accepted sequence, and acquired sample count.

Until DAQ-M05 supplies the TDMS recorder, tests shall attach an injected consumer that proves the contract.

### 9.2 Live-display queue

The live-display queue is advisory and must never control acquisition timing.

Required behavior:

- It is bounded and nonblocking from the acquisition worker perspective.
- When full, the oldest unpublished display item may be replaced by newer data.
- Every discarded or superseded display item increments a display-drop counter.
- Display loss is reported separately from acquisition or recording loss.
- Display pressure shall not transition the experiment to `FAULTED` unless memory or internal invariants are violated.
- A paused or absent display consumer shall not affect recording continuity.

The queue may contain raw chunks or a lightweight display envelope. Actual numerical decimation belongs to DAQ-M07. M04 may only coalesce or select whole chunk references to bound display latency.

### 9.3 Data ownership

Fan-out should avoid unnecessary copies of four-channel raw arrays. The preferred design publishes references to an immutable chunk. Any consumer that needs mutation must copy locally.

## 10. Continuity and integrity invariants

For every accepted chunk `n`:

```text
sequence_number[n] = sequence_number[n - 1] + 1
start_sample_index[0] = 0
start_sample_index[n] = start_sample_index[n - 1] + sample_count[n - 1]
```

Additional requirements:

- All channels in a chunk contain the same sample count.
- Channel order remains constant for the entire run.
- Actual sample rate remains constant unless the DAQ port explicitly supports and reports a rate change. V1 shall reject mid-run rate changes.
- Counters are per-channel sample counts, not the total number of scalar values across all channels.
- Chunk size may vary only for bounded final reads or adapter-defined conditions.
- Any detected discontinuity, malformed shape, channel-order change, or invalid sample count faults the run before the malformed chunk enters the recording queue.

## 11. Required event and fault vocabulary

Names may follow repository conventions, but the implementation shall distinguish at least the following conditions.

### 11.1 Informational events

- `ACQUISITION_CONFIGURED`
- `ACQUISITION_ARMED`
- `ACQUISITION_STARTED`
- `ACQUISITION_PROGRESS`
- `STOP_REQUESTED`
- `FINAL_CHUNK_ACCEPTED`
- `ACQUISITION_COMPLETED`
- `ACQUISITION_RESET`

### 11.2 Warnings

- `DISPLAY_FRAME_SUPERSEDED`
- `RECORDING_QUEUE_PRESSURE`
- `STOP_REQUEST_REPEATED`
- `CLEANUP_WARNING`

### 11.3 Faults

- `ILLEGAL_STATE_TRANSITION`
- `DAQ_READ_TIMEOUT`
- `DAQ_TASK_FAILURE`
- `DAQ_DEVICE_DISCONNECTED`
- `MALFORMED_ACQUISITION_CHUNK`
- `SAMPLE_CONTINUITY_VIOLATION`
- `CHANNEL_ORDER_CHANGED`
- `RECORDING_QUEUE_SATURATED`
- `ACQUISITION_WORKER_FAILURE`
- `STOP_TIMEOUT`
- `DAQ_CLEANUP_FAILURE`

Translated NI driver errors shall retain the original driver code and diagnostic text in structured context without exposing NI exception types outside the adapter boundary.

## 12. Public application service

Provide an application-facing service with equivalent operations:

```text
configure(configuration) -> AcquisitionStatus
arm() -> AcquisitionStatus
start() -> AcquisitionStatus
request_stop(reason) -> AcquisitionStatus
wait_for_terminal(timeout) -> AcquisitionCompletion
status() -> AcquisitionStatus
reset() -> AcquisitionStatus
subscribe_events(consumer) -> subscription
recording_stream() -> lossless queue or consumer attachment point
display_stream() -> bounded queue or consumer attachment point
```

The exact API may vary to match established repository conventions. The behavioral separation and state restrictions are mandatory.

Calls used by the future GUI shall return promptly. Blocking waits must be explicit and must not occur on the GUI thread.

## 13. Configuration added by this milestone

Runtime configuration shall define and validate at least:

- Acquisition chunk size in samples per channel
- DAQ read timeout
- Stop timeout
- Recording-queue capacity in chunks or bytes
- Recording-queue backpressure timeout
- Display-queue capacity
- Progress-event interval
- Optional run duration

Defaults shall be appropriate for four channels at 50 kS/s/channel, but no acceptance claim shall depend solely on an untested default. Queue sizing should be expressed in both chunks and estimated memory where practical.

## 14. Testing requirements

### 14.1 State-machine unit tests

Test every allowed transition and representative forbidden transitions, including:

- Start before configure or arm
- Double configure without reset
- Stop while idle
- Reset while running
- Restart after completed without reset
- Repeated stop requests
- Fault followed by reset

### 14.2 Continuity tests

Using the deterministic simulator:

- Reconstruct a continuous run across multiple fixed chunk sizes.
- Verify sequence numbers and sample indices for arbitrary chunk sizes.
- Verify exact duration target with a shorter final chunk.
- Verify four aligned channels shaped `[4, samples]`.
- Verify no duplicate or skipped sample index after manual stop.
- Verify channel-order changes and malformed shapes fault before enqueue.

### 14.3 Queue-isolation tests

- Block the display consumer and prove acquisition and recording counts remain continuous.
- Fill the display queue and verify old display items are superseded, drop counts increase, and the run completes.
- Slow the recording consumer within the allowed buffer window and verify no loss.
- Stall the recording consumer beyond the backpressure limit and verify an explicit terminal fault with no silent eviction.
- Verify recording sequence order under concurrent consumption.

### 14.4 Stop and race tests

- Stop before the first read completes.
- Stop between reads.
- Stop during a delayed read.
- Issue multiple concurrent stop requests.
- Reach the duration target while a manual stop is requested.
- Verify exactly one terminal event and one cleanup sequence.

### 14.5 Fault-injection tests

Use DAQ-M02 scenarios or adapter doubles for:

- Reader timeout
- Delayed read
- Simulated disconnect
- DAQ task exception
- Worker exception
- Zero-length or malformed read
- Recording-queue saturation
- Stop timeout
- Cleanup failure following an earlier primary fault

Each fault test shall assert terminal state, fault code, preserved counters, cleanup attempt, and absence of silent continuation.

### 14.6 Integration tests

- Run the complete engine with the simulated DAQ for four channels at 50 kS/s/channel.
- Attach independent recording and display consumers.
- Verify the recording consumer receives every accepted sample exactly once.
- Verify display pressure does not alter the recording result.
- Repeat configure, arm, start, complete, reset cycles to expose leaked workers or queues.

### 14.7 NI contract tests

When NI hardware is available:

- Configure and run a short four-channel acquisition through the same engine API.
- Verify reported channel order, actual rate, chunk shape, and count continuity.
- Perform repeated manual starts and stops and verify task release.
- Disconnect or simulate task failure only where safe and practical.

Hardware tests shall be separately marked so the normal suite remains runnable without NI software or hardware.

## 15. Acceptance criteria

DAQ-M04 is accepted when all of the following are demonstrated.

1. Illegal transitions are rejected without changing state.
2. A duration-based simulated run acquires exactly its target number of samples per channel.
3. A manual stop produces an orderly terminal result and a valid final partial chunk when samples are available.
4. Every recording chunk has continuous sequence and sample indices across arbitrary chunk sizes.
5. Slow, paused, or absent plotting cannot slow the acquisition reader or cause recorded-data loss.
6. Display frames may be superseded only through the declared bounded-display policy, with separate drop accounting.
7. Recording chunks are never silently discarded. Sustained recording backpressure becomes an explicit fault.
8. Reader timeout, task failure, forced disconnect, malformed data, queue saturation, and cleanup failure become typed terminal faults.
9. Success and failure paths release the adapter task and terminate worker activity.
10. The engine is verified primarily against the deterministic simulator.
11. The NI adapter passes the same engine contract during a short physical acquisition when hardware is available.
12. Core and application imports remain free of NI, GUI, storage-format, and signal-processing dependencies.
13. Focused tests and the full existing test suite pass.

## 16. Required documentation updates

Implementation shall update:

- The roadmap milestone status and acceptance evidence
- Architecture documentation for worker ownership, state transitions, fan-out, and queue policy
- Developer documentation for starting, stopping, observing, and testing the engine
- Event and fault-code documentation
- Configuration schema documentation for queue and timeout fields
- An ADR recording the single-read, dual-consumer policy and why display data may be superseded while recording data may not
- An ADR recording stop, terminal-state, and cleanup semantics if these are not already covered

A state-transition diagram and producer-consumer data-flow diagram shall be included in the milestone or architecture documentation.

## 17. Completion evidence expected from Codex

The implementation report shall include:

- Files added and modified
- Final state transition table
- Queue capacities and backpressure behavior
- Sample-continuity invariants
- Fault codes implemented
- Focused test command and result
- Full test-suite command and result
- Simulated run configuration, sample count, display-drop count, and recording continuity result
- NI hardware result, or a clear statement that physical verification remains pending
- Remaining assumptions and deferred work

## 18. Implementation prompt

> Implement DAQ-M04 only, using `DAQ-Software-Roadmap.md`, the repository architecture documents, and `Milestones/DAQ-M04-acquisition-engine-and-state-machine.md` as authoritative requirements. First inspect the completed DAQ-M00 through DAQ-M03 implementation and tests. Preserve all established domain records, DAQ-port contracts, adapter isolation, and existing behavior. Implement the headless acquisition state machine, worker, chunk continuity accounting, lossless recording queue contract, bounded live-display queue contract, duration and manual-stop behavior, structured events, fault handling, cleanup, tests, ADRs, and documentation. Do not implement TDMS persistence, signal conditioning, plotting algorithms, GUI behavior, qualification workflows, or later milestone features. Use the deterministic simulator for primary acceptance and add NI hardware contract tests behind an explicit hardware marker. Run focused tests and the full suite. Report changed files, architecture decisions, verification results, unresolved assumptions, and any criterion not physically verified.
