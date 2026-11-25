# LabDAQ 0.1.0.dev2 Internal Feedback Preview

This unsigned, unqualified build replaces 0.1.0.dev1 for supervised internal
feedback. It is not the DAQ-M11 laboratory release. Physical NI qualification,
clean-computer acceptance, soak testing, repeated physical cycles, and
independent-user acceptance remain `NOT_VERIFIED`.

## NI packaging hotfix

Version 0.1.0.dev1 omitted distribution metadata required when the bundled
`nitypes` package initialized. That prevented the NI launcher from opening and
could report `PackageNotFoundError: nitypes` before NI-DAQmx discovery. Version
0.1.0.dev2 bundles and verifies the active NI Python dependency metadata.

Stop using the dev1 installer. Close LabDAQ and run the dev2 installer; it may be
installed directly over dev1. No manual uninstall is required, and the upgrade
does not remove recorded sessions or existing LabDAQ preferences.

## Install and verify

1. Obtain the dev2 installer, release JSON, checksum, and these notes through the
   approved internal channel.
2. Compare the installer's SHA-256 with
   `LabDAQ-0.1.0.dev2-feedback-win64-setup.exe.sha256`.
3. Expect an unknown-publisher warning because this preview is not signed.
4. Install for the current user and accept the internal evaluation notice.
5. Start with **LabDAQ Feedback Preview — Simulated**.

The installer includes LabDAQ, Python, Qt, scientific dependencies, and the NI
Python adapter. The NI-DAQmx Windows driver remains an external prerequisite.

## Safe operation

Every normal GUI launch requires acknowledgement before any DAQ backend is
created. Simulator results exercise software only and do not qualify wiring,
sensors, calibration, external conditioning, or NI hardware.

Use **LabDAQ Feedback Preview — NI Hardware (Unqualified)** only under
supervision. Independently verify hardware identity, wiring, sensor power,
terminal configuration, ranges, calibration provenance, polarity, and external
conditioning before a physical task. A missing or unusable NI-DAQmx prerequisite
must not prevent simulated-mode use.

## Feedback, data, and uninstall

Use **Help > Create feedback bundle**, the Start Menu helper, or:

```powershell
LabDAQ-CLI.exe feedback-bundle --output C:\existing\feedback-folder
```

The ZIP is created locally and is never transmitted automatically. Automatic
diagnostics exclude sessions, samples, calibration data, usernames, absolute
user paths, and telemetry.

Store sessions outside the application installation directory. Uninstall removes
the application, shortcuts, and preview registration but does not remove sessions
or existing LabDAQ preferences.

## Known limitations

- Installer and executables are unsigned.
- Clean-machine and dedicated non-administrator-account acceptance is incomplete.
- Physical NI acquisition through this package is not accepted.
- Ten-minute target-rate, long-duration, and 100-cycle physical evidence is absent.
- Packaged disconnect, disk-full, forced-close, and independent-operator evidence
  remains incomplete.
- The repository has no source commit; build identity therefore records a
  source-tree SHA-256 and an unborn/dirty Git state.

Immediately stop and report silent loss, sample-count mismatches, corrupt or
falsely completed sessions, unreleased NI tasks, or destructive uninstall behavior.
