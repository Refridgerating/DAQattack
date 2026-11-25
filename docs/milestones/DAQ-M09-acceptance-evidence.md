# DAQ-M09 Acceptance Evidence

Status: Software/simulator accepted on Windows; supervised physical NI GUI verification pending

## Implemented scope

- PySide6 shell with Experiment Setup, Hardware Tests, and Sessions pages.
- Qt-free presenter plus backend-owned draft validation, destination preview,
  readiness/launch intent, qualification progress, and resilient session catalog.
- Explicit synthetic and intentionally incomplete physical four-channel templates.
- Keyed generation-token background work, stale-result suppression, typed operator
  messages, correlation IDs for unexpected failures, and bounded shutdown.
- QSettings schema-version 1 geometry/recent-root preferences.
- Additive fast session summaries for specimen, duration, physical channels,
  hardware, sensor/calibration, recipe, and qualification provenance.

## Automated evidence

Focused M09 application, GUI, and architecture tests exercise invalid raw draft
retention, explicit normalizations, stable/collision-safe previews, catalog fault
isolation, message mapping, preference round trips, stale background replies,
offscreen launch/edit/close, active-qualification shutdown, and the complete
simulator readiness/freshness workflow.

The simulator workflow produced a completed, warning-eligible preflight report.
Metadata-only notes preserved eligibility; a chunk-size change made the report
stale; restoring the exact fingerprinted configuration restored eligibility. No
experiment session was created by the M09 handoff.

Final local Windows results:

- `ruff format --check .`: passed, 186 files formatted.
- `ruff check .`: passed.
- `mypy src/labdaq`: passed in strict mode for 75 source files.
- `lint-imports`: 5 contracts kept, 0 broken.
- `pytest -q` with `QT_QPA_PLATFORM=offscreen`: 435 passed, 6 hardware tests
  skipped, 85.14% total coverage.
- `python -m build`: source distribution and wheel built successfully; the wheel
  contains the M09 GUI modules and packaged synthetic template.
- `python -m labdaq.gui --help`: passed.
- Timed offscreen simulated entry-point smoke: application exited cleanly with
  result 0 and the window hidden after orderly close.

## Acceptance disposition

Software/simulator criteria are accepted. The following remain pending because no
supervised laboratory-hardware session was authorized or performed:

- NI chassis/module discovery display against the target cDAQ-9171/NI-9239.
- Physical wiring, sensor excitation, grounding, safe-range, conditioning, and
  calibration confirmation.
- Physical qualification execution and report review.
- Physical task release during idle close and active-qualification shutdown.
- Manual target-machine DPI scaling, keyboard navigation, table readability,
  folder chooser, and repeated launch/close checks.

Simulator evidence must not be interpreted as any of the physical evidence above.
