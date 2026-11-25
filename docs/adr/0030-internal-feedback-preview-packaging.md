# ADR-0030: Internal Feedback-Preview Packaging

Status: Accepted

This decision records the historical dev1–dev7 feedback-preview channel. The
separate 1.0.0 laboratory package and its current launcher and installation policy
are described in [ADR-0034](0034-version-one-laboratory-package.md); the preview
installation remains separately identifiable and is not implicitly removed.

## Context

LabDAQ M00 through M10A has simulator-backed software evidence, but M11 physical,
clean-computer, soak, repeated-run, recovery, and independent-operator release
gates are incomplete. Internal users need an installable build to provide early
workflow feedback. Calling that build a laboratory release would overstate the
available evidence, while distributing the Python wheel would require a
development environment and expose an avoidable setup barrier.

## Decision

Version 0.1.0.dev1 established the `feedback-preview` channel; 0.1.0.dev2
superseded it with corrected NI dependency metadata, and 0.1.0.dev3 changed the
installed user surface to a single NI hardware launcher. Version 0.1.0.dev4
opened that launcher in a streamlined functional commissioning workflow, and
0.1.0.dev5 makes bounded multichannel open monitoring the default Acquisition mode.
Version 0.1.0.dev6 superseded dev5 without changing the qualification boundary.
Version 0.1.0.dev7 changes installation scope to one shared machine installation
and includes the current review GUI. Scientific-data schemas remain unchanged.
PyInstaller creates
one Windows `onedir` payload containing a windowed GUI and console CLI; NSIS wraps
that payload in an administrator-elevated, all-users installer starting with dev7.
The default is the 64-bit Program Files directory with an editable destination
page, shared Start menu shortcuts, and 64-bit HKLM installation/uninstall records.
Earlier versions used current-user, non-elevating installation. Those per-user
copies are left untouched; users uninstall them from their owning accounts before
switching to the shared copy. Shared upgrades reuse the registered folder; changing
that location requires uninstall first.

The installer requires an empty dedicated directory or its registered shared
installation, rejects drive/system roots and filesystem links, and uses a generated
UTF-16LE file inventory for cleanup. Only inventoried files are deleted; empty
parent directories are pruned nonrecursively. Unrecognized files survive even
inside runtime directories. Invalid inventories stop cleanup. NSIS include files
participate in the build's source-tree digest. The finish page directs users to
the Start menu rather than starting the application with the installer's elevated
token. Application launches and per-user preferences retain normal privileges.

The installed executable
defaults to NI, and NI-DAQmx remains an external prerequisite.

Packaged Windows launches do not require the source/developer acknowledgement;
the tester accepts the evaluation notice during installation, and unqualified
status remains visible in the shortcut and GUI title. Source/developer launches
retain a configurable acknowledgement boundary. Feedback is collected only
through a local, tester-initiated bundle with path-bearing diagnostics removed
and no network transmission. Build identity records the source-tree digest and
source state because the repository may not have a commit. Installer removal
never deletes sessions or shared LabDAQ preferences.

The simulator remains in the shared payload for engineering CLI checks and
automated validation, but the installer exposes no simulator shortcut or default.
NI startup never falls back to simulation.

The dev5 packaged workflow bypasses production qualification and run-time
diagnostic confirmations only for an explicitly unqualified timed commissioning
run. Open monitoring retains the raw-diagnostic electrical confirmation boundary.
It uses the valid X/Y/Z plus raw-only-ai3 configuration, records all four raw
rows, derives products only for calibrated X/Y/Z, and attaches no qualification
evidence. The source/developer qualified and raw-diagnostic workflows remain
unchanged. This policy does not add or reinterpret a scientific-data schema.

The open workflow retains and plots all enabled raw channels in bounded memory.
Each Record/Stop cycle creates a separate synchronized raw-diagnostic segment.
Schema-v2 provenance adds `recorded_channel_ids` while retaining the historical
primary-channel field. Metadata-only edits retain hardware compatibility evidence;
DAQ-facing edits trigger serialized automatic revalidation, and launch still
performs a fresh validation.

The package is unsigned and restricted to owner-authorized internal evaluation.
Release metadata keeps all unperformed M11 gates `NOT_VERIFIED`.

## Consequences

- Internal testers can install, uninstall, and launch the NI GUI without
  installing Python or selecting a backend.
- Preview warnings and hashes distinguish the package from a qualified release.
- NI-DAQmx remains an external prerequisite; dormant simulator evidence remains
  scientifically separate from physical acceptance.
- M11 remains incomplete until its clean-machine, physical, soak, failure,
  compatibility, and usability evidence exists.
- Later signed releases may replace this packaging channel without changing
  session, configuration, recipe, or qualification schemas.
