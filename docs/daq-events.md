# DAQ Events and Fault Codes

DAQ events contain a stable code, severity, backend, operation, timezone-aware
timestamp, bounded structured context, and optional simulator-local sample
index or read sequence. Errors retain the equivalent event.

M04 acquisition events add a session ID, engine state, monotonically increasing
event sequence, and monotonic timestamp. Adapter events keep their existing
codes and diagnostic context when the engine enriches and republishes them.

M02 defines:

- `DAQ.ACQUISITION.LIFECYCLE_INVALID`
- `DAQ.HARDWARE.PROFILE_MISMATCH`
- `DAQ.HARDWARE.CONFIGURATION_UNSUPPORTED`
- `DAQ.ACQUISITION.STARTED`
- `DAQ.ACQUISITION.STOPPED`
- `DAQ.ACQUISITION.INPUT_CLIPPED`
- `DAQ.ACQUISITION.SAMPLE_LOSS`
- `DAQ.ACQUISITION.READ_DELAYED`
- `DAQ.ACQUISITION.READ_TIMEOUT`
- `DAQ.HARDWARE.DEVICE_DISCONNECTED`
- `DAQ.ACQUISITION.RESET_COMPLETED`
- `DAQ.ACQUISITION.RESET_FAILED`
- `DAQ.SIMULATOR.SCENARIO_INVALID`
- `DAQ.SIMULATOR.GENERATOR_FAILURE`

M03 adds:

- `DAQ.HARDWARE.PACKAGE_UNAVAILABLE`
- `DAQ.HARDWARE.DRIVER_UNAVAILABLE`
- `DAQ.HARDWARE.BACKEND_ARGUMENT_INVALID`
- `DAQ.HARDWARE.DEVICE_NOT_FOUND`
- `DAQ.HARDWARE.RESOURCE_RESERVED`
- `DAQ.HARDWARE.TASK_VERIFICATION_FAILED`
- `DAQ.HARDWARE.RATE_COERCED`
- `DAQ.HARDWARE.DRIVER_WARNING`
- `DAQ.ACQUISITION.START_FAILED`
- `DAQ.ACQUISITION.PARTIAL_READ`
- `DAQ.ACQUISITION.READ_FAILED`
- `DAQ.ACQUISITION.MALFORMED_READ`
- `DAQ.ACQUISITION.STOP_FAILED`
- `DAQ.ACQUISITION.CLEANUP_FAILED`

M04 adds pending-read cancellation codes:

- `DAQ.ACQUISITION.READ_CANCEL_REQUESTED`
- `DAQ.ACQUISITION.READ_CANCELLED`

M04 adds engine lifecycle and progress codes:

- `DAQ.ACQUISITION.CONFIGURED`
- `DAQ.ACQUISITION.ARMED`
- `DAQ.ACQUISITION.PROGRESS`
- `DAQ.ACQUISITION.STOP_REQUESTED`
- `DAQ.ACQUISITION.STOP_REQUEST_REPEATED`
- `DAQ.ACQUISITION.FINAL_CHUNK_ACCEPTED`
- `DAQ.ACQUISITION.COMPLETED`
- `DAQ.ACQUISITION.FAULTED`
- `DAQ.ACQUISITION.ENGINE_CLOSED`

M04 adds queue, continuity, worker, and terminal-integrity codes:

- `DAQ.ACQUISITION.RECORDING_QUEUE_PRESSURE`
- `DAQ.ACQUISITION.RECORDING_QUEUE_SATURATED`
- `DAQ.ACQUISITION.DISPLAY_FRAME_SUPERSEDED`
- `DAQ.ACQUISITION.SAMPLE_CONTINUITY_VIOLATION`
- `DAQ.ACQUISITION.CHANNEL_ORDER_CHANGED`
- `DAQ.ACQUISITION.MALFORMED_CHUNK`
- `DAQ.ACQUISITION.TARGET_COUNT_INVALID`
- `DAQ.ACQUISITION.WORKER_FAILURE`
- `DAQ.ACQUISITION.STOP_TIMEOUT`
- `DAQ.ACQUISITION.ILLEGAL_STATE_TRANSITION`
- `DAQ.ACQUISITION.STREAM_UNAVAILABLE`
- `DAQ.ACQUISITION.WAIT_TIMEOUT`

M05 adds session orchestration codes:

- `DAQ.SESSION.PLAN_MISMATCH`
- `DAQ.SESSION.RECIPE_MISMATCH`
- `DAQ.SESSION.ILLEGAL_STATE_TRANSITION`
- `DAQ.SESSION.CANCELLED_BEFORE_START`

M05 adds storage lifecycle and write codes:

- `DAQ.STORAGE.INVALID_SESSION_PARENT`
- `DAQ.STORAGE.REMOTE_RECORDING_UNSUPPORTED`
- `DAQ.STORAGE.SESSION_PATH_COLLISION`
- `DAQ.STORAGE.SESSION_ALREADY_OWNED`
- `DAQ.STORAGE.INSUFFICIENT_SPACE`
- `DAQ.STORAGE.SPACE_LOW`
- `DAQ.STORAGE.RUNTIME_SPACE_THRESHOLD`
- `DAQ.STORAGE.DISK_FULL`
- `DAQ.STORAGE.TDMS_APPEND_FAILED`
- `DAQ.STORAGE.HDF5_APPEND_FAILED`
- `DAQ.STORAGE.HDF5_FLUSH_FAILED`
- `DAQ.STORAGE.HDF5_FINALIZATION_FAILED`
- `DAQ.STORAGE.EVENT_APPEND_FAILED`
- `DAQ.STORAGE.MANIFEST_UPDATE_FAILED`
- `DAQ.STORAGE.PERSISTED_COUNT_MISMATCH`
- `DAQ.STORAGE.CONSUMER_STOP_TIMEOUT`

M05 adds verification and recovery codes:

- `DAQ.STORAGE.UNSUPPORTED_SESSION_SCHEMA`
- `DAQ.STORAGE.TDMS_LAYOUT_UNSUPPORTED`
- `DAQ.STORAGE.HDF5_LAYOUT_UNSUPPORTED`
- `DAQ.STORAGE.HDF5_PARSE_FAILED`
- `DAQ.STORAGE.HDF5_COMMIT_INVALID`
- `DAQ.STORAGE.HDF5_UNCOMMITTED_TAIL`
- `DAQ.STORAGE.HDF5_FINALIZATION_INVALID`
- `DAQ.STORAGE.MANIFEST_COUNT_MISMATCH`
- `DAQ.STORAGE.CHANNEL_ORDER_MISMATCH`
- `DAQ.STORAGE.CHANNEL_LENGTH_MISMATCH`
- `DAQ.STORAGE.METADATA_MISMATCH`
- `DAQ.STORAGE.CHECKSUM_MISMATCH`
- `DAQ.STORAGE.EVENT_SEQUENCE_INVALID`
- `DAQ.STORAGE.EVENT_FINAL_LINE_TRUNCATED`
- `DAQ.STORAGE.ACTIVE_WRITER_DETECTED`
- `DAQ.STORAGE.STALE_RECORDING_LOCK`
- `DAQ.STORAGE.SESSION_NOT_RECOVERABLE`
- `DAQ.STORAGE.SESSION_NOT_REPLAYABLE`
- `DAQ.STORAGE.SESSION_CORRUPT`

M06 adds calibration and recipe codes:

- `DAQ.PROCESSING.CALIBRATION_MISSING`
- `DAQ.PROCESSING.CALIBRATION_INVALID`
- `DAQ.PROCESSING.CALIBRATION_CHANNEL_MISMATCH`
- `DAQ.PROCESSING.NOMINAL_CALIBRATION_FALLBACK`
- `DAQ.PROCESSING.CALIBRATION_INPUT_OUT_OF_RANGE`
- `DAQ.PROCESSING.RECORD_SCHEMA_INVALID`
- `DAQ.PROCESSING.RECIPE_SCHEMA_UNSUPPORTED`
- `DAQ.PROCESSING.RECIPE_HASH_MISMATCH`
- `DAQ.PROCESSING.RECIPE_CHANNEL_INCOMPATIBLE`
- `DAQ.PROCESSING.RECIPE_SAMPLE_RATE_INCOMPATIBLE`
- `DAQ.PROCESSING.NONCAUSAL_STAGE_IN_LIVE_PIPELINE`

M06 adds continuity, metric, and spectral codes:

- `DAQ.PROCESSING.PROCESSING_RANGE_INVALID`
- `DAQ.PROCESSING.PROCESSING_CHUNK_GAP`
- `DAQ.PROCESSING.PROCESSING_CHUNK_OVERLAP`
- `DAQ.PROCESSING.PROCESSING_CHANNEL_ORDER_CHANGED`
- `DAQ.PROCESSING.PROCESSING_NONFINITE_INPUT`
- `DAQ.PROCESSING.FILTER_STATE_RESET`
- `DAQ.PROCESSING.METRIC_NOT_EVALUATED`
- `DAQ.PROCESSING.SPECTRAL_RANGE_TOO_SHORT`
- `DAQ.PROCESSING.SPECTRAL_SELECTION_TOO_LARGE`

M07 adds plot and derived-artifact codes:

- `DAQ.PLOT.REQUEST_INVALID`
- `DAQ.PLOT.RANGE_INVALID`
- `DAQ.PLOT.RANGE_OUT_OF_BOUNDS`
- `DAQ.PLOT.CHANNEL_NOT_FOUND`
- `DAQ.PLOT.RECIPE_REQUIRED`
- `DAQ.PLOT.CANCELLED`
- `DAQ.EXPORT.REQUEST_INVALID`
- `DAQ.EXPORT.DESTINATION_INVALID`
- `DAQ.EXPORT.PATH_COLLISION`
- `DAQ.EXPORT.DOCUMENT_INVALID`
- `DAQ.EXPORT.WRITE_FAILED`
- `DAQ.EXPORT.CHECKSUM_FAILED`
- `DAQ.EXPORT.RENDER_FAILED`
- `DAQ.EXPORT.CANCELLED`
- `DAQ.EXPORT.PARTIAL_FAILURE`
- `DAQ.EXPORT.TDMS_WRITE_FAILED`

M08 adds qualification lifecycle, compatibility, and evidence codes:

- `DAQ.QUALIFICATION.QUALIFICATION_STARTED`
- `DAQ.QUALIFICATION.QUALIFICATION_COMPLETED`
- `DAQ.QUALIFICATION.QUALIFICATION_CANCELLED`
- `DAQ.QUALIFICATION.QUALIFICATION_FAILED`
- `DAQ.QUALIFICATION.HARDWARE_IDENTITY_MISMATCH`
- `DAQ.QUALIFICATION.HARDWARE_CAPABILITY_MISMATCH`
- `DAQ.QUALIFICATION.HARDWARE_SELF_TEST_PASSED`
- `DAQ.QUALIFICATION.HARDWARE_SELF_TEST_FAILED`
- `DAQ.QUALIFICATION.HARDWARE_SELF_TEST_NOT_SUPPORTED`
- `DAQ.QUALIFICATION.CHANNEL_BIAS_OUT_OF_RANGE`
- `DAQ.QUALIFICATION.CHANNEL_NOISE_ABOVE_LIMIT`
- `DAQ.QUALIFICATION.CHANNEL_NEAR_CLIPPING`
- `DAQ.QUALIFICATION.CHANNEL_CLIPPING_DETECTED`
- `DAQ.QUALIFICATION.CHANNEL_FROZEN`
- `DAQ.QUALIFICATION.CHANNEL_DROPOUT_DETECTED`
- `DAQ.QUALIFICATION.CHANNEL_NONFINITE_SAMPLE`
- `DAQ.QUALIFICATION.SENSOR_RESPONSE_MISSING`
- `DAQ.QUALIFICATION.SENSOR_AXIS_MAPPING_AMBIGUOUS`
- `DAQ.QUALIFICATION.CHARGE_CONDITIONING_INCOMPATIBLE`
- `DAQ.QUALIFICATION.CROSS_CHANNEL_MISALIGNMENT`
- `DAQ.QUALIFICATION.CROSSTALK_ABOVE_LIMIT`
- `DAQ.QUALIFICATION.THROUGHPUT_GAP_DETECTED`
- `DAQ.QUALIFICATION.THROUGHPUT_CHANNEL_LENGTH_MISMATCH`
- `DAQ.QUALIFICATION.THROUGHPUT_STORAGE_BACKPRESSURE`
- `DAQ.QUALIFICATION.REPORT_STALE`
- `DAQ.QUALIFICATION.REQUIRED_TEST_NOT_RUN`
- `DAQ.QUALIFICATION.BIAS_LIMIT_UNRESOLVED`
- `DAQ.QUALIFICATION.CROSSTALK_LIMIT_UNRESOLVED`
- `DAQ.QUALIFICATION.RENDERING_UNAVAILABLE`
- `DAQ.QUALIFICATION.PLAN_INVALID`
- `DAQ.QUALIFICATION.PLAN_MISMATCH`
- `DAQ.QUALIFICATION.REPORT_INVALID`
- `DAQ.QUALIFICATION.REPORT_READ_FAILED`
- `DAQ.QUALIFICATION.REPORT_WRITE_FAILED`

Clipping and delay are nonterminal warnings. Dropout, timeout, and disconnect
fault the local adapter. Stop remains available for cleanup; reset restores
recoverable scenarios to sample zero.

NI events retain the numeric driver code and bounded driver text when exposed.
The primary operation error remains authoritative when cleanup also fails;
cleanup failures are attached as structured secondary context. NI warnings are
returned as events and are never silently logged or allowed to escape the
adapter as raw warning objects.

Recording pressure is a warning while bounded enqueue is still possible;
saturation is terminal and includes queue capacity/occupancy plus acquired and
recording-accepted counters. Display supersession is nonterminal and separately
counted. Stop timeout is terminal and records forced adapter closure. Exactly
one of `DAQ.ACQUISITION.COMPLETED` or `DAQ.ACQUISITION.FAULTED` terminates an
engine session.

M05 persists acquisition events in a separate version-1 JSON object per line,
assigning a session-local monotonic `event_sequence` while retaining the source
sequence. A storage warning or fault uses the same schema with source
`session_writer`. A malformed intermediate line is corruption; a truncated final
line is ignored only during interrupted-session analysis and remains a visible
integrity warning. Storage failure stops further persistence and preserves the
first storage fault as the terminal cause.

Processing faults identify the source session, channel, and half-open sample
range when those values are available. Nominal fallback and finite values beyond
the calibration span are nonterminal warnings. Continuity, context, and
nonfinite-input faults are raised before filter state advances; explicit reset
starts a new labeled segment.

Plot and export failures are returned as typed errors and persisted in terminal
job manifests when execution has started. Cancellation is distinct from a
fault. A failed current artifact pair is removed; completed earlier artifacts
remain checksummed and listed. Display reduction rejection never alters replay,
processing state, CSV data, or source records.

Qualification reports separate result codes from the underlying DAQ,
acquisition, storage, processing, and export event codes. Blocking qualification
codes identify the affected test and retain channel/sample/limit context in the
associated metric or source event. Missing external confirmation or unresolved
authoritative limits produce `not-tested` or advisory warning results rather
than fabricated passes. Report staleness and live-identity mismatch are always
blocking eligibility findings.

M10 adds presentation-safe, non-source event distinctions:

- `DAQ.DISPLAY.PROCESSING_SOURCE_GAP` means the disposable display consumer did
  not receive a contiguous chunk. Live causal filter state and conditioned
  history reset. It does not assert a gap in TDMS or fault recording.
- `DAQ.DEPENDENCY.UNAVAILABLE` disables live plotting, storage review, analysis,
  or export capability when its explicit extra is absent. It never selects a
  simulator in place of requested NI hardware.
- `DAQ.APPLICATION.START_DUPLICATE` rejects Start while a run is active.
- `DAQ.APPLICATION.COORDINATOR_CLOSED` rejects work after orderly shutdown.

While running, acquired-minus-persisted count is queue lag. M10 publishes
`persistence_loss_samples` only after terminal evidence. M04
`display_frames_dropped`, source continuity telemetry, and terminal storage loss
must never be combined into one warning count or interpreted interchangeably.

M10A adds detachable open-acquisition distinctions:

- `DAQ.OPEN_ACQUISITION.RECORDING_STARTED` records the selected channel and
  captured source start cursor in a segment event stream.
- `DAQ.OPEN_ACQUISITION.RECORDING_STOPPED` records an orderly segment boundary;
  monitoring continues afterward.
- `DAQ.APPLICATION.OPEN_MONITOR_DUPLICATE` rejects a second monitor start.
- `DAQ.APPLICATION.OPEN_MONITOR_ILLEGAL_STATE` rejects record, stop-recording,
  or end commands outside their legal nested lifecycle state.
- `DAQ.STORAGE.OPEN_SEGMENT_WRITE_FAILED` and
  `DAQ.STORAGE.OPEN_SEGMENT_FINALIZATION_FAILED` fault the affected segment but
  do not relabel display replacement or terminate a healthy DAQ monitor.
- `DAQ.SESSION.OPEN_MONITOR_FAULT` records a DAQ fault propagated into an active
  segment before monitor cleanup.
