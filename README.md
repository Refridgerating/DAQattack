# LabDAQ

> Raw channel monitoring and optional recording are the primary workflow; qualification and processing are optional. Physical NI verification remains separate from simulator evidence. See [DAQ-M12](Milestones/DAQ-M12-raw-first-monitoring.md).

LabDAQ is a laboratory desktop data-acquisition project for dependable,
synchronized multichannel measurements. The first planned system is an NI
cDAQ-9171 chassis with an NI-9239 module, three TE 830M1-0200 accelerometer
channels, and one externally conditioned charge-converter channel.

DAQ-M03 adds an optional NI-DAQmx backend for the physical cDAQ-9171 and
NI-9239 while preserving the deterministic simulator. DAQ-M04 adds the
headless acquisition state machine and one-reader fan-out pipeline: lossless
recording delivery has priority, disposable display delivery cannot block the
reader, and stop/fault cleanup converges on one terminal result. DAQ-M05 adds
exclusive session planning, synchronized streaming recording, atomic
metadata/events, explicit recovery, full integrity verification, and exact
hardware-free replay. DAQ-M06 adds explicit calibration resolution, causal
`baseline-v1` conditioning, metrics, FFT amplitude, Welch PSD, and deterministic
provenance for live chunks and verified replay. DAQ-M07 adds bounded headless
plot data, exact streaming CSV, static PNG/SVG figures, schema-validated
sidecars, and atomic export manifests. DAQ-M08 adds versioned hardware-
qualification plans/reports, live identity and self-test, synchronized raw
diagnostics, source-index and throughput evidence, conservative eligibility,
recovery, and exact experiment attachment. DAQ-M09 adds a PySide6 desktop for
configuration, hardware qualification, readiness, and bounded session browsing.
DAQ-M10 connects that desktop to single-stream acquisition, lossless recording,
bounded raw/conditioned PyQtGraph views, live metrics, orderly stop/finalization,
disk-backed replay review, FFT/PSD, and CSV/PNG/SVG export. Supervised physical
NI GUI qualification remains pending.

## Requirements

- CPython 3.11
- Windows
- Git
- NI-DAQmx driver and supported hardware only when using the `ni` backend

LabDAQ currently supports Python 3.11 on Windows, matching its planned use with
Windows NI-DAQmx drivers. `jsonschema` is the platform-independent core
configuration dependency. NumPy-backed simulation is isolated in the
`simulated` extra. PySide6 and PyQtGraph are isolated in the `gui` extra; other
larger dependencies remain isolated in the
`analysis`, `storage`, `ni`, `gui`, `release`, and `full` extras.

## Configuration validation

```powershell
labdaq profile validate path\to\hardware-profile.json
labdaq config validate path\to\experiment-config.json
labdaq recipe validate path\to\processing-recipe.json
labdaq recipe show baseline-v1 --json
```

Add `--json` for deterministic machine-readable output. Configuration
rejection returns exit code 5. Validation never imports NI or GUI packages.

The production four-channel operator template is intentionally invalid until
the real charge-conditioning-chain sensitivity, unit, polarity, and source are
entered. LabDAQ does not substitute an identity or nominal charge conversion.
The synthetic configuration is explicitly marked `test_fixture` and is not
physical calibration evidence. The raw-monitoring default does not require the
production template or its sensor and calibration fields.

## NI hardware

Install development and NI dependencies without making NI a core requirement:

```powershell
python -m pip install -e ".[dev,ni]"
labdaq hardware list --backend ni
labdaq hardware inspect --backend ni --device cDAQ1 --json
labdaq hardware validate --backend ni `
  --profile src\labdaq\resources\config\hardware-profiles\cdaq-9171-ni-9239-four-channel-v1.json `
  --experiment path\to\validated-experiment.json
labdaq hardware acquire-test --backend ni `
  --profile src\labdaq\resources\config\hardware-profiles\cdaq-9171-ni-9239-four-channel-v1.json `
  --experiment path\to\validated-experiment.json `
  --duration 2 --chunk-size 5000 --timeout 2 --json
```

The `nidaqmx` Python package does not install or replace the NI-DAQmx Windows
driver. NI aliases such as `cDAQ1` are local addressing names; model and serial
identity are reported separately. The acquire-test command creates no session,
performs no calibration, and makes no wiring or signal-quality claim. Review
the [NI hardware guide](docs/ni-hardware.md) before starting a physical task.

## Simulated hardware

```powershell
python -m pip install -e ".[simulated]"
labdaq hardware list --backend simulated
labdaq hardware inspect --backend simulated --json
labdaq hardware validate --backend simulated `
  --profile src\labdaq\resources\config\hardware-profiles\cdaq-9171-ni-9239-four-channel-v1.json `
  --experiment tests\fixtures\configuration\valid-four-channel-synthetic-v1.json
labdaq hardware acquire-test --backend simulated `
  --experiment tests\fixtures\configuration\valid-four-channel-synthetic-v1.json `
  --scenario src\labdaq\resources\config\simulator-scenarios\clean-four-channel-v1.json `
  --duration 2 --chunk-size 4096 --json
```

The acquisition test never writes a session or prints raw arrays. It reports
channel statistics, events, cleanup health, throughput, and a canonical
SHA-256 checksum. DAQ failures return exit code 6. Continuous experiments
require `--duration` or `--samples`.

## Headless acquisition engine

The Python application API supports `configure`, `arm`, `start`, idempotent
`request_stop`, explicit terminal waiting, reset, status, event subscriptions,
recording/display streams, and idempotent close. It creates no storage and adds
no M04 CLI behavior. Recording consumers must drain concurrently whenever a
run can exceed the configured queue capacity.

Version-1 experiment JSON may optionally set these `data_chunk` fields; older
documents receive the shown defaults: 5,000 samples/read, 2.0 s read timeout,
5.0 s stop timeout, 32 recording chunks, 2.0 s recording backpressure timeout,
2 display chunks, and a 0.5 s progress interval. At four channels the default
raw `float64` payload capacities are 5.12 MB recording and 0.32 MB display.

See the [acquisition engine guide](docs/acquisition-engine.md) for the state,
stream, continuity, fan-out, and cancellation contracts.

## Durable recording and replay

Install the storage extra to compose the M04 engine with M05 persistence:

```powershell
python -m pip install -e ".[simulated,storage]"
labdaq session summary path\to\session
labdaq session summary path\to\session --verify --json
```

Session names use UTC, the validated experiment slug, and a preallocated UUID.
The selected parent must already exist and every directory is created
exclusively. New schema-v2 sessions store all logical channels in the chunked,
SWMR HDF5 dataset `/raw/samples`; its commit journal identifies the durable
readable prefix. `manifest.json`, `events.jsonl`, and the unchanged
raw-only recipe preserve configuration, hardware, sensor/calibration, timing,
count, fault, and integrity provenance. No M05 path applies calibration.

Fast summaries read bounded metadata. Full verification streams raw values and
checks logical/file digests, event order, duplicated container metadata, journal
continuity, and counts. Schema-v1 TDMS sessions remain fully readable. Replay is bounded, cancellable, exact under rechunking, and
uses the live `AcquisitionChunk` contract. See the
[session format](docs/storage/session-format.md),
[recovery guide](docs/storage/recovery.md), and
[session-summary CLI](docs/cli/session-summary.md).

TDMS is generated only as a verified interoperability artifact:

```powershell
python -m pip install -e ".[storage,tdms]"
labdaq export tdms path\to\session --json
```

See the [HDF5 layout](docs/storage/hdf5-layout.md) and
[TDMS export guide](docs/cli/tdms-export.md).

## Calibration and processing

Install the optional analysis and storage dependencies, then process a fully
verified terminal session or an explicitly recovered verified prefix:

```powershell
python -m pip install -e ".[analysis,storage]"
labdaq recipe show baseline-v1
labdaq process-session path\to\session --summary --json
```

`baseline-v1` converts each channel with its resolved affine calibration. The
three accelerometer roles then receive causal fourth-order 1 Hz high-pass and
sixth-order 12 kHz low-pass Butterworth SOS stages; the conditioned-charge role
receives affine calibration only. Real charge-chain sensitivity and polarity
remain mandatory external inputs. Nominal 6.3 mV/g accelerometer sensitivity is
allowed only by an exact hardware-profile policy match and remains visibly
labeled `nominal`.

New desktop configurations use hardware-profile revision 2 and select one of
the 31 NI-9239 internal-clock rates `50,000 / n`, `n = 1..31`. Changing rate
sets Samples/read/channel to a decimal-half-up 100 ms target and automatically
chooses `baseline-v1` at 50/25 kHz or `baseline-low-rate-v1` below 25 kHz. The
low-rate recipe omits the incompatible 12 kHz low-pass. Revision 1 and existing
sessions remain unchanged; each physical rate/read-size combination requires
fresh qualification evidence.

Processing returns metrics and compact FFT/PSD summaries through APIs or stdout.
It does not write under `processed/` or modify the source raw container, manifest, recipe,
checksums, or completion state. See [calibration and processing](docs/processing.md)
and the [processing CLI](docs/cli/processing.md).

## Plotting and export

Install the analysis, storage, and static-rendering extras:

```powershell
python -m pip install -e ".[analysis,storage,export]"
labdaq plot-data path\to\session --view raw --max-points 2000 --json
labdaq export csv path\to\session --view calibrated --name calibrated-data
labdaq export figure path\to\session --kind overlay --view filtered `
  --recipe baseline-v1 --format png --name filtered-overlay
labdaq export figure path\to\session --kind psd --view psd `
  --spectral-input conditioned --recipe baseline-v1 --format svg --json
```

Omitted channels retain all source channels in order. Ranges may use half-open
sample indices or seconds. CSV is undecimated and incremental; plot/figure data
uses bounded peak-preserving min/max reduction. Every primary has a version-1
metadata sidecar and every job writes a collision-safe terminal manifest. The
source session is never modified. See the [plot/export service guide](docs/plotting-export.md)
and [CLI reference](docs/cli/plotting-export.md).

## Hardware qualification

Run a deterministic software preflight through the production acquisition,
TDMS, verification, and replay path:

```powershell
labdaq qualify hardware --backend simulated `
  --profile src\labdaq\resources\config\hardware-profiles\cdaq-9171-ni-9239-four-channel-v1.json `
  --config tests\fixtures\configuration\valid-four-channel-synthetic-v1.json `
  --plan preflight-v1 --json
labdaq qualification show path\to\qualification-report.json
labdaq qualification check path\to\qualification-report.json `
  --profile path\to\hardware-profile.json --config path\to\experiment.json `
  --backend simulated
```

Ineligible qualification and check results return exit code 10. Human mode can
prompt for assisted evidence; JSON mode is noninteractive and accepts a
versioned confirmations file. Unresolved lab limits and absent confirmations
remain visible warnings or `not-tested` results. Simulator evidence does not
qualify physical wiring, conditioning, or hardware. See the
[operator guide](docs/hardware-qualification.md) and
[qualification CLI reference](docs/cli/qualification.md).

## Development setup

Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

POSIX shell:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

The POSIX instructions and Ubuntu CI job are advisory portability checks. Linux
is not a supported product platform unless the project charter is revised.

## Desktop workflow

Install the complete GUI workflow, then launch the safe simulated mode:

```powershell
python -m pip install -e ".[gui,simulated,storage,analysis,export]"
python -m labdaq.gui
python -m labdaq.gui --daq ni
```

The desktop provides Experiment Setup, Hardware Tests, Acquisition, and Sessions
/ Review pages. Discover the device, enable one or more available voltage inputs,
choose valid acquisition settings, and select **Start monitoring** under **Open
monitoring and recording**. Raw volts from the selected channels share one DAQ
task and a bounded ten-second display history. Monitoring needs no session path,
qualification report, sensor identity, calibration certificate, or confirmation
checklist. Select a writable session destination when recording is desired; each
**Record** / **Stop recording** cycle saves a separate synchronized session
without stopping monitoring. **End monitoring** finalizes any active recording
and releases the task. There is no pre-roll; raw voltage is not calibrated data.
Qualification and the timed acquisition workflow remain optional. The review
surface opens recorded sessions and delegates plotting, analysis, and export to
backend services. See the [desktop operator guide](docs/gui.md).

Offline FFT/PSD includes checked raw-only ai3 as volts, alongside calibrated or
conditioned acceleration spectra on separate unit axes. Raw-diagnostic and saved
open-acquisition recordings also support voltage spectra and CSV/PNG/SVG exports.
Per-channel metadata records the actual input stage; no charge calibration or
qualification is inferred. The LabDAQ 1.0.0 package includes this workflow.

## Windows laboratory package

LabDAQ 1.0.0 uses an unsigned all-users Windows installer. Setup requires
administrator approval and defaults to `C:\Program Files\LabDAQ`, with a choice
of dedicated installation folder. Start menu shortcuts select **LabDAQ — NI
Hardware** or **LabDAQ — Simulated**. NI-DAQmx remains an external prerequisite
for the hardware shortcut; simulation works without the NI driver. The previous
Feedback Preview is installed separately and may be removed independently.

The default workflow shows selected raw-voltage channels and can save multiple
independent recordings while monitoring continues. The operator chooses a writable
session parent when recording. Hardware configuration errors and recording faults
are displayed; qualification and sensor calibration are optional.

Build prerequisites are CPython 3.11, PyInstaller 6.22.2, and NSIS 3.12:

```powershell
packaging\windows\build-v1.ps1 `
  -NsisCompiler C:\path\to\makensis.exe
```

Generated installers, release manifests, and hashes are written under
`dist/lab-release` and remain ignored by Git. See the [1.0.0 package
notes](docs/releases/DAQ-1.0.0-lab-release.md) and the
[operator guide](docs/gui.md). Simulator verification does not establish physical
NI operation; target-hardware and clean-computer checks remain unverified unless
separately evidenced. The historical dev7 feedback preview is described in its
[release notes](docs/releases/DAQ-0.1.0.dev7-feedback-preview.md).

## Verification

```text
ruff format --check .
ruff check .
mypy src
lint-imports
pytest -q
python -m build
labdaq --version
labdaq diagnostics
labdaq diagnostics --json
python -m labdaq diagnostics --json
labdaq profile validate src/labdaq/resources/config/hardware-profiles/cdaq-9171-ni-9239-four-channel-v1.json
labdaq config validate tests/fixtures/configuration/valid-four-channel-synthetic-v1.json
labdaq recipe validate src/labdaq/resources/config/processing-recipes/baseline-v1.json
labdaq recipe show baseline-v1 --json
labdaq session summary path\to\session --verify --json
labdaq process-session path\to\session --recipe baseline-v1 --summary --json
```

Diagnostics inspect only the software environment and optional package
availability. They do not create an NI task or enumerate hardware.

## Architecture and governance

- [Project charter](PROJECT_CHARTER.md)
- [Architecture](ARCHITECTURE.md)
- [Roadmap](DAQ-Software-Roadmap.md)
- [Development guide](docs/development.md)
- [Glossary](docs/glossary.md)
- [ADR process](docs/adr/README.md)
- [DAQ-M00 specification](docs/milestones/DAQ-M00-design-charter-repository-foundation.md)
- [DAQ-M01 specification](docs/milestones/DAQ-M01-domain-records-validated-configuration.md)
- [DAQ-M01 acceptance evidence](docs/milestones/DAQ-M01-acceptance-evidence.md)
- [DAQ-M02 specification](Milestones/DAQ-M02-simulated-daq-deterministic-signal-generation.md)
- [DAQ-M02 acceptance evidence](docs/milestones/DAQ-M02-acceptance-evidence.md)
- [DAQ-M03 specification](Milestones/DAQ-M03-NI-DAQmx-hardware-adapter-and-discovery.md)
- [DAQ-M03 acceptance evidence](docs/milestones/DAQ-M03-acceptance-evidence.md)
- [DAQ-M04 specification](Milestones/DAQ-M04-acquisition-engine-and-state-machine.md)
- [DAQ-M04 acceptance evidence](docs/milestones/DAQ-M04-acceptance-evidence.md)
- [DAQ-M05 specification](Milestones/DAQ-M05-durable-recording-metadata-parsing-replay.md)
- [DAQ-M05 acceptance evidence](docs/milestones/DAQ-M05-acceptance-evidence.md)
- [DAQ-M06 specification](Milestones/DAQ-M06-calibration-signal-conditioning-pipeline.md)
- [DAQ-M06 acceptance evidence](docs/milestones/DAQ-M06-acceptance-evidence.md)
- [DAQ-M07 specification](Milestones/DAQ-M07-plotting-and-export-services.md)
- [DAQ-M07 acceptance evidence](docs/milestones/DAQ-M07-acceptance-evidence.md)
- [DAQ-M08 specification](Milestones/DAQ-M08-hardware-qualification-and-diagnostics.md)
- [DAQ-M08 acceptance evidence](docs/milestones/DAQ-M08-acceptance-evidence.md)
- [DAQ-M09 specification](Milestones/DAQ-M09-GUI-Shell-Configuration-Session-Browser.md)
- [DAQ-M09 acceptance evidence](docs/milestones/DAQ-M09-acceptance-evidence.md)
- [DAQ-M10 specification](Milestones/DAQ-M10-live-acquisition-plots-stop-control-post-run-analysis.md)
- [DAQ-M10 acceptance evidence](docs/milestones/DAQ-M10-acceptance-evidence.md)
- [DAQ-M10A specification](Milestones/DAQ-M10A-open-single-channel-acquisition.md)
- [DAQ-M10A acceptance evidence](docs/milestones/DAQ-M10A-acceptance-evidence.md)
- [GUI operator guide](docs/gui.md)
- [Calibration and processing](docs/processing.md)
- [Processing CLI](docs/cli/processing.md)
- [Plotting and export](docs/plotting-export.md)
- [Plotting/export CLI](docs/cli/plotting-export.md)
- [Hardware qualification](docs/hardware-qualification.md)
- [Qualification CLI](docs/cli/qualification.md)
- [Acquisition engine](docs/acquisition-engine.md)
- [Durable session format](docs/storage/session-format.md)
- [Session verification and recovery](docs/storage/recovery.md)
- [NI hardware setup and acceptance](docs/ni-hardware.md)
- [Simulator scenarios](docs/simulator-scenarios.md)
- [DAQ events](docs/daq-events.md)

The local `Data/` captures, generated sessions, and the retained manufacturer
PDF are deliberately excluded from version control. The historical NI script
is retained under `references/legacy/` and is not an implementation template.

## License

Proprietary. See [LICENSE](LICENSE).
