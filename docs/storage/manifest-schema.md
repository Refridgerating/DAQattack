# Session Manifest Schemas

`manifest.json` is UTF-8 JSON validated against its packaged Draft 2020-12
schema. New writers emit schema version 2. Readers continue to parse schema
version 1 unchanged and reject unknown versions instead of inferring meaning.

Schema v2 replaces the TDMS-specific top-level `tdms_layout_version` with
`raw_container`. That section fixes format `hdf5`, filename `raw.h5`, HDF5
layout version, `/raw/samples`, `float64-le`, `[channel, sample_index]`, chunk
geometry, no compression, Fletcher32, and SWMR. Schema-v1 manifests retain
`tdms_layout_version` and imply `raw.tdms`.

Top-level sections are:

| Section | Contents |
|---|---|
| `session` | UUID, names, operator/sample context, creation paths, UTC times, requested and acquired durations, state, reason, and path warnings |
| `software` | LabDAQ/Python versions, operating system, host, and implementation |
| `hardware` | Complete profile, discovery/driver report, and effective DAQ configuration snapshots |
| `acquisition` | Requested/actual rate, mode, duration, chunk size, ordered logical/physical channels, ranges, trigger, float64 dtype, and raw-voltage classification |
| `channels` | Ordered physical mapping, raw and intended engineering units, sensor record, calibration record, and conditioning-chain snapshot |
| `processing_recipe` | Immutable recipe reference/version and hash; explicit raw-path `applies_calibration: false`/V-to-V metadata plus derived-path calibration/output-unit intent |
| `counts` | Acquired, recording-accepted, persisted/common, index extent, gaps/overlaps, display drops, events, warnings, and faults |
| `checkpoint` | Sequence, UTC timestamp, and acknowledged persisted count |
| `integrity` | Digest version/value/extent, terminal file checksums, and finalization/verification times |
| `fault` / `recovery` | Typed terminal fault or explicit recovery provenance, otherwise null |
| `raw_container` (v2) | Format-neutral raw filename, layout, dataset, dtype, axes, chunks, checksum, compression, and SWMR settings |
| `open_acquisition` (optional v2) | Monitor run identity/start, full configured task channel order, authoritative recorded raw-channel order and units, legacy primary channel, half-open source interval, record command times, local-to-source index origin, and rolling-buffer policy |

An open-acquisition recording remains a schema-v2 raw source session with
`session_type: raw-diagnostic` and the ordered channels named by
`recorded_channel_ids`; `recorded_channel_units` aligns one-for-one. Local sample
zero maps to `source_start_sample_index`; `source_stop_sample_index` is exclusive
and the local-to-source mapping has unit scale. Historical single-channel M10A
manifests may omit both additive arrays and use `selected_channel_id` as their
scope. The rolling ring is never a source artifact, and the manifest makes no
calibrated or conditioned measurement claim. Ordinary schema-v2 and legacy
schema-v1 sessions omit this optional section and retain their existing
interpretation.

New M06 sessions snapshot the active bundled `baseline-v1` resource, whose
public identity is recipe `baseline`, version `v1`. The manifest distinguishes
the immutable raw path (no calibration, V output) from the recipe's intended
derived engineering units. The recipe JSON is copied unchanged to
`processing-recipe.json` and processing never rewrites it.

The bundled `baseline-v1-draft` resource remains readable for M05 compatibility;
it has recipe `baseline`, version `v1-draft`, and no stages. Sessions containing
that historical snapshot remain raw source records but scientific processing
uses the separately resolved, qualified `baseline-v1` recipe.

Creation paths are provenance. Moving the complete directory produces
`DAQ.STORAGE.SESSION_RELOCATED` as an integrity warning, not corruption.
