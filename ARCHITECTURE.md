# LabDAQ Architecture

> The following sections describe existing architecture. [ADR-0033](docs/adr/0033-raw-first-monitoring-and-optional-diagnostics.md) sets the intended raw-first workflow; qualification gates remain current behavior until [DAQ-M12](Milestones/DAQ-M12-raw-first-monitoring.md) is implemented.

Status: Accepted through DAQ-M10A software/simulator acceptance; physical NI acceptance remains pending

## System style

LabDAQ uses ports and adapters with explicit dependency injection:

```text
GUI and CLI
    -> application services
        -> domain models and ports
            <- concrete adapters
```

`src/labdaq/bootstrap.py` is the production composition root. DAQ-M01 binds the
JSON configuration adapter to the configuration-document port only when a
configuration command is requested.

Offline spectral eligibility is resolved by the application plotting service:
raw-only diagnostic channels use raw voltage while other channels use the requested
stage. Stage groups share the selected range and return results in requested order.
Per-series input-stage provenance flows to GUI and export adapters; mixed units use
separate axes. See [ADR-0032](docs/adr/0032-raw-voltage-spectra-in-session-review.md).

## Package ownership

- `domain` owns infrastructure-independent scientific and operational types.
- `ports` owns abstract contracts consumed by application services.
- `application` owns use cases, state, orchestration, and logging configuration.
- `processing` owns GUI-independent scientific processing.
- `storage` owns format-independent session layout and storage policy.
- `adapters` owns NI, simulated, TDMS, filesystem, and exporter integrations.
- `cli` and `gui` own input and presentation only.
- `bootstrap` is the composition root.

M01 configuration ownership is split deliberately:

- `domain` owns frozen records, controlled vocabulary, and pure semantic rules.
- `ports` owns the configuration-document loading contract.
- `application.configuration_service` resolves profiles and coordinates validation.
- `adapters.configuration_json` owns UTF-8 JSON I/O and Draft 2020-12 validation.
- Packaged resources own the canonical version-1 schemas and initial profile.

The `storage` package does not implement TDMS. Format-specific behavior belongs
under `adapters/tdms`, preventing competing ownership.

M02 DAQ ownership is similarly explicit:

- `ports.daq` owns the backend-neutral contract, runtime records, events, and
  DAQ-specific errors.
- `application.daq_service` owns discovery, validation, and bounded
  test-acquisition use cases.
- `adapters.simulated` owns scenario decoding, deterministic numeric
  generation, local lifecycle, virtual topology, pacing, and injected faults.
- `bootstrap` is the only normal binding point and requires an explicit backend.

The simulator's idle/configured/running/stopped/faulted/closed lifecycle is not
the M04 acquisition state machine. It owns no session identity, authoritative
chunk sequence, queues, recording, or display fan-out.

M03 binds the same port to `adapters.ni` only when `backend="ni"` is selected.
That package exclusively owns `nidaqmx` imports, driver objects, task handles,
stream readers, local aliases, and NI error translation. Discovery normalizes
driver metadata without reserving hardware. One adapter owns at most one
four-channel task, adds channels in experiment order, verifies and commits
timing, copies valid reads out of a private exact-shape buffer, and closes the
task on normal and fault paths.

The diagnostic application service bounds test acquisitions and formats
summaries. It does not import NI or own a task. DAQ-M04 preserves that workflow
and adds a separate headless acquisition service:

- `ports.acquisition` owns immutable chunk, event, status, completion, cleanup,
  and fault records plus read-only pull-stream protocols.
- `application.acquisition_engine` owns session identity, the acquisition state
  machine, one blocking read worker, authoritative sequence/index continuity,
  stop coordination, and queue fan-out.
- `ports.daq` owns expected pending-read cancellation; adapter packages retain
  the implementation details for interruptible simulator waits and NI task abort.
- `bootstrap` binds both application services to the same explicitly selected
  adapter factory without making either service depend on a concrete adapter.

The application state transitions are serialized under one lock:

```text
IDLE -> CONFIGURED -> ARMED -> RUNNING -> STOPPING -> COMPLETED
  ^                                |             \-> FAULTED
  |                                +----------------> FAULTED
  +--------- explicit reset from CONFIGURED, ARMED, COMPLETED, or FAULTED
```

One validated adapter read is published recording-first:

```text
DaqPort.read -> validate/index -> bounded lossless recording FIFO -> M05
                              `-> bounded latest-data display FIFO -> later GUI
```

Recording enqueue uses timed backpressure and faults rather than evicting data.
Display publication never blocks; it replaces the oldest unpublished whole
chunk and maintains a separate supersession count. The streams close out of
band, so terminal consumers can drain all remaining chunks without a sentinel.

M05 turns the recording stream into a durable backend vertical slice:

```text
AcquisitionEngine recording stream ----> RecordingSessionService
AcquisitionEngine event stream --------/          |
                                                    v
                                        SessionWriterPort receipts
                                                    |
                             lazy session adapter + filesystem capacity
                                                    |
                         raw.h5 + manifest + JSONL + recipe
                         (schema-v1 raw.tdms remains readable)

SessionInspection/Recovery/Replay services -> SessionReader/Replay ports
                                                    |
                                        verified AcquisitionChunk replay
```

- `ports.session` owns storage-neutral planning, policy, receipt, lifecycle,
  integrity, summary, writer/reader/capacity, and cancellable replay contracts.
- `application.session_service` owns preview-to-terminal orchestration, drains
  recording and event streams concurrently, preserves the first storage fault,
  requests one orderly acquisition stop, and exposes inspection/recovery/replay
  use cases without importing filesystems, TDMS, or npTDMS.
- `storage` owns the stable path, capacity-estimation, serialization, and logical
  digest policies; it does not parse or write TDMS.
- `adapters.tdms` exclusively owns the version-1 directory, atomic JSON,
  append-only JSONL, synchronized npTDMS writer/parser, process-aware lock,
  recovery, file checksums, and bounded replay mechanics.
- `bootstrap` lazily composes those services. Core, CLI import, and simulator
  operation do not load npTDMS; asking for durable sessions without the storage
  extra raises a typed dependency error.

M06 adds a read-only scientific-processing slice over the common live/replay
chunk contract:

```text
AcquisitionChunk -> application processing services -> processing ports
                                                    <- NumPy/SciPy implementation

verified M05 session -> storage-neutral metadata + replay -> same processor
                                                        -> metrics/FFT/PSD summary
```

- `domain` owns versioned calibration, recipe, range, notice, and provenance
  records plus canonical deterministic hashing.
- `ports.processing` owns immutable NumPy result carriers and storage-neutral
  recipe, numerical-processing, and session-metadata contracts.
- `application.processing_service` owns calibration resolution, nominal-policy
  enforcement, live processor creation, causal replay reconstruction, range
  selection, and provenance assembly.
- `processing` owns affine calibration, realized Butterworth SOS design/state,
  metrics, FFT normalization, and Welch PSD conventions.
- `adapters.configuration_json` and `adapters.processing_json` own schema-backed
  recipe/calibration/provenance JSON. The TDMS adapter exposes only verified
  source metadata and the existing replay port.
- `bootstrap` lazily imports the numerical implementation only when requested;
  importing domain, application, configuration, or CLI code does not require
  SciPy.

M07 adds a read-only plotting/export slice over verified replay and the M06
numerical port:

```text
verified replay -> recorded-signal stream -> plot-data service -> bounded bundle
                                          -> CSV adapter       -> CSV + sidecar
bounded bundle -> static renderer port -> lazy Matplotlib PNG/SVG adapter
completed pairs -> export coordinator -> terminal manifest
```

- `domain.plotting` owns immutable ranges, selections, request, figure, export,
  progress, artifact, and manifest records. It remains NumPy-free.
- `ports.plotting` owns NumPy-backed plot/tabular carriers and renderer,
  serializer, CSV-writer, and artifact-store protocols.
- `application.processing_service.RecordedSignalStreamService` is the reusable
  stage-aware replay path. Raw needs no calibration or recipe; calibrated
  resolves selected channels; conditioned reconstructs causal state from zero.
- `application.plotting_service` owns exact range validation, post-processing
  min/max reduction, staged FFT/PSD orchestration, and provenance-rich bundles.
- `application.export_service` owns job preflight, progress, artifact-pair
  semantics, partial failure, cancellation, and manifest truthfulness.
- `adapters.export` owns incremental CSV encoding, collision-safe filesystem
  installation, and noninteractive Matplotlib rendering. `adapters.plotting_json`
  owns the version-1 plot/export JSON codecs and schema validation.
- No M07 application module imports Matplotlib, TDMS, Qt, NI, or a concrete
  adapter. The composition root binds adapters only when the CLI/API requests
  plotting or export.

M08 adds a qualification slice without a second task or writer:

```text
DAQ discovery/self-test + configuration evidence -> qualification services
AcquisitionEngine -> RecordingSessionService -> one qualification TDMS session
verified replay -> bounded diagnostic analysis -> atomic report checkpoints
current identity + fingerprint + report -> start-eligibility decision
```

- `domain.qualification` owns immutable plans, provenance-bearing limits,
  metrics, confirmations, results, reports, fingerprints, overrides, and
  eligibility records.
- `ports.qualification` owns analysis, prompting, report repository,
  environment, and attachment contracts; the DAQ port now exposes backend-
  neutral self-test and source-relative read indices.
- `application.qualification_service` owns plan binding, compatibility,
  orchestration, aggregation, recovery, attachment, and eligibility policy.
- `processing.qualification` owns bounded NumPy diagnostics. JSON, NI,
  simulator, TDMS, filesystem, and interactive concerns remain adapters or
  composition-root responsibilities.
- Qualification sessions retain raw data and atomic plan/report checkpoints.
  Later experiments copy exact eligible report bytes during exclusive creation.

M09 adds a presentation slice without adding acquisition ownership:

```text
PySide6 widgets -> Qt-free DesktopPresenter -> M01-M08 application services
       |                    |
       `-> keyed workers    `-> typed launch intent only
```

- `application.configuration_workspace`, `destination_service`,
  `readiness_service`, and `session_catalog_service` own validation, path,
  eligibility, and bounded catalog policy.
- `ports.configuration`, `ports.session`, `ports.qualification`, and
  `ports.preferences` own the new public template, catalog, progress, and local
  preference contracts.
- `gui.presentation` owns Qt-free presentation records and orchestration;
  `gui.main_window` renders them. Widgets import neither concrete DAQ/storage
  adapters nor numerical processing implementations.
- `gui.background` assigns a generation to keyed thread-pool work and ignores
  stale replies. Qualification cancellation is routed back to the application
  service before worker shutdown.
- `bootstrap` lazily composes Qt, QSettings, DAQ, session, configuration, and
  qualification adapters. M09 emits a validated `AcquisitionLaunchIntent` but
  creates no experiment task or session.

M10A adds a detachable raw-diagnostic recording slice alongside the existing
one-run M10 coordinator:

```text
configured continuous DAQ task -> AcquisitionEngine
    |-> bounded display stream -> selected-channel ten-second ring -> Qt frame request
    `-> lossless source stream -> always-draining monitor dispatcher
                                  `-> bounded segment queue -> single-channel writer
```

- `application.open_acquisition_service` owns monitor and nested recording state,
  full-task-to-selected-channel projection, whole-chunk command boundaries, and
  orderly shutdown.
- `ports.operator` owns immutable open-monitor status records. `ports.session` owns
  segment completion and monitor-source provenance independently from DAQ cleanup.
- The schema-v2 HDF5 adapter records segment-local indices from zero and an optional
  `open_acquisition` mapping back to the continuous monitor cursor.
- Storage failure terminalizes only its active segment after preserving the readable
  prefix. DAQ failure terminalizes monitoring and any active segment.
- The existing qualified acquisition and one-shot raw diagnostic coordinators retain
  their accepted lifecycle and storage behavior.

## Import boundaries

- Domain imports no outer LabDAQ layer.
- Ports may import domain but not implementations or presentation.
- Application may import domain and ports but not concrete adapters.
- Processing imports neither NI nor GUI code.
- Core imports never load NI, Qt, PyQtGraph, TDMS, or concrete adapters.
- Adapter packages are imported only when the composition root requests them.
- The root package exposes only stable version metadata.

The standards-compliant JSON Schema validator is the one small core runtime
dependency accepted by ADR-0005. NumPy and SciPy are isolated simulator or
analysis dependencies and remain unloaded by core configuration imports.
Hardware, GUI, storage, and plotting dependencies remain optional and isolated.

Import Linter enforces internal direction. Subprocess architecture tests enforce
optional-dependency isolation and absence of file, logging, thread, hardware,
and GUI side effects.

## Scientific and concurrency direction

The M04 engine publishes immutable raw chunks with explicit units and channel
order, monotonic sample indices, and session identity. One DAQ read stream feeds
a lossless recording consumer and a bounded display consumer; a GUI will never
create a second hardware task. M05 acknowledges complete synchronized TDMS
segments and replays verified raw values through that same chunk contract. M06
applies explicit affine calibration and causal stateful conditioning to either
source. A selected replay interval reconstructs filter state from sample zero,
and processing never changes the raw session. GUI ownership remains deferred.

## Error and observability ownership

`labdaq.errors` owns stable typed errors. `labdaq.application.logging` owns
explicit standard-library logging setup. Importing LabDAQ never configures
logging. Operational logs are diagnostic evidence, not scientific records.

See [ADR-0001](docs/adr/0001-package-and-layer-boundaries.md),
[ADR-0002](docs/adr/0002-optional-dependency-isolation.md), and
[ADR-0003](docs/adr/0003-structured-errors-and-logging.md). The M02 decisions
are recorded in [ADR-0006](docs/adr/0006-hardware-independent-daq-port.md) and
[ADR-0007](docs/adr/0007-deterministic-simulator-semantics.md). NI ownership,
rate, task, cleanup, and buffer policy are recorded in
[ADR-0008](docs/adr/0008-ni-daqmx-task-and-buffer-ownership.md). M04 fan-out and
terminal behavior are recorded in
[ADR-0009](docs/adr/0009-single-read-dual-consumer-fan-out.md) and
[ADR-0010](docs/adr/0010-acquisition-stop-terminal-cleanup-semantics.md). M05
format, durability/recovery, and replay decisions are recorded in
[ADR-0011](docs/adr/0011-v1-session-format-synchronized-writer.md),
[ADR-0012](docs/adr/0012-atomic-checkpoint-digest-recovery.md), and
[ADR-0013](docs/adr/0013-common-live-replay-chunk-contract.md). M06 calibration,
hashing, causal-state, and spectral decisions are recorded in
[ADR-0014](docs/adr/0014-versioned-calibration-recipe-hashes.md) and
[ADR-0015](docs/adr/0015-causal-baseline-processing.md). M07 range/decimation
and derived-artifact decisions are recorded in
[ADR-0016](docs/adr/0016-half-open-ranges-and-bounded-min-max-decimation.md) and
[ADR-0017](docs/adr/0017-headless-rendering-and-atomic-derived-artifacts.md).
M08 identity, thresholds, external evidence, and storage decisions are recorded
in [ADR-0018](docs/adr/0018-qualification-fingerprint-and-start-eligibility.md),
[ADR-0019](docs/adr/0019-qualification-threshold-provenance.md),
[ADR-0020](docs/adr/0020-external-qualification-confirmations.md), and
[ADR-0021](docs/adr/0021-qualification-session-and-report-storage.md).
M09 presentation, worker, public-port, and preference decisions are recorded in
[ADR-0022](docs/adr/0022-desktop-presentation-background-preferences.md).
M10 live buffering and detachable monitoring decisions are recorded in
[ADR-0023](docs/adr/0023-live-operator-buffering-and-disk-review.md) and
[ADR-0029](docs/adr/0029-detachable-open-acquisition-recording.md).
