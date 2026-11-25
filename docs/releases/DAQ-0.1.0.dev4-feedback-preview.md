# LabDAQ 0.1.0.dev4 Internal Feedback Preview

> Superseded by 0.1.0.dev5. Close LabDAQ and install dev5 directly over dev4.

This unsigned, unqualified build replaces 0.1.0.dev3 for internal functional
feedback. It is not the DAQ-M11 laboratory release. Physical NI qualification,
clean-computer acceptance, soak testing, repeated physical cycles, and
independent-user acceptance remain `NOT_VERIFIED`.

## Functional NI launch

Close LabDAQ and run the dev4 installer directly over dev3. No manual uninstall
is required. The finish page, the NI Start Menu shortcut, and a bare launch of
`LabDAQ.exe` open the same streamlined NI functional workflow.

The packaged workflow uses the valid accelerometer-commissioning configuration:
ai0-ai2 retain their operator-transcribed, unverified sensitivities, while ai3
is synchronized and stored only as raw volts. It never substitutes charge-chain
sensitivity, polarity, units, or evidence.

Start is gated by a valid editable configuration, writable session destination,
available acquisition dependencies, and current compatible NI discovery. The
packaged workflow does not require a qualification report, charge-chain fields,
or run-time measurement confirmations. Acquisition never starts automatically.

All four raw rows are recorded. Live and offline calibrated/conditioned
processing includes X/Y/Z only. ai3 remains labeled `RAW ONLY / NOT CHARGE` and
is limited to raw-voltage display, replay, and export. Sessions remain visibly
`accelerometer_commissioning`, contain no qualification attachment, and cannot
be used as production or qualification evidence.

## Install and verify

1. Obtain the dev4 installer, release JSON, checksum, and these notes through the
   approved internal channel.
2. Compare the installer's SHA-256 with
   `LabDAQ-0.1.0.dev4-feedback-win64-setup.exe.sha256`.
3. Expect an unknown-publisher warning because this preview is not signed.
4. Install for the current user and accept the internal evaluation notice.
5. Start **LabDAQ Feedback Preview — NI Hardware (Unqualified)**.
6. Choose an existing writable session folder, confirm the acquisition settings,
   and refresh hardware if the automatic discovery result became stale.
7. Select **Continue to Acquisition**, then **Start Functional Run**.

The installer bundles LabDAQ, Python, Qt, scientific dependencies, and the NI
Python adapter. The NI-DAQmx Windows driver remains an external prerequisite. A
missing driver or incompatible/missing hardware blocks Start and never activates
simulation.

## Feedback, data, and uninstall

Use **Help > Create feedback bundle**, the Start Menu helper, or:

```powershell
LabDAQ-CLI.exe feedback-bundle --output C:\existing\feedback-folder
```

The ZIP is local and is never transmitted automatically. Automatic diagnostics
exclude sessions, samples, calibration data, usernames, absolute user paths,
and telemetry. Store sessions outside the installation directory. Uninstall
removes the application and shortcuts but not sessions or LabDAQ preferences.

## Known limitations

- Installer and executables are unsigned.
- X/Y/Z sensitivities are unqualified commissioning values.
- ai3 has no charge interpretation.
- Clean-machine and dedicated non-administrator-account acceptance is incomplete.
- Physical NI acquisition through this package is not accepted.
- Ten-minute target-rate, soak, 100-cycle, disconnect, disk-full, forced-close,
  and independent-operator evidence remains incomplete.
- The NI-only workflow does not satisfy M11's packaged simulator GUI criterion.

Immediately report silent loss, sample-count mismatches, corrupt or falsely
completed sessions, unreleased NI tasks, or destructive uninstall behavior.
