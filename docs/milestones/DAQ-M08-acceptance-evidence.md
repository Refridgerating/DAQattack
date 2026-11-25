# DAQ-M08 Acceptance Evidence

Status: Software implementation accepted locally on Windows; physical qualification pending  
Date: 2026-08-14  
Specification: [DAQ-M08 hardware qualification and diagnostics](../../Milestones/DAQ-M08-hardware-qualification-and-diagnostics.md)

## Changes and architecture

M08 adds immutable qualification plans, provenance-bearing limits, metrics,
confirmations, results, reports, lifecycle, overrides, fingerprints, and
eligibility decisions. Three schema-versioned bundled plans cover preflight,
full, and throughput workflows. JSON adapters validate and round-trip plans,
reports, and confirmation files; plan creation is exclusive and reports are
atomically checkpointed after each result.

The DAQ port now exposes backend-neutral self-test and source-relative read
indices. NI and simulator adapters implement both without leaking driver
objects. The M04 engine validates source continuity and records bounded read,
queue, drop, pressure, and stop telemetry. M05 sessions support the optional
`hardware-qualification` type and exact report attachment to a later experiment.

One qualification run owns one DAQ task, one `AcquisitionEngine`, one
`RecordingSessionService`, and one synchronized TDMS writer. Bounded raw
diagnostics and phase-window response/alignment/crosstalk metrics read verified
replay. Powered-idle overlay and conditioned PSD evidence use the existing M07
export coordinator; unavailable optional rendering is advisory and never loses
raw evidence.

ADRs 0018 through 0021 fix fingerprint/eligibility, threshold provenance,
external confirmation, and qualification-session storage decisions.

The M03–M10 commissioning extension adds schema-v2 `experiment_mode`, a
four-row commissioning template with the operator-transcribed X/Y/Z
values (6.914/6.791/7.398 mV/g), explicit unverified provenance and
transcription metadata, variable profile channel count, and persistent
commissioning warnings in manifests and reopened GUI review. ai3 is persisted
as `raw_only_diagnostic` volts with null engineering interpretation and the
permanent unpowered/not-charge warning. It is excluded from all derived
products and production readiness. The 422E03 charge path remains excluded
from commissioning. Guided physical prompts now
record settling-aware, half-open evidence windows; task-stopped fixture
sub-runs remain a production follow-up because rewiring an active task is
intentionally prohibited.

## Software acceptance matrix

| Criterion | Evidence | Result |
|---|---|---|
| Healthy deterministic qualification report | Short preflight, full phase-addressed, and throughput integrations record real TDMS sessions and reload schema-valid reports | Satisfied |
| Production acquisition and recording paths | Integration composes the simulator DAQ, M04 engine, M05 service/writer, full inspection, and exact replay; there is no qualification reader or writer fork | Satisfied |
| Raw evidence remains immutable and recoverable | Qualification sessions retain TDMS/events; recovery preserves source bytes, completed checkpoints, and marks unfinished tests `not-tested` | Satisfied |
| Reports and plans are durable | Schema/semantic round trips, canonical hashes, exclusive plan creation, atomic checkpoints, bad-document translation, and recovery are tested | Satisfied |
| Fingerprints invalidate relevant changes | Tests cover plan/profile/config hashes, rate/duration/buffers, storage path, ordered logical/physical channels, ranges, scientific identities, and live hardware identity; presentation text is excluded | Satisfied |
| Eligibility and overrides are conservative | Exact pass/warning/fail/not-tested precedence, stale/age/current-identity checks, required-result checks, and advisory-only override rules are tested | Satisfied |
| Thresholds are traceable | Every bundled threshold includes operator, unit, signal stage, source category/reference, rationale, and consequence; null unresolved values never evaluate as pass | Satisfied |
| Raw diagnostics are bounded and exact | Tests cover DC/RMS/std/MAD, `1.4826 × median(abs(x − median))`, headroom, exact 80/98 percent boundaries, clipping, nonfinite values, and longest repeated runs | Satisfied |
| Assisted ranges are traceable | Full integration records 2-second phase ranges, confirmation source indices, response ratios, raw cross-correlation lag, normalized correlation, and crosstalk dB | Satisfied in simulator |
| Throughput continuity is explicit | Source gaps, duplicates, reorder/stale reads, channel count inequality, queue saturation, storage/session faults, acquired/persisted/replay counts, and exact legacy 1,120,000/1,120,000/1,100,000/1,100,000 pattern are represented and blocking | Satisfied in software |
| Simulator qualification controls | Version-1 scenarios cover topology/capabilities, self-test, deterministic bias/noise/mains signals, clipping, dropout/delay/disconnect, frozen/nonfinite/stale/source-index faults, channel delay, crosstalk, and phase-addressed healthy evidence | Satisfied |
| M06/M07 evidence remains optional | Full integration produces checksummed raw time-history and conditioned PSD PNGs plus an export manifest; absence/failure maps to `DAQ.QUALIFICATION.RENDERING_UNAVAILABLE` | Satisfied |
| Experiment attachment is exact | Integration byte-compares the copied report and verifies UUID, hash, fingerprint, status, age, eligibility, and override fields in the new experiment manifest | Satisfied |
| CLI contracts and exit codes | `qualify hardware`, `qualification show`, `check`, and `recover` are parser/integration tested; ineligible qualify/check uses exit 10 and show accepts every valid status | Satisfied |
| Optional dependencies and layers remain isolated | Architecture tests prohibit NI, Qt, TDMS, Matplotlib, and concrete adapters in qualification domain/application modules and retain established import-linter contracts | Satisfied |
| Existing accepted behavior remains intact | Full regression suite passes with hardware tests safely skipped | Satisfied (478 passed, 6 skipped; 85.04 percent coverage) |

## Verification

Final local commands and results:

| Command | Result |
|---|---|
| `ruff format --check .` | Passed |
| `ruff check .` | Passed |
| `mypy src` | Passed |
| `lint-imports` | Passed; established contracts kept |
| Focused M08 unit/contract/integration/CLI tests | Passed |
| Focused M04–M07 regression tests | Passed |
| `pytest tests/architecture -q --no-cov` | Passed |
| `pytest -q` | Passed; 478 tests, 6 hardware skips, 85.04 percent coverage |
| Representative simulator workflows | Passed for preflight/full application runs and throughput/show/current-check/offline-check CLI behavior |
| `python -m build` | Passed; sdist and pure-Python wheel built in isolation |

The short throughput durations used by automated tests prove orchestration and
fault semantics. They are not evidence for the default 600-second physical run.

## Physical acceptance

No `LABDAQ_RUN_NI_HARDWARE`, `LABDAQ_HARDWARE_PROFILE`, or
`LABDAQ_EXPERIMENT_CONFIG` opt-in variable was set. No NI task was reserved and
no physical electrical claim was made.

| Physical criterion | Result |
|---|---|
| Live cDAQ-9171 and NI-9239 discovery/model/serial match | Discovery captured 2026-08-14; qualification match pending |
| Repeated physical task create/start/stop/clear/recreate | Pending |
| Four electrically plausible powered-idle inputs | Pending |
| TE 830M1-0200 power, bias, above-cutoff response, mapping, and certificate values | Pending |
| Compatible PCB 422E03 conditioning, NI-terminal bias, polarity, range, and response | Pending |
| Known-common-input relative alignment | Pending |
| Optional physical crosstalk result or supervised not-tested record | Pending |
| 600 seconds at four channels by 50 kS/s on the target computer/storage | Pending |
| Zero physical missing/duplicated/reordered indices and equal TDMS lengths | Pending |
| Reopened physical TDMS/manifest/report count agreement and experiment attachment | Pending |
| Three-axis commissioning supply, mapping, offsets, and two-minute GUI release | Pending physical execution |
| ai3 disconnected +/-8 V precheck, raw four-row capture, and raw-only export review | Pending physical execution |

## Assumptions and risks

- The bundled physical profile remains intentionally incomplete for actual
  sensor power, charge conditioning, serials, calibration certificates, and
  laboratory bias/noise/response/crosstalk limits. It cannot establish physical
  start eligibility without a reviewed local configuration.
- Simulator and fake-driver evidence verifies software only. It does not verify
  voltage safety, grounding, shielding, power, mounting, external conditioning,
  target-disk throughput, or NI driver/device timing.
- Exact median/MAD and phase windows are limited to ten seconds; throughput
  analysis stays streaming and M07 PSD uses its bounded Welch implementation.
- Qualification never deletes raw evidence or edits a completed source session.
  Recovery and attachment are explicit new operations.
- DAQ-M09 GUI presentation and report widgets remain out of scope.
- Commissioning `preflight-v1` retains executable discovery, capability,
  self-test, powered-idle, and accelerometer-bias definitions. The exact
  missing-definition regression is covered end to end.
- The configured full suite passes its coverage gate: 486 tests passed, 6
  physical-hardware tests skipped, and branch coverage is 85.20 percent.
