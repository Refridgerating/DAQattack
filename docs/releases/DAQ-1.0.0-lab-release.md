# LabDAQ 1.0.0 laboratory package

LabDAQ monitors selected voltage inputs and can save multiple recording segments
while the same acquisition continues. The displayed and recorded values are raw
volts. Qualification and calibration are optional and are not required to monitor
or record.

## Install on Windows

1. Close LabDAQ. The earlier Feedback Preview is a separate installation; remove
   it through Windows if it is no longer needed to avoid opening an old shortcut.
2. Verify `LabDAQ-1.0.0-win64-setup.exe` against the accompanying SHA-256 file,
   then run the installer. Windows may identify the unsigned installer as coming
   from an unknown publisher.
3. Approve the administrator prompt and choose a dedicated application folder.
   The default is `C:\Program Files\LabDAQ`. The shared installation is available
   to Windows users on that computer; the application itself runs without elevation.
4. Use the LabDAQ Start menu shortcut for **NI hardware** or **Simulator**.
   NI operation requires the NI-DAQmx Windows driver; the Python package included
   with LabDAQ does not install that driver. Simulation does not need NI software.
5. Choose a separate existing writable data folder when you record. Do not save
   sessions in the application installation folder.

For example, run `Get-FileHash .\LabDAQ-1.0.0-win64-setup.exe -Algorithm SHA256`
in the folder containing the installer and compare the result with the supplied
`.sha256` file.

Upgrades reuse the registered shared installation folder; uninstall first to move
it. Setup and uninstall require administrator approval. The installer tracks its
own files and preserves unrelated files rather than deleting a directory recursively.

## Monitor and record

1. Open **Experiment Setup**, refresh hardware discovery, and enable one or more
   available voltage inputs. Check the displayed physical channels, sample rate,
   input range, and any validation messages.
2. Open **Acquisition** and select **Start monitoring**. Each selected channel
   appears in volts against time derived from its sample index. Monitoring alone
   does not create a session or require a storage selection.
3. Set **Session destination** to an existing writable parent folder, then select
   **Record**. If the destination is empty, a folder chooser appears. **Stop
   recording** finalizes that segment while monitoring continues. Repeat to create
   separate sessions; there is no pre-recording buffer.
4. Select **End monitoring** when finished. An active recording is finalized
   before the task is released. Open **Sessions / Review**, select the session
   parent and refresh, then review the saved session.

The session folder includes `manifest.json`, `raw.h5`, and `events.jsonl` as well
as processing and output directories. Keep the session folder together. Recording
and acquisition errors are displayed; preserve any partial session for diagnosis.
Display frame drops are distinct from acquisition or storage loss.

## Troubleshooting startup

NI startup may reuse a profile, experiment configuration, or qualification-plan
path saved by that Windows user. If a saved-path error identifies a file that was
moved or removed, restore that file to its previous path and restart. Saved paths
can be inspected with `reg query HKCU\Software\LabDAQ\LabDAQ\validated\production`
in Command Prompt; if needed, ask the lab maintainer to help correct the saved
setting. Simulator startup does not depend on those NI paths. Do not delete session
files to resolve a startup preference.

## Verification and scope

The source software and simulator workflow passed automated tests. The physical
NI cDAQ-9171 / NI-9239 workflow and task release, installation on a clean Windows
computer, and shared upgrade and uninstall remain unverified unless separately
recorded in release evidence. Simulator results do not verify physical channel
mapping, wiring, grounding, excitation, sensor power, voltage compatibility, or
external conditioning. The package is unsigned. Consult `LICENSE` for the terms
applicable to the software.
