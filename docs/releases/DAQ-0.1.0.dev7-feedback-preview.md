# LabDAQ 0.1.0.dev7 Internal Feedback Preview

This unsigned, unqualified preview replaces dev6 with a shared Windows installer
and the current source GUI, including review-panel minimize/maximize/restore and
offline ai3 voltage spectra. It is not the DAQ-M11 laboratory release.

## Install for all Windows users

1. Close LabDAQ. If you have a dev6 or older per-user copy, uninstall it from its
   owning Windows account first so its old Start menu shortcut cannot open the old
   version. The new installer does not remove or migrate those copies.
2. Compare the installer SHA-256 against
   `LabDAQ-0.1.0.dev7-feedback-win64-setup.exe.sha256` and run setup.
3. Approve the administrator prompt. The unsigned preview may also display an
   unknown-publisher warning.
4. The default folder is `C:\Program Files\LabDAQ Feedback Preview` on standard
   Windows installations. Windows' 64-bit Program Files folder is used if its
   location differs. Use the directory page to select another dedicated, empty
   local folder. Drive/system roots, junction paths, and unrelated nonempty folders
   are rejected.
5. Close setup, then launch **LabDAQ Feedback Preview — NI Hardware (Unqualified)**
   from the Start menu. Both the NI launcher and feedback helper are available to
   every Windows account. The application itself runs without elevation.
6. Save recordings to a separately selected writable data folder. Preferences and
   recent locations remain personal to each Windows account.

Shared upgrades remember the existing shared installation folder. To relocate
that installation, uninstall it first, then choose the new folder in setup.
Shared installation, upgrades, and uninstall require administrator approval.
Uninstall uses an installed file inventory and nonrecursive directory removal:
unrecognized files, including files inside runtime subfolders, are retained.
An unreadable or invalid inventory blocks cleanup rather than deleting broadly.

## Included workflow

The NI GUI retains open multichannel monitoring and repeated recording segments,
with the existing electrical authorization and timed-run commissioning workflow.
Session review supports raw-voltage FFT/PSD for checked ai3 alongside acceleration
spectra where eligible. Raw samples and their calibration provenance are preserved.

All eight review panels have minimize/maximize controls. Restore hidden panels
from the bar below the workspace, use Escape to restore a maximized panel, or use
Reset layout. Layout state lasts for the application window only. Analysis and
exports continue while their panels are hidden.

## Verification boundary

NI-DAQmx remains an external prerequisite. Build-time frozen CLI/simulator and
NI-import checks do not verify physical acquisition. Administrator/UAC interaction,
second-account launch, full installer upgrade/uninstall, clean Windows, physical
NI acquisition, soak, recovery, and independent-user acceptance remain
`NOT_VERIFIED` unless separately evidenced. The build task does not install or
uninstall the application on the development workstation.
