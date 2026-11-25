# DAQ-M07: Plotting and Export Services

Status: Complete; accepted locally on Windows  
Roadmap milestone: DAQ-M07  
Depends on: DAQ-M00 through DAQ-M06  
Primary layer: application services, domain records, ports, and file or rendering adapters  
Primary interfaces: session replay, processing pipeline, plot-data service, export service, CLI  
Physical hardware required for completion: no  

## 1. Objective

Implement headless plotting and export services that transform an authoritative recorded session into reproducible plot data, static figures, CSV files, and machine-readable export provenance.

DAQ-M07 shall support raw, calibrated, filtered, FFT, and PSD views without depending on PySide6, PyQtGraph, NI-DAQmx, or active hardware. It shall preserve sample alignment, channel identity, engineering units, calibration provenance, processing-recipe identity, and source-session identity through every derived artifact.

The milestone is complete when the same services can be called from tests and the CLI, large sessions are handled without loading the complete recording into memory, plot decimation preserves visible extrema, and every export can be traced back to its source samples and processing recipe.

## 2. Architectural question

How can the application generate responsive plot data and shareable analysis products while preserving the following invariants?

1. The recorded session remains the authoritative source record.
2. Plotting and export never alter raw samples.
3. Sample index remains the authoritative time coordinate.
4. Processing is delegated to the DAQ-M06 pipeline rather than reimplemented by exporters.
5. Display-oriented decimation never changes the data used for filtering, metrics, FFT, PSD, or CSV export unless the user explicitly requests a reduced-data export product.
6. Qt widgets and GUI state remain outside the plotting and export services.
7. File output is atomic, collision-safe, and traceable to a source session and recipe version.

## 3. Source requirements

The following roadmap requirements are authoritative for this milestone:

- Backend plot-data service for raw, calibrated, filtered, and spectral views
- Viewport-aware decimation that preserves peaks and does not alter recorded data
- Static figure generation for time histories, overlays, FFT, and PSD
- CSV export with metadata sidecar
- Figure export to PNG and a vector format
- Selected-time-range and selected-channel export
- Export manifest linking each artifact to its source session and baseline-recipe version
- Headless operation without Qt
- Exact agreement between exported CSV data and the source session
- Bounded-memory processing and export for large sessions
- Calibration and baseline-recipe provenance on conditioned exports

The existing `ReadAllChannels.py` is legacy reference material only. Its direct acquisition, text-file writing, time-vector construction, and plotting in one script shall not define the new architecture. Importing its four text files is outside DAQ-M07.

## 4. Scope

### 4.1 In scope

- Plot request and plot result domain records
- Export request, job, artifact, and manifest records
- Time-range and channel-selection validation
- Raw-voltage time-history plot data
- Calibrated engineering-unit time-history plot data
- Baseline-recipe conditioned time-history plot data
- FFT amplitude plot data produced through DAQ-M06 services
- PSD plot data produced through DAQ-M06 services
- Peak-preserving viewport decimation for time-domain display data
- Safe reduction of already computed spectral display data when needed
- Static time-history, overlay, FFT, and PSD figures
- PNG and SVG figure output
- CSV output with JSON metadata sidecars
- Export manifests and checksums
- Chunked source reads and bounded working memory
- Atomic output and overwrite prevention
- CLI access for plot-data inspection and exports
- Unit, contract, integration, and end-to-end simulator tests
- Architecture, schema, user, and developer documentation

### 4.2 Explicitly out of scope

- PySide6 widgets or screens
- PyQtGraph live widgets
- Live acquisition display orchestration, which belongs to DAQ-M10
- NI device discovery or hardware acquisition
- New calibration or filtering algorithms, which belong to DAQ-M06
- User-defined processing recipe libraries or a recipe editor
- Modification of the raw container or source manifest
- MATLAB, database, or cloud export
- Coherence, transfer functions, order tracking, modal analysis, or experiment-specific plugins
- Legacy four-text-file import
- Automatic report generation beyond figures, CSV data, sidecars, and export manifests
- Silent interpolation between channels with incompatible sample grids

## 5. Required domain concepts

Implement immutable, serializable records equivalent to the following concepts. Names may follow repository conventions, but the information and invariants are required.

### 5.1 Plot representation

`PlotRepresentation`

- `RAW_TIME`
- `CALIBRATED_TIME`
- `FILTERED_TIME`
- `FFT_AMPLITUDE`
- `PSD`

Do not create representation values for future analyses.

### 5.2 Sample range

`SampleRange`

- `start_index`, inclusive
- `stop_index`, exclusive
- resolved source sample rate
- optional user-entered start and stop seconds retained as request provenance

All internal range operations shall use the half-open interval `[start_index, stop_index)`.

A time range entered in seconds shall be resolved once against the authoritative sample rate. Floating-point time shall not be repeatedly accumulated to locate samples.

### 5.3 Channel selection

`ChannelSelection`

- ordered logical channel identifiers
- optional requested display names
- validation that each channel exists in the source session
- preservation of source channel order unless the request explicitly provides another order

### 5.4 Viewport

`PlotViewport`

- optional start and stop sample index
- target display width in pixels or maximum output point count
- optional target height only if required by the chosen renderer
- decimation mode, limited to the approved DAQ-M07 algorithm

Viewport width is a rendering constraint, not a processing or acquisition setting.

### 5.5 Plot request

`PlotRequest`

- source session identifier or path
- representation
- channel selection
- sample range
- baseline-recipe identifier when required
- spectral configuration reference when required
- viewport or maximum point count
- unit and label preferences that do not alter numeric values

### 5.6 Plot series

`PlotSeries`

- logical channel identifier
- physical-channel provenance
- sensor identifier when present
- x values
- y values
- x units
- y units
- series label
- representation
- source sample range
- source sample rate
- output point count
- decimation status and algorithm version
- calibration provenance when applicable
- processing-recipe provenance when applicable

### 5.7 Plot bundle

`PlotBundle`

- source session UUID
- representation
- ordered series
- shared axis metadata where applicable
- request summary
- warnings
- processing provenance
- source data checksum or manifest checksum when available

### 5.8 Export format and product

`ExportFormat`

- `CSV`
- `PNG`
- `SVG`

`ExportProduct`

- raw time-domain data
- calibrated time-domain data
- filtered time-domain data
- FFT amplitude data
- PSD data
- time-history figure
- overlay figure
- FFT figure
- PSD figure

### 5.9 Export request

`ExportRequest`

- source session identifier or path
- export product
- channel selection
- sample range
- recipe identifier when required
- destination directory
- requested base name
- output format
- figure options from a constrained versioned record
- explicit collision policy, which shall be reject for DAQ-M07

### 5.10 Export job state

`ExportJobState`

- `PENDING`
- `RUNNING`
- `COMPLETED`
- `CANCELLED`
- `FAULTED`

An export job shall expose progress in source samples or completed artifacts. Cancellation shall leave no completed-looking partial file.

### 5.11 Export artifact

`ExportArtifact`

- artifact UUID
- export job UUID
- source session UUID
- artifact type and format
- relative output path
- byte count
- SHA-256 checksum
- channel identifiers
- sample range
- representation
- units
- recipe and calibration provenance
- creation timestamp
- completion state

### 5.12 Export manifest

`ExportManifest`

- schema version
- export job UUID
- source session UUID
- source manifest checksum when available
- application version
- export service version
- request summary
- artifact records
- warnings and faults
- creation and completion timestamps
- final job state

## 6. Required application services and ports

### 6.1 Plot-data service

Provide a headless application service equivalent to:

```python
build_plot_data(request: PlotRequest) -> PlotBundle
```

Responsibilities:

- validate the request
- resolve the source session through the DAQ-M05 parser or replay API
- resolve sample range from authoritative sample indices
- obtain raw or DAQ-M06 processed data
- apply display decimation only after the required numeric product exists
- attach units, identity, and provenance
- return plain domain or NumPy-backed data structures with no Qt types

The service shall not:

- import PySide6 or PyQtGraph
- open NI tasks
- read files through ad hoc format-specific code when a DAQ-M05 source port already exists
- implement calibration or filtering formulas owned by DAQ-M06
- save figures or CSV files directly

### 6.2 Decimation service

Provide a deterministic service equivalent to:

```python
decimate_time_series(
    sample_indices,
    values,
    target_points,
    algorithm_version,
) -> DecimatedSeries
```

The approved DAQ-M07 baseline algorithm shall be a peak-preserving min-max bucket method or an equivalent deterministic envelope method.

Required behavior:

1. Preserve the first and last selected samples.
2. Preserve the minimum and maximum value in every populated bucket.
3. Preserve monotonic sample-index order.
4. Never create values not present in the selected source data.
5. Return the original selected data when it already fits within the target point budget.
6. Handle NaN or missing-data markers according to existing DAQ-M05 and DAQ-M06 validity rules. Do not silently bridge gaps.
7. Use shared sample-index bucket boundaries for synchronized channel overlays so channel alignment remains visually comparable.
8. Record the decimation algorithm name and version in plot metadata.

Do not use simple stride sampling as the baseline algorithm because it can remove narrow peaks and impulses.

### 6.3 Static figure renderer port

Define a rendering port that consumes `PlotBundle` and figure options and returns or writes a static figure artifact.

A Matplotlib adapter is the recommended DAQ-M07 implementation because it supports headless PNG and SVG output. This is an implementation recommendation, not permission for application services to depend directly on Matplotlib objects.

Required figure types:

- single-channel time history
- synchronized multi-channel overlay
- FFT amplitude
- PSD

Required figure content:

- descriptive title
- x-axis and y-axis labels with units
- channel legend for overlays
- source session identifier in metadata or caption
- representation name
- recipe version for conditioned figures
- selected time range
- readable clipping of labels without truncating data

Figure rendering shall work in a headless test process.

### 6.4 CSV export service

Provide an application service equivalent to:

```python
export_csv(request: ExportRequest) -> ExportArtifact
```

The service shall stream rows or bounded chunks to a temporary file, flush and close it, calculate its checksum, then atomically move it to the final destination.

### 6.5 Figure export service

Provide an application service equivalent to:

```python
export_figure(request: ExportRequest) -> ExportArtifact
```

The figure service shall call the plot-data service and renderer port. It shall not duplicate source loading, processing, or range-selection logic.

### 6.6 Export coordinator

Provide a coordinator equivalent to:

```python
run_export_job(requests: Sequence[ExportRequest]) -> ExportManifest
```

The coordinator shall:

- assign an export job UUID
- validate all destinations before starting
- reject existing final paths
- write artifacts through temporary files
- record completed artifacts as they are finalized
- write the export manifest atomically
- report a faulted manifest when any requested artifact fails
- preserve already completed artifacts but never label the job fully completed after a partial failure

## 7. Time and sample-index rules

1. Sample index is authoritative.
2. Relative time for a source sample is derived as `sample_index / sample_rate` or through an equivalent exact vectorized formulation.
3. Do not use `linspace` with an inclusive endpoint for sample time because it can create an extra timestamp or alter the interval.
4. A selected time range shall resolve to one deterministic sample range.
5. CSV rows shall use the exact resolved sample indices.
6. The metadata sidecar shall record both the requested time range and resolved sample range.
7. Absolute wall-clock timestamps may be included as derived metadata, but they shall not replace sample indices.

## 8. Plot-data behavior by representation

### 8.1 Raw time-domain

- Source values are the immutable recorded channel values.
- Units are the source raw units, normally volts.
- No calibration, bias removal, filtering, resampling, or interpolation is applied.
- Decimation may be applied only to the returned display series.

### 8.2 Calibrated time-domain

- Values come from DAQ-M06 calibration primitives.
- Per-channel units and calibration provenance are required.
- Channels with different engineering units may be returned in one bundle but shall not be placed on one unlabeled shared y-axis by the static renderer.

### 8.3 Filtered time-domain

- Values come from the selected, versioned DAQ-M06 recipe.
- The request shall identify `baseline-v1` or another recipe already supported by DAQ-M06.
- Live-causal and offline-only provenance shall remain distinguishable.
- DAQ-M07 shall not silently substitute one recipe for another.

### 8.4 FFT amplitude

- FFT input shall be the requested raw, calibrated, or filtered source representation as defined by the DAQ-M06 interface.
- Display decimation shall not occur before FFT calculation.
- Frequency axis, amplitude normalization, window, coherent-gain correction, selected sample count, and units shall be preserved from DAQ-M06 provenance.
- DC and Nyquist handling shall follow DAQ-M06 definitions.

### 8.5 PSD

- PSD input and parameters shall be delegated to DAQ-M06.
- Display reduction, when needed, shall operate on the completed PSD product.
- Frequency units and density units shall be explicit.
- Log-axis rendering may be a figure option but shall not alter exported numeric values.

## 9. Decimation and large-session requirements

### 9.1 Time-domain display decimation

For a viewport of width `W` pixels, the service should normally return no more than approximately `2W + 2` points per channel when min-max buckets are used. The exact bound shall be documented and tested.

Decimation shall operate over the selected range only. It shall not require the full session to be loaded when the source range can be streamed in chunks.

For synchronized overlays, bucket boundaries shall be defined by source sample index and shared across selected channels.

### 9.2 Numeric export

CSV export shall not use display-decimated data by default. It shall export every selected source or processed sample.

A future reduced-data export mode may be added only through an explicit product type and schema version. It is not part of DAQ-M07.

### 9.3 Spectral products

Full FFT requests can require memory proportional to the selected sample count. DAQ-M07 shall therefore do one of the following, consistent with DAQ-M06:

- enforce a documented maximum FFT input length and reject larger selections with an actionable error, or
- use an existing bounded-memory spectral implementation that produces the same declared product

PSD should use the bounded segment behavior defined by DAQ-M06 when available.

DAQ-M07 shall not silently truncate a selected range to fit memory.

### 9.4 Testable bounded-memory contract

Do not rely only on process RSS measurements. Add test doubles or instrumentation that prove:

- the source is consumed through bounded chunks
- CSV writing does not concatenate the complete session
- plot decimation maintains a declared maximum working buffer
- export functions do not call an unrestricted `read_all()` path for large sessions

## 10. CSV schemas

### 10.1 Time-domain CSV

Use a wide synchronized schema when selected channels share the same sample grid.

Required leading columns:

- `sample_index`
- `relative_time_s`

Then one numeric column per logical channel, in requested channel order.

Column headers shall be stable identifiers. Human-readable labels and units belong in the sidecar. An optional second header row is not permitted because it complicates machine parsing.

If selected channels do not share an identical grid, do not silently interpolate. Write separate CSV artifacts grouped by compatible grid and link them through one export manifest.

### 10.2 Spectral CSV

Required leading column:

- `frequency_hz`

Then one numeric column per compatible channel product.

FFT amplitude and PSD shall use distinct product names and sidecar schemas. Units, normalization, window, overlap, segment length, and recipe provenance belong in the sidecar.

### 10.3 Numeric formatting

- Use a locale-independent decimal point.
- Use sufficient precision to round-trip IEEE-754 values within the declared tolerance.
- Do not write thousands separators.
- Encode missing values using one documented token consistent with project parsing rules.
- Use UTF-8.
- Use deterministic channel ordering.

## 11. Metadata sidecar

Each CSV and figure shall have a JSON sidecar or an equivalent artifact record that contains at least:

- sidecar schema version
- artifact UUID
- export job UUID
- source session UUID
- source manifest path and checksum when available
- source application and schema versions
- export application version
- representation and export product
- requested time range
- resolved sample range
- source and output sample counts
- selected channels in order
- logical and physical channel identities
- sensor identity when present
- x and y units
- calibration coefficients, certificate source, and fallback status when applicable
- processing-recipe identifier and checksum when applicable
- spectral settings when applicable
- decimation status and algorithm when applicable
- output filename, byte count, and checksum
- creation timestamp and timezone
- warnings

The sidecar shall contain enough information to interpret the numeric or visual artifact without opening the GUI.

## 12. Output paths and atomicity

1. Default output shall be under the source session `exports/` or `plots/` directory according to product type.
2. A user-selected destination may be accepted if validated by existing path rules.
3. Base names shall be sanitized through the DAQ-M01 or DAQ-M05 filename policy.
4. Existing final paths shall cause an explicit collision error.
5. Each artifact shall first be written to a unique temporary path in the destination filesystem.
6. Finalization shall use an atomic replace or rename operation that does not overwrite an existing final artifact.
7. Temporary files from cancellation or failure shall be removed when safe.
8. An incomplete artifact shall never be entered in the export manifest as completed.

## 13. Structured events and errors

Use the existing application error hierarchy. Add typed codes equivalent to the following where the repository does not already provide them:

- `PLOT_REQUEST_INVALID`
- `PLOT_CHANNEL_NOT_FOUND`
- `PLOT_RANGE_EMPTY`
- `PLOT_RANGE_OUT_OF_BOUNDS`
- `PLOT_RECIPE_REQUIRED`
- `PLOT_RECIPE_NOT_FOUND`
- `PLOT_SOURCE_INVALID`
- `DECIMATION_FAILED`
- `SPECTRAL_SELECTION_TOO_LARGE`
- `EXPORT_REQUEST_INVALID`
- `EXPORT_DESTINATION_INVALID`
- `EXPORT_PATH_COLLISION`
- `EXPORT_WRITE_FAILED`
- `EXPORT_RENDER_FAILED`
- `EXPORT_CHECKSUM_FAILED`
- `EXPORT_CANCELLED`
- `EXPORT_PARTIAL_FAILURE`

Structured progress events should include:

- export job UUID
- artifact UUID when available
- state
- processed sample count
- total selected sample count when known
- completed artifact count
- total artifact count
- current operation
- timestamp

Error messages shall identify the failed session, representation, channel, range, or destination when that information is safe and available.

## 14. CLI requirements

Expose headless commands consistent with the repository's existing CLI conventions. Equivalent behavior is required even if exact command names differ.

Examples:

```text
labdaq plot-data SESSION --view raw --channels accel_x,accel_y --start 0 --stop 10 --max-points 2000

labdaq export csv SESSION --view calibrated --channels accel_x,accel_y,accel_z --start 0 --stop 30 --output SESSION/exports

labdaq export csv SESSION --view psd --recipe baseline-v1 --channels accel_x,accel_y,accel_z

labdaq export figure SESSION --kind overlay --view filtered --recipe baseline-v1 --format png

labdaq export figure SESSION --kind psd --recipe baseline-v1 --format svg
```

CLI output shall report:

- export job UUID
- artifact paths
- artifact checksums
- selected channels and sample range
- representation and recipe version
- warnings
- final state

The CLI shall return a nonzero exit code for validation, source, rendering, write, or partial-failure errors.

## 15. Required tests

### 15.1 Domain and validation tests

- valid plot and export requests are accepted
- empty channel selections are rejected
- unknown and duplicate channel identifiers are rejected
- invalid and reversed ranges are rejected
- out-of-bounds ranges are rejected or clipped only if the existing project policy explicitly permits clipping
- conditioned requests without a recipe are rejected
- raw requests do not require a recipe
- unsafe filenames and existing output paths are rejected
- serialization round-trips preserve all request and manifest fields

### 15.2 Sample-index and time tests

- sample index zero maps to time zero
- sample index `n` maps to `n / sample_rate`
- a range of `N` samples produces exactly `N` times
- no inclusive-endpoint off-by-one error exists
- conversion from seconds to sample range is deterministic at boundary values
- exported sample indices and relative times agree with the source rate

### 15.3 Decimation unit tests

- no decimation occurs below the point budget
- first and last samples are preserved
- one-sample impulses are preserved within populated buckets
- positive and negative extrema are preserved
- monotonic increasing and decreasing signals remain correctly ordered
- flat signals remain flat
- short ranges and one-sample ranges behave correctly
- NaN or gap markers are not silently bridged
- synchronized channels use identical bucket boundaries
- result size respects the documented bound
- repeated execution is deterministic
- simple stride decimation would fail at least one reference case that the approved method passes

### 15.4 Plot-data service tests

- raw plot values exactly equal selected source values before display decimation
- calibrated values equal DAQ-M06 reference outputs
- filtered values equal DAQ-M06 reference outputs
- FFT and PSD values equal DAQ-M06 reference outputs
- source and processed units are correct
- provenance fields are complete
- no Qt or NI dependency is imported by the application service
- plot requests do not write files or mutate the session

### 15.5 CSV tests

- exported raw values round-trip to the selected source values within the documented numeric tolerance
- calibrated and filtered values match DAQ-M06 outputs
- channel order is stable
- sample indices and times are correct
- selected range boundaries are exact
- selected-channel export excludes unrequested channels
- spectral CSV frequency and values match DAQ-M06 outputs
- incompatible grids create separate artifacts rather than silent interpolation
- metadata sidecar contains required identity, unit, calibration, recipe, and range fields
- checksums match final file contents
- export is UTF-8 and locale independent

### 15.6 Figure tests

- PNG and SVG are generated in a headless process
- each required figure type renders successfully
- axis labels contain correct units
- overlays contain the expected channel labels
- conditioned figures identify the recipe version
- figure generation uses decimated display data when the selected range exceeds the point budget
- figure generation does not decimate before FFT or PSD calculation
- rendering failure becomes a typed export fault

Do not use brittle full-image pixel equality as the primary assertion. Prefer structural metadata, dimensions, labels, series counts, and a small number of stable renderer checks.

### 15.7 Bounded-memory tests

- a synthetic large session is consumed through bounded reads
- CSV export writes incrementally
- decimation working buffers remain under the declared bound
- no unrestricted full-session read is called
- oversized FFT requests fail explicitly when required by the selected implementation
- cancellation removes temporary files and reports `CANCELLED`

### 15.8 Integration tests

Create a deterministic DAQ-M02 session through the completed acquisition and recording path, then:

1. reopen it through DAQ-M05
2. process it through DAQ-M06
3. request raw, calibrated, filtered, FFT, and PSD plot bundles
4. export selected channels and a selected time range to CSV
5. export time-history, overlay, FFT, and PSD figures to PNG and SVG
6. reopen the CSV and sidecars
7. compare exported values and provenance to the source session and processing results
8. verify the source manifest and raw container are unchanged

### 15.9 Architecture tests

- domain and application modules do not import PySide6, PyQtGraph, Matplotlib, `nidaqmx`, or TDMS adapter internals unless the dependency belongs to the specific adapter under test
- the Matplotlib adapter implements a rendering port
- CSV writers consume application records or ports rather than GUI state
- plot and export services obtain source data through DAQ-M05 interfaces
- conditioning is invoked through DAQ-M06 interfaces

## 16. Required scenarios

Add deterministic scenario coverage for at least:

1. Four-channel raw time-history export
2. Three accelerometer channels calibrated to acceleration units plus one charge channel with different units
3. Narrow impulse that would be lost by stride decimation
4. Positive and negative transients in the same viewport bucket
5. Selected mid-session time range
6. Single selected channel
7. Multi-channel overlay with shared sample grid
8. Conditioned export using `baseline-v1`
9. FFT amplitude export of a known sine
10. PSD export of seeded noise plus a known tone
11. Large session exported through bounded chunks
12. Existing destination collision
13. Injected write failure after one artifact completes
14. Renderer failure
15. Export cancellation
16. Incomplete or invalid source session
17. Channel selection with incompatible grids, if DAQ-M06 can produce such a case

## 17. Acceptance criteria

DAQ-M07 is accepted only when all of the following are demonstrated.

1. Plot services are callable from CLI and tests without Qt.
2. Application and domain plotting modules do not import NI-DAQmx or GUI dependencies.
3. Raw plot data equals the selected source data before display decimation.
4. Calibrated, filtered, FFT, and PSD results equal DAQ-M06 outputs.
5. Selected time ranges resolve through sample indices with no off-by-one timestamp error.
6. Viewport decimation preserves first sample, last sample, per-bucket minimum, and per-bucket maximum.
7. Decimation does not alter recorded data, CSV numeric export, filtering input, FFT input, PSD input, or stored session content.
8. Static time-history, overlay, FFT, and PSD figures export successfully to PNG and SVG in a headless process.
9. CSV exports contain the exact selected channel data within the documented round-trip precision.
10. Every conditioned artifact records calibration provenance and baseline-recipe identifier or checksum.
11. Every artifact records source session UUID, selected channels, selected sample range, units, output checksum, and artifact type.
12. Large time-domain sessions are read and exported through bounded chunks without unrestricted full-session loading.
13. Oversized non-streamable spectral requests are either handled through an approved bounded algorithm or rejected explicitly before memory exhaustion.
14. Existing destination files are never silently overwritten.
15. Write, render, checksum, cancellation, and partial-job failures produce structured faults and leave no completed-looking partial artifact.
16. Source raw-container and manifest content remain unchanged after plotting and export.
17. Focused tests and the full existing test suite pass.
18. Documentation explains plot representations, range semantics, decimation behavior, CSV schemas, sidecars, export manifests, and CLI usage.

No physical hardware acceptance is required for DAQ-M07. A recorded physical session may be used as additional evidence, but simulator-backed end-to-end evidence is sufficient.

## 18. Required documentation

Create or update documentation equivalent to:

- `Milestones/DAQ-M07-plotting-and-export-services.md`
- plotting and export architecture documentation
- plot request and result record reference
- decimation algorithm and limitations
- CSV schema reference
- metadata sidecar schema reference
- export manifest schema reference
- CLI examples
- user workflow for exporting selected channels and time ranges
- developer guidance for adding a future renderer or exporter
- roadmap status only after acceptance evidence exists

Do not claim GUI completion or live-plot completion in DAQ-M07 documentation.

## 19. ADRs

Create ADRs if the repository does not already contain accepted decisions for:

1. Peak-preserving decimation and why stride sampling is rejected
2. Sample-index-based range semantics and half-open intervals
3. Static renderer boundary and selected rendering adapter
4. CSV layout, metadata sidecar, and incompatible-grid handling
5. Atomic export finalization and collision policy
6. Bounded-memory policy for large time-domain and spectral products

Do not create an ADR for a decision already governed by an existing accepted ADR. Update references instead.

## 20. Implementation order

Recommended internal sequence:

1. Inspect DAQ-M05 replay and DAQ-M06 processing contracts.
2. Add domain records and schemas.
3. Add request validation and sample-range resolution.
4. Implement deterministic peak-preserving decimation.
5. Implement the plot-data service.
6. Add the static renderer port and headless adapter.
7. Add streaming CSV export and sidecars.
8. Add the export coordinator and manifest.
9. Add CLI commands.
10. Add unit, architecture, bounded-memory, and integration tests.
11. Update documentation and ADRs.
12. Run focused tests and the full suite.

Do not begin GUI work from DAQ-M09 or DAQ-M10.

## 21. Assumptions

- DAQ-M05 provides a validated, chunked session data source or replay interface.
- DAQ-M06 provides deterministic calibration, filtering, FFT, and PSD services with explicit provenance.
- All four initial NI-9239 channels share one synchronized sample clock in the baseline experiment.
- Raw channel arrays use the repository standard `[channels, samples]` orientation when handled as blocks.
- The project has or will add a headless static plotting dependency behind an adapter.
- SHA-256 is acceptable for artifact integrity checks.
- PNG and SVG satisfy the roadmap requirement for raster and vector figure output.

If an assumption is not true in the repository, adapt the interface without weakening the milestone invariants and document the change.

## 22. Risks and required controls

### Risk: decimation hides transient peaks

Control: use deterministic min-max or equivalent peak-preserving buckets and prove impulse preservation with tests.

### Risk: display decimation contaminates scientific analysis

Control: apply decimation only after calibration, filtering, FFT, or PSD products are computed. Keep numeric exports undecimated by default.

### Risk: time vectors drift or contain an extra endpoint

Control: derive time from integer sample indices and use half-open sample ranges.

### Risk: large exports exhaust memory

Control: consume session chunks, stream CSV rows, bound decimation buffers, and reject unsupported oversized FFT requests explicitly.

### Risk: metadata and numeric data diverge

Control: construct artifact data and sidecar from one validated export request and final result record. Checksum only finalized files.

### Risk: partial outputs look complete

Control: write temporary files, finalize atomically, and record explicit job and artifact states.

### Risk: renderer details leak into application code

Control: define a renderer port and keep Matplotlib or another renderer inside an adapter.

### Risk: channels with different units are plotted misleadingly

Control: require explicit units per series and prevent one unlabeled shared axis for incompatible units.

## 23. Prohibited implementation shortcuts

Do not:

- plot directly from NI-DAQmx reads
- import Qt into plot or export application services
- write one ad hoc text file per channel
- construct time using inclusive `linspace` endpoints
- use stride-only decimation
- compute FFT or PSD from decimated display data
- copy calibration or filter equations into exporters
- load an entire large session merely to write CSV
- silently overwrite existing exports
- silently interpolate incompatible channel grids
- omit units or recipe provenance from conditioned artifacts
- mark a partial export job completed
- modify the source manifest or raw container during export

## 24. Completion evidence

The implementation report shall include:

- changed files
- architecture summary
- domain records and ports added
- decimation algorithm and tested point bound
- CSV and sidecar schema summary
- figure formats and renderer adapter
- CLI commands added
- focused test commands and results
- full test-suite command and result
- deterministic scenario results
- bounded-memory evidence
- example export manifest summary
- confirmation that source raw data and manifest remain unchanged
- assumptions and risks remaining
- any acceptance criterion not verified

## 25. Codex implementation prompt

> Implement DAQ-M07 only, using `DAQ-Software-Roadmap.md`, `Milestones/DAQ-M07-plotting-and-export-services.md`, the repository architecture documents, accepted ADRs, and completed DAQ-M00 through DAQ-M06 behavior as authoritative requirements.
>
> First inspect the existing repository, especially the DAQ-M05 session parser and replay interfaces, DAQ-M06 calibration and processing interfaces, domain records, ports, CLI conventions, tests, and architecture-boundary checks. Do not assume the proposed class or command names in the milestone document already exist. Reuse and extend established repository patterns.
>
> Implement headless plot-data services for raw, calibrated, filtered, FFT, and PSD representations. Implement deterministic viewport-aware peak-preserving decimation. Sample index must remain the authoritative time coordinate. Decimation must never alter the source session, numeric CSV export, calibration or filter input, FFT input, or PSD input.
>
> Implement static time-history, overlay, FFT, and PSD figure generation behind a renderer port. Support PNG and SVG through a headless adapter. Implement selected-channel and selected-time-range CSV export with JSON metadata sidecars, SHA-256 checksums, atomic finalization, collision rejection, and an export manifest that links every artifact to the source session and the applicable calibration and `baseline-v1` processing provenance.
>
> Large time-domain sessions must be read and exported through bounded chunks. Do not use an unrestricted full-session read for CSV or time-domain decimation. For FFT products that cannot be computed within the documented memory bound, reject the request explicitly before memory exhaustion unless DAQ-M06 already provides an approved bounded implementation. Do not silently truncate data.
>
> Preserve all architectural boundaries. Application and domain code must not import PySide6, PyQtGraph, NI-DAQmx, Matplotlib, or TDMS adapter internals. Rendering-library code belongs in an adapter. Source data must be accessed through DAQ-M05 interfaces. Calibration, filtering, FFT, and PSD must be invoked through DAQ-M06 interfaces. Raw TDMS and the source manifest must remain unchanged.
>
> Add domain records, schemas, structured errors and events, CLI commands, unit tests, architecture tests, bounded-memory tests, integration tests, deterministic scenarios, documentation, and any required ADRs. Do not implement DAQ-M09 GUI screens, DAQ-M10 live plotting, user-defined recipe libraries, legacy text-file import, or deferred analysis and export formats.
>
> Demonstrate exact sample-index and relative-time behavior, CSV agreement with source and DAQ-M06 products, peak preservation under decimation, headless PNG and SVG output, collision safety, cancellation and partial-failure behavior, complete provenance, and unchanged raw source records.
>
> Run focused DAQ-M07 tests and the complete existing test suite. Stop after DAQ-M07. Report changed files, architecture decisions, records and ports added, decimation behavior, export schemas, CLI commands, test commands and results, scenario evidence, bounded-memory evidence, remaining assumptions and risks, and any acceptance criterion that could not be verified.
