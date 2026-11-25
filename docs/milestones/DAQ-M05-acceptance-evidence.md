# DAQ-M05 Acceptance Evidence

Status: Implementation complete on Windows; physical NI verification pending  
Evidence date: 2026-08-12  
Specification: [DAQ-M05 durable recording, metadata, parsing, and replay](../../Milestones/DAQ-M05-durable-recording-metadata-parsing-replay.md)

## Scope delivered

M05 implements the backend vertical slice from a preallocated session preview,
through M04 acquisition and concurrent recording/event drains, to synchronized
TDMS persistence, atomic metadata, explicit completion/recovery, full integrity
verification, and exact hardware-free replay. It adds no calibration, filtering,
plotting, export, GUI, trigger sequencing, or plugin behavior.

The bundled `baseline-v1-draft` resource is stored unchanged. It has no stages;
the manifest explicitly records `applies_calibration: false` and V-to-V raw
units.

## Automated acceptance

| Criterion | Evidence | Result |
|---|---|---|
| Deterministic simulator records, closes, verifies, and replays exactly | `test_simulator_record_verify_replay_and_cli_round_trip` compares concatenated replay to analytical simulator values with `np.array_equal` | Satisfied |
| Replay chunk size cannot change values | Round trips use 128, 37, 777, 257, and recovered-prefix chunk sizes; the 50,000-sample test reconstructs exact values | Satisfied |
| Only synchronized chunks are acknowledged | Unit/integration cases reject wrong session, sequence, overlap, gap, order, rate, shape/dtype through the common chunk contract; injected append failure leaves persisted count zero | Satisfied |
| Completion mapping is exact | Duration, manual stop, disconnect/acquisition fault, disk fault, recovered interruption, and corruption cases assert their terminal states | Satisfied |
| Metadata is self-contained | Manifest/TDMS tests inspect profile, discovery, effective configuration, ordered channels, sensor/calibration/conditioning snapshots, units, counts, recipe, and properties | Satisfied |
| Manifest/TDMS disagreements fail closed | Larger/smaller manifest counts, unequal channel lengths, unsupported schema, checksum corruption, and replay refusal are tested | Satisfied |
| Abrupt execution is recoverable to an immutable prefix | A real subprocess checkpoints 64 samples under a Unicode/space-containing Windows path and exits with `os._exit`; restart recovery preserves raw/event files and replays the prefix | Satisfied |
| Disk capacity and write failures are explicit | Preflight rejection, low hard-stop injection, `ENOSPC`, TDMS/event/manifest failures, first-fault preservation, and reserve cleanup are tested | Satisfied |
| Session directories never overwrite | Stable UUID preview, exclusive creation, collision rejection, invalid parent, long-path warning, and Unicode/space parent cases are tested | Satisfied |
| Parser/replay do not require NI or GUI | Architecture subprocess tests import contracts, application service, CLI, bootstrap, and lazy TDMS package without loading npTDMS, NI, or GUI dependencies | Satisfied |
| Replay and verification are bounded | npTDMS is opened lazily; verification reads 8,192-frame slices and replay reads caller-sized channel slices; a 50,000-sample multi-chunk test verifies bounded output | Satisfied |
| Human and JSON CLI summaries work | Integration smoke test exercises fast JSON and full human forms; stable storage failure exit code is asserted | Satisfied |
| Existing behavior remains accepted | Full suite, lint, formatting, typing, import contracts, package build, and optional-dependency checks are recorded below | Satisfied |

## Verification record

The final local commands and exact results were:

```text
ruff format --check .                       117 files already formatted
ruff check .                                all checks passed
mypy src                                    success: 45 source files
lint-imports                                4 contracts kept, 0 broken
pytest tests/unit/storage \
  tests/integration/test_durable_sessions.py -q --no-cov
                                              42 passed
pytest -q                                    313 passed, 6 skipped;
                                              86.51% branch coverage
python -m build                              sdist and wheel built successfully
```

CLI smoke tests use a deterministic simulator session and both
`labdaq session summary <path> --json` and `--verify`. Core isolation is checked
in a subprocess in which importing LabDAQ contracts/application/CLI does not load
`nptdms`; durable-session composition remains the explicit storage-extra boundary.

NI discovery returned NI-DAQmx driver `16.1.0`, Python package `1.6.0`, and an
empty `devices` list. Six opt-in hardware tests therefore skipped safely,
including the new physical M05 durable round trip.

## Physical NI acceptance

`tests/hardware/test_cdaq9171_ni9239.py::test_physical_durable_session_round_trip`
is implemented and guarded by `LABDAQ_RUN_NI_HARDWARE=1`. It records a bounded
four-channel run through the production recording service, full-verifies and
replays it, checks equal channel lengths and rate/count metadata, and opens a
fresh adapter to verify resource release.

NI discovery on this host reports that the driver is available but no connected
devices are present. Therefore these claims remain **not verified physically**:

- cDAQ-9171/NI-9239 channel order and simultaneous physical sample extent
- requested versus actual physical sampling rate in the stored session
- physical values surviving close/replay exactly
- task release after M05 finalization on the connected hardware
- external sensor power, wiring, grounding, and charge-conditioning correctness

Simulator and file-format evidence does not satisfy those criteria. The
milestone is consequently labeled implementation complete, physical NI recording
verification pending.

## Assumptions and limitations

- npTDMS 1.11 is the V1 adapter dependency and remains optional at runtime.
- An append receipt confirms a complete writer call; sudden-power-loss durability
  extends only through the latest synced checkpoint.
- Recovery accepts only the synchronized prefix npTDMS can identify as complete;
  it does not salvage arbitrary byte corruption.
- Creation paths are provenance. Moving a self-contained session is a warning,
  not corruption.
- M06 may select a new processing recipe for future sessions but may not rewrite
  an M05 recipe snapshot or raw source record.
