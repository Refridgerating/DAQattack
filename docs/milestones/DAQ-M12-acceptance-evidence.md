# DAQ-M12 acceptance evidence

Status: software/simulator accepted; physical NI workflow unverified.

## Implemented workflow

Raw monitoring uses one configured acquisition stream for selected channels.
Recording is optional and creates separate, locally rebased raw sessions while
the stream continues. Qualification and processing remain optional. Sensorless
raw channels preserve unknown sensor metadata as null rather than inventing a
calibration or sensor identity. Existing session formats and qualification
reports remain supported.

## Software and simulator evidence

- Raw configuration and architecture tests: **47 passed**.
- Simulator CLI discovery and four-channel raw acquisition: **1,000 samples**
  acquired and task cleanup reported successful.
- `.venv-m00/Scripts/python -m pytest --no-cov tests/integration/test_simulated_daq.py tests/integration/test_open_acquisition_workflow.py tests/unit/application/test_m09_desktop_services.py tests/unit/application/test_open_acquisition_service.py -q`: **56 passed**. This covers monitoring without a session, repeated recordings, exact replay and boundaries, raw plot timing, selected ai1/ai3 signal mapping, and clipping events. Historical scientific scenarios retain strict channel matching.
- Simulator integration after the desktop real-time pacing correction: **7 passed**.
- Import-linter: **5 contracts passed**.
- `mypy src`: **passed**.
- Sensorless HDF5 round trip, full integrity inspection, exact replay, raw
  plotting and CSV sidecar, and TDMS export: **passed** in the focused
  `test_raw_session_without_sensor_metadata_round_trips_and_exports` test.
- Final static checks passed: `ruff check .`, `ruff format --check .` (248 files),
  `mypy src` (92 files), and `lint-imports` (5 contracts).
- `$env:QT_QPA_PLATFORM="offscreen"; python -m pytest -q -o addopts="" tests/gui`:
  **48 passed** after the GUI fix. Two additional missing-channel and repeated-
  recording GUI tests also passed.
- Full suite: `$env:QT_QPA_PLATFORM="offscreen"; .\.venv-m00\Scripts\python.exe -m pytest -q` produced **652 passed, 6 skipped** in 168.54 seconds, with **85.65%** coverage (85% required). A prior non-GUI run exposed a presenter compatibility failure; it was corrected before the passing full run.

These are simulator/software results. They do not establish physical input
compatibility, wiring, sensor power, measured channel behavior, or NI task
release on the target chassis.

## Physical acceptance

Manual verification on the NI cDAQ-9171 / NI-9239 remains **unverified**:
select physical channels, observe voltage, start and stop recording, reopen and
replay the recording, stop monitoring, and confirm task cleanup. No qualification
campaign, soak, or packaged release is claimed by this milestone.
