"""Deterministic implementation of the hardware-independent DAQ port."""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import UTC, datetime
from threading import Event
from typing import TYPE_CHECKING, Protocol

import numpy as np

from labdaq.adapters.simulated.generators import generate_samples
from labdaq.adapters.simulated.scenario import (
    ChannelDelayFault,
    ClippingFault,
    CrosstalkFault,
    DelayFault,
    DisconnectFault,
    DropoutFault,
    Fault,
    FrozenFault,
    NonfiniteFault,
    SimulatorScenario,
    SimulatorTopology,
    SourceIndexFault,
    StaleReadFault,
    validate_scenario_for_experiment,
)
from labdaq.domain.enums import (
    ExperimentMode,
    PhysicalQuantity,
    SamplingMode,
    TerminalConfiguration,
    Unit,
)
from labdaq.domain.timing import divided_sample_rates_hz, rounded_sample_count
from labdaq.domain.validation import validate_experiment_config
from labdaq.errors import ErrorSeverity
from labdaq.ports.daq import (
    DaqCancellationResult,
    DaqConfigurationError,
    DaqConfigurationResult,
    DaqDeviceDescriptor,
    DaqDisconnectedError,
    DaqDiscoveryReport,
    DaqDriverDescriptor,
    DaqEvent,
    DaqHealthSnapshot,
    DaqLifecycleError,
    DaqLifecycleState,
    DaqModuleDescriptor,
    DaqReadCancelledError,
    DaqReadResult,
    DaqReadTimeoutError,
    DaqResetError,
    DaqResetResult,
    DaqSampleLossError,
    DaqSelfTestResult,
    DaqStartResult,
    DaqStopResult,
    DaqValidationIssue,
    DaqValidationReport,
    SimulatorInternalError,
)

if TYPE_CHECKING:
    from labdaq.domain.models import ChannelConfig, ExperimentConfig, HardwareProfile
    from labdaq.ports.daq import DaqPort

BACKEND_ID = "simulated"
ADAPTER_VERSION = "1"
DEVICE_ID = "sim-cdaq-9171"
MODULE_ID = "sim-ni-9239"
DEVICE_ALIAS = "SimDev1"
MODULE_ALIAS = "SimDev1Mod1"
SUPPORTED_RATE_HZ = 50_000.0
INPUT_RANGE_V = (-10.0, 10.0)
DELAY_WARNING_SECONDS = 0.05


class Clock(Protocol):
    def wall_time(self) -> datetime: ...

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...

    def wait(self, cancellation: Event, seconds: float) -> bool: ...


class SystemClock:
    def wall_time(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def wait(self, cancellation: Event, seconds: float) -> bool:
        return cancellation.wait(seconds)


class SimulatedDaqAdapter:
    """One deterministic simulated four-channel voltage stream."""

    def __init__(
        self,
        scenario: SimulatorScenario,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._scenario = scenario
        self._base_scenario = scenario
        self._clock = clock or SystemClock()
        self._state = DaqLifecycleState.IDLE
        self._experiment: ExperimentConfig | None = None
        self._channel_order: tuple[str, ...] = ()
        self._physical_order: tuple[str, ...] = ()
        self._sample_rate_hz: float | None = None
        self._finite_target: int | None = None
        self._next_sample = 0
        self._read_sequence = 0
        self._started_monotonic: float | None = None
        self._device_available = True
        self._last_operation: str | None = None
        self._last_event: DaqEvent | None = None
        self._last_error: str | None = None
        self._emitted_clipping_faults: set[str] = set()
        self._disconnect_fault: DisconnectFault | None = None
        self._cancel_requested = Event()
        self._last_samples: np.ndarray | None = None
        self._last_source_start: int | None = None

    def discover(self) -> DaqDiscoveryReport:
        self._require_not_closed("discover")
        topology = self._scenario.topology
        module = _module_descriptor(topology)
        devices: tuple[DaqDeviceDescriptor, ...] = ()
        if topology.device_present:
            devices = (
                DaqDeviceDescriptor(
                    device_id=DEVICE_ID,
                    vendor="National Instruments",
                    model=topology.device_model,
                    serial_number=topology.device_serial_number,
                    alias=DEVICE_ALIAS,
                    simulated=True,
                    accessible=self._device_available,
                    modules=(module,) if topology.module_present else (),
                ),
            )
        report = DaqDiscoveryReport(
            backend_id=BACKEND_ID,
            discovered_at=self._clock.wall_time(),
            adapter_version=ADAPTER_VERSION,
            devices=devices,
            warnings=(),
            driver=DaqDriverDescriptor(
                provider="LabDAQ",
                package_name=None,
                package_version=None,
                driver_version=None,
            ),
        )
        self._last_operation = "discover"
        return report

    def self_test(self) -> DaqSelfTestResult:
        self._require_not_closed("self_test")
        topology = self._scenario.topology
        supported = self._scenario.self_test_supported
        passed = self._scenario.self_test_passed and topology.device_present if supported else None
        if not supported:
            code = "DAQ.QUALIFICATION.HARDWARE_SELF_TEST_NOT_SUPPORTED"
            severity = ErrorSeverity.WARNING
            message = "The simulated backend reports that self-test is not supported."
        elif passed:
            code = "DAQ.QUALIFICATION.HARDWARE_SELF_TEST_PASSED"
            severity = ErrorSeverity.INFO
            message = "The simulated hardware self-test passed."
        else:
            code = "DAQ.QUALIFICATION.HARDWARE_SELF_TEST_FAILED"
            severity = ErrorSeverity.ERROR
            message = "The simulated hardware self-test failed as requested by the scenario."
        event = self._event(code, severity, "self_test", message, {"source": "simulated"})
        self._last_operation = "self_test"
        self._last_event = event
        return DaqSelfTestResult(
            backend_id=BACKEND_ID,
            device_id=DEVICE_ID if topology.device_present else None,
            supported=supported,
            passed=passed,
            performed_at=event.timestamp,
            message=message,
            events=(event,),
        )

    def validate(
        self,
        hardware_profile: HardwareProfile,
        experiment: ExperimentConfig,
    ) -> DaqValidationReport:
        self._require_not_closed("validate")
        scenario = _scenario_for_experiment(self._base_scenario, experiment)
        issues: list[DaqValidationIssue] = [
            DaqValidationIssue(
                code="DAQ.HARDWARE.PROFILE_MISMATCH",
                field_path=issue.field_path,
                message=issue.rule,
                correction=issue.correction,
            )
            for issue in validate_experiment_config(experiment, hardware_profile)
        ]
        issues.extend(_topology_issues(hardware_profile, self._scenario.topology))
        issues.extend(
            DaqValidationIssue(
                code="DAQ.SIMULATOR.SCENARIO_INVALID",
                field_path=str(issue["field_path"]),
                message=str(issue["message"]),
                correction=str(issue["correction"]),
            )
            for issue in validate_scenario_for_experiment(
                scenario,
                experiment.acquisition.channel_ids,
                experiment.acquisition.sample_rate_hz,
            )
        )
        ordered_physical = _physical_channel_order(experiment)
        report = DaqValidationReport(
            accepted=not issues,
            hardware_profile_id=hardware_profile.hardware_profile_id,
            profile_revision=hardware_profile.profile_revision,
            experiment_config_id=experiment.experiment_config_id,
            config_revision=experiment.config_revision,
            backend_id=BACKEND_ID,
            scenario_id=self._scenario.scenario_id,
            scenario_schema_version=self._scenario.schema_version,
            generator_version=self._scenario.generator_version,
            seed=self._scenario.seed,
            selected_device_id=DEVICE_ID if not issues else None,
            selected_module_id=MODULE_ID if not issues else None,
            ordered_logical_channels=experiment.acquisition.channel_ids,
            ordered_physical_channels=ordered_physical,
            requested_sample_rate_hz=experiment.acquisition.sample_rate_hz,
            supported_input_range_v=(
                self._scenario.topology.input_min_v,
                self._scenario.topology.input_max_v,
            ),
            errors=tuple(issues),
            warnings=(),
        )
        self._last_operation = "validate"
        return report

    def configure(self, experiment: ExperimentConfig) -> DaqConfigurationResult:
        self._require_state(
            "configure",
            (DaqLifecycleState.IDLE, DaqLifecycleState.STOPPED),
        )
        if not self._device_available or not self._scenario.topology.device_present:
            self._raise_disconnected("configure")
        scenario = _scenario_for_experiment(self._base_scenario, experiment)
        issues = _intrinsic_configuration_issues(experiment, scenario)
        if issues:
            event = self._event(
                "DAQ.HARDWARE.CONFIGURATION_UNSUPPORTED",
                ErrorSeverity.ERROR,
                "configure",
                "The experiment is not supported by the simulated DAQ.",
                {"issues": [issue.to_dict() for issue in issues]},
            )
            self._record_error(event)
            raise DaqConfigurationError(
                event.message,
                event=event,
                remediation="Correct the experiment or scenario and configure again.",
            )
        self._scenario = scenario
        self._experiment = experiment
        self._channel_order = experiment.acquisition.channel_ids
        self._physical_order = _physical_channel_order(experiment)
        self._sample_rate_hz = experiment.acquisition.sample_rate_hz
        self._finite_target = _finite_target(experiment)
        self._next_sample = 0
        self._read_sequence = 0
        self._last_samples = None
        self._last_source_start = None
        self._started_monotonic = None
        self._emitted_clipping_faults.clear()
        self._disconnect_fault = None
        self._cancel_requested.clear()
        self._last_error = None
        self._state = DaqLifecycleState.CONFIGURED
        self._last_operation = "configure"
        requested_ranges = tuple(
            (channel.requested_input_min, channel.requested_input_max)
            for channel in _ordered_channels(experiment)
        )
        return DaqConfigurationResult(
            backend_id=BACKEND_ID,
            device_id=DEVICE_ID,
            module_id=MODULE_ID,
            ordered_logical_channels=self._channel_order,
            ordered_physical_channels=self._physical_order,
            requested_sample_rate_hz=self._sample_rate_hz,
            effective_sample_rate_hz=self._sample_rate_hz,
            sampling_mode=experiment.acquisition.sampling_mode.value,
            requested_input_ranges_v=requested_ranges,
            effective_input_ranges_v=(
                (
                    self._scenario.topology.input_min_v,
                    self._scenario.topology.input_max_v,
                ),
            )
            * len(self._channel_order),
            samples_per_read=experiment.acquisition.chunk_config.samples_per_channel,
            warnings=(),
        )

    def start(self) -> DaqStartResult:
        self._require_state("start", (DaqLifecycleState.CONFIGURED,))
        if not self._device_available:
            self._raise_disconnected("start")
        previous = self._state
        self._cancel_requested.clear()
        self._state = DaqLifecycleState.RUNNING
        self._started_monotonic = self._clock.monotonic()
        event = self._event(
            "DAQ.ACQUISITION.STARTED",
            ErrorSeverity.INFO,
            "start",
            "The simulated DAQ stream started.",
            {"source": "simulated", "channel_count": len(self._channel_order)},
        )
        self._record_success("start", event)
        return DaqStartResult(
            backend_id=BACKEND_ID,
            previous_state=previous,
            state=self._state,
            started_at=event.timestamp,
            events=(event,),
        )

    def read(self, samples_per_channel: int, timeout_seconds: float) -> DaqReadResult:
        if self._state is DaqLifecycleState.FAULTED and not self._device_available:
            self._raise_disconnected("read")
        self._require_state("read", (DaqLifecycleState.RUNNING,))
        if (
            isinstance(samples_per_channel, bool)
            or samples_per_channel <= 0
            or not np.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            event = self._event(
                "DAQ.HARDWARE.CONFIGURATION_UNSUPPORTED",
                ErrorSeverity.ERROR,
                "read",
                "Read size and timeout must be positive finite values.",
                {
                    "samples_per_channel": samples_per_channel,
                    "timeout_seconds": timeout_seconds,
                },
            )
            raise DaqConfigurationError(event.message, event=event)
        if not self._device_available:
            self._raise_disconnected("read")
        assert self._sample_rate_hz is not None
        read_sequence = self._read_sequence
        self._read_sequence += 1
        if self._cancel_requested.is_set():
            self._raise_read_cancelled(read_sequence)
        self._raise_read_sequence_disconnect(read_sequence)
        requested = self._remaining_count(samples_per_channel)
        if requested == 0:
            return self._read_result(
                np.empty((len(self._channel_order), 0), dtype=np.float64),
                end_of_stream=True,
                events=(),
                read_sequence=read_sequence,
                source_start_sample_index=self._next_sample,
            )
        self._apply_wait(requested, timeout_seconds, read_sequence)
        boundary = self._nearest_terminal_boundary(requested)
        if boundary == self._next_sample:
            self._raise_terminal_fault()
        if boundary is not None:
            requested = boundary - self._next_sample
        start_sample = self._next_sample
        try:
            samples = generate_samples(
                self._scenario,
                self._channel_order,
                start_sample=start_sample,
                sample_count=requested,
                sample_rate_hz=self._sample_rate_hz,
            )
        except Exception as cause:
            event = self._event(
                "DAQ.SIMULATOR.GENERATOR_FAILURE",
                ErrorSeverity.ERROR,
                "read",
                "The simulated signal generator failed.",
                {"exception_type": type(cause).__name__, "source": "simulated"},
                sample_index=start_sample,
                read_sequence=read_sequence,
            )
            self._state = DaqLifecycleState.FAULTED
            self._record_error(event)
            raise SimulatorInternalError(event.message, event=event, cause=cause) from cause
        events = self._apply_clipping(samples, start_sample, read_sequence)
        samples, source_start_sample_index, stale = self._apply_qualification_faults(
            samples,
            start_sample,
            read_sequence,
        )
        if not stale:
            self._last_samples = np.array(samples, dtype=np.float64, order="C", copy=True)
            self._last_source_start = start_sample
        self._next_sample += requested
        end_of_stream = self._finite_target is not None and self._next_sample >= self._finite_target
        result = self._read_result(
            samples,
            end_of_stream=end_of_stream,
            events=events,
            read_sequence=read_sequence,
            source_start_sample_index=source_start_sample_index,
        )
        self._record_success("read", events[-1] if events else None)
        return result

    def cancel_pending_read(self) -> DaqCancellationResult:
        self._require_not_closed("cancel_pending_read")
        previous: DaqLifecycleState
        if self._state is DaqLifecycleState.CONFIGURED and self._cancel_requested.is_set():
            previous = self._state
        else:
            self._require_state("cancel_pending_read", (DaqLifecycleState.RUNNING,))
            previous = self._state
            self._cancel_requested.set()
        event = self._event(
            "DAQ.ACQUISITION.READ_CANCEL_REQUESTED",
            ErrorSeverity.INFO,
            "cancel_pending_read",
            "Cancellation was requested for the pending simulated read.",
            {"source": "simulated"},
            sample_index=self._next_sample,
            read_sequence=self._read_sequence,
        )
        self._record_success("cancel_pending_read", event)
        return DaqCancellationResult(
            backend_id=BACKEND_ID,
            previous_state=previous,
            state=self._state,
            requested_at=event.timestamp,
            events=(event,),
        )

    def stop(self) -> DaqStopResult:
        self._require_not_closed("stop")
        if self._state not in {
            DaqLifecycleState.CONFIGURED,
            DaqLifecycleState.RUNNING,
            DaqLifecycleState.FAULTED,
            DaqLifecycleState.STOPPED,
        }:
            self._raise_lifecycle("stop", "configured, running, faulted, or stopped")
        previous = self._state
        self._state = DaqLifecycleState.STOPPED
        event = self._event(
            "DAQ.ACQUISITION.STOPPED",
            ErrorSeverity.INFO,
            "stop",
            "The simulated DAQ stream stopped.",
            {"source": "simulated", "samples_emitted_per_channel": self._next_sample},
            sample_index=self._next_sample,
        )
        self._record_success("stop", event)
        return DaqStopResult(
            backend_id=BACKEND_ID,
            previous_state=previous,
            state=self._state,
            stopped_at=event.timestamp,
            samples_emitted_per_channel=self._next_sample,
            events=(event,),
        )

    def health(self) -> DaqHealthSnapshot:
        details: dict[str, object] = {
            "scenario_id": self._scenario.scenario_id,
            "scenario_revision": self._scenario.scenario_revision,
            "seed": self._scenario.seed,
            "generator_version": self._scenario.generator_version,
            "next_sample_index": self._next_sample,
            "next_read_sequence": self._read_sequence,
            "source": "simulated",
        }
        return DaqHealthSnapshot(
            backend_id=BACKEND_ID,
            state=self._state,
            device_available=self._device_available,
            configured=self._experiment is not None
            and self._state
            in {
                DaqLifecycleState.CONFIGURED,
                DaqLifecycleState.RUNNING,
                DaqLifecycleState.FAULTED,
                DaqLifecycleState.STOPPED,
            },
            running=self._state is DaqLifecycleState.RUNNING,
            last_successful_operation=self._last_operation,
            last_event_code=self._last_event.code if self._last_event else None,
            last_error_summary=self._last_error,
            effective_sample_rate_hz=self._sample_rate_hz,
            ordered_channel_count=len(self._channel_order),
            backend_details=details,
        )

    def reset(self) -> DaqResetResult:
        self._require_state(
            "reset",
            (DaqLifecycleState.STOPPED, DaqLifecycleState.FAULTED),
        )
        if not self._device_available and (
            self._disconnect_fault is None or not self._disconnect_fault.reconnectable
        ):
            event = self._event(
                "DAQ.ACQUISITION.RESET_FAILED",
                ErrorSeverity.ERROR,
                "reset",
                "The simulated device is not reconnectable in this scenario.",
                {"scenario_id": self._scenario.scenario_id, "source": "simulated"},
            )
            self._record_error(event)
            raise DaqResetError(
                event.message,
                event=event,
                remediation="Create a new adapter instance or use a reconnectable scenario.",
            )
        previous = self._state
        self._device_available = True
        self._experiment = None
        self._channel_order = ()
        self._physical_order = ()
        self._sample_rate_hz = None
        self._finite_target = None
        self._next_sample = 0
        self._read_sequence = 0
        self._last_samples = None
        self._last_source_start = None
        self._started_monotonic = None
        self._emitted_clipping_faults.clear()
        self._disconnect_fault = None
        self._cancel_requested.clear()
        self._last_error = None
        self._state = DaqLifecycleState.IDLE
        event = self._event(
            "DAQ.ACQUISITION.RESET_COMPLETED",
            ErrorSeverity.INFO,
            "reset",
            "The simulated DAQ adapter reset to idle.",
            {"source": "simulated"},
        )
        self._record_success("reset", event)
        return DaqResetResult(
            backend_id=BACKEND_ID,
            previous_state=previous,
            state=self._state,
            reset_at=event.timestamp,
            events=(event,),
        )

    def close(self) -> None:
        if self._state is DaqLifecycleState.CLOSED:
            return
        self._cancel_requested.set()
        self._state = DaqLifecycleState.CLOSED
        self._experiment = None
        self._channel_order = ()
        self._physical_order = ()
        self._sample_rate_hz = None
        self._finite_target = None
        self._started_monotonic = None
        self._last_operation = "close"

    def __enter__(self) -> DaqPort:
        self._require_not_closed("enter")
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object | None,
    ) -> None:
        self.close()

    def _remaining_count(self, requested: int) -> int:
        if self._finite_target is None:
            return requested
        return min(requested, max(0, self._finite_target - self._next_sample))

    def _apply_wait(self, count: int, timeout: float, read_sequence: int) -> None:
        assert self._sample_rate_hz is not None
        wait_seconds = 0.0
        if self._scenario.pacing_mode == "real_time":
            assert self._started_monotonic is not None
            target = (self._next_sample + count) / self._sample_rate_hz
            elapsed = self._clock.monotonic() - self._started_monotonic
            wait_seconds += max(0.0, target - elapsed)
        delay = self._active_delay(read_sequence)
        if delay is not None:
            wait_seconds += delay.latency_seconds
        if wait_seconds > timeout:
            event = self._event(
                "DAQ.ACQUISITION.READ_TIMEOUT",
                ErrorSeverity.ERROR,
                "read",
                "The simulated read exceeded its timeout.",
                {
                    "timeout_seconds": timeout,
                    "required_wait_seconds": wait_seconds,
                    "source": "simulated",
                },
                sample_index=self._next_sample,
                read_sequence=read_sequence,
            )
            self._state = DaqLifecycleState.FAULTED
            self._record_error(event)
            raise DaqReadTimeoutError(
                event.message,
                event=event,
                remediation="Increase the timeout or correct the delay scenario.",
            )
        if wait_seconds > 0:
            try:
                cancelled = self._clock.wait(self._cancel_requested, wait_seconds)
            except AttributeError:
                # Preserve the pre-M04 injected-clock test seam.
                self._clock.sleep(wait_seconds)
                cancelled = self._cancel_requested.is_set()
            if cancelled:
                self._raise_read_cancelled(read_sequence)

    def _active_delay(self, read_sequence: int) -> DelayFault | None:
        for fault in self._scenario.fault_schedule:
            if not isinstance(fault, DelayFault):
                continue
            if fault.start_read is not None:
                stop = fault.stop_read
                if read_sequence >= fault.start_read and (stop is None or read_sequence < stop):
                    return fault
            elif fault.start_sample is not None:
                stop = fault.stop_sample
                if self._next_sample >= fault.start_sample and (
                    stop is None or self._next_sample < stop
                ):
                    return fault
        return None

    def _nearest_terminal_boundary(self, requested: int) -> int | None:
        end = self._next_sample + requested
        boundaries: list[int] = []
        for fault in self._scenario.fault_schedule:
            boundary: int | None = None
            if isinstance(fault, DropoutFault):
                boundary = fault.start_sample
            elif isinstance(fault, DisconnectFault):
                boundary = fault.sample_index
            if boundary is not None and self._next_sample <= boundary < end:
                boundaries.append(boundary)
        return min(boundaries) if boundaries else None

    def _raise_terminal_fault(self) -> None:
        for fault in self._scenario.fault_schedule:
            if isinstance(fault, DropoutFault) and fault.start_sample == self._next_sample:
                event = self._event(
                    "DAQ.ACQUISITION.SAMPLE_LOSS",
                    ErrorSeverity.ERROR,
                    "read",
                    "The simulator injected a sample dropout.",
                    {
                        "fault_id": fault.fault_id,
                        "missing_samples_per_channel": fault.missing_samples_per_channel,
                        "expected_channel_count": len(self._channel_order),
                        "scenario_id": self._scenario.scenario_id,
                        "source": "simulated",
                    },
                    sample_index=self._next_sample,
                    read_sequence=self._read_sequence - 1,
                )
                self._state = DaqLifecycleState.FAULTED
                self._record_error(event)
                raise DaqSampleLossError(event.message, event=event)
            if isinstance(fault, DisconnectFault) and fault.sample_index == self._next_sample:
                self._disconnect(fault, self._read_sequence - 1)

    def _raise_read_cancelled(self, read_sequence: int) -> None:
        if self._state is not DaqLifecycleState.CLOSED:
            self._state = DaqLifecycleState.CONFIGURED
        event = self._event(
            "DAQ.ACQUISITION.READ_CANCELLED",
            ErrorSeverity.INFO,
            "read",
            "The pending simulated read was cancelled by the application.",
            {"source": "simulated"},
            sample_index=self._next_sample,
            read_sequence=read_sequence,
        )
        self._record_success("cancel_pending_read", event)
        raise DaqReadCancelledError(event.message, event=event)

    def _raise_read_sequence_disconnect(self, read_sequence: int) -> None:
        for fault in self._scenario.fault_schedule:
            if isinstance(fault, DisconnectFault) and fault.read_sequence == read_sequence:
                self._disconnect(fault, read_sequence)

    def _disconnect(self, fault: DisconnectFault, read_sequence: int) -> None:
        self._device_available = False
        self._disconnect_fault = fault
        event = self._event(
            "DAQ.HARDWARE.DEVICE_DISCONNECTED",
            ErrorSeverity.ERROR,
            "read",
            "The simulator injected a device disconnect.",
            {
                "fault_id": fault.fault_id,
                "scenario_id": self._scenario.scenario_id,
                "reconnectable": fault.reconnectable,
                "source": "simulated",
            },
            sample_index=self._next_sample,
            read_sequence=read_sequence,
        )
        self._state = DaqLifecycleState.FAULTED
        self._record_error(event)
        raise DaqDisconnectedError(event.message, event=event)

    def _apply_clipping(
        self,
        samples: np.ndarray,
        start_sample: int,
        read_sequence: int,
    ) -> tuple[DaqEvent, ...]:
        indices = np.arange(start_sample, start_sample + samples.shape[1])
        events: list[DaqEvent] = []
        for fault in self._scenario.fault_schedule:
            if not isinstance(fault, ClippingFault):
                continue
            active = indices >= fault.start_sample
            if fault.stop_sample is not None:
                active &= indices < fault.stop_sample
            if not np.any(active):
                continue
            affected: dict[str, int] = {}
            for channel_id in fault.channel_ids:
                row = self._channel_order.index(channel_id)
                values = samples[row, active]
                clipped = (values < fault.minimum_v) | (values > fault.maximum_v)
                count = int(np.count_nonzero(clipped))
                if count:
                    affected[channel_id] = count
                    samples[row, active] = np.clip(
                        values,
                        fault.minimum_v,
                        fault.maximum_v,
                    )
            if affected and fault.fault_id not in self._emitted_clipping_faults:
                first_offset = int(np.flatnonzero(active)[0])
                event = self._event(
                    "DAQ.ACQUISITION.INPUT_CLIPPED",
                    ErrorSeverity.WARNING,
                    "read",
                    "The simulator clipped one or more input samples.",
                    {
                        "fault_id": fault.fault_id,
                        "channels": affected,
                        "minimum_v": fault.minimum_v,
                        "maximum_v": fault.maximum_v,
                        "source": "simulated",
                    },
                    sample_index=start_sample + first_offset,
                    read_sequence=read_sequence,
                )
                events.append(event)
                self._emitted_clipping_faults.add(fault.fault_id)
        delay = self._active_delay(read_sequence)
        if delay is not None and delay.latency_seconds >= DELAY_WARNING_SECONDS:
            events.insert(
                0,
                self._event(
                    "DAQ.ACQUISITION.READ_DELAYED",
                    ErrorSeverity.WARNING,
                    "read",
                    "The simulator delayed the read.",
                    {
                        "fault_id": delay.fault_id,
                        "latency_seconds": delay.latency_seconds,
                        "warning_threshold_seconds": DELAY_WARNING_SECONDS,
                        "source": "simulated",
                    },
                    sample_index=start_sample,
                    read_sequence=read_sequence,
                ),
            )
        return tuple(events)

    def _apply_qualification_faults(
        self,
        samples: np.ndarray,
        start_sample: int,
        read_sequence: int,
    ) -> tuple[np.ndarray, int, bool]:
        assert self._sample_rate_hz is not None
        output = samples
        source_start = start_sample
        stale = False
        stop_sample = start_sample + samples.shape[1]
        indices = np.arange(start_sample, stop_sample, dtype=np.int64)
        by_channel = {channel_id: row for row, channel_id in enumerate(self._channel_order)}
        for fault in self._scenario.fault_schedule:
            if (
                isinstance(fault, NonfiniteFault)
                and start_sample <= fault.sample_index < stop_sample
            ):
                value = {
                    "nan": np.nan,
                    "positive-infinity": np.inf,
                    "negative-infinity": -np.inf,
                }[fault.value]
                for channel_id in fault.channel_ids:
                    output[by_channel[channel_id], fault.sample_index - start_sample] = value
            elif isinstance(fault, FrozenFault):
                mask = (indices >= fault.start_sample) & (indices < fault.stop_sample)
                for channel_id in fault.channel_ids:
                    output[by_channel[channel_id], mask] = fault.value_v
            elif isinstance(fault, ChannelDelayFault):
                active_stop = fault.stop_sample if fault.stop_sample is not None else stop_sample
                mask = (indices >= fault.start_sample) & (indices < active_stop)
                delayed_indices = indices[mask] - fault.delay_samples
                valid = delayed_indices >= 0
                if not np.any(valid):
                    continue
                for channel_id in fault.channel_ids:
                    delayed = generate_samples(
                        self._scenario,
                        (channel_id,),
                        start_sample=int(delayed_indices[valid][0]),
                        sample_count=int(np.count_nonzero(valid)),
                        sample_rate_hz=self._sample_rate_hz,
                    )[0]
                    positions = np.flatnonzero(mask)[valid]
                    output[by_channel[channel_id], positions] = delayed
            elif isinstance(fault, CrosstalkFault):
                active_stop = fault.stop_sample if fault.stop_sample is not None else stop_sample
                mask = (indices >= fault.start_sample) & (indices < active_stop)
                source = output[by_channel[fault.source_channel_id], mask].copy()
                for channel_id in fault.coupled_channel_ids:
                    output[by_channel[channel_id], mask] += fault.gain * source
            elif isinstance(fault, SourceIndexFault) and fault.read_sequence == read_sequence:
                source_start = max(0, start_sample + fault.offset_samples)
            elif isinstance(fault, StaleReadFault) and fault.read_sequence == read_sequence:
                if self._last_samples is not None and self._last_samples.shape == output.shape:
                    output = self._last_samples.copy(order="C")
                    source_start = self._last_source_start or 0
                    stale = True
        return output, source_start, stale

    def _read_result(
        self,
        samples: np.ndarray,
        *,
        end_of_stream: bool,
        events: tuple[DaqEvent, ...],
        read_sequence: int,
        source_start_sample_index: int,
    ) -> DaqReadResult:
        owned = np.array(samples, dtype=np.float64, order="C", copy=True)
        owned.flags.writeable = False
        assert self._sample_rate_hz is not None
        return DaqReadResult(
            samples=owned,
            samples_read_per_channel=owned.shape[1],
            ordered_logical_channel_ids=self._channel_order,
            actual_sample_rate_hz=self._sample_rate_hz,
            end_of_stream=end_of_stream,
            events=events,
            read_sequence=read_sequence,
            source_start_sample_index=source_start_sample_index,
        )

    def _require_not_closed(self, operation: str) -> None:
        if self._state is DaqLifecycleState.CLOSED:
            self._raise_lifecycle(operation, "an open adapter")

    def _require_state(
        self,
        operation: str,
        allowed: tuple[DaqLifecycleState, ...],
    ) -> None:
        if self._state not in allowed:
            expected = ", ".join(state.value for state in allowed)
            self._raise_lifecycle(operation, expected)

    def _raise_lifecycle(self, operation: str, expected: str) -> None:
        event = self._event(
            "DAQ.ACQUISITION.LIFECYCLE_INVALID",
            ErrorSeverity.ERROR,
            operation,
            f"Operation {operation} is invalid while the adapter is {self._state.value}.",
            {"state": self._state.value, "expected_state": expected, "source": "simulated"},
        )
        self._record_error(event)
        raise DaqLifecycleError(
            event.message,
            event=event,
            remediation="Call DAQ operations in the documented lifecycle order.",
        )

    def _raise_disconnected(self, operation: str) -> None:
        event = self._event(
            "DAQ.HARDWARE.DEVICE_DISCONNECTED",
            ErrorSeverity.ERROR,
            operation,
            "The simulated DAQ device is unavailable.",
            {"scenario_id": self._scenario.scenario_id, "source": "simulated"},
            sample_index=self._next_sample,
            read_sequence=self._read_sequence,
        )
        self._state = DaqLifecycleState.FAULTED
        self._record_error(event)
        raise DaqDisconnectedError(event.message, event=event)

    def _event(
        self,
        code: str,
        severity: ErrorSeverity,
        operation: str,
        message: str,
        context: dict[str, object],
        *,
        sample_index: int | None = None,
        read_sequence: int | None = None,
    ) -> DaqEvent:
        return DaqEvent(
            code=code,
            severity=severity,
            backend_id=BACKEND_ID,
            operation=operation,
            message=message,
            timestamp=self._clock.wall_time(),
            context=context,
            sample_index=sample_index,
            read_sequence=read_sequence,
        )

    def _record_success(self, operation: str, event: DaqEvent | None) -> None:
        self._last_operation = operation
        if event is not None:
            self._last_event = event

    def _record_error(self, event: DaqEvent) -> None:
        self._last_event = event
        self._last_error = event.message


def _module_descriptor(topology: SimulatorTopology) -> DaqModuleDescriptor:
    return DaqModuleDescriptor(
        module_id=MODULE_ID,
        parent_device_id=DEVICE_ID,
        vendor="National Instruments",
        model=topology.module_model,
        slot=0,
        serial_number=topology.module_serial_number,
        available_channels=tuple(f"{MODULE_ALIAS}/ai{index}" for index in range(4)),
        simultaneous_sampling=True,
        supported_measurement_quantities=("voltage",),
        supported_terminal_configurations=("differential",),
        input_range_v=(topology.input_min_v, topology.input_max_v),
        supported_sample_rates_hz=(
            divided_sample_rates_hz(topology.maximum_sample_rate_hz, 31)
            if topology.module_model == "NI-9239"
            else (topology.maximum_sample_rate_hz,)
        ),
    )


def _topology_issues(
    profile: HardwareProfile,
    topology: SimulatorTopology,
) -> list[DaqValidationIssue]:
    issues: list[DaqValidationIssue] = []
    device = profile.devices[0] if len(profile.devices) == 1 else None
    module = profile.modules[0] if len(profile.modules) == 1 else None
    if not topology.device_present:
        issues.append(_mismatch("devices", "Select a scenario with the chassis present."))
    elif device is None or device.model != topology.device_model:
        issues.append(_mismatch("devices", f"Use the discovered {topology.device_model} chassis."))
    elif (
        device.expected_serial_number is not None
        and device.expected_serial_number != topology.device_serial_number
    ):
        issues.append(
            _mismatch("devices[0].expected_serial_number", "Use the discovered chassis serial.")
        )
    if not topology.module_present:
        issues.append(_mismatch("modules", "Select a scenario with the module present."))
    elif module is None or module.model != topology.module_model:
        issues.append(_mismatch("modules", f"Use the discovered {topology.module_model} module."))
    elif (
        module.expected_serial_number is not None
        and module.expected_serial_number != topology.module_serial_number
    ):
        issues.append(
            _mismatch("modules[0].expected_serial_number", "Use the discovered module serial.")
        )
    if profile.required_terminal_configuration is not TerminalConfiguration.DIFFERENTIAL:
        issues.append(
            _mismatch(
                "required_terminal_configuration",
                "Use differential terminal configuration.",
            )
        )
    if not any(
        rate <= topology.maximum_sample_rate_hz
        for rate in profile.application_supported_sample_rates_hz
    ):
        issues.append(
            _mismatch(
                "application_supported_sample_rates_hz",
                "Include 50000 Hz in the profile.",
            )
        )
    if profile.input_min_v < topology.input_min_v or profile.input_max_v > topology.input_max_v:
        issues.append(
            _mismatch(
                "input_range_v",
                f"Use a range within {topology.input_min_v} V to {topology.input_max_v} V.",
            )
        )
    if len(profile.supported_physical_channels) != 4:
        issues.append(_mismatch("supported_physical_channels", "Declare exactly four channels."))
    return issues


def _scenario_for_experiment(
    scenario: SimulatorScenario, experiment: ExperimentConfig
) -> SimulatorScenario:
    """Project scenario inputs by physical index for generic raw-voltage channels.

    Named scientific scenarios keep their historical strict channel matching.
    """
    channels = _ordered_channels(experiment)
    if not channels or any(
        channel.sensor_id is not None
        or channel.channel_id != f"ai{channel.physical_channel.channel_index}"
        for channel in channels
    ):
        return scenario
    if any(
        channel.physical_channel.channel_index >= len(scenario.channel_signals)
        for channel in channels
    ):
        return scenario
    mapping = {
        scenario.channel_signals[
            channel.physical_channel.channel_index
        ].channel_id: channel.channel_id
        for channel in channels
    }
    signals = tuple(
        replace(
            scenario.channel_signals[channel.physical_channel.channel_index],
            channel_id=channel.channel_id,
        )
        for channel in channels
    )
    faults: list[Fault] = []
    for fault in scenario.fault_schedule:
        if isinstance(fault, CrosstalkFault):
            coupled = tuple(mapping[item] for item in fault.coupled_channel_ids if item in mapping)
            if fault.source_channel_id in mapping and coupled:
                faults.append(
                    replace(
                        fault,
                        source_channel_id=mapping[fault.source_channel_id],
                        coupled_channel_ids=coupled,
                    )
                )
        elif isinstance(fault, (ClippingFault, NonfiniteFault, FrozenFault, ChannelDelayFault)):
            selected = tuple(mapping[item] for item in fault.channel_ids if item in mapping)
            if selected:
                faults.append(replace(fault, channel_ids=selected))
        elif isinstance(fault, DropoutFault) and fault.channel_ids is not None:
            selected = tuple(mapping[item] for item in fault.channel_ids if item in mapping)
            if selected:
                faults.append(replace(fault, channel_ids=selected))
        else:
            faults.append(fault)
    return replace(scenario, channel_signals=signals, fault_schedule=tuple(faults))


def _intrinsic_configuration_issues(
    experiment: ExperimentConfig,
    scenario: SimulatorScenario,
) -> tuple[DaqValidationIssue, ...]:
    issues: list[DaqValidationIssue] = []
    topology = scenario.topology
    if not topology.device_present or not topology.module_present:
        issues.append(
            _mismatch(
                "topology",
                "Use a scenario with the required chassis and module present.",
            )
        )
    if experiment.acquisition.sample_rate_hz > topology.maximum_sample_rate_hz:
        issues.append(
            _mismatch(
                "acquisition.sample_rate_hz",
                f"Use at most {topology.maximum_sample_rate_hz:g} Hz.",
            )
        )
    channels = _ordered_channels(experiment)
    expected_counts = (
        {1, 2, 3, 4}
        if all(channel.sensor_id is None for channel in channels)
        else {3, 4}
        if experiment.experiment_mode is ExperimentMode.ACCELEROMETER_COMMISSIONING
        else {4}
    )
    if len(channels) not in expected_counts:
        expected = (
            "1 to 4"
            if len(expected_counts) == 4
            else "3 or 4"
            if len(expected_counts) == 2
            else "4"
        )
        issues.append(_mismatch("channels", f"Enable exactly {expected} channels for this mode."))
    for index, channel in enumerate(channels):
        if (
            channel.raw_quantity is not PhysicalQuantity.VOLTAGE
            or channel.raw_unit is not Unit.VOLT
        ):
            issues.append(_mismatch(f"channels[{index}]", "Use raw voltage channels in volts."))
        if (
            channel.physical_channel.terminal_configuration
            is not TerminalConfiguration.DIFFERENTIAL
        ):
            issues.append(
                _mismatch(
                    f"channels[{index}].physical_channel.terminal_configuration",
                    "Use differential terminal configuration.",
                )
            )
        if (
            channel.requested_input_min < topology.input_min_v
            or channel.requested_input_max > topology.input_max_v
        ):
            issues.append(
                _mismatch(
                    f"channels[{index}].requested_input_range",
                    "Use a requested range within the scenario capability.",
                )
            )
    issues.extend(
        DaqValidationIssue(
            code="DAQ.SIMULATOR.SCENARIO_INVALID",
            field_path=str(issue["field_path"]),
            message=str(issue["message"]),
            correction=str(issue["correction"]),
        )
        for issue in validate_scenario_for_experiment(
            scenario,
            experiment.acquisition.channel_ids,
            experiment.acquisition.sample_rate_hz,
        )
    )
    return tuple(issues)


def _mismatch(path: str, correction: str) -> DaqValidationIssue:
    return DaqValidationIssue(
        code="DAQ.HARDWARE.PROFILE_MISMATCH",
        field_path=path,
        message="The requested value is incompatible with the simulated topology.",
        correction=correction,
    )


def _ordered_channels(experiment: ExperimentConfig) -> tuple[ChannelConfig, ...]:
    by_id = {channel.channel_id: channel for channel in experiment.channels}
    return tuple(by_id[channel_id] for channel_id in experiment.acquisition.channel_ids)


def _physical_channel_order(experiment: ExperimentConfig) -> tuple[str, ...]:
    channels = _ordered_channels(experiment)
    return tuple(
        f"{MODULE_ALIAS}/ai{channel.physical_channel.channel_index}" for channel in channels
    )


def _finite_target(experiment: ExperimentConfig) -> int | None:
    acquisition = experiment.acquisition
    if acquisition.sampling_mode is SamplingMode.CONTINUOUS:
        return None
    assert acquisition.requested_duration_seconds is not None
    return rounded_sample_count(
        acquisition.sample_rate_hz,
        acquisition.requested_duration_seconds,
    )
