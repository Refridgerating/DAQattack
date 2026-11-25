# LabDAQ 0.1.0.dev1 Internal Feedback Preview

This is an unsigned, unqualified build for supervised internal feedback. It is
not the DAQ-M11 laboratory release. Physical NI qualification, clean-computer
acceptance, soak testing, 100 physical start/stop cycles, and independent-user
acceptance remain `NOT_VERIFIED`.

## Install and verify

1. Obtain the installer, release JSON, checksum, and these notes through the
   approved internal channel.
2. Compare the installer's SHA-256 with
   `LabDAQ-0.1.0.dev1-feedback-win64-setup.exe.sha256` before running it.
3. Expect Windows to identify an unknown publisher because this preview is not
   Authenticode-signed.
4. Install for the current user. No administrator access is required.
5. Read and accept the internal evaluation notice.

The application installs under the current user's local application-data
directory. The installer does not install Python or the NI-DAQmx Windows driver.

## Safe first launch

Use **LabDAQ Feedback Preview — Simulated** first. Every application launch
requires acknowledgement of the preview limitations before any DAQ backend is
created. Cancelling that dialog exits without initializing DAQ hardware.

The simulator is the safe default. Its deterministic results exercise software
behavior only and do not qualify wiring, sensors, calibration, external
conditioning, or NI hardware.

## NI hardware launcher

**LabDAQ Feedback Preview — NI Hardware (Unqualified)** is an explicit,
supervised-use launcher. The NI-DAQmx Windows driver must be installed separately.
Before any physical task, independently verify the chassis/module identity,
wiring, sensor power, terminal configuration, input range, calibration
provenance, polarity, and charge-conditioner compatibility. Follow
`docs/ni-hardware.md` and retain qualification evidence.

Missing or unusable NI prerequisites must not prevent simulated-mode use. Do not
automatically retry a physical acquisition after a fault.

## Send feedback

Choose **Help > Create feedback bundle** or the Start Menu entry. Complete the
structured form and select a writable destination. LabDAQ creates a ZIP locally;
it sends no network traffic.

Automatic diagnostics include application/build identity, operating-system and
runtime versions, dependency availability, and a temporary-file probe. They
exclude sessions, samples, calibration data, usernames, executable/working paths,
and telemetry. Review any manually added screenshot or data before returning the
bundle through the lab's approved internal channel.

The equivalent CLI is:

```powershell
LabDAQ-CLI.exe feedback-bundle --output C:\existing\feedback-folder
```

## Data and uninstall behavior

Select a writable session parent outside the installed application directory.
Raw HDF5 sessions remain authoritative source records. Never treat CSV, plots, or
processed arrays as substitutes.

Uninstall removes the program payload, shortcuts, and uninstall registration. It
does not remove recorded sessions or the existing LabDAQ user preferences.

## Known limitations

- The installer and executables are unsigned.
- Windows clean-machine acceptance is not yet retained.
- Physical NI discovery/acquisition through this package is not yet accepted.
- Target-rate ten-minute and long-duration physical results are not available.
- One hundred physical start/stop cycles have not been performed.
- Forced-close, disconnect, disk-full, and independent-operator package evidence
  remains incomplete.
- The repository has no source commit yet; build identity therefore retains a
  source-tree SHA-256 and marks the Git source state as unborn/dirty.

Report any silent loss, sample/count mismatch, corrupted or falsely completed
session, unreleased NI task, or destructive uninstall behavior immediately and
stop using that build.
