# DAQ-M10A Acceptance Evidence

Status: software/simulator accepted; physical NI verification pending

## Automated evidence

- 2026-09-08: saved multichannel recording segments reopen for raw-voltage FFT/PSD
  with every recorded channel present, per-channel raw-stage provenance, and no
  calibration. See [M10 correction evidence](DAQ-M10-acceptance-evidence.md#2026-09-08-ai3-voltage-fftpsd-correction).

- Deterministic simulated monitoring runs one continuous configured four-channel task
  while retaining and plotting every enabled raw-voltage channel in bounded memory.
- Multiple Record/Stop cycles create distinct synchronized multichannel schema-v2 HDF5
  sessions. Full verification and chunked replay reproduce every persisted channel.
- Segment chunks use local sequence and sample indices beginning at zero; the manifest
  records the monitor run, authoritative ordered channel IDs and units, and half-open
  source range.
- Ending monitoring during an active recording finalizes that segment before the
  acquisition engine releases the adapter.
- Injected HDF5 append failure creates a faulted/recoverable recording while monitored
  sample counts continue increasing.
- Legacy single-channel intent, status, authorization, and manifest provenance remain
  accepted through the primary-channel compatibility fields.
- Focused M10A simulator workflow:
  `pytest tests/integration/test_open_acquisition_workflow.py -q --no-cov`:
  **4 passed**.
- Full Windows suite with configured coverage:
  `pytest -q`: **538 passed, 6 skipped**, **85.23% coverage**.
- `ruff format --check .`: **233 files already formatted**; `ruff check .`: **passed**.
- `mypy src`: **89 source files passed**; `lint-imports`: **5 contracts kept**.
- `python -m build`: built `labdaq-0.1.0.dev5.tar.gz` and
  `labdaq-0.1.0.dev5-py3-none-any.whl` successfully.
- Frozen `LabDAQ-CLI.exe diagnostics --json`: **overall ok** on Windows/CPython 3.11.

## Physical criteria not executed

- Sustained cDAQ-9171/NI-9239 open monitoring at 50 kS/s/channel.
- Repeated multichannel Record/Stop cycles with exact physical continuity evidence.
- Bounded process memory and task release after normal close, disconnect, and storage
  failure on the target laboratory computer.

Simulator evidence does not establish external wiring, sensor power, conditioner safety,
or physical NI task behavior.
