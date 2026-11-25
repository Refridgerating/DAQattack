# DAQ-M11 Feedback-Preview Packaging Evidence

This document records a deliberately incomplete packaging slice. It is not DAQ-M11
completion evidence and does not authorize an unconditional laboratory release.

## 2026-09-09 dev7 shared installation

Dev7 changes setup to an administrator-elevated installation for all Windows
accounts, defaulting to the 64-bit Program Files folder with an editable directory
page. Shared shortcuts and 64-bit HKLM registration replace the per-user install
policy for new packages. Old per-user copies remain untouched. The finish page
directs users to the Start menu instead of launching an elevated application.

The package includes the previously verified review-panel controls and current
offline voltage spectra. No scientific session schema or application service
contract changes in this packaging task.

Changes include the NSIS installer, new `install-paths.nsh` policy include,
build script, PyInstaller version resources, release-metadata/inventory generator,
Python version metadata, packaging tests and a non-elevating NSIS test harness.
ADR-0030, the roadmap, README, developer guide, evaluation notice, and new dev7
release notes record the scope change. Previous release notes/artifacts remain;
no source files were removed.

The inventory permits upgrades to remove old versioned runtime metadata without
deleting unrecognized files, even inside runtime folders. System roots, unrelated
nonempty destinations, junction paths, and invalid inventory entries are rejected.
Shared upgrades retain their registered folder; relocating requires uninstall.

Focused packaging verification: `python -m pytest tests/unit/packaging -q --no-cov`
passed **18 tests**, including executable NSIS path and cleanup tests in temporary
folders. Ruff format/lint, mypy (92 source files), and all five import contracts
passed. Full build and suite results are recorded after completion below.

Final source baseline: `python -m pytest -q --cov-report=term:skip-covered --durations=5`
with `QT_QPA_PLATFORM=offscreen` passed **645 tests, 6 skipped**, in **195.32 s**,
with **85.65% coverage** (85% required). Ruff formatting/lint passed for 244 files;
mypy and all five import contracts passed. This final run was performed with the
packager idle after the fixture corrections below.

Full-suite investigation corrected two existing test-fixture timing problems:
the GUI test now retains a real hardware presentation for pending destination
callbacks, and the small-chunk open-monitor test preserves its original
160,000-sample recording buffer instead of accidentally shrinking initialization
headroom to 160 ms. All four open-acquisition tests, including explicit queue
saturation, pass. Production acquisition/buffering policy was not changed.
The build script also handles PyInstaller INFO output on redirected stderr under
Windows PowerShell using the process exit status. The complete NSIS script passes
a compiler-only check with a dummy payload; that check does not install software.

Final build command: `packaging/windows/build-feedback-preview.ps1 -AutomatedBaseline PASS`.
PyInstaller 6.22.2 and NSIS 3.12 produced the unsigned
`LabDAQ-0.1.0.dev7-feedback-win64-setup.exe` (**78,903,463 bytes**) under
`dist/feedback-preview`, along with build identity, third-party notices, release
notes, evaluation notice, release manifest, checksum, and smoke evidence JSON.

- Build ID: `feedback-9f8cec27776e`.
- Build-time source SHA-256: `9f8cec27776e69cf45d51e11d9c633ac1df4960701cd8359c1ec9e16c72a0a09`.
- Installer SHA-256: `37f9847b2aa70b3dd135696625f99540874bed9d1cede8f173a7f046834435e4`.
- Frozen CLI version/diagnostics and bundled `baseline-v1` recipe loading passed
  from a temporary working directory outside the source tree, without Python
  environment variables.
- Frozen simulator discovery and four-channel acquisition passed: 10,000 samples
  per channel, `[4, 10000]` shape, 257-sample reads, and successful cleanup.
- Frozen NI import/discovery returned `overall: ok`. Exactly one metadata directory
  exists for LabDAQ dev7, nidaqmx 1.6.0, nitypes 1.1.0, and hightime 1.0.0.
  This is discovery evidence only; no physical acquisition was started.
- PE inspection confirmed installer `requireAdministrator`, GUI/CLI `asInvoker`,
  and `0.1.0.7` version resources. Archive inspection confirmed the review-panel
  controller is bundled.
- The release manifest records automated baseline and local package smoke as PASS.
  Its artifact hash/size agrees with the installer, standalone checksum, and smoke
  evidence. Build identity refers to the snapshot preceding this final evidence
  update. Previous versioned release artifacts were preserved.

The workstation's installed copy is not replaced. Administrator/UAC interaction,
second-standard-user launch, complete installed upgrade/uninstall, clean Windows,
and physical NI acceptance remain **NOT_VERIFIED**; the non-elevating harness
does not claim those results. M11 remains incomplete.

## Implemented preview controls

- Versioned `feedback-preview` build identity and source-tree SHA-256
- NI-first packaged GUI launch without a separate acknowledgement modal
- Packaged-only functional readiness path with no qualification or run confirmation
- Four-row recording with derived processing restricted to commissioning X/Y/Z
- Default open monitoring of all enabled raw channels with authorized repeated segments
- Smart serialized hardware revalidation after DAQ-facing setup edits
- Single NI application shortcut plus a backend-free feedback helper
- Local, sanitized, non-transmitting feedback bundles
- Shared all-users PyInstaller/NSIS packaging configuration (dev7 onward)
- Internal evaluation notice, release manifest, and artifact SHA-256 generation
- Uninstall policy that preserves sessions and LabDAQ preferences

## 0.1.0.dev2 replacement

The 0.1.0.dev1 frozen payload omitted `nitypes` distribution metadata and could
not initialize the NI Python adapter. Version 0.1.0.dev2 supersedes dev1 and adds
recursive NI dependency-metadata collection plus a mandatory frozen NI-discovery
build gate. This is packaging evidence only and does not qualify physical NI use.

## 0.1.0.dev3 replacement

Version 0.1.0.dev3 supersedes dev2 with an NI-only installed user experience.
The frozen desktop defaults to NI and bypasses the source/developer
acknowledgement, while the simulator remains dormant for engineering CLI checks.
The installer removes the obsolete dev2 simulator shortcut during an in-place
upgrade. This does not qualify physical hardware or relax acquisition readiness.

## 0.1.0.dev4 replacement

Version 0.1.0.dev4 supersedes dev3 with a packaged-only functional NI workflow.
It uses the valid accelerometer-commissioning template, records four synchronized
raw rows, derives X/Y/Z products, preserves ai3 as raw volts only, and requires no
qualification report or run-time diagnostic confirmation. Start still requires a
writable destination and fresh compatible hardware validation. Sessions attach
no qualification evidence and remain visibly commissioning/unqualified.

## 0.1.0.dev5 replacement

Version 0.1.0.dev5 supersedes dev4 with open monitoring selected by default in
the packaged functional GUI. It retains all enabled channels in a bounded live
ring and records repeated synchronized raw-diagnostic HDF5 segments without
restarting the DAQ task. Open monitoring keeps the explicit electrical
authorization; timed functional runs remain available without it. Metadata edits
no longer discard compatible hardware evidence, and DAQ-facing edits schedule a
serialized automatic recheck.

## 0.1.0.dev6 replacement

Version 0.1.0.dev6 supersedes dev5 as the current installable feedback preview.
It retains the dev5 open-monitoring workflow and unqualified M11 boundary while
aligning the Python package, installer, frozen executable, release-manifest,
checksum, notice, and release-note version metadata.

## Dev6 acceptance state (historical)

| Criterion | State | Evidence |
| --- | --- | --- |
| Existing automated suite | PASS | `pytest -q`: 538 passed, 6 hardware-only skips, 85.23% coverage. Ruff format/lint passed for 239 files, strict mypy passed for 91 source files, and all 5 import-linter contracts were kept. |
| Python package construction | PASS | `python -m build` produced the 0.1.0.dev6 sdist and wheel. |
| Frozen NI metadata/import gate | PASS | The payload contains exactly one `nidaqmx` 1.6.0, `nitypes` 1.1.0, and `hightime` 1.0.0 metadata directory. Frozen NI discovery imported successfully through NI-DAQmx 16.1.0. No device was enumerated during this build and no physical acquisition was attempted. |
| Frozen CLI/resource smoke tests | PASS | Frozen version and diagnostics, bundled baseline-recipe loading, engineering-only simulator discovery, and a 10,000-sample-per-channel four-channel acquisition passed outside the Python environment. |
| Dev5-to-dev6 installer upgrade and uninstall | NOT_VERIFIED | The dev6 installer is built but is not installed or uninstalled automatically by this task. |
| Published artifact hash | PASS | The unsigned dev6 manifest and standalone SHA-256 were regenerated; the installer, manifest, and checksum digest agree. |
| Dedicated non-administrator account | NOT_VERIFIED | Local installation used a non-elevated token, but the account is a member of the local Administrators group. |
| Clean Windows without Python/NI | NOT_VERIFIED | Requires a separate clean host or VM. |
| Packaged simulated GUI workflow | NOT_VERIFIED | The NI-only preview deliberately exposes no tester simulator launcher and does not satisfy this M11 criterion. |
| Packaged functional NI GUI acquisition | NOT_VERIFIED | Packaged startup and hardware discovery passed, but no physical task or recorded session was started. |
| Packaged physical NI workflow | NOT_VERIFIED | Requires target hardware and supervised protocol. |
| Ten-minute physical target-rate run | NOT_VERIFIED | Requires target hardware. |
| Long-duration soak | NOT_VERIFIED | Procedure and retained measurements remain required. |
| 100 physical start/stop cycles | NOT_VERIFIED | Requires target hardware. |
| Disconnect/disk-full/forced-close package evidence | NOT_VERIFIED | Controlled package testing remains required. |
| Independent operator | NOT_VERIFIED | Requires an unfamiliar lab user. |
| Unconditional M11 release | NOT_VERIFIED | Blocked by the gates above. |

Generated installers, release manifests, hashes, and large evidence are retained
outside version control under `dist/feedback-preview` or the approved internal
release location.
