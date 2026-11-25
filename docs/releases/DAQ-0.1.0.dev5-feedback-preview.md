# LabDAQ 0.1.0.dev5 Internal Feedback Preview

> Superseded by 0.1.0.dev6. Close LabDAQ and install dev6 directly over dev5.

This unsigned, unqualified build replaces 0.1.0.dev4 for internal functional
feedback. It is not the DAQ-M11 laboratory release. Physical NI acquisition,
clean-computer acceptance, soak testing, repeated physical cycles, recovery, and
independent-user acceptance remain `NOT_VERIFIED`.

## Open monitoring and recording

The packaged Acquisition tab now defaults to **Open monitoring and recording**.
After current hardware and destination checks pass, **Start monitoring** collects
the existing electrical authorization and starts one continuous DAQ task for all
enabled channels. The live raw-voltage plot retains at most ten seconds per
channel; display settings never alter acquisition or recording.

**Record** begins a new synchronized four-channel raw-diagnostic HDF5 session at
the next whole source chunk. **Stop recording** finalizes that segment while the
monitor continues, and the cycle may be repeated. **End monitoring** finalizes an
active segment before releasing the DAQ task. There is no pre-roll and these
sessions authorize no calibration, conditioning, metrics, FFT, PSD, acceleration,
or charge claims.

The legacy primary-channel manifest field remains for compatibility, while
`recorded_channel_ids` states the authoritative stored channel order. Existing
single-channel M10A sessions remain readable.

## Setup and timed runs

Editing experiment metadata, destination, sensor, calibration, or conditioning
fields no longer forces a hardware refresh. Acquisition or DAQ-facing channel
edits schedule a serialized automatic validation; invalid settings remain blocked
until corrected. The explicit refresh control and fresh pre-launch validation
remain in place.

Choose **Timed functional run** to retain the dev4 finite unqualified workflow.
It records ai0-ai3, derives X/Y/Z using the unverified commissioning sensitivities,
and keeps ai3 as raw volts only. It creates no qualification attachment.

## Install and verify

1. Close LabDAQ and run the dev5 installer directly over dev4.
2. Compare the installer's SHA-256 with
   `LabDAQ-0.1.0.dev5-feedback-win64-setup.exe.sha256`.
3. Expect an unknown-publisher warning because this preview is unsigned.
4. Store sessions outside the installation directory.
5. Report any silent loss, sample-count mismatch, corrupt session, or unreleased
   NI task immediately.

The NI-DAQmx Windows driver remains an external prerequisite. The installer and
its uninstall path are not executed automatically as part of the build task.
