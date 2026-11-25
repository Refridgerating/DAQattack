# LabDAQ 0.1.0.dev3 Internal Feedback Preview

> Superseded by 0.1.0.dev4. Close LabDAQ and install dev4 directly over dev3.

This unsigned, unqualified build replaces 0.1.0.dev2 for supervised internal
feedback. It is not the DAQ-M11 laboratory release. Physical NI qualification,
clean-computer acceptance, soak testing, repeated physical cycles, and
independent-user acceptance remain `NOT_VERIFIED`.

## NI-only packaged launch

Version 0.1.0.dev3 removes the simulated launcher from the installed user
experience. The installer finish page, the Start Menu application shortcut, and
a bare launch of the installed `LabDAQ.exe` all select the NI backend. The
packaged launch no longer displays a separate acknowledgement modal; acceptance
of this evaluation notice remains required during installation, and the window
title and shortcut continue to identify the build as an unqualified feedback
preview.

The simulator remains bundled only for engineering CLI checks and automated
validation. Testers should not use it as an operational workflow, and LabDAQ
never falls back to it when NI-DAQmx is missing or unavailable.

Stop using the dev2 installer. Close LabDAQ and run the dev3 installer; it may be
installed directly over dev2. No manual uninstall is required. The upgrade
removes the obsolete simulated Start Menu shortcut without removing recorded
sessions or existing LabDAQ preferences.

## Install and verify

1. Obtain the dev3 installer, release JSON, checksum, and these notes through the
   approved internal channel.
2. Compare the installer's SHA-256 with
   `LabDAQ-0.1.0.dev3-feedback-win64-setup.exe.sha256`.
3. Expect an unknown-publisher warning because this preview is not signed.
4. Install for the current user and accept the internal evaluation notice.
5. Start **LabDAQ Feedback Preview — NI Hardware (Unqualified)**.

The installer includes LabDAQ, Python, Qt, scientific dependencies, and the NI
Python adapter. The NI-DAQmx Windows driver remains an external prerequisite. A
missing or unusable driver is an error and does not activate simulated mode.

Opening the GUI initializes the NI application services but does not start an
acquisition task. Existing readiness, hardware validation, qualification, and
start-eligibility controls remain in force. Independently verify hardware
identity, wiring, sensor power, terminal configuration, ranges, calibration
provenance, polarity, and external conditioning before starting a physical task.

## Feedback, data, and uninstall

Use **Help > Create feedback bundle**, the Start Menu helper, or:

```powershell
LabDAQ-CLI.exe feedback-bundle --output C:\existing\feedback-folder
```

The ZIP is created locally and is never transmitted automatically. Automatic
diagnostics exclude sessions, samples, calibration data, usernames, absolute
user paths, and telemetry. The feedback helper does not initialize a DAQ backend.

Store sessions outside the application installation directory. Uninstall removes
the application, current and obsolete shortcuts, and preview registration but
does not remove sessions or existing LabDAQ preferences.

## Known limitations

- Installer and executables are unsigned.
- Clean-machine and dedicated non-administrator-account acceptance is incomplete.
- Physical NI acquisition through this package is not accepted.
- Ten-minute target-rate, long-duration, and 100-cycle physical evidence is absent.
- Packaged disconnect, disk-full, forced-close, and independent-operator evidence
  remains incomplete.
- The NI-only tester workflow does not satisfy M11's clean-machine simulated GUI
  acceptance criterion.
- The repository has no source commit; build identity therefore records a
  source-tree SHA-256 and an unborn/dirty Git state.

Immediately stop and report silent loss, sample-count mismatches, corrupt or
falsely completed sessions, unreleased NI tasks, or destructive uninstall behavior.
