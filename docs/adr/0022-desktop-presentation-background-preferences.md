# ADR-0022: Desktop Presentation, Background Work, and Preferences

Status: Accepted

## Context

DAQ-M09 must expose configuration, qualification, readiness, and stored-session
workflows without moving their policy into Qt widgets. Discovery, qualification,
destination checks, and catalog scans may block. The desktop also needs small,
machine-local preferences that are not scientific session data.

## Decision

`labdaq.bootstrap` remains the sole production composition root and lazily
constructs the desktop. It supplies a Qt-free `DesktopPresenter` with application
services, ports, immutable presentation records, and adapter-neutral results.
Widgets render those results and translate user events into presenter calls; they
do not parse configuration JSON, manifests, or TDMS and do not determine hardware
compatibility or start eligibility.

The public M09 boundary adds configuration-workspace, destination-preview,
readiness/launch-intent, session-catalog, qualification-progress, operator-message,
and versioned preference records. A successful M09 start action emits an
`AcquisitionLaunchIntent`; it does not start a task or create an experiment
session.

Potentially blocking desktop commands use a keyed Qt thread-pool runner. Each
submission receives a monotonically increasing generation for its key. Only the
current generation can update widgets, and superseded results are discarded.
Qualification cancellation remains an application-service operation. Shutdown
rejects new work, invalidates generations, requests cancellation, clears queued
work, waits for active workers, closes adapters, then synchronizes preferences.

`UserPreferencePort` persists schema-version 1 window geometry and at most eight
recent destination and browser roots. The production adapter uses `QSettings`.
Preferences contain no experiment configuration, qualification evidence, or raw
scientific data.

## Consequences

- Headless presenters and M09 application policy remain testable without Qt.
- Qt workers are execution mechanisms and never become DAQ or domain owners.
- Stale background replies cannot overwrite a newer operator choice.
- NI selection remains explicit; an unavailable NI package is displayed as a
  structured disabled state and never falls back to simulation.
- QSettings is intentionally local convenience state, not a source record.
- M10 can consume the typed launch intent without retrofitting task ownership into
  M09 widgets.
