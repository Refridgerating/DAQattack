# DAQ-M05: Durable Recording, Metadata, Parsing, and Replay

Status: implementation complete; physical NI recording verification pending  
Depends on: DAQ-M00 through DAQ-M04  
Produces the first complete vertical slice: configure, acquire, stop, save, reopen, verify, and replay

## 1. Goal

Create a trustworthy, self-contained source record for every acquisition. A session must remain interpretable without the original hardware, the original GUI state, or undocumented operator knowledge. Recorded samples, metadata, and events must remain synchronized, verifiable, and recoverable after normal stop, acquisition fault, storage fault, or interrupted execution.

This milestone is complete only when a deterministic simulated run and a short physical NI run can be written, closed, reopened, validated, and replayed with exact sample equality.

## 2. Engineering rationale

The supplied legacy acquisition writes four independent text files. The current outputs contain different channel lengths:

- `mydatax.txt`: 1,120,000 samples
- `mydatay.txt`: 1,120,000 samples
- `mydataz.txt`: 1,100,000 samples
- `mydatap.txt`: 1,100,000 samples

That pattern cannot establish one authoritative synchronized measurement record. It also separates sampling data from rate, channel order, calibration provenance, hardware identity, and completion state.

M05 replaces that pattern with one append-only multichannel record and one explicit integrity protocol. In computer-science language, this is an ordered log under partial failure. In measurement-science language, it is preservation of traceability and synchronized sample identity. In database language, each accepted acquisition chunk is a transaction whose ordering and extent must be reconstructable.

## 3. Normative assumptions

1. “Raw” means the unconditioned synchronized voltage samples returned by the DAQ adapter, stored before calibration, filtering, detrending, or unit conversion.
2. Raw voltage is the V1 source record. Preserving ADC integer codes is not required unless the NI adapter already exposes and validates them through the common data-chunk contract.
3. Samples are stored as IEEE 754 float64 unless an earlier milestone has already established another lossless canonical dtype.
4. Sample index is the authoritative relative-time coordinate. Per-sample wall-clock timestamps shall not be synthesized.
5. Relative time is reconstructed as `sample_index / actual_sample_rate_hz`.
6. Wall-clock timestamps identify session start, session end, and discrete events.
7. A clean manual stop is a valid terminal outcome, not an incomplete session.
8. A session may be replayable even when its terminal outcome is faulted or interrupted, but only through the last verified synchronized sample.
9. M05 does not calibrate or condition samples. It preserves the information required for M06 to do so reproducibly.

These are conventional engineering choices rather than first-principles necessities. The critical invariants are synchronization, provenance, loss detection, and immutable raw data.

## 4. Required outcomes

M05 shall provide:

- Collision-resistant session planning and exclusive session-folder creation
- A streaming TDMS recording adapter
- Atomic manifest creation and update
- An append-only structured event log
- Strict chunk-order and channel-shape validation
- Explicit persisted-sample acknowledgments
- Disk-capacity preflight and runtime monitoring
- Clean finalization for duration completion and manual stop
- Best-effort fault finalization for acquisition and storage errors
- Detection and controlled recovery of interrupted sessions
- A parser that validates metadata, TDMS structure, channel lengths, counts, and available checksums
- A replay service that emits the same sample-chunk record consumed by live downstream services
- A headless CLI session summary and integrity report
- Unit, integration, fault-injection, and physical acceptance evidence

## 5. Scope boundaries

### Included

- Session-path planning and validation
- Session lifecycle metadata
- Raw synchronized voltage persistence
- Acquisition and storage event persistence
- Session inspection, integrity verification, recovery, and replay
- A versioned raw-only processing-recipe snapshot
- CLI inspection of existing sessions

### Excluded

- Calibration and engineering-unit conversion
- Filters, detrending, FFT, PSD, and time-domain metrics
- Plotting and export products
- GUI folder selection or session browsing
- Direct NI-DAQmx logging that bypasses the common recording path
- Legacy text-file import
- User-defined processing recipes
- Live replay timing controls beyond a minimal deterministic replay iterator
- Editing or replacing raw samples during recovery

Legacy `mydata*.txt` files may be retained as test fixtures, but they shall not be accepted as authoritative M05 sessions.

## 6. Architectural placement

The implementation shall preserve the existing layer boundaries.

```text
Acquisition engine from M04
    |
    | DataChunk and AcquisitionEvent
    v
Recording application service
    |-- validates continuity and ownership
    |-- coordinates session lifecycle
    |-- reports persisted progress
    |
    +--> Session writer port
    |        `-- TDMS/session-folder adapter
    |
    +--> Event writer port
    |        `-- JSONL adapter
    |
    +--> Capacity probe port
             `-- filesystem adapter

Session inspection and replay application services
    |
    +--> Session reader port
             `-- TDMS/session-folder adapter
```

The GUI shall not create folders, write TDMS, serialize manifests, append events, calculate checksums, or parse sessions.

The NI adapter shall not own session files. Simulated and physical acquisition shall use the same recording service and storage adapter.

Only one writer may own a session. Readers shall refuse an actively written session unless a later milestone explicitly introduces safe live-tail behavior.

## 7. Recommended records and contracts

Names may follow existing repository conventions. The behavior and invariants below are normative.

### 7.1 Session planning

`SessionPlan`

- Session UUID
- User-selected parent directory
- Requested experiment name
- Sanitized experiment slug
- Planned session-directory name
- Full planned path
- Created-at timestamp
- Path warnings

`SessionPathPolicy`

- Reject empty or unusable experiment names
- Replace or remove Windows-invalid characters
- Reject `.` and `..`
- Reject Windows device names such as `CON`, `PRN`, `AUX`, `NUL`, `COM1` through `COM9`, and `LPT1` through `LPT9`
- Remove trailing spaces and periods
- Apply a documented component-length limit
- Warn when the complete path approaches conservative Windows path limits
- Never silently overwrite an existing path

Recommended directory format:

```text
YYYY-MM-DD_HHMMSS_experiment-slug_<8-char-session-id>/
```

The preview and actual run shall use the same preallocated session UUID. Creation shall use an exclusive directory operation. A collision shall fail explicitly rather than selecting or deleting an existing directory.

### 7.2 Session lifecycle

`SessionCompletionState`

- `INITIALIZING`
- `RECORDING`
- `FINALIZING`
- `COMPLETED`
- `STOPPED`
- `FAULTED`
- `INTERRUPTED`
- `CORRUPT`

`SessionCompletionReason`

Examples:

- `DURATION_REACHED`
- `MANUAL_STOP`
- `ACQUISITION_FAULT`
- `READER_TIMEOUT`
- `HARDWARE_DISCONNECTED`
- `STORAGE_WRITE_FAILED`
- `DISK_SPACE_EXHAUSTED`
- `PROCESS_INTERRUPTED`
- `INTEGRITY_CHECK_FAILED`

State and reason shall be separate. A session can be terminally `STOPPED` with reason `MANUAL_STOP`, or terminally `FAULTED` with reason `STORAGE_WRITE_FAILED`.

### 7.3 Recording input and acknowledgment

The recorder shall accept the M04 synchronized chunk type. At minimum, each chunk shall carry:

- Session ID
- First sample index
- Sample count
- Actual sample rate
- Ordered channel IDs
- Array shaped `[channels, samples]`
- Canonical dtype
- Acquisition monotonic timestamp or elapsed time when available

The recorder shall return a receipt containing:

- Session ID
- First and last accepted sample indices
- Accepted sample count
- Total persisted sample count
- Storage checkpoint sequence
- Write completion timestamp

Persisted progress shall advance only after the storage adapter reports a successful complete multichannel append. A queued or partially attempted chunk shall not increment persisted counts.

### 7.4 Ports

Recommended interfaces:

`SessionWriterPort`

- `create_session(plan, manifest_snapshot)`
- `append_chunk(chunk)`
- `append_event(event)`
- `checkpoint(runtime_summary)`
- `finalize(final_summary)`
- `abort(fault_summary)`

`SessionReaderPort`

- `inspect_session(path)`
- `read_manifest(path)`
- `read_events(path)`
- `iter_raw_chunks(path, chunk_size)`
- `read_channel_metadata(path)`
- `verify_session(path, level)`

`StorageCapacityPort`

- `free_bytes(path)`
- `is_writable(path)`
- `estimate_required_bytes(configuration)`

Application services shall depend on ports, not `nptdms`, `pathlib` filesystem mutation, or platform-specific disk APIs.

## 8. Session directory

Each run shall create:

```text
YYYY-MM-DD_HHMMSS_experiment-name_<session-id>/
|-- manifest.json
|-- raw.tdms
|-- events.jsonl
|-- processing-recipe.json
|-- processed/
|-- plots/
`-- exports/
```

The empty `processed`, `plots`, and `exports` directories may be created at session initialization so later milestones have a stable target. They contain no M05-derived scientific products.

Transient hidden files are permitted for integrity handling:

```text
.recording.lock
.metadata-reserve
manifest.json.tmp
```

They shall not be treated as scientific artifacts. Normal finalization shall remove the lock, reserve, and stale temporary files.

## 9. TDMS layout

Use one TDMS file with one raw-data group.

Recommended structure:

```text
/raw
    /accel_x
    /accel_y
    /accel_z
    /charge_conditioned
```

Channel names shall derive from stable logical channel IDs, not mutable plot labels. Human-readable labels may be stored as properties.

### 9.1 Root properties

- Session UUID
- Session schema version
- Application version
- Requested sample rate
- Actual sample rate
- Channel count
- Ordered channel IDs
- Start time in UTC
- Timezone identifier
- Canonical sample dtype
- Source classification such as `unconditioned_voltage`

### 9.2 Per-channel properties

- Logical channel ID
- Display name
- Physical channel
- Channel order index
- Raw units, normally volts
- Sensor type
- Sensor identifier and serial number when known
- Calibration identifier and version
- Calibration date and source when known
- Terminal configuration
- Input range
- Coupling when applicable
- Conditioning-chain description

The same provenance shall also be present in the manifest. This intentional redundancy allows basic interpretation when either the TDMS properties or human-readable manifest is examined independently. The parser shall compare duplicated critical fields and report disagreement.

### 9.3 Append invariants

Every TDMS append shall contain all configured channels with identical sample counts.

The writer shall reject:

- Missing channels
- Additional channels
- Reordered channels
- Zero-length chunks unless explicitly used as a terminal marker outside TDMS
- Nonfinite dimensions or unsupported dtype
- Sample-index overlap
- Duplicate chunk start
- Sample-index gaps
- Sample-rate changes during a session
- Session-ID mismatch

A chunk shall be written as one synchronized logical segment. If the underlying TDMS library fails during append, the session shall be faulted and no successful receipt shall be returned.

## 10. Manifest requirements

`manifest.json` is the human-readable authoritative description of the session. It shall be schema-versioned and written through an atomic replace operation.

### 10.1 Identity and operator context

- Session UUID
- Experiment name and sanitized slug
- Description
- Operator
- Sample or specimen ID
- Notes
- Parent and session paths as recorded at creation

### 10.2 Time and completion

- Created time
- Acquisition start time
- Acquisition end time
- Timezone
- Requested duration
- Acquired duration calculated from samples and actual rate
- Completion state
- Completion reason
- Fault code and fault summary when applicable

### 10.3 Software and computer provenance

- Application version
- Manifest schema version
- TDMS layout version
- Processing-recipe schema version
- Python version
- Operating system and version
- Host name or stable machine identifier appropriate for the lab
- NI-DAQmx driver version when available

### 10.4 Hardware and acquisition configuration

- Hardware profile ID and revision
- Chassis model, alias, and serial number when available
- Module model, slot, alias, and serial number when available
- Requested and actual sample rate
- Acquisition mode
- Requested duration
- Acquisition chunk size
- Ordered channel configuration snapshot
- Input range
- Terminal configuration
- Trigger configuration snapshot, even when disabled

### 10.5 Sensor and calibration provenance

For every channel:

- Stable logical channel ID
- Physical channel
- Display name
- Sensor type
- Sensor model
- Sensor serial number
- Raw engineering units
- Calibration identifier
- Calibration coefficients or a reference snapshot sufficient for later processing
- Calibration date and source
- Conditioning-chain description

M05 records this information but does not apply it to raw samples.

### 10.6 Integrity and counts

- Acquired sample count per channel
- Persisted sample count per channel
- Common synchronized sample count
- First sample index
- Last sample index
- Detected gap count
- Detected overlap count
- Dropped display-frame count, if reported by M04
- Acquisition warning count
- Acquisition fault count
- Storage warning count
- Storage fault count
- Event count
- Raw logical-data digest when generated
- Final file checksums where practical
- Finalization and verification timestamps

Counts shall be exact at clean finalization. During recording, checkpointed counts may lag the latest successfully appended chunk. The manifest shall identify the checkpoint sequence and time.

## 11. Atomicity and durability protocol

### 11.1 Initialization

1. Validate the parent directory and planned path.
2. Estimate required storage.
3. Verify the parent is writable.
4. Create the session directory exclusively.
5. Create `processed`, `plots`, and `exports` directories.
6. Create `.recording.lock` with session ID, host, process ID, and creation time.
7. Create a small `.metadata-reserve` file.
8. Write `processing-recipe.json`.
9. Atomically write the initial manifest with state `INITIALIZING`.
10. Create the TDMS and event writers.
11. Atomically checkpoint state `RECORDING` before accepting acquisition chunks.

The metadata reserve exists so a disk-full handler can delete it and recover enough space to write a final fault manifest and event. Its size shall be documented and configurable, with a conservative default such as 1 MiB.

### 11.2 Chunk commit

For each chunk:

1. Validate session ownership, channel order, shape, sample rate, and next expected sample index.
2. Check runtime free-space policy at a bounded interval.
3. Append all channels as one synchronized TDMS segment.
4. Update the incremental logical-data digest using a documented canonical byte representation.
5. Update in-memory persisted counts.
6. Return a successful receipt.
7. Periodically flush and checkpoint manifest counts.

A canonical logical-data digest may use channel-major, little-endian float64 bytes preceded by the chunk start index and sample count. The digest definition shall be versioned so verification is reproducible across platforms.

### 11.3 Event commit

Events shall be appended as one complete JSON object per line. Each event shall include:

- Event schema version
- Session ID
- Monotonic sequence number
- UTC timestamp
- Source component
- Severity
- Event code
- Human-readable message
- Sample index when the event corresponds to acquisition position
- Structured details

The writer shall flush at a documented interval and during every terminal transition. A malformed intermediate line is corruption. A truncated final line after abrupt termination may be ignored only with an explicit integrity warning.

### 11.4 Checkpointing

Checkpoint policy shall be configurable by time or chunk count. A default near one second is reasonable for V1. The checkpoint shall:

- Flush the TDMS writer when supported
- Flush the event writer
- Perform an OS-level sync where practical
- Atomically update the manifest with current counts and checkpoint sequence

The implementation shall document the difference between a successful append receipt and survival of sudden power loss before the next checkpoint. It shall not claim stronger durability than the adapter can prove.

### 11.5 Clean finalization

1. Stop accepting new chunks.
2. Drain the lossless recording queue.
3. Append any terminal acquisition event.
4. Transition the manifest to `FINALIZING`.
5. Flush and close TDMS and event writers.
6. Reopen or inspect the finished TDMS.
7. Verify channel lengths and common sample count.
8. Finalize digests and file checksums where configured.
9. Atomically write exact final counts and terminal state.
10. Remove `.recording.lock` and `.metadata-reserve`.

A duration-complete run ends as `COMPLETED`. A clean operator stop ends as `STOPPED` with reason `MANUAL_STOP`.

## 12. Disk-space policy

### 12.1 Preflight estimate

For a fixed-duration run, estimate at minimum:

```text
channels × sample_rate × duration × bytes_per_sample
+ TDMS overhead margin
+ event and metadata margin
+ configured free-space reserve
```

Use a documented conservative multiplier. The estimate is a safety check, not a precise prediction.

For an indefinite manual run, require a configured minimum free-space reserve and continuous runtime checks.

### 12.2 Runtime behavior

- Check free space before starting.
- Recheck at a bounded interval during recording.
- Emit a warning before the hard stop threshold.
- Stop acquisition before all free space is consumed when possible.
- On `ENOSPC`, release `.metadata-reserve` and attempt fault finalization.
- Never continue acquisition after the recorder can no longer persist the lossless stream.
- Never reinterpret display-queue drops as storage loss.

Storage exhaustion shall produce an explicit storage fault. If final metadata cannot be written, the stale active manifest and lock shall allow the parser to identify an interrupted or storage-faulted session later.

## 13. Parsing and integrity verification

The parser shall support two levels.

### 13.1 Fast inspection

Fast inspection reads bounded metadata only:

- Session directory structure
- Manifest and recipe schemas
- Lock state
- Completion state
- TDMS group and channel names
- Channel lengths
- Critical property agreement
- Event count where cheaply available
- Common readable sample extent

### 13.2 Full verification

Full verification may scan all raw data and shall:

- Recompute the logical-data digest
- Verify configured file checksums
- Confirm every channel has equal length
- Confirm manifest counts equal TDMS counts
- Confirm required metadata exists for every channel
- Confirm sample rate and channel order agree across manifest and TDMS
- Validate event sequence monotonicity
- Validate all event lines except a permitted truncated final line
- Report unknown schema versions without guessing

`SessionIntegrityReport` shall include:

- Overall status: `PASS`, `WARNING`, or `FAIL`
- Effective completion state
- Readable common sample count
- Manifest and TDMS counts
- Per-channel lengths
- Missing or contradictory metadata
- Checksum results
- Event-log results
- Recoverability classification
- Actionable fault codes and messages

The parser shall not silently repair or reinterpret contradictory source data.

## 14. Interrupted-session recovery

Recovery shall be explicit and metadata-only. Raw samples shall never be edited, padded, interpolated, or reordered.

A session is a recovery candidate when:

- The manifest remains `INITIALIZING`, `RECORDING`, or `FINALIZING`
- A stale `.recording.lock` exists
- The writer process is no longer active or the application is reopening after restart
- At least one complete synchronized TDMS segment is readable

Recovery procedure:

1. Refuse recovery if the session appears actively owned by a live writer.
2. Parse the longest trustworthy synchronized prefix supported by the TDMS reader.
3. Discard no valid raw segment.
4. Ignore only an incomplete trailing TDMS segment when the library can distinguish it safely.
5. Determine the common channel length.
6. Inspect the event log and ignore only a truncated final line with warning.
7. Record original manifest state and checkpoint counts.
8. Atomically update the manifest to `INTERRUPTED` or `FAULTED` with recovery details.
9. Preserve the original raw file and event log.
10. Remove the stale lock only after the recovery manifest is durable.

If no trustworthy synchronized prefix can be identified, classify the session as `CORRUPT` and refuse replay.

Recovery does not guarantee salvage from arbitrary byte-level TDMS corruption. It guarantees detection and preservation of any complete readable prefix that the selected TDMS library can verify.

## 15. Processing-recipe snapshot

M05 shall create `processing-recipe.json` but shall not implement M06 signal processing.

Until M06 establishes the validated baseline recipe, M05 shall store a schema-valid, versioned raw-only declaration such as:

```json
{
  "schema_version": 1,
  "recipe_id": "raw-only-v1",
  "recipe_version": 1,
  "stages": [],
  "applies_calibration": false,
  "source_units": "V",
  "output_units": "V"
}
```

M06 may use a different recipe for future sessions. It shall not rewrite the recipe snapshot of an already recorded M05 session.

## 16. Replay behavior

Replay shall expose recorded raw samples through the same downstream sample-chunk record used by live acquisition. Replay is not a fake NI device and does not need to implement hardware discovery or configuration.

### Required behavior

- Preserve session ID, channel IDs, channel order, units, sample rate, and provenance
- Start at sample index zero for a complete session
- For recovered sessions, stop at the verified common synchronized extent
- Produce arrays shaped `[channels, samples]`
- Allow replay chunk size to differ from original recording chunk size
- Never change sample values due to rechunking
- Be deterministic and bounded-memory
- Support cancellation
- Emit replay status separately from source acquisition events
- Refuse corrupt or unsupported sessions

### Equality requirement

For simulator reference data:

```text
concatenate(recorded replay chunks) == concatenate(original acquired chunks)
```

Equality is exact for stored float values. No tolerance-based comparison is acceptable for the raw persistence round trip.

### Event handling

Source events remain immutable. Replay consumers may receive source events at the corresponding sample indices, but replay shall not append new events to the original session.

## 17. CLI surface

Add a headless command consistent with the existing CLI conventions.

Recommended command:

```text
labdaq session summary <session-path> [--verify] [--json]
```

Default output shall include:

- Session ID and experiment name
- Completion state and reason
- Start and end times
- Hardware profile
- Requested and actual sample rate
- Channel order and raw units
- Per-channel and common sample counts
- Acquired duration
- Event, warning, and fault counts
- Integrity status
- Recoverability status

`--verify` performs full verification. `--json` emits a stable machine-readable summary.

A minimal replay-check command may be added only if needed for acceptance testing. It shall not add plotting, filtering, or export behavior.

## 18. Error taxonomy

Add typed application-level errors or fault codes for at least:

- Invalid session parent
- Invalid or unsafe session name
- Session path collision
- Session already owned
- Session ID mismatch
- Unsupported session schema
- Unsupported TDMS layout
- Manifest parse failure
- Manifest and TDMS metadata mismatch
- Channel-order mismatch
- Channel-length mismatch
- Sample-index gap
- Sample-index overlap
- Duplicate chunk
- TDMS append failure
- Event-log append failure
- Manifest atomic-update failure
- Insufficient preflight disk space
- Runtime disk-space threshold reached
- Disk full
- Checksum mismatch
- Stale recording lock
- Active writer detected
- Session not replayable
- Session corruption

Errors shall contain actionable context without embedding raw third-party exception objects in domain records.

## 19. Testing requirements

### 19.1 Unit tests

- Experiment-name sanitization
- Windows reserved names and invalid characters
- Stable session preview using a preallocated UUID
- Exclusive creation and collision rejection
- Storage-size estimation
- Completion-state transitions
- Chunk continuity, overlap, duplicate, and gap rejection
- Channel order and shape validation
- Atomic manifest replace behavior
- Event JSONL encoding and final-line truncation handling
- Logical-data digest reproducibility
- Integrity-report classification
- Replay rechunking

### 19.2 Storage adapter integration tests

- Write multiple synchronized chunks and reopen TDMS
- Verify channel order and exact values
- Verify all channel lengths remain equal
- Verify TDMS properties match the manifest
- Verify arbitrary acquisition chunk boundaries
- Verify arbitrary replay chunk boundaries
- Verify clean manual stop
- Verify duration completion
- Verify empty run handling
- Verify repeated create, finalize, and reopen cycles

### 19.3 Fault-injection tests

Provide injectable filesystem and writer faults rather than depending only on real disk failure.

Required cases:

- Failure before session-directory creation
- Failure after initial manifest but before TDMS creation
- Failure on a selected chunk append
- Failure during event append
- Failure during manifest checkpoint
- Failure during finalization
- Runtime disk-space warning threshold
- Runtime disk-full threshold
- Abrupt subprocess termination after several committed chunks
- Stale lock on reopen
- Truncated final event line
- Truncated or unreadable final TDMS segment
- Manifest count larger than TDMS count
- Manifest count smaller than TDMS count
- One channel shorter than the others
- Checksum mismatch
- Unsupported schema version

The system shall fail closed. It shall never report a complete session when counts or integrity checks disagree.

### 19.4 Deterministic round-trip test

Use the M02 simulator with a fixed seed and known four-channel signal.

1. Acquire through M04.
2. Record through M05.
3. Finalize.
4. Reopen.
5. Full-verify.
6. Replay with a different chunk size.
7. Concatenate replay chunks.
8. Assert exact array equality, channel order, sample rate, sample count, and session identity.

### 19.5 Bounded-memory test

Record and replay a session materially larger than one application chunk. Assert that neither the recorder nor replay service loads the full session into memory. The test may use instrumentation or a constrained fake reader to prove bounded reads.

### 19.6 Windows acceptance tests

On the Windows target environment:

- User-selected parent directory works
- Spaces and Unicode in parent paths work
- Sanitized experiment names are predictable
- Existing folders are never overwritten
- Atomic manifest replacement works with the selected file-handling strategy
- Long-path warnings are clear
- Session can be reopened after application restart

### 19.7 Physical NI acceptance test

Using the cDAQ-9171 and NI-9239:

- Record four simultaneous channels for a short bounded run
- Store requested and actual sampling rate
- Store physical channel order and hardware identity
- Stop cleanly
- Reopen and verify equal channel lengths
- Replay all samples
- Confirm manifest sample count equals TDMS sample count
- Confirm no NI task remains reserved after finalization

The physical signal values need not be scientifically calibrated in M05. That belongs to M06 and M08.

## 20. Acceptance criteria

M05 is accepted only when all applicable criteria pass.

### Automated acceptance

- A deterministic simulated run records, closes, reopens, verifies, and replays with exact sample equality.
- Changing replay chunk size does not change reconstructed samples.
- Channel divergence cannot be written as a successful synchronized chunk.
- Manifest and TDMS disagreements are detected.
- Manual stop produces terminal state `STOPPED`, exact counts, and a replayable session.
- Duration completion produces terminal state `COMPLETED`.
- Acquisition fault produces a finalized `FAULTED` session when storage remains available.
- Injected write failure never increments persisted counts for the failed chunk.
- Abrupt termination leaves a session identifiable as interrupted and recoverable to a verified common prefix when readable.
- Disk-space preflight and runtime threshold behavior are explicit and tested.
- A disk-full fault does not produce a false complete state.
- Session creation never overwrites an existing directory.
- Parser and replay operate without NI-DAQmx or GUI dependencies.
- Large-session replay uses bounded memory.
- The full existing test suite passes.

### Physical acceptance

- One short four-channel NI run is recorded and reopened.
- All channels have equal length and declared order.
- Requested and actual sampling rate are present.
- Hardware and channel provenance are present.
- Exact stored values survive close and replay.
- The NI task is released after completion.

If hardware is unavailable, Codex shall mark physical acceptance as not verified rather than claiming completion.

## 21. Documentation and ADRs

Update or add:

- `docs/storage/session-format.md`
- `docs/storage/manifest-schema.md`
- `docs/storage/tdms-layout.md`
- `docs/storage/recovery.md`
- `docs/cli/session-summary.md`
- Architecture diagrams showing recorder and replay boundaries
- User guidance for choosing a session parent directory
- Troubleshooting for interrupted, disk-full, and corrupt sessions

Recommended ADRs:

1. TDMS plus JSON manifest and JSONL events as the V1 session format
2. One synchronized multichannel writer instead of one file per channel
3. Sample index as authoritative relative time
4. Atomic manifest checkpoints and append-only event logging
5. Explicit interrupted-session recovery without raw-data mutation
6. Raw-only recipe snapshot before M06 processing is introduced
7. Common live and replay sample-chunk contract

## 22. Implementation sequence

1. Inspect M01 data records and M04 chunk and event contracts.
2. Add storage-domain records and error taxonomy.
3. Add session planning and path policy.
4. Add writer, reader, and capacity ports.
5. Implement atomic JSON and JSONL adapters.
6. Implement the TDMS session adapter.
7. Implement the recording application service.
8. Integrate recorder receipts and storage faults with M04.
9. Implement fast inspection and full verification.
10. Implement interrupted-session recovery.
11. Implement deterministic chunked replay.
12. Add the session-summary CLI.
13. Add fault-injection and subprocess interruption tests.
14. Run the simulator round trip.
15. Run the physical NI acceptance test when hardware is available.
16. Update roadmap, milestone status, architecture docs, ADRs, and user docs.

Do not begin M06 conditioning, M07 plotting/export, M09 GUI browsing, or M10 live display work.

## 23. Codex implementation prompt

> Implement DAQ-M05 only, using `DAQ-Software-Roadmap.md`, the repository architecture documents, and `Milestones/DAQ-M05-durable-recording-metadata-parsing-replay.md` as authoritative requirements. First inspect the completed M00 through M04 implementation, especially the domain records, synchronized data-chunk contract, acquisition events, state machine, and recording queue. Preserve all completed behavior and import boundaries. Implement collision-resistant session planning, exclusive session creation, streaming synchronized TDMS recording, atomic manifest checkpoints, append-only JSONL events, disk-capacity checks, clean and faulted finalization, explicit interrupted-session recovery, session parsing and integrity reporting, deterministic bounded-memory replay, the session-summary CLI, tests, ADRs, and documentation. The recorder must reject channel-order changes, unequal channel lengths, duplicate chunks, gaps, overlaps, session-ID mismatch, and sample-rate changes. Persisted counts may advance only after a complete successful multichannel append. Raw samples must remain immutable and unconditioned. Do not implement calibration, filtering, plotting, export, GUI, or later milestone features. Run focused tests and the complete test suite. If NI hardware is available, perform one short four-channel physical round trip. Report changed files, architecture decisions, session format, fault behavior, exact verification commands and results, remaining assumptions, and every acceptance criterion that could not be physically verified.

## 24. Definition of done

The milestone is done when the project has one trustworthy answer to the question:

> Given this session directory, what was acquired, in what order, at what rate, from which hardware and sensors, how did the run end, are the stored samples internally consistent, and can the exact synchronized raw sample stream be replayed without the original DAQ?

If any part of that answer depends on the GUI, operator memory, separate channel text files, or silent assumptions, M05 is not complete.
