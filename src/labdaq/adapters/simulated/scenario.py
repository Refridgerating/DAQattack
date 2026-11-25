"""Versioned simulator scenario records and JSON codec."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.resources import files
from typing import TYPE_CHECKING, Literal, TypeAlias, cast

from jsonschema import Draft202012Validator

from labdaq.errors import ErrorSeverity
from labdaq.ports.daq import DaqEvent, SimulatorScenarioError

if TYPE_CHECKING:
    from pathlib import Path

GENERATOR_VERSION = "sim-v1"
MAX_SEED = (1 << 64) - 1


@dataclass(frozen=True, slots=True)
class SineComponent:
    component_id: str
    amplitude_v: float
    frequency_hz: float
    phase_rad: float
    offset_v: float = 0.0
    start_sample: int | None = None
    stop_sample: int | None = None
    kind: Literal["sine"] = "sine"


@dataclass(frozen=True, slots=True)
class ImpulseComponent:
    component_id: str
    start_sample: int
    amplitude_v: float
    width_samples: int
    kind: Literal["impulse"] = "impulse"


@dataclass(frozen=True, slots=True)
class ChirpComponent:
    component_id: str
    start_frequency_hz: float
    end_frequency_hz: float
    start_sample: int
    duration_samples: int
    amplitude_v: float
    initial_phase_rad: float
    kind: Literal["linear_chirp"] = "linear_chirp"


@dataclass(frozen=True, slots=True)
class NoiseComponent:
    component_id: str
    rms_v: float
    start_sample: int | None = None
    stop_sample: int | None = None
    kind: Literal["gaussian_noise"] = "gaussian_noise"


@dataclass(frozen=True, slots=True)
class DriftComponent:
    component_id: str
    initial_offset_v: float
    slope_v_per_second: float
    reference_sample: int
    start_sample: int | None = None
    stop_sample: int | None = None
    kind: Literal["linear_drift"] = "linear_drift"


SignalComponent: TypeAlias = (
    SineComponent | ImpulseComponent | ChirpComponent | NoiseComponent | DriftComponent
)


@dataclass(frozen=True, slots=True)
class ChannelSignal:
    channel_id: str
    raw_unit: Literal["V"]
    bias_v: float
    components: tuple[SignalComponent, ...]


@dataclass(frozen=True, slots=True)
class ClippingFault:
    fault_id: str
    channel_ids: tuple[str, ...]
    minimum_v: float
    maximum_v: float
    start_sample: int
    stop_sample: int | None
    kind: Literal["clipping"] = "clipping"


@dataclass(frozen=True, slots=True)
class DropoutFault:
    fault_id: str
    start_sample: int
    missing_samples_per_channel: int
    channel_ids: tuple[str, ...] | None
    kind: Literal["dropout"] = "dropout"


@dataclass(frozen=True, slots=True)
class DelayFault:
    fault_id: str
    latency_seconds: float
    start_sample: int | None
    stop_sample: int | None
    start_read: int | None
    stop_read: int | None
    kind: Literal["read_delay"] = "read_delay"


@dataclass(frozen=True, slots=True)
class DisconnectFault:
    fault_id: str
    sample_index: int | None
    read_sequence: int | None
    reconnectable: bool
    kind: Literal["disconnect"] = "disconnect"


@dataclass(frozen=True, slots=True)
class NonfiniteFault:
    fault_id: str
    channel_ids: tuple[str, ...]
    sample_index: int
    value: Literal["nan", "positive-infinity", "negative-infinity"]
    kind: Literal["nonfinite"] = "nonfinite"


@dataclass(frozen=True, slots=True)
class FrozenFault:
    fault_id: str
    channel_ids: tuple[str, ...]
    start_sample: int
    stop_sample: int
    value_v: float
    kind: Literal["frozen"] = "frozen"


@dataclass(frozen=True, slots=True)
class SourceIndexFault:
    fault_id: str
    read_sequence: int
    offset_samples: int
    mode: Literal["gap", "duplicate", "reorder"]
    kind: Literal["source_index"] = "source_index"


@dataclass(frozen=True, slots=True)
class StaleReadFault:
    fault_id: str
    read_sequence: int
    kind: Literal["stale_read"] = "stale_read"


@dataclass(frozen=True, slots=True)
class ChannelDelayFault:
    fault_id: str
    channel_ids: tuple[str, ...]
    delay_samples: int
    start_sample: int
    stop_sample: int | None
    kind: Literal["channel_delay"] = "channel_delay"


@dataclass(frozen=True, slots=True)
class CrosstalkFault:
    fault_id: str
    source_channel_id: str
    coupled_channel_ids: tuple[str, ...]
    gain: float
    start_sample: int
    stop_sample: int | None
    kind: Literal["crosstalk"] = "crosstalk"


Fault: TypeAlias = (
    ClippingFault
    | DropoutFault
    | DelayFault
    | DisconnectFault
    | NonfiniteFault
    | FrozenFault
    | SourceIndexFault
    | StaleReadFault
    | ChannelDelayFault
    | CrosstalkFault
)


@dataclass(frozen=True, slots=True)
class SimulatorTopology:
    """Scenario-controlled discovery and capability surface for qualification."""

    device_present: bool = True
    module_present: bool = True
    device_model: str = "cDAQ-9171"
    device_serial_number: str = "SIM-CDAQ-9171-0001"
    module_model: str = "NI-9239"
    module_serial_number: str = "SIM-NI-9239-0001"
    maximum_sample_rate_hz: float = 50_000.0
    input_min_v: float = -10.0
    input_max_v: float = 10.0


@dataclass(frozen=True, slots=True)
class SimulatorScenario:
    schema_version: int
    scenario_id: str
    scenario_revision: int
    name: str
    description: str
    generator_version: str
    seed: int
    pacing_mode: Literal["immediate", "real_time"]
    channel_signals: tuple[ChannelSignal, ...]
    fault_schedule: tuple[Fault, ...]
    expected_reference_values: dict[str, object] | None
    notes: str | None
    self_test_supported: bool = True
    self_test_passed: bool = True
    topology: SimulatorTopology = SimulatorTopology()


class JsonSimulatorScenarioCodec:
    """Load closed version-1 scenario documents into immutable records."""

    def load(self, path: Path) -> SimulatorScenario:
        try:
            payload = path.read_text(encoding="utf-8")
        except OSError as cause:
            raise self._error(
                "The simulator scenario could not be read.",
                context={"document": str(path), "exception_type": type(cause).__name__},
                cause=cause,
            ) from cause
        return self.loads(payload, document=str(path))

    def load_bundled(self, scenario_id: str) -> SimulatorScenario:
        resource = files("labdaq.resources").joinpath(
            f"config/simulator-scenarios/{scenario_id}-v1.json"
        )
        try:
            payload = resource.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError) as cause:
            raise self._error(
                "The bundled simulator scenario was not found.",
                context={"scenario_id": scenario_id},
                cause=cause,
            ) from cause
        return self.loads(payload, document=f"bundled:{scenario_id}")

    def loads(self, payload: str, *, document: str = "<memory>") -> SimulatorScenario:
        try:
            raw_value = json.loads(payload)
        except json.JSONDecodeError as cause:
            raise self._error(
                "The simulator scenario is not valid JSON.",
                context={"document": document, "line": cause.lineno, "column": cause.colno},
                cause=cause,
            ) from cause
        if not isinstance(raw_value, dict):
            raise self._error(
                "The simulator scenario must be a JSON object.",
                context={"document": document},
            )
        raw = cast("dict[str, object]", raw_value)
        self._validate_schema(raw, document)
        scenario = _decode_scenario(raw)
        issues = _semantic_issues(scenario)
        if issues:
            raise self._error(
                "The simulator scenario violates semantic rules.",
                context={
                    "document": document,
                    "scenario_id": scenario.scenario_id,
                    "issues": issues,
                },
            )
        return scenario

    def dumps(self, scenario: SimulatorScenario) -> str:
        """Serialize one immutable scenario in its canonical JSON form."""
        primitive = _to_wire(asdict(scenario))
        return json.dumps(
            primitive,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def _validate_schema(self, raw: dict[str, object], document: str) -> None:
        received_version = raw.get("schema_version")
        if received_version != 1:
            raise self._error(
                "The simulator scenario schema version is unsupported.",
                context={
                    "document": document,
                    "received_version": received_version,
                    "supported_versions": [1],
                },
            )
        schema_resource = files("labdaq.resources").joinpath(
            "schemas/v1/simulator-scenario.schema.json"
        )
        schema_raw = json.loads(schema_resource.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema_raw)
        errors = sorted(validator.iter_errors(raw), key=lambda error: list(error.path))
        if errors:
            issues = [
                {
                    "field_path": "$." + ".".join(str(part) for part in error.path),
                    "rule": error.validator,
                    "message": error.message,
                    "correction": "Supply a value accepted by the version-1 scenario schema.",
                }
                for error in errors
            ]
            raise self._error(
                "The simulator scenario does not match its schema.",
                context={"document": document, "issues": issues},
            )

    @staticmethod
    def _error(
        message: str,
        *,
        context: dict[str, object],
        cause: BaseException | None = None,
    ) -> SimulatorScenarioError:
        event = DaqEvent(
            code="DAQ.SIMULATOR.SCENARIO_INVALID",
            severity=ErrorSeverity.ERROR,
            backend_id="simulated",
            operation="load_scenario",
            message=message,
            timestamp=datetime.now(UTC),
            context=context,
        )
        return SimulatorScenarioError(
            message,
            event=event,
            remediation="Correct the reported scenario fields and validate again.",
            cause=cause,
        )


def _decode_scenario(raw: dict[str, object]) -> SimulatorScenario:
    channels = tuple(_decode_channel(item) for item in _objects(raw, "channel_signals"))
    faults = tuple(_decode_fault(item) for item in _objects(raw, "fault_schedule"))
    expected = raw.get("expected_reference_values")
    return SimulatorScenario(
        schema_version=_integer(raw, "schema_version"),
        scenario_id=_string(raw, "scenario_id"),
        scenario_revision=_integer(raw, "scenario_revision"),
        name=_string(raw, "name"),
        description=_string(raw, "description"),
        generator_version=_string(raw, "generator_version"),
        seed=_integer(raw, "seed"),
        pacing_mode=cast("Literal['immediate', 'real_time']", _string(raw, "pacing_mode")),
        channel_signals=channels,
        fault_schedule=faults,
        expected_reference_values=(
            cast("dict[str, object]", expected) if isinstance(expected, dict) else None
        ),
        notes=cast("str | None", raw.get("notes")),
        self_test_supported=_optional_boolean(raw, "self_test_supported", True),
        self_test_passed=_optional_boolean(raw, "self_test_passed", True),
        topology=_decode_topology(raw.get("topology")),
    )


def _decode_topology(raw: object) -> SimulatorTopology:
    if raw is None:
        return SimulatorTopology()
    topology = cast("dict[str, object]", raw)
    return SimulatorTopology(
        device_present=_optional_boolean(topology, "device_present", True),
        module_present=_optional_boolean(topology, "module_present", True),
        device_model=cast("str", topology.get("device_model", "cDAQ-9171")),
        device_serial_number=cast(
            "str", topology.get("device_serial_number", "SIM-CDAQ-9171-0001")
        ),
        module_model=cast("str", topology.get("module_model", "NI-9239")),
        module_serial_number=cast("str", topology.get("module_serial_number", "SIM-NI-9239-0001")),
        maximum_sample_rate_hz=_optional_number(topology, "maximum_sample_rate_hz", 50_000.0),
        input_min_v=_optional_number(topology, "input_min_v", -10.0),
        input_max_v=_optional_number(topology, "input_max_v", 10.0),
    )


def _decode_channel(raw: dict[str, object]) -> ChannelSignal:
    return ChannelSignal(
        channel_id=_string(raw, "channel_id"),
        raw_unit="V",
        bias_v=_number(raw, "bias_v"),
        components=tuple(_decode_component(item) for item in _objects(raw, "components")),
    )


def _decode_component(raw: dict[str, object]) -> SignalComponent:
    kind = _string(raw, "type")
    component_id = _string(raw, "component_id")
    if kind == "sine":
        return SineComponent(
            component_id=component_id,
            amplitude_v=_number(raw, "amplitude_v"),
            frequency_hz=_number(raw, "frequency_hz"),
            phase_rad=_number(raw, "phase_rad"),
            offset_v=_optional_number(raw, "offset_v", 0.0),
            start_sample=_optional_integer(raw, "start_sample"),
            stop_sample=_optional_integer(raw, "stop_sample"),
        )
    if kind == "impulse":
        return ImpulseComponent(
            component_id=component_id,
            start_sample=_integer(raw, "start_sample"),
            amplitude_v=_number(raw, "amplitude_v"),
            width_samples=_integer(raw, "width_samples"),
        )
    if kind == "linear_chirp":
        return ChirpComponent(
            component_id=component_id,
            start_frequency_hz=_number(raw, "start_frequency_hz"),
            end_frequency_hz=_number(raw, "end_frequency_hz"),
            start_sample=_integer(raw, "start_sample"),
            duration_samples=_integer(raw, "duration_samples"),
            amplitude_v=_number(raw, "amplitude_v"),
            initial_phase_rad=_number(raw, "initial_phase_rad"),
        )
    if kind == "gaussian_noise":
        return NoiseComponent(
            component_id=component_id,
            rms_v=_number(raw, "rms_v"),
            start_sample=_optional_integer(raw, "start_sample"),
            stop_sample=_optional_integer(raw, "stop_sample"),
        )
    if kind == "linear_drift":
        return DriftComponent(
            component_id=component_id,
            initial_offset_v=_number(raw, "initial_offset_v"),
            slope_v_per_second=_number(raw, "slope_v_per_second"),
            reference_sample=_integer(raw, "reference_sample"),
            start_sample=_optional_integer(raw, "start_sample"),
            stop_sample=_optional_integer(raw, "stop_sample"),
        )
    raise AssertionError(f"Schema admitted unsupported component: {kind}")


def _decode_fault(raw: dict[str, object]) -> Fault:
    kind = _string(raw, "type")
    fault_id = _string(raw, "fault_id")
    if kind == "clipping":
        return ClippingFault(
            fault_id=fault_id,
            channel_ids=tuple(_strings(raw, "channel_ids")),
            minimum_v=_number(raw, "minimum_v"),
            maximum_v=_number(raw, "maximum_v"),
            start_sample=_integer(raw, "start_sample"),
            stop_sample=_optional_integer(raw, "stop_sample"),
        )
    if kind == "dropout":
        channel_ids = raw.get("channel_ids")
        return DropoutFault(
            fault_id=fault_id,
            start_sample=_integer(raw, "start_sample"),
            missing_samples_per_channel=_integer(raw, "missing_samples_per_channel"),
            channel_ids=(
                tuple(cast("list[str]", channel_ids)) if isinstance(channel_ids, list) else None
            ),
        )
    if kind == "read_delay":
        return DelayFault(
            fault_id=fault_id,
            latency_seconds=_number(raw, "latency_seconds"),
            start_sample=_optional_integer(raw, "start_sample"),
            stop_sample=_optional_integer(raw, "stop_sample"),
            start_read=_optional_integer(raw, "start_read"),
            stop_read=_optional_integer(raw, "stop_read"),
        )
    if kind == "disconnect":
        return DisconnectFault(
            fault_id=fault_id,
            sample_index=_optional_integer(raw, "sample_index"),
            read_sequence=_optional_integer(raw, "read_sequence"),
            reconnectable=_boolean(raw, "reconnectable"),
        )
    if kind == "nonfinite":
        return NonfiniteFault(
            fault_id=fault_id,
            channel_ids=tuple(_strings(raw, "channel_ids")),
            sample_index=_integer(raw, "sample_index"),
            value=cast(
                "Literal['nan', 'positive-infinity', 'negative-infinity']",
                _string(raw, "value"),
            ),
        )
    if kind == "frozen":
        return FrozenFault(
            fault_id=fault_id,
            channel_ids=tuple(_strings(raw, "channel_ids")),
            start_sample=_integer(raw, "start_sample"),
            stop_sample=_integer(raw, "stop_sample"),
            value_v=_number(raw, "value_v"),
        )
    if kind == "source_index":
        return SourceIndexFault(
            fault_id=fault_id,
            read_sequence=_integer(raw, "read_sequence"),
            offset_samples=_integer(raw, "offset_samples"),
            mode=cast("Literal['gap', 'duplicate', 'reorder']", _string(raw, "mode")),
        )
    if kind == "stale_read":
        return StaleReadFault(
            fault_id=fault_id,
            read_sequence=_integer(raw, "read_sequence"),
        )
    if kind == "channel_delay":
        return ChannelDelayFault(
            fault_id=fault_id,
            channel_ids=tuple(_strings(raw, "channel_ids")),
            delay_samples=_integer(raw, "delay_samples"),
            start_sample=_integer(raw, "start_sample"),
            stop_sample=_optional_integer(raw, "stop_sample"),
        )
    if kind == "crosstalk":
        return CrosstalkFault(
            fault_id=fault_id,
            source_channel_id=_string(raw, "source_channel_id"),
            coupled_channel_ids=tuple(_strings(raw, "coupled_channel_ids")),
            gain=_number(raw, "gain"),
            start_sample=_integer(raw, "start_sample"),
            stop_sample=_optional_integer(raw, "stop_sample"),
        )
    raise AssertionError(f"Schema admitted unsupported fault: {kind}")


def _semantic_issues(scenario: SimulatorScenario) -> list[dict[str, object]]:
    issues: list[dict[str, object]] = []
    if scenario.generator_version != GENERATOR_VERSION:
        issues.append(_issue("$.generator_version", "unsupported_generator_version"))
    if scenario.seed < 0 or scenario.seed > MAX_SEED:
        issues.append(_issue("$.seed", "unsigned_64_bit_seed"))
    topology = scenario.topology
    if topology.module_present and not topology.device_present:
        issues.append(_issue("$.topology.module_present", "module_requires_device"))
    if (
        not topology.device_model.strip()
        or not topology.device_serial_number.strip()
        or not topology.module_model.strip()
        or not topology.module_serial_number.strip()
    ):
        issues.append(_issue("$.topology", "nonempty_identity"))
    if (
        not math.isfinite(topology.maximum_sample_rate_hz)
        or topology.maximum_sample_rate_hz <= 0
        or not math.isfinite(topology.input_min_v)
        or not math.isfinite(topology.input_max_v)
        or topology.input_min_v >= topology.input_max_v
    ):
        issues.append(_issue("$.topology", "valid_capabilities"))
    channel_ids = [channel.channel_id for channel in scenario.channel_signals]
    _duplicates(channel_ids, "$.channel_signals", issues)
    fault_ids = [fault.fault_id for fault in scenario.fault_schedule]
    _duplicates(fault_ids, "$.fault_schedule", issues)
    for channel_index, channel in enumerate(scenario.channel_signals):
        path = f"$.channel_signals[{channel_index}]"
        if not math.isfinite(channel.bias_v):
            issues.append(_issue(f"{path}.bias_v", "finite"))
        _duplicates([component.component_id for component in channel.components], path, issues)
        for component_index, component in enumerate(channel.components):
            component_path = f"{path}.components[{component_index}]"
            _validate_component(component, component_path, issues)
    terminal_boundaries: list[int] = []
    for index, fault in enumerate(scenario.fault_schedule):
        path = f"$.fault_schedule[{index}]"
        _validate_fault(fault, path, set(channel_ids), issues)
        if isinstance(fault, DropoutFault):
            terminal_boundaries.append(fault.start_sample)
        elif isinstance(fault, DisconnectFault) and fault.sample_index is not None:
            terminal_boundaries.append(fault.sample_index)
    if len(terminal_boundaries) != len(set(terminal_boundaries)):
        issues.append(_issue("$.fault_schedule", "ambiguous_terminal_fault_boundary"))
    return issues


def validate_scenario_for_experiment(
    scenario: SimulatorScenario,
    channel_ids: tuple[str, ...],
    sample_rate_hz: float,
) -> tuple[dict[str, object], ...]:
    issues: list[dict[str, object]] = []
    scenario_channels = tuple(channel.channel_id for channel in scenario.channel_signals)
    if set(scenario_channels) != set(channel_ids) or len(scenario_channels) != len(channel_ids):
        issues.append(
            {
                "field_path": "$.channel_signals",
                "rule": "exact_experiment_channel_set",
                "message": "Scenario channels must exactly match enabled experiment channels.",
                "correction": "Add or remove scenario channels to match the experiment.",
            }
        )
    nyquist = sample_rate_hz / 2.0
    for channel_index, channel in enumerate(scenario.channel_signals):
        for component_index, component in enumerate(channel.components):
            path = f"$.channel_signals[{channel_index}].components[{component_index}]"
            if isinstance(component, SineComponent) and component.frequency_hz >= nyquist:
                issues.append(_issue(f"{path}.frequency_hz", "below_nyquist"))
            if isinstance(component, ChirpComponent) and (
                component.start_frequency_hz >= nyquist or component.end_frequency_hz >= nyquist
            ):
                issues.append(_issue(path, "chirp_endpoints_below_nyquist"))
    return tuple(issues)


def _validate_component(
    component: SignalComponent,
    path: str,
    issues: list[dict[str, object]],
) -> None:
    values: list[float] = []
    if isinstance(component, SineComponent):
        values = [
            component.amplitude_v,
            component.frequency_hz,
            component.phase_rad,
            component.offset_v,
        ]
        if component.frequency_hz < 0:
            issues.append(_issue(f"{path}.frequency_hz", "non_negative"))
        _validate_interval(component.start_sample, component.stop_sample, path, issues)
    elif isinstance(component, ImpulseComponent):
        values = [component.amplitude_v]
        if component.start_sample < 0 or component.width_samples <= 0:
            issues.append(_issue(path, "valid_impulse"))
    elif isinstance(component, ChirpComponent):
        values = [
            component.start_frequency_hz,
            component.end_frequency_hz,
            component.amplitude_v,
            component.initial_phase_rad,
        ]
        if (
            component.start_sample < 0
            or component.duration_samples <= 0
            or component.start_frequency_hz < 0
            or component.end_frequency_hz < 0
        ):
            issues.append(_issue(path, "valid_linear_chirp"))
    elif isinstance(component, NoiseComponent):
        values = [component.rms_v]
        if component.rms_v < 0:
            issues.append(_issue(f"{path}.rms_v", "non_negative"))
        _validate_interval(component.start_sample, component.stop_sample, path, issues)
    elif isinstance(component, DriftComponent):
        values = [component.initial_offset_v, component.slope_v_per_second]
        if component.reference_sample < 0:
            issues.append(_issue(f"{path}.reference_sample", "non_negative"))
        _validate_interval(component.start_sample, component.stop_sample, path, issues)
    if any(not math.isfinite(value) for value in values):
        issues.append(_issue(path, "finite_numeric_values"))


def _validate_fault(
    fault: Fault,
    path: str,
    channel_ids: set[str],
    issues: list[dict[str, object]],
) -> None:
    if isinstance(fault, ClippingFault):
        if (
            not math.isfinite(fault.minimum_v)
            or not math.isfinite(fault.maximum_v)
            or fault.minimum_v >= fault.maximum_v
            or fault.start_sample < 0
        ):
            issues.append(_issue(path, "valid_clipping"))
        _validate_interval(fault.start_sample, fault.stop_sample, path, issues)
        if not set(fault.channel_ids).issubset(channel_ids):
            issues.append(_issue(f"{path}.channel_ids", "known_channel_ids"))
    elif isinstance(fault, DropoutFault):
        if fault.start_sample < 0 or fault.missing_samples_per_channel <= 0:
            issues.append(_issue(path, "valid_dropout"))
        if fault.channel_ids is not None and not set(fault.channel_ids).issubset(channel_ids):
            issues.append(_issue(f"{path}.channel_ids", "known_channel_ids"))
    elif isinstance(fault, DelayFault):
        if not math.isfinite(fault.latency_seconds) or fault.latency_seconds < 0:
            issues.append(_issue(f"{path}.latency_seconds", "non_negative_finite"))
        has_sample = fault.start_sample is not None
        has_read = fault.start_read is not None
        if has_sample == has_read:
            issues.append(_issue(path, "exactly_one_delay_basis"))
        _validate_interval(fault.start_sample, fault.stop_sample, path, issues)
        _validate_interval(fault.start_read, fault.stop_read, path, issues)
    elif isinstance(fault, DisconnectFault):
        if (fault.sample_index is None) == (fault.read_sequence is None):
            issues.append(_issue(path, "exactly_one_disconnect_basis"))
    elif isinstance(fault, NonfiniteFault):
        if not set(fault.channel_ids).issubset(channel_ids):
            issues.append(_issue(f"{path}.channel_ids", "known_channel_ids"))
    elif isinstance(fault, FrozenFault):
        if not set(fault.channel_ids).issubset(channel_ids):
            issues.append(_issue(f"{path}.channel_ids", "known_channel_ids"))
        _validate_interval(fault.start_sample, fault.stop_sample, path, issues)
    elif isinstance(fault, SourceIndexFault):
        if fault.offset_samples == 0:
            issues.append(_issue(f"{path}.offset_samples", "nonzero_offset"))
        if fault.mode == "gap" and fault.offset_samples < 0:
            issues.append(_issue(f"{path}.offset_samples", "positive_gap"))
        if fault.mode in {"duplicate", "reorder"} and fault.offset_samples > 0:
            issues.append(_issue(f"{path}.offset_samples", "negative_rewind"))
    elif isinstance(fault, ChannelDelayFault):
        if not set(fault.channel_ids).issubset(channel_ids):
            issues.append(_issue(f"{path}.channel_ids", "known_channel_ids"))
        if fault.delay_samples <= 0:
            issues.append(_issue(f"{path}.delay_samples", "positive_delay"))
        _validate_interval(fault.start_sample, fault.stop_sample, path, issues)
    elif isinstance(fault, CrosstalkFault):
        affected = {fault.source_channel_id, *fault.coupled_channel_ids}
        if not affected.issubset(channel_ids):
            issues.append(_issue(path, "known_channel_ids"))
        if not math.isfinite(fault.gain):
            issues.append(_issue(f"{path}.gain", "finite"))
        _validate_interval(fault.start_sample, fault.stop_sample, path, issues)


def _validate_interval(
    start: int | None,
    stop: int | None,
    path: str,
    issues: list[dict[str, object]],
) -> None:
    if start is not None and start < 0:
        issues.append(_issue(path, "non_negative_start"))
    if stop is not None and (start is None or stop <= start):
        issues.append(_issue(path, "ordered_half_open_interval"))


def _duplicates(
    values: list[str],
    path: str,
    issues: list[dict[str, object]],
) -> None:
    if len(values) != len(set(values)):
        issues.append(_issue(path, "unique_ids"))


def _issue(field_path: str, rule: str) -> dict[str, object]:
    return {
        "field_path": field_path,
        "rule": rule,
        "message": f"Scenario value violates rule: {rule}.",
        "correction": "Correct the reported scenario value.",
    }


def _objects(raw: dict[str, object], key: str) -> list[dict[str, object]]:
    return cast("list[dict[str, object]]", raw[key])


def _strings(raw: dict[str, object], key: str) -> list[str]:
    return cast("list[str]", raw[key])


def _string(raw: dict[str, object], key: str) -> str:
    return cast("str", raw[key])


def _integer(raw: dict[str, object], key: str) -> int:
    return cast("int", raw[key])


def _optional_integer(raw: dict[str, object], key: str) -> int | None:
    return cast("int | None", raw.get(key))


def _number(raw: dict[str, object], key: str) -> float:
    return float(cast("int | float", raw[key]))


def _optional_number(raw: dict[str, object], key: str, default: float) -> float:
    return float(cast("int | float", raw.get(key, default)))


def _boolean(raw: dict[str, object], key: str) -> bool:
    return cast("bool", raw[key])


def _optional_boolean(raw: dict[str, object], key: str, default: bool) -> bool:
    return cast("bool", raw.get(key, default))


def _to_wire(value: object) -> object:
    if isinstance(value, dict):
        converted = {
            ("type" if key == "kind" else str(key)): _to_wire(item)
            for key, item in value.items()
            if item is not None
        }
        return converted
    if isinstance(value, (list, tuple)):
        return [_to_wire(item) for item in value]
    return value
