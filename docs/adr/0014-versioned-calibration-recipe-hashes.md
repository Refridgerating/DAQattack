# ADR-0014: Versioned Calibration, Recipes, and Canonical Hashes

Status: Accepted

## Context

Raw voltage is not scientifically interpretable without channel-specific
calibration and an exact processing recipe. Whitespace, JSON member ordering,
runtime timestamps, and implicit nominal values must not change or weaken a
derived result's identity.

## Decision

Resolved calibration and processing provenance use version-1 immutable records.
Experiment schema version 1 gains optional calibration offset, span, frequency,
and uncertainty fields so accepted older documents remain readable. Scientific
processing nevertheless requires explicit offset and valid input span.

`baseline-v1` is the only active recipe. `baseline-v1-draft` remains readable as
the M05 raw-only snapshot. Canonical hashes are SHA-256 over UTF-8 JSON with
sorted object keys, compact separators, finite numbers, and no volatile
processing time. A calibration hash covers its normalized conversion and source
identity; a provenance hash covers source digest/range, calibrations, recipe,
algorithms, versions, units, qualities, and deterministic notices.

Manufacturer nominal calibration is accepted only when the stored hardware
profile exactly permits the sensor model, sensitivity unit/value, and channel
role. It remains `nominal`, never `traceable`.

## Consequences

- Dictionary insertion order and formatting cannot change scientific identity.
- Old M01/M05 documents still load, but may be rejected for M06 processing.
- Real certificate offsets/sensitivities and charge-chain data remain explicit
  external dependencies rather than software defaults.
- Changing a qualified stage parameter changes the recipe and provenance hashes.
