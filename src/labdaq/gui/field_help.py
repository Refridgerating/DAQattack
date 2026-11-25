"""Central descriptive hover help for LabDAQ operator data fields."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QWidget

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_HELP_ID_PROPERTY = "labdaqFieldHelpId"
_NOTICE_PROPERTY = "labdaqFieldHelpNotice"
_TABLE_COLUMNS_ATTRIBUTE = "_labdaq_field_help_columns"


@dataclass(frozen=True)
class FieldHelp:
    """Plain-language explanation of one operator-facing data field."""

    what: str
    usage: str
    guidance: str | None = None

    def __post_init__(self) -> None:
        if not self.what.strip():
            raise ValueError("field help requires a description")
        if not self.usage.strip():
            raise ValueError("field help requires usage guidance")
        if self.guidance is not None and not self.guidance.strip():
            raise ValueError("field help guidance must be non-empty when supplied")

    def render(self) -> str:
        """Render compact native-tooltip text."""
        lines = (self.what.strip(), self.usage.strip())
        if self.guidance is None:
            return "\n".join(lines)
        return "\n".join((*lines, self.guidance.strip()))


def _help(what: str, usage: str, guidance: str | None = None) -> FieldHelp:
    return FieldHelp(what, usage, guidance)


FIELD_HELP: Mapping[str, FieldHelp] = MappingProxyType(
    {
        # Application shell and setup.
        "shell.readiness_status": _help(
            "Overall launch readiness for the current configuration and hardware evidence.",
            "LabDAQ uses this result to enable or block qualified and diagnostic acquisition.",
            "Current blockers and their remediation are appended below when present.",
        ),
        "setup.profile": _help(
            "The hardware profile that defines supported chassis, module, channels, and rates.",
            "LabDAQ uses it to validate discovery and acquisition settings before a run.",
        ),
        "setup.profile_identity": _help(
            "The identity and revision of the active hardware profile.",
            "It identifies the compatibility rules applied to the current setup.",
        ),
        "setup.discovery": _help(
            "The latest hardware discovery and compatibility result.",
            "LabDAQ requires current compatible hardware evidence before physical acquisition.",
        ),
        "experiment.name": _help(
            "A short name for this experiment or run.",
            "LabDAQ includes it in the session directory name and recorded metadata.",
        ),
        "experiment.operator": _help(
            "The person responsible for operating this acquisition.",
            "LabDAQ records it as session provenance and in diagnostic authorization evidence.",
        ),
        "experiment.sample_id": _help(
            "The laboratory identifier for the specimen or sample being measured.",
            "LabDAQ stores it with the session so results can be traced to the tested item.",
        ),
        "experiment.description": _help(
            "A concise description of the experiment and measurement purpose.",
            "LabDAQ preserves it in the session manifest for later interpretation.",
        ),
        "experiment.notes": _help(
            "Free-form operator notes about conditions or observations.",
            "LabDAQ records them as metadata; they do not alter acquisition or processing.",
        ),
        "acquisition.duration_s": _help(
            "The requested duration of a finite acquisition, in seconds.",
            "LabDAQ uses it to determine the planned sample count and automatic stop point.",
            "The finalized session reports the actual sample-derived duration separately.",
        ),
        "acquisition.chunk_size": _help(
            "The number of samples read per channel in each acquisition batch.",
            "It controls read cadence and buffering without changing the sample rate.",
            "A rate change resets this value to approximately a 100 ms batch.",
        ),
        "acquisition.sample_rate_hz": _help(
            "The requested samples per second for every synchronized channel.",
            "LabDAQ validates it against the active hardware profile and selects a compatible recipe.",
            "The driver-reported actual rate is retained separately when available.",
        ),
        "acquisition.read_interval": _help(
            "The derived time between acquisition reads.",
            "LabDAQ calculates it as samples per read per channel divided by sample rate.",
        ),
        "storage.parent_directory": _help(
            "The existing parent directory where new session directories will be created.",
            "LabDAQ validates writability and previews a collision-resistant session path here.",
            "Selecting or typing a parent does not create the previewed session.",
        ),
        "storage.destination_preview": _help(
            "The proposed directory for the next recorded session.",
            "LabDAQ uses this non-mutating preview to expose naming and destination problems early.",
        ),
        "processing.active_recipe": _help(
            "The immutable processing recipe selected for the current sample rate.",
            "LabDAQ records its ID, version, and hash so derived values can be reproduced.",
        ),
        "readiness.qualified": _help(
            "Findings that determine eligibility for qualified acquisition.",
            "LabDAQ lists every blocking field or evidence requirement that must be resolved.",
        ),
        "readiness.diagnostic": _help(
            "Findings that determine eligibility for raw-voltage diagnostic operation.",
            "This path permits voltage observation only and does not grant calibrated qualification.",
        ),
        # Channel summary and calibration fields.
        "channel.table": _help(
            "The stable ordered set of configured acquisition channels.",
            "LabDAQ uses this order for synchronized arrays, recording, replay, and display.",
        ),
        "channel.column.order": _help(
            "The channel's zero-based position in synchronized sample arrays.",
            "LabDAQ preserves this order across acquisition, storage, and replay.",
        ),
        "channel.column.enabled": _help(
            "Whether this configured channel participates in acquisition.",
            "Only enabled channels are requested from the DAQ and retained in new sessions.",
        ),
        "channel.column.logical_id": _help(
            "The stable software identity for the measurement channel.",
            "LabDAQ uses it to associate samples, metadata, processing, and exports.",
        ),
        "channel.column.role": _help(
            "The scientific role assigned to the channel, such as an axis or diagnostic voltage.",
            "LabDAQ uses the role to determine which calibrated or derived products are permitted.",
        ),
        "channel.column.physical": _help(
            "The DAQ module and analog-input terminal mapped to this channel.",
            "LabDAQ validates this mapping before configuring the hardware task.",
        ),
        "channel.column.sensor": _help(
            "The sensor record associated with the logical channel.",
            "LabDAQ uses it to retain asset identity and calibration provenance.",
        ),
        "channel.column.calibration": _help(
            "The calibration record associated with the channel.",
            "LabDAQ uses it to convert raw voltage only when the channel is authorized for processing.",
        ),
        "channel.input_min": _help(
            "Requested minimum input voltage for the selected channel.",
            "The DAQ validates this range against the supported module.",
        ),
        "channel.input_max": _help(
            "Requested maximum input voltage for the selected channel.",
            "The DAQ validates this range against the supported module.",
        ),
        "channel.sensor": _help(
            "The identifier of the sensor record used by this channel.",
            "It links channel samples to the sensor's identity and provenance metadata.",
        ),
        "channel.sensor_manufacturer": _help(
            "The sensor manufacturer's recorded name.",
            "LabDAQ stores it to identify the physical measurement asset.",
        ),
        "channel.sensor_model": _help(
            "The sensor model designation.",
            "LabDAQ records it for traceability and hardware review.",
        ),
        "channel.sensor_serial": _help(
            "The unique serial number of the installed sensor.",
            "LabDAQ uses it to tie sensitivity and calibration evidence to the correct asset.",
        ),
        "channel.sensor_mounting": _help(
            "A description of sensor mounting and measurement orientation.",
            "LabDAQ records it so channel direction and mechanical coupling can be interpreted later.",
        ),
        "channel.sensor_certificate": _help(
            "The reference for the sensor's calibration certificate or source document.",
            "LabDAQ stores it as provenance; it does not independently verify the certificate.",
        ),
        "channel.calibration": _help(
            "The identifier of the calibration applied to this channel.",
            "LabDAQ records it with derived products so the conversion can be reproduced.",
        ),
        "channel.sensitivity": _help(
            "The sensor or measurement-chain sensitivity coefficient.",
            "LabDAQ combines it with the declared sensitivity unit to convert raw voltage.",
            "Enter only a value supported by the cited calibration evidence.",
        ),
        "channel.sensitivity_unit": _help(
            "The unit of the sensitivity coefficient.",
            "It tells LabDAQ how voltage relates to the engineering output unit.",
        ),
        "channel.output_unit": _help(
            "The engineering unit produced after calibration.",
            "LabDAQ uses it to label calibrated values, plots, metrics, and exports.",
        ),
        "channel.polarity": _help(
            "The sign convention of the calibration conversion.",
            "LabDAQ applies it when mapping raw voltage to the engineering quantity.",
            "Use only the polarity established by calibration or wiring evidence.",
        ),
        "channel.source_reference": _help(
            "The authoritative source for the calibration values.",
            "LabDAQ records it so coefficients, units, and polarity remain traceable.",
        ),
        "channel.effective_date": _help(
            "The date from which this calibration is considered applicable.",
            "LabDAQ preserves it as calibration provenance for the session.",
        ),
        "channel.input_offset": _help(
            "The declared raw-input offset used by the calibration model.",
            "LabDAQ removes it as part of the documented conversion before applying sensitivity.",
        ),
        "channel.conditioning": _help(
            "The identifier of the external signal-conditioning chain.",
            "It links this channel to the ordered hardware stages used before the DAQ input.",
        ),
        "channel.conditioning_components": _help(
            "The ordered components in the external conditioning chain.",
            "LabDAQ records the semicolon-separated entries as measurement provenance.",
        ),
        "channel.conditioning_input_unit": _help(
            "The physical unit entering the external conditioning chain.",
            "It documents what quantity the chain is intended to receive.",
        ),
        "channel.conditioning_supply": _help(
            "A description of external excitation or supply for the conditioning chain.",
            "LabDAQ stores this evidence but does not infer electrical compatibility from it.",
        ),
        "channel.conditioning_polarity": _help(
            "The sign convention across the complete conditioning chain.",
            "LabDAQ uses it to preserve the intended direction of the engineering result.",
        ),
        "channel.conditioning_evidence": _help(
            "The source that supports the conditioning-chain configuration.",
            "LabDAQ records it for later verification of the conversion path.",
        ),
        "channel.converter_manufacturer": _help(
            "The manufacturer of the external charge or signal converter.",
            "LabDAQ stores it as conditioning hardware identity.",
        ),
        "channel.converter_model": _help(
            "The model designation of the external converter.",
            "LabDAQ records it to identify the conversion hardware used during acquisition.",
        ),
        "channel.converter_serial": _help(
            "The serial number of the external converter.",
            "LabDAQ uses it to tie converter settings and evidence to the installed asset.",
        ),
        "channel.converter_sensitivity": _help(
            "The external converter's documented sensitivity coefficient.",
            "LabDAQ uses it only as part of the explicitly validated measurement chain.",
        ),
        "channel.converter_unit": _help(
            "The unit associated with converter sensitivity.",
            "It defines how the converter input quantity maps to its voltage output.",
        ),
        "channel.converter_polarity": _help(
            "The external converter's documented sign convention.",
            "LabDAQ preserves it when constructing the complete chain polarity.",
        ),
        "channel.converter_excitation": _help(
            "The converter excitation mode or source description.",
            "LabDAQ stores it as provenance for the external conditioning setup.",
        ),
        "channel.converter_current": _help(
            "The documented converter excitation current, in milliamperes.",
            "LabDAQ records this setting; it does not establish that the wiring is safe.",
        ),
        "channel.converter_source": _help(
            "The evidence source for converter settings and behavior.",
            "LabDAQ retains it so the external conversion can be independently checked.",
        ),
        "channel.ni_stage": _help(
            "A description of the signal presented at the NI analog-input terminal.",
            "LabDAQ records it to distinguish external conditioning from DAQ-side interpretation.",
        ),
        "channel.ni_bias_removed": _help(
            "Whether removal of the expected terminal bias is documented.",
            "LabDAQ requires an explicit true or false value when that stage is modeled.",
        ),
        "channel.ni_expected_bias": _help(
            "The expected DC bias at the NI terminal, in volts.",
            "LabDAQ uses it only as declared by the validated conditioning model.",
        ),
        "channel.ni_bias_tolerance": _help(
            "The allowed difference from expected terminal bias, in volts.",
            "LabDAQ uses it when evaluating qualification evidence for the input stage.",
        ),
        "channel.ni_dynamic_min": _help(
            "The documented minimum dynamic signal at the NI terminal, in volts.",
            "LabDAQ retains it as a qualification bound for the conditioned input.",
        ),
        "channel.ni_dynamic_max": _help(
            "The documented maximum dynamic signal at the NI terminal, in volts.",
            "LabDAQ retains it as a qualification bound for the conditioned input.",
        ),
        "channel.ni_source": _help(
            "The evidence source for NI-terminal bias and dynamic limits.",
            "LabDAQ records it so those bounds are not treated as unexplained assumptions.",
        ),
        # Hardware qualification.
        "qualification.plan": _help(
            "The versioned set of hardware checks to run.",
            "LabDAQ uses the selected plan to determine required tests and launch evidence.",
        ),
        "qualification.revision": _help(
            "The revision of the selected qualification plan.",
            "LabDAQ records it so the exact acceptance procedure remains identifiable.",
        ),
        "qualification.table": _help(
            "The tests and current results in the selected qualification plan.",
            "LabDAQ uses required passing evidence when evaluating acquisition readiness.",
        ),
        "qualification.column.test": _help(
            "The human-readable name of a qualification check.",
            "It identifies the check whose evidence and result are reported in this row.",
        ),
        "qualification.column.purpose": _help(
            "The condition that the qualification check is intended to assess.",
            "Use it to understand why the check is part of launch readiness.",
        ),
        "qualification.column.required": _help(
            "Whether this check is mandatory for the selected plan.",
            "A required failing or missing result can block qualified acquisition.",
        ),
        "qualification.column.status": _help(
            "The current result of this qualification check.",
            "LabDAQ retains pass, fail, warning, and not-tested states explicitly.",
        ),
        "qualification.progress": _help(
            "Progress through the active hardware qualification plan.",
            "LabDAQ updates it as background checks complete without blocking the interface.",
        ),
        "qualification.status": _help(
            "A summary of the current or most recent qualification run.",
            "LabDAQ uses the finalized report, not this display text, as readiness evidence.",
        ),
        "qualification.report": _help(
            "The detailed qualification findings and evidence summary.",
            "Review it to distinguish passes, warnings, failures, and untested physical criteria.",
        ),
        # Acquisition and live display.
        "acquisition.mode": _help(
            "The authorized acquisition workflow to launch.",
            "LabDAQ applies different readiness, recording, and processing rules to each mode.",
        ),
        "acquisition.monitor_channels": _help(
            "The enabled configured channels retained during open monitoring.",
            "LabDAQ acquires them through one synchronized stream and records raw volts when asked.",
        ),
        "acquisition.pause_display": _help(
            "Whether live plot rendering is temporarily frozen.",
            "Acquisition, recording, status updates, and metrics continue while display is paused.",
        ),
        "acquisition.live_window": _help(
            "The recent time span shown in live plots.",
            "Changing it affects display only and never reconfigures acquisition or recording.",
        ),
        "acquisition.state": _help(
            "The backend acquisition or monitoring state.",
            "Use it to distinguish running, stopping, finalized, and faulted operation.",
        ),
        "acquisition.elapsed": _help(
            "Elapsed wall time since the current acquisition or monitor started.",
            "It is an operator status value; sample index remains the authoritative relative time.",
        ),
        "acquisition.counts": _help(
            "Acquired and durably persisted sample counts per channel.",
            "LabDAQ compares terminal counts to identify recording loss; temporary running lag is not loss.",
        ),
        "acquisition.warnings": _help(
            "Counts of warnings, source gaps, and superseded display frames.",
            "Display drops are reported separately from acquisition gaps and persistence loss.",
        ),
        "acquisition.identity": _help(
            "Identity, path, rates, channels, duration, and finalization details for the active session.",
            "Use it to confirm what LabDAQ is recording and where the source record will be retained.",
        ),
        "acquisition.recording_queue": _help(
            "Occupancy of the lossless queue feeding session storage.",
            "LabDAQ treats backpressure that threatens recording as an explicit fault.",
        ),
        "acquisition.display_queue": _help(
            "Occupancy of the bounded live-display queue.",
            "Older display frames may be superseded without affecting the recording path.",
        ),
        "metrics.table": _help(
            "Conditioned live metrics calculated over the current rolling window.",
            "They support operator awareness and are not authoritative replacements for raw data.",
        ),
        "metrics.column.channel": _help(
            "The stable identity of the channel summarized by this row.",
            "It links each metric to the corresponding synchronized measurement.",
        ),
        "metrics.column.unit": _help(
            "The engineering unit of the conditioned metric values.",
            "Use it when comparing values; metrics with unlike units are not interchangeable.",
        ),
        "metrics.column.rms": _help(
            "Root-mean-square magnitude over the live metric window.",
            "LabDAQ uses it to summarize the signal's effective level.",
        ),
        "metrics.column.peak": _help(
            "Largest absolute conditioned value in the live metric window.",
            "It highlights the strongest observed magnitude regardless of sign.",
        ),
        "metrics.column.peak_to_peak": _help(
            "Difference between the maximum and minimum window values.",
            "It summarizes the complete conditioned signal excursion.",
        ),
        "metrics.column.crest": _help(
            "Absolute peak divided by RMS for the live metric window.",
            "It helps identify impulsive content; unavailable values remain explicit.",
        ),
        "metrics.column.clipping": _help(
            "Fraction of window samples identified at the declared clipping limit.",
            "Use it as a warning that the measured waveform may not represent the true input.",
        ),
        # Session catalog and review.
        "sessions.root": _help(
            "The parent directory scanned for LabDAQ session candidates.",
            "LabDAQ examines immediate child directories without loading complete raw datasets.",
        ),
        "sessions.table": _help(
            "Recorded sessions and invalid session candidates found under the selected root.",
            "Select a row to inspect integrity and open valid retained data for review.",
        ),
        "sessions.column.session": _help(
            "The experiment or session name recorded in the candidate.",
            "It helps identify which run to inspect.",
        ),
        "sessions.column.started": _help(
            "The timezone-aware wall-clock start time recorded for the session.",
            "It identifies the acquisition epoch; sample indices define relative time.",
        ),
        "sessions.column.completion": _help(
            "The recorded completion state of the session.",
            "LabDAQ distinguishes completed, stopped, interrupted, and faulted source records.",
        ),
        "sessions.column.integrity": _help(
            "The result of source-record structure and integrity checks.",
            "LabDAQ uses it to determine which review and export operations are safe to offer.",
        ),
        "sessions.column.type": _help(
            "The acquisition type that produced the session.",
            "It determines whether calibrated and derived processing is authorized.",
        ),
        "sessions.details": _help(
            "Metadata, counts, hardware identity, integrity, and fault details for the selected row.",
            "Review it before opening or interpreting a retained session.",
        ),
        "review.stage": _help(
            "The signal stage used by time-domain review and transient detection.",
            "Choose raw voltage, calibrated engineering values, or causal conditioned values.",
        ),
        "review.representation": _help(
            "The waveform or spectral representation requested for the review plot.",
            "LabDAQ derives it from the verified source range and session processing provenance.",
        ),
        "review.start": _help(
            "The inclusive relative start time of the selected review range, in seconds.",
            "Leave it blank to start at the beginning of the verified extent.",
        ),
        "review.stop": _help(
            "The exclusive relative stop time of the selected review range, in seconds.",
            "Leave it blank to stop at the end of the verified extent.",
        ),
        "review.mad_multiplier": _help(
            "The median-absolute-deviation multiplier used as the transient threshold.",
            "Higher values require a larger deviation before LabDAQ suggests an event.",
        ),
        "review.pre_padding": _help(
            "Seconds included before the first threshold crossing in a suggested event.",
            "LabDAQ uses it to retain context before a detected transient.",
        ),
        "review.post_padding": _help(
            "Seconds included after the last threshold crossing in a suggested event.",
            "LabDAQ uses it to retain context after a detected transient.",
        ),
        "review.merge_gap": _help(
            "Maximum same-channel time gap merged into one suggested event, in seconds.",
            "Crossings farther apart remain separate suggestions.",
        ),
        "review.detection_status": _help(
            "Status and summary of transient suggestions for the selected channels and stage.",
            "Suggestions are not persisted events and become stale when their inputs change.",
        ),
        "review.channels": _help(
            "Channels included in review, analysis, detection, and selected exports.",
            "Checked channels share the same synchronized half-open sample range.",
        ),
        "review.plot_metadata": _help(
            "The resolved sample range, rate, units, recipe, and spectral settings for the plot.",
            "Use it to interpret the displayed derived product and reproduce the request.",
        ),
        "review.extrema_table": _help(
            "Located maxima, minima, and absolute peaks for the selected synchronized range.",
            "Results include source sample indices and relative times for traceability.",
        ),
        "review.extrema.channel": _help(
            "The stable channel identity for this extrema row.",
            "It ties each located value back to its recorded source channel.",
        ),
        "review.extrema.unit": _help(
            "The unit of the extrema values in this row.",
            "It reflects the selected raw, calibrated, or conditioned signal stage.",
        ),
        "review.extrema.maximum": _help(
            "The largest signed value in the selected range.",
            "LabDAQ reports its exact source sample and relative time in adjacent fields.",
        ),
        "review.extrema.max_sample": _help(
            "The authoritative source sample index of the maximum.",
            "Use it to locate the value precisely in the recorded session.",
        ),
        "review.extrema.max_time": _help(
            "The maximum's relative time in seconds.",
            "LabDAQ derives it from sample index divided by sample rate.",
        ),
        "review.extrema.minimum": _help(
            "The smallest signed value in the selected range.",
            "LabDAQ reports its exact source sample and relative time in adjacent fields.",
        ),
        "review.extrema.min_sample": _help(
            "The authoritative source sample index of the minimum.",
            "Use it to locate the value precisely in the recorded session.",
        ),
        "review.extrema.min_time": _help(
            "The minimum's relative time in seconds.",
            "LabDAQ derives it from sample index divided by sample rate.",
        ),
        "review.extrema.absolute_peak": _help(
            "The value with the greatest absolute magnitude in the selected range.",
            "Its sign is preserved while magnitude determines which value is selected.",
        ),
        "review.extrema.peak_sample": _help(
            "The authoritative source sample index of the absolute peak.",
            "Use it to locate the value precisely in the recorded session.",
        ),
        "review.extrema.peak_time": _help(
            "The absolute peak's relative time in seconds.",
            "LabDAQ derives it from sample index divided by sample rate.",
        ),
        "review.events": _help(
            "Structured acquisition, storage, warning, and fault events retained by the session.",
            "Use them to interpret scientifically relevant conditions during the run.",
        ),
        "review.artifacts": _help(
            "Export history and derived-artifact diagnostics for the selected session.",
            "LabDAQ records final state, provenance, paths, and failures for each export request.",
        ),
        "review.detected_table": _help(
            "Transient waveform suggestions generated from recorded data.",
            "Selecting a row moves the review range; it does not create a persisted DAQ event.",
        ),
        "review.detected.channel": _help(
            "The channel on which this transient suggestion was detected.",
            "The selected padded range is still applied synchronously to all checked channels.",
        ),
        "review.detected.start": _help(
            "Inclusive relative start time of the padded suggestion, in seconds.",
            "It includes the configured pre-event context.",
        ),
        "review.detected.stop": _help(
            "Exclusive relative stop time of the padded suggestion, in seconds.",
            "It includes the configured post-event context.",
        ),
        "review.detected.duration": _help(
            "Duration of the padded transient suggestion, in seconds.",
            "LabDAQ calculates it as exclusive stop time minus inclusive start time.",
        ),
        "review.detected.peak": _help(
            "Relative time of the strongest deviation in the suggestion, in seconds.",
            "Use it to locate the detected transient within the source session.",
        ),
        "review.detected.score": _help(
            "Peak deviation normalized by the channel's robust MAD threshold scale.",
            "Larger scores indicate stronger deviations; infinity remains explicit when applicable.",
        ),
        "review.export_progress": _help(
            "Progress of the active CSV, figure, or full-extent TDMS export.",
            "Exports run from verified session data and retain provenance in their manifests or sidecars.",
        ),
        # Shared plot controls.
        "plot.panel": _help(
            "A bounded visual representation of live or disk-backed channel data.",
            "Plot interaction changes presentation only and never modifies recorded raw samples.",
        ),
        "plot.scale_mode": _help(
            "How visible channels share or independently scale their vertical axes.",
            "Choose compatible-unit sharing, independent axes, or explicit manual limits.",
        ),
        "plot.minimum": _help(
            "The lower vertical-axis bound used in manual-limits mode.",
            "It affects display only and must be less than the manual maximum.",
        ),
        "plot.maximum": _help(
            "The upper vertical-axis bound used in manual-limits mode.",
            "It affects display only and must be greater than the manual minimum.",
        ),
        "plot.channels": _help(
            "The channels currently available to this plot panel.",
            "Check or clear entries to change visibility without affecting acquisition or recording.",
        ),
        "plot.coordinate": _help(
            "The data coordinate beneath the pointer on a plot canvas.",
            "Use it for visual inspection; exact extrema remain tied to source sample indices.",
        ),
        # Raw-diagnostic authorization.
        "diagnostic.supply_voltage": _help(
            "The sensor-board supply voltage measured by the operator, in volts.",
            "LabDAQ records this physical observation as diagnostic authorization evidence.",
            "Enter only a measurement that was actually performed with suitable equipment.",
        ),
        "diagnostic.supply_polarity": _help(
            "Confirmation that supply polarity was physically measured and verified.",
            "LabDAQ requires this explicit evidence before authorizing raw-voltage diagnostics.",
        ),
        "diagnostic.ai3_disconnected": _help(
            "Confirms that the ai3 input is physically disconnected for this diagnostic.",
            "LabDAQ then records no claimed external ai3 voltage bounds.",
        ),
        "diagnostic.ai3_connected": _help(
            "Confirms that connected ai3 voltage was independently checked at high impedance.",
            "LabDAQ requires the observed bounds and instrument confirmation for this choice.",
        ),
        "diagnostic.ai3_minimum": _help(
            "The minimum ai3 voltage observed independently before connection, in volts.",
            "LabDAQ records it as authorization evidence, not as a calibrated measurement.",
        ),
        "diagnostic.ai3_maximum": _help(
            "The maximum ai3 voltage observed independently before connection, in volts.",
            "LabDAQ records it as authorization evidence, not as a calibrated measurement.",
        ),
        "diagnostic.ai3_instrument": _help(
            "Confirmation that a suitable high-impedance scope or meter was used for ai3.",
            "LabDAQ requires it when ai3 remains connected during the raw diagnostic.",
        ),
    }
)


def tooltip_text(help_id: str, notice: str | None = None) -> str:
    """Compose catalog help and an optional live contextual notice."""
    description = FIELD_HELP[help_id].render()
    if notice is None or not notice.strip():
        return description
    return f"{description}\n\nCurrent status:\n{notice.strip()}"


def bind_field_help(widget: QWidget, help_id: str) -> None:
    """Bind catalog help to a data-bearing widget."""
    if help_id not in FIELD_HELP:
        raise KeyError(f"unknown field-help id: {help_id}")
    widget.setProperty(_HELP_ID_PROPERTY, help_id)
    _refresh_widget_help(widget)


def set_field_notice(widget: QWidget, notice: str | None) -> None:
    """Set live context without replacing a widget's descriptive help."""
    normalized = notice.strip() if notice is not None and notice.strip() else None
    widget.setProperty(_NOTICE_PROPERTY, normalized)
    _refresh_widget_help(widget)
    if isinstance(widget, QTableWidget):
        _refresh_table_help(widget)


def bind_table_help(
    table: QTableWidget,
    table_help_id: str,
    column_help_ids: Sequence[str],
) -> None:
    """Bind general and per-column help to a table."""
    if len(column_help_ids) != table.columnCount():
        raise ValueError("table help must describe every column")
    unknown = tuple(help_id for help_id in column_help_ids if help_id not in FIELD_HELP)
    if unknown:
        raise KeyError(f"unknown table field-help ids: {', '.join(unknown)}")
    setattr(table, _TABLE_COLUMNS_ATTRIBUTE, tuple(column_help_ids))
    bind_field_help(table, table_help_id)
    _refresh_table_help(table)


def set_described_table_item(
    table: QTableWidget,
    row: int,
    column: int,
    item: QTableWidgetItem,
) -> None:
    """Insert a table item with the description assigned to its column."""
    item.setToolTip(_table_column_tooltip(table, column))
    table.setItem(row, column, item)


def field_help_id(widget: QWidget) -> str | None:
    """Return a widget's bound help identifier for coverage tests."""
    value = widget.property(_HELP_ID_PROPERTY)
    return value if isinstance(value, str) and value else None


def _refresh_widget_help(widget: QWidget) -> None:
    help_id = field_help_id(widget)
    if help_id is None:
        return
    notice_value = widget.property(_NOTICE_PROPERTY)
    notice = notice_value if isinstance(notice_value, str) else None
    widget.setToolTip(tooltip_text(help_id, notice))


def _table_column_tooltip(table: QTableWidget, column: int) -> str:
    help_ids = getattr(table, _TABLE_COLUMNS_ATTRIBUTE, ())
    if not isinstance(help_ids, tuple) or column < 0 or column >= len(help_ids):
        return table.toolTip()
    notice_value = table.property(_NOTICE_PROPERTY)
    notice = notice_value if isinstance(notice_value, str) else None
    return tooltip_text(str(help_ids[column]), notice)


def _refresh_table_help(table: QTableWidget) -> None:
    for column in range(table.columnCount()):
        tooltip = _table_column_tooltip(table, column)
        header = table.horizontalHeaderItem(column)
        if header is not None:
            header.setToolTip(tooltip)
        for row in range(table.rowCount()):
            item = table.item(row, column)
            if item is not None:
                item.setToolTip(tooltip)
