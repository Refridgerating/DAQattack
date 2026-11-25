# Desktop Operator Guide

## Launch

For the packaged Windows application, launch **LabDAQ — NI Hardware** or
**LabDAQ — Simulated** from the LabDAQ Start menu. Setup is shared across
Windows users and requires an administrator; the application runs without
elevation. NI mode requires the separately installed NI-DAQmx Windows driver.
Simulation does not require NI software. See the 1.0.0 laboratory package notes
included with the installer for installation and verification status.

For a source checkout, install the complete GUI workflow with plotting, analysis,
export, simulation, and session storage:

```powershell
python -m pip install -e ".[gui,simulated,storage,analysis,export]"
python -m labdaq.gui
```

Run these commands after activating the intended Python 3.11 virtual
environment. In PowerShell, `-m` is an argument to `python`; it is not a command
by itself. For example:

```powershell
. .\.venv\Scripts\Activate.ps1
python -m labdaq.gui --daq simulated
```

Simulation is the safe default. Physical mode is explicit:

```powershell
python -m labdaq.gui --daq ni
```

The normal workflow starts with raw monitoring. Select one or more available
voltage channels and valid acquisition settings, then start monitoring; no
session destination, sensor identity, calibration certificate, qualification
report, or confirmation checklist is needed. Qualification and the legacy
timed acquisition workflow remain available as optional tools. NI mode requires
NI-DAQmx and never falls back to simulation.

### Field hover help

Hover over an editable field, selector, readout, table heading or cell, or plot
control to see a short native tooltip explaining what the value represents and
how LabDAQ uses it. Help remains available on read-only and temporarily disabled
fields. Table cells repeat their column description so the meaning stays visible
while reviewing data away from the heading. Plot canvases retain coordinate
inspection instead of showing field help.

When a field has a validation problem or live condition, the fixed description
remains at the top of the tooltip and the current message appears under
`Current status:`. This includes readiness blockers, raw-diagnostic processing
restrictions, stale analysis, the live metric window, and export progress. A
cleared condition removes only the status section; it does not remove the field
description. Hover help is explanatory and does not establish electrical safety
or replace authoritative calibration and conditioning evidence.

An unavailable optional dependency disables its capability with a structured
message. Requested NI mode never falls back to simulation. Simulator results do
not verify wiring, grounding, excitation, sensor power, conditioner
compatibility, safe input voltage, physical channel mapping, or task release on
the target chassis.

## Start raw monitoring

1. Launch the simulator or NI hardware shortcut, or choose a mode from a source
   checkout as shown above.
   Use **Refresh hardware** to discover the supported device and channels.
2. On **Experiment Setup**, enable one or more available voltage inputs in the
   channel table. Check the physical channel identities, input range, sample
   rate, and samples per read. Correct any field-level validation messages.
3. On **Acquisition**, choose **Open monitoring and recording** and select
   **Start monitoring**. Each selected channel appears as raw voltage against
   sample-index-derived time. No session is created for monitoring alone.
4. To save data, choose **Record**. If **Session destination** is empty, choose
   an existing writable parent when prompted; otherwise the entered parent is
   used. **Stop recording** closes the current segment while monitoring continues.
   Repeat to save further segments.
5. Choose **End monitoring** to finalize an active recording and release the
   DAQ task. An orderly application close also releases resources.

The sample-rate dropdown reflects the active hardware profile. Revision 2
contains 31 `50,000 / n` internal-clock rates. A rate change resets **Samples /
read / channel** to the nearest 100 ms batch; a manual batch override remains
independent until the next rate change. The software reports driver, device,
channel, and configuration errors so they can be corrected.

**Hardware Tests** runs optional qualification away from the UI thread and
retains its report and raw evidence. Qualification does not authorize or certify
ordinary raw monitoring or recording. The optional timed workflow and processing
features retain their own configuration and provenance requirements. Neither
simulator output nor software validation verifies wiring, grounding, excitation,
sensor power, conditioner compatibility, safe input voltage, physical channel
mapping, or task release on the target chassis.

## Acquisition

Use **Hide navigation** in the application header to give the active page the
full window width; **Show navigation** restores the same selected page. The
navigation always starts visible on a new launch.

The acquisition workspace gives most of its height to the live plots. Drag the
horizontal divider between status and metrics, the vertical divider between
monitoring and plots, or the divider between raw and conditioned plots to adjust
the tiles. A subtle border identifies each resizable tile. Both live plots remain
active and visible in the timed workflow. Within each plot panel,
the channel list and canvas are horizontally resizable, and separate unit or
independent-channel canvases have their own vertical dividers. Layout sizes
return to their plot-focused defaults on the next launch.

The Acquisition page reports backend and finalization states, session ID/path,
requested and actual rates separately, ordered channels/units, requested and
sample-derived actual duration, elapsed time, acquired
and persisted counts, recording/display queue occupancy, warnings, source gaps,
display replacements, and terminal persistence loss. Persisted lag while
running is queue progress; it is not called loss until terminal evidence exists.

In the timed workflow, raw-voltage and causal selected-recipe panels refresh toward 10 Hz from immutable
application frames. The backend retains no more than ten seconds, displays five
seconds by default, and emits at most 2,000 peak-preserving points per channel.
Choose a 1, 2, 5, or 10 second window; toggle channels; use compatible-unit
shared, independent, manual, or automatic scaling; and move over a plot to
inspect coordinates. These operations do not reconfigure the DAQ task.

**Pause display** freezes widget rendering only. Acquisition, recording persistence,
status, display-source draining, and metrics continue. Resume requests the
newest window rather than replaying a frame backlog. Display replacements are
reported separately from source gaps and persistence loss.

Timed-workflow live metrics use a one-second conditioned rolling window and a 500 ms target
cadence. RMS, absolute peak, peak-to-peak, crest factor, and clipping fraction
are calculated through M06 and carry units, signal stage, and effective recipe
bandwidth.

In the timed workflow, **Stop** requests orderly stop. The engine accepts a safe final chunk, the
recorder drains, source artifacts finalize, and DAQ cleanup completes before the
run becomes terminal. The finalized session then opens automatically in the
same disk-backed review used by the browser. When persisted raw samples are
available, Review immediately plots all recorded channels over the complete run
using the bounded M07 display decimation path. Completed, stopped, and recoverable
partial sessions use the same behavior; a zero-sample session retains its summary
and diagnostics without attempting an empty plot.

### Open acquisition

Choose **Open monitoring and recording**. **Start monitoring** starts one
continuous DAQ task and retains every enabled channel in the bounded ten-second
raw-voltage ring. The default viewport remains five seconds, and normal
channel-visibility and raw-voltage scaling controls apply. The readings are raw
volts, not calibrated or certified measurements.

**Record** begins a new synchronized multichannel session at the next complete source chunk.
There is no pre-roll. **Stop recording** finalizes that segment as
`stopped/manual_stop` while the monitor and live waveform continue. Repeat the
toggle to create independent session directories. An immediate stop preserves a
zero-sample session. The page remains live after finalization, refreshes the
catalog, and reports monitor acquired count, segment persisted count, recording
state, last saved path, display replacements, and storage faults separately.

A segment storage fault is visible and ends that recording, but the source stream
continues. A DAQ fault ends monitoring and faults the active segment. **End
monitoring** or an orderly GUI close first finalizes any active segment and then
releases the hardware. Acquisition mode and channel scope are locked while
monitoring. Open mode never displays conditioned plots or live metrics.

Changing acquisition settings or DAQ-facing channel fields schedules a serialized
background hardware recheck; invalid settings remain blocked until corrected.
**Refresh hardware** remains available, and launch performs fresh validation.

## Sessions and review

Choose a root and refresh to inspect immediate session candidates. Valid entries
sort newest first; corrupt or unsupported entries remain visible without
blocking other sessions. Select **Review selected** for full source verification,
bounded structured events, diagnostics, and export-manifest history.

The review page uses draggable tiles for the session browser, plot workspace,
and event/artifact diagnostics, with the plot receiving most of the initial
height. Subtle borders identify the resizable tile boundaries. The browser
details, review channel list/canvas, and event/artifact panes
also resize horizontally.

Each of the eight review panels has title-bar **Minimize** and **Maximize**
controls: Sessions, Session details, Channels, Session plot, FFT / PSD / Extrema,
Structured events, Detected events, and Export history. Minimize frees the panel's
space and adds a named **Restore** button below the workspace. Scroll that bar
horizontally if its buttons do not fit. Maximize fills the workspace with one panel;
the page's analysis, detection, export, progress, and status controls stay available.
The FFT, PSD, and Extrema tabs remain together.

Use the maximized panel's **Restore** control or **Escape** to return to the
previous layout. Other minimized panels stay minimized. Minimizing a maximized
panel or restoring another panel also exits maximized mode. **Reset layout**
reveals all panels and restores the default proportions. Layout choices survive
page and recorded-session changes, but reset on the next application launch.
These controls preserve plot zoom, cursor selection, and results; background
analysis and export continue while panels are hidden.

Review always reopens the session through M05. It never uses the last live frame,
rolling ring, widget metrics, or unsaved GUI state. Select channels and an
optional relative-time range; blank bounds mean the verified extent. Ranges are
resolved to deterministic half-open sample indices shared by all channels.
Sessions opened manually from the browser wait for **Plot range** so the current
selection remains an explicit operator request.

Choose one explicit Raw, Calibrated, or Conditioned signal stage. The time waveform
stays visible while FFT, PSD, and Extrema appear in analysis tabs. Drag the shaded
start/end region or enter relative seconds directly; both controls resolve to the
same half-open sample range. Changing the range, stage, or checked channels marks
prior analysis as stale. Choose **Analyze selection** to refresh the products in
independent background jobs, so an FFT size error does not remove valid extrema.

**Detect events** scans the complete verified prefix independently for every checked
channel. The MAD multiplier, pre-padding, post-padding, and same-channel merge gap
are editable and default to 6, 0.05 s, 0.05 s, and 0.02 s. The detected-waveform
table is distinct from the persisted DAQ event log. Selecting a row moves the shared
cursors to that channel's padded suggestion; analysis still applies the synchronized
range to all checked channels and starts only when **Analyze selection** is chosen.
Suggestions and cursor positions are not saved when the application closes.

Raw, calibrated, causal conditioned, FFT-amplitude, and PSD views use M06/M07.
The plot metadata reports resolved indices, rate, units, recipe, and spectral
window/overlap/normalization details when applicable. Only the established
recipe retained by the session is exposed; there is no recipe editor or
invented zero-phase mode.
Checked raw-only ai3 is always included in FFT/PSD as **Raw voltage**, even when
X/Y/Z use calibrated or conditioned inputs. FFT amplitude uses V and PSD uses
V^2/Hz, on separate axes from acceleration units. Metadata records each channel's
actual input stage; a mixed result reports `input_stage=mixed`. Time plots,
extrema, and detection retain their stage eligibility, with an explanation when
raw-only channels are excluded. Select Raw to inspect those channels in time.

Raw-diagnostic sessions and saved open-acquisition recordings support raw-voltage
FFT/PSD and their CSV/PNG/SVG exports. The stage stays fixed to Raw;
calibrated/conditioned processing remains unavailable. Spectra require the recorded
recipe provenance, and do not qualify the hardware or supply a charge calibration.

**Export CSV**, **Export PNG**, and **Export SVG** submit DAQ-M07 jobs away from
the Qt thread. Progress and cancellation are visible, collisions are rejected,
and the refreshed artifact history reports completed, cancelled, or faulted
manifests. Sidecars retain source session, channels, exact range, calibration,
recipe, software, spectral settings, and checksums.

**Export TDMS** is a separate interoperability action. It exports every channel
over the entire fully verified raw extent, without calibration, filtering, or a
range selector. It uses the same background progress/cancellation mechanism,
rejects collisions, and writes a provenance sidecar. It is disabled when the
optional TDMS dependency is unavailable.

## Faults and shutdown

Acquisition or storage fault dialogs appear after recovery/finalization. They
show the typed code, acquired/persisted counts, retained raw/manifest
availability, and copyable technical details. Actions open review/folder or
focus diagnostics; no action retries a reserved task. Display degradation and
stale metrics remain distinct non-storage warnings.

Closing while acquisition is running or stopping offers **Stop and close** or
**Cancel**. Stop-and-close waits asynchronously for recorder drain,
finalization, and cleanup before closing. Normal shutdown cancels qualification
and export work, invalidates queued results, waits for workers, closes services,
and flushes QSettings. A timed-out shutdown is rejected rather than abandoning
a worker.
