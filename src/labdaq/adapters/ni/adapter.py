"""NI-DAQmx implementation of the hardware-independent DAQ port."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from threading import Event
from typing import TYPE_CHECKING

import numpy as np

from labdaq.domain.enums import PhysicalQuantity, SamplingMode, TerminalConfiguration, Unit
from labdaq.domain.timing import divided_sample_rates_hz, rates_equivalent, rounded_sample_count
from labdaq.domain.validation import validate_experiment_config
from labdaq.errors import ErrorSeverity
from labdaq.ports.daq import (
    DaqCancellationResult,
    DaqCleanupError,
    DaqConfigurationError,
    DaqConfigurationResult,
    DaqDeviceDescriptor,
    DaqDisconnectedError,
    DaqDiscoveryReport,
    DaqDriverDescriptor,
    DaqDriverUnavailableError,
    DaqError,
    DaqEvent,
    DaqHealthSnapshot,
    DaqLifecycleError,
    DaqLifecycleState,
    DaqMalformedReadError,
    DaqModuleDescriptor,
    DaqReadCancelledError,
    DaqReadError,
    DaqReadResult,
    DaqReadTimeoutError,
    DaqResetError,
    DaqResetResult,
    DaqResourceReservedError,
    DaqSelfTestResult,
    DaqStartError,
    DaqStartResult,
    DaqStopError,
    DaqStopResult,
    DaqValidationIssue,
    DaqValidationReport,
    DaqVerificationError,
)

if TYPE_CHECKING:
    from labdaq.adapters.ni.driver import (
        NiDeviceSnapshot,
        NiDriver,
        NiModuleSnapshot,
        NiSystemSnapshot,
        NiTask,
    )
    from labdaq.domain.models import ChannelConfig, ExperimentConfig, HardwareProfile
    from labdaq.ports.daq import DaqPort

BACKEND_ID = "ni"
ADAPTER_VERSION = "1"
RESOURCE_RESERVED_CODES = {-50103}
READ_TIMEOUT_CODES = {-200284}
DEVICE_UNAVAILABLE_CODES = {-201003, -200220, -88710, -88709, -88708}


class NIDaqmxAdapter:
    """Own one physical NI task and expose only backend-neutral records."""

    def __init__(self, driver: NiDriver, *, rate_tolerance_hz: float = 0.0) -> None:
        if not math.isfinite(rate_tolerance_hz) or rate_tolerance_hz < 0:
            raise ValueError("rate_tolerance_hz must be finite and non-negative")
        self._driver = driver
        self._rate_tolerance_hz = rate_tolerance_hz
        self._state = DaqLifecycleState.IDLE
        self._system: NiSystemSnapshot | None = None
        self._selected_device: NiDeviceSnapshot | None = None
        self._selected_module: NiModuleSnapshot | None = None
        self._validated_experiment: ExperimentConfig | None = None
        self._task: NiTask | None = None
        self._buffer: np.ndarray | None = None
        self._channel_order: tuple[str, ...] = ()
        self._physical_order: tuple[str, ...] = ()
        self._sample_rate_hz: float | None = None
        self._finite_target: int | None = None
        self._samples_read = 0
        self._read_sequence = 0
        self._device_available = False
        self._last_operation: str | None = None
        self._last_event: DaqEvent | None = None
        self._last_error: str | None = None
        self._cancel_requested = Event()

    def discover(self) -> DaqDiscoveryReport:
        self._require_not_closed("discover")
        try:
            system = self._driver.discover()
        except Exception as cause:
            raise self._translate(cause, "discover") from cause
        self._system = system
        self._device_available = bool(system.devices)
        devices = tuple(self._device_descriptor(device) for device in system.devices)
        self._record_success("discover")
        return DaqDiscoveryReport(
            backend_id=BACKEND_ID,
            discovered_at=datetime.now(UTC),
            adapter_version=ADAPTER_VERSION,
            devices=devices,
            warnings=(),
            driver=DaqDriverDescriptor(
                provider="National Instruments",
                package_name="nidaqmx",
                package_version=system.package_version,
                driver_version=system.driver_version,
            ),
        )

    def self_test(self) -> DaqSelfTestResult:
        self._require_not_closed("self_test")
        if self._selected_device is None:
            message = "Live validation must select a device before NI self-test."
            event = self._event(
                "DAQ.QUALIFICATION.HARDWARE_SELF_TEST_FAILED",
                ErrorSeverity.ERROR,
                "self_test",
                message,
                {"source": "ni", "reason": "device-not-selected"},
            )
            return DaqSelfTestResult(
                backend_id=BACKEND_ID,
                device_id=None,
                supported=True,
                passed=False,
                performed_at=event.timestamp,
                message=message,
                events=(event,),
            )
        alias = self._selected_device.alias
        driver_self_test = getattr(self._driver, "self_test_device", None)
        if driver_self_test is None:
            supported = False
            passed: bool | None = None
            message = "The installed NI driver wrapper does not expose device self-test."
            code = "DAQ.QUALIFICATION.HARDWARE_SELF_TEST_NOT_SUPPORTED"
            severity = ErrorSeverity.WARNING
        else:
            try:
                snapshot = driver_self_test(alias)
            except Exception as cause:
                event = self._event(
                    "DAQ.QUALIFICATION.HARDWARE_SELF_TEST_FAILED",
                    ErrorSeverity.ERROR,
                    "self_test",
                    "The NI device self-test failed.",
                    {
                        "source": "ni",
                        "device_alias": alias,
                        "driver_error_code": getattr(cause, "error_code", None),
                        "driver_error_type": type(cause).__name__,
                    },
                )
                self._last_event = event
                self._last_error = str(cause)
                return DaqSelfTestResult(
                    backend_id=BACKEND_ID,
                    device_id=alias,
                    supported=True,
                    passed=False,
                    performed_at=event.timestamp,
                    message=event.message,
                    events=(event,),
                )
            supported = bool(snapshot.supported)
            passed = True if supported else None
            message = (
                "The NI device self-test passed."
                if supported
                else "The NI device or driver does not support self-test."
            )
            code = (
                "DAQ.QUALIFICATION.HARDWARE_SELF_TEST_PASSED"
                if supported
                else "DAQ.QUALIFICATION.HARDWARE_SELF_TEST_NOT_SUPPORTED"
            )
            severity = ErrorSeverity.INFO if supported else ErrorSeverity.WARNING
        event = self._event(
            code,
            severity,
            "self_test",
            message,
            {"source": "ni", "device_alias": alias},
        )
        self._record_success("self_test", event)
        return DaqSelfTestResult(
            backend_id=BACKEND_ID,
            device_id=alias,
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
        if self._system is None:
            self.discover()
        assert self._system is not None
        issues = [
            DaqValidationIssue(
                code="DAQ.HARDWARE.PROFILE_MISMATCH",
                field_path=issue.field_path,
                message=issue.rule,
                correction=issue.correction,
            )
            for issue in validate_experiment_config(experiment, hardware_profile)
        ]
        topology_issues, device, module = _topology_issues(self._system, hardware_profile)
        issues.extend(topology_issues)
        ordered_physical: tuple[str, ...] = ()
        if module is not None:
            configuration_issues, ordered_physical = _configuration_issues(
                module,
                hardware_profile,
                experiment,
            )
            issues.extend(configuration_issues)
        accepted = not issues and device is not None and module is not None
        if accepted:
            self._selected_device = device
            self._selected_module = module
            self._validated_experiment = experiment
            self._device_available = True
        else:
            self._selected_device = None
            self._selected_module = None
            self._validated_experiment = None
        self._last_operation = "validate"
        return DaqValidationReport(
            accepted=accepted,
            hardware_profile_id=hardware_profile.hardware_profile_id,
            profile_revision=hardware_profile.profile_revision,
            experiment_config_id=experiment.experiment_config_id,
            config_revision=experiment.config_revision,
            backend_id=BACKEND_ID,
            scenario_id=None,
            scenario_schema_version=None,
            generator_version=None,
            seed=None,
            selected_device_id=_device_id(device) if accepted and device else None,
            selected_module_id=_module_id(module) if accepted and module else None,
            ordered_logical_channels=experiment.acquisition.channel_ids,
            ordered_physical_channels=ordered_physical,
            requested_sample_rate_hz=experiment.acquisition.sample_rate_hz,
            supported_input_range_v=module.input_range_v if module else None,
            errors=tuple(issues),
            warnings=(),
        )

    def configure(self, experiment: ExperimentConfig) -> DaqConfigurationResult:
        self._require_state("configure", (DaqLifecycleState.IDLE, DaqLifecycleState.STOPPED))
        if self._validated_experiment != experiment:
            raise self._configuration_error(
                "The NI configuration must match a previously accepted validation report.",
                {"experiment_config_id": experiment.experiment_config_id},
                "Call validate with the hardware profile before configuring the task.",
            )
        assert self._selected_device is not None
        assert self._selected_module is not None
        self._clear_runtime()
        task: NiTask | None = None
        operation = "configure"
        try:
            task = self._driver.create_task()
            channels = _ordered_channels(experiment)
            physical_order = tuple(
                f"{self._selected_module.alias}/ai{channel.physical_channel.channel_index}"
                for channel in channels
            )
            effective_ranges = tuple(
                task.add_voltage_channel(
                    physical,
                    channel.requested_input_min,
                    channel.requested_input_max,
                )
                for physical, channel in zip(physical_order, channels, strict=True)
            )
            finite_target = _finite_target(experiment)
            timing_target = (
                finite_target
                if finite_target is not None
                else experiment.acquisition.chunk_config.samples_per_channel
            )
            task.configure_timing(
                experiment.acquisition.sample_rate_hz,
                experiment.acquisition.sampling_mode.value,
                timing_target,
            )
            operation = "verify"
            actual_rate = task.verify_and_commit()
            effective_ranges = task.effective_input_ranges()
            _check_range_count(effective_ranges, len(channels))
            _check_rate(
                experiment.acquisition.sample_rate_hz,
                actual_rate,
                self._rate_tolerance_hz,
            )
        except Exception as cause:
            cleanup_errors = _release_task(task, stop_first=False)
            self._state = DaqLifecycleState.FAULTED
            error = self._translate(
                cause,
                operation,
                cleanup_errors=cleanup_errors,
            )
            self._record_error(error.event)
            raise error from cause
        warnings: tuple[DaqEvent, ...] = ()
        driver_warnings = self._warning_events(task, "configure")
        if not rates_equivalent(actual_rate, experiment.acquisition.sample_rate_hz):
            warning = self._event(
                "DAQ.HARDWARE.RATE_COERCED",
                ErrorSeverity.WARNING,
                "configure",
                "NI-DAQmx selected a sample rate within the configured tolerance.",
                {
                    "requested_sample_rate_hz": experiment.acquisition.sample_rate_hz,
                    "actual_sample_rate_hz": actual_rate,
                    "tolerance_hz": self._rate_tolerance_hz,
                },
            )
            warnings = (*driver_warnings, warning)
        else:
            warnings = driver_warnings
        self._task = task
        self._channel_order = experiment.acquisition.channel_ids
        self._physical_order = physical_order
        self._sample_rate_hz = actual_rate
        self._finite_target = finite_target
        self._samples_read = 0
        self._read_sequence = 0
        self._cancel_requested.clear()
        self._buffer = np.empty(
            (len(self._channel_order), experiment.acquisition.chunk_config.samples_per_channel),
            dtype=np.float64,
        )
        self._state = DaqLifecycleState.CONFIGURED
        self._last_error = None
        self._record_success("configure")
        requested_ranges = tuple(
            (channel.requested_input_min, channel.requested_input_max) for channel in channels
        )
        return DaqConfigurationResult(
            backend_id=BACKEND_ID,
            device_id=_device_id(self._selected_device),
            module_id=_module_id(self._selected_module),
            ordered_logical_channels=self._channel_order,
            ordered_physical_channels=self._physical_order,
            requested_sample_rate_hz=experiment.acquisition.sample_rate_hz,
            effective_sample_rate_hz=actual_rate,
            sampling_mode=experiment.acquisition.sampling_mode.value,
            requested_input_ranges_v=requested_ranges,
            effective_input_ranges_v=effective_ranges,
            samples_per_read=experiment.acquisition.chunk_config.samples_per_channel,
            warnings=warnings,
        )

    def start(self) -> DaqStartResult:
        self._require_state("start", (DaqLifecycleState.CONFIGURED,))
        assert self._task is not None
        previous = self._state
        self._cancel_requested.clear()
        try:
            self._task.start()
        except Exception as cause:
            cleanup_errors = _release_task(self._task, stop_first=False)
            self._task = None
            self._state = DaqLifecycleState.FAULTED
            error = self._translate(cause, "start", cleanup_errors=cleanup_errors)
            self._record_error(error.event)
            raise error from cause
        self._state = DaqLifecycleState.RUNNING
        driver_warnings = self._warning_events(self._task, "start")
        event = self._event(
            "DAQ.ACQUISITION.STARTED",
            ErrorSeverity.INFO,
            "start",
            "The NI-DAQmx task started.",
            {"channel_count": len(self._channel_order), "source": "ni"},
        )
        self._record_success("start", event)
        return DaqStartResult(
            backend_id=BACKEND_ID,
            previous_state=previous,
            state=self._state,
            started_at=event.timestamp,
            events=(event, *driver_warnings),
        )

    def read(self, samples_per_channel: int, timeout_seconds: float) -> DaqReadResult:
        self._require_state("read", (DaqLifecycleState.RUNNING,))
        if (
            isinstance(samples_per_channel, bool)
            or samples_per_channel <= 0
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise self._configuration_error(
                "Read size and timeout must be positive finite values.",
                {
                    "samples_per_channel": samples_per_channel,
                    "timeout_seconds": timeout_seconds,
                },
                "Use a positive read size and finite timeout.",
            )
        assert self._task is not None
        assert self._sample_rate_hz is not None
        requested = self._remaining_count(samples_per_channel)
        read_sequence = self._read_sequence
        self._read_sequence += 1
        if requested == 0:
            return self._read_result(
                np.empty((len(self._channel_order), 0), dtype=np.float64),
                end_of_stream=True,
                events=(),
                read_sequence=read_sequence,
                source_start_sample_index=self._samples_read,
            )
        source_start_sample_index = self._samples_read
        buffer = self._ensure_buffer(requested)
        try:
            count = self._task.read(buffer, requested, timeout_seconds)
        except Exception as cause:
            if self._cancel_requested.is_set():
                if self._state is not DaqLifecycleState.CLOSED:
                    self._state = DaqLifecycleState.CONFIGURED
                event = self._event(
                    "DAQ.ACQUISITION.READ_CANCELLED",
                    ErrorSeverity.INFO,
                    "read",
                    "The pending NI-DAQmx read was cancelled by the application.",
                    {"source": "ni"},
                    read_sequence=read_sequence,
                )
                self._record_success("cancel_pending_read", event)
                raise DaqReadCancelledError(event.message, event=event, cause=cause) from cause
            cleanup_errors = _release_task(self._task, stop_first=True)
            self._task = None
            self._buffer = None
            self._state = DaqLifecycleState.FAULTED
            error = self._translate(cause, "read", cleanup_errors=cleanup_errors)
            if isinstance(error, DaqDisconnectedError):
                self._device_available = False
            self._record_error(error.event)
            raise error from cause
        if count < 0 or count > requested:
            malformed_error = ValueError(
                f"NI-DAQmx returned invalid sample count {count} for {requested}"
            )
            cleanup_errors = _release_task(self._task, stop_first=True)
            self._task = None
            self._buffer = None
            self._state = DaqLifecycleState.FAULTED
            error = self._translate(
                malformed_error,
                "malformed_read",
                cleanup_errors=cleanup_errors,
            )
            self._record_error(error.event)
            raise error from malformed_error
        samples = np.array(buffer[:, :count], dtype=np.float64, order="C", copy=True)
        if not np.all(np.isfinite(samples)):
            malformed_error = ValueError("NI-DAQmx returned non-finite voltage samples")
            cleanup_errors = _release_task(self._task, stop_first=True)
            self._task = None
            self._buffer = None
            self._state = DaqLifecycleState.FAULTED
            error = self._translate(
                malformed_error,
                "malformed_read",
                cleanup_errors=cleanup_errors,
            )
            self._record_error(error.event)
            raise error from malformed_error
        samples.flags.writeable = False
        self._samples_read += count
        events = self._warning_events(self._task, "read", read_sequence=read_sequence)
        if count != requested:
            events = (
                *events,
                self._event(
                    "DAQ.ACQUISITION.PARTIAL_READ",
                    ErrorSeverity.WARNING,
                    "read",
                    "NI-DAQmx returned fewer samples than requested.",
                    {"requested_samples": requested, "returned_samples": count},
                    read_sequence=read_sequence,
                ),
            )
        end_of_stream = (
            self._finite_target is not None and self._samples_read >= self._finite_target
        )
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
            assert self._task is not None
            try:
                self._task.abort()
            except Exception as cause:
                self._state = DaqLifecycleState.FAULTED
                error = self._translate(cause, "stop")
                self._record_error(error.event)
                raise error from cause
            self._state = DaqLifecycleState.CONFIGURED
        event = self._event(
            "DAQ.ACQUISITION.READ_CANCEL_REQUESTED",
            ErrorSeverity.INFO,
            "cancel_pending_read",
            "Cancellation was requested for the pending NI-DAQmx read.",
            {"source": "ni"},
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
        task = self._task
        driver_warnings: tuple[DaqEvent, ...] = ()
        if self._state is not DaqLifecycleState.STOPPED:
            errors = _release_task(
                self._task,
                stop_first=self._state is DaqLifecycleState.RUNNING,
            )
            self._task = None
            self._buffer = None
            driver_warnings = self._warning_events(task, "stop")
            if errors:
                self._state = DaqLifecycleState.FAULTED
                operation = (
                    "stop" if len(errors) > 1 or previous is DaqLifecycleState.RUNNING else "close"
                )
                error = self._translate(errors[0], operation, cleanup_errors=errors[1:])
                self._record_error(error.event)
                raise error from errors[0]
        self._state = DaqLifecycleState.STOPPED
        event = self._event(
            "DAQ.ACQUISITION.STOPPED",
            ErrorSeverity.INFO,
            "stop",
            "The NI-DAQmx task stopped and released its resources.",
            {"samples_read_per_channel": self._samples_read, "source": "ni"},
        )
        self._record_success("stop", event)
        return DaqStopResult(
            backend_id=BACKEND_ID,
            previous_state=previous,
            state=self._state,
            stopped_at=event.timestamp,
            samples_emitted_per_channel=self._samples_read,
            events=(event, *driver_warnings),
        )

    def health(self) -> DaqHealthSnapshot:
        system = self._system
        details: dict[str, object] = {
            "source": "ni",
            "package_version": system.package_version if system else None,
            "driver_version": system.driver_version if system else None,
            "device_alias": self._selected_device.alias if self._selected_device else None,
            "module_alias": self._selected_module.alias if self._selected_module else None,
            "ordered_physical_channels": list(self._physical_order),
            "samples_read_per_channel": self._samples_read,
            "next_read_sequence": self._read_sequence,
        }
        return DaqHealthSnapshot(
            backend_id=BACKEND_ID,
            state=self._state,
            device_available=self._device_available,
            configured=self._validated_experiment is not None
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
        self._require_state("reset", (DaqLifecycleState.STOPPED, DaqLifecycleState.FAULTED))
        if self._selected_device is None:
            raise self._configuration_error(
                "No selected NI device is available for reset.",
                {},
                "Discover and validate the selected device first.",
            )
        previous = self._state
        errors = _release_task(self._task, stop_first=False)
        self._task = None
        self._buffer = None
        if errors:
            error = self._translate(errors[0], "close", cleanup_errors=errors[1:])
            self._record_error(error.event)
            raise error from errors[0]
        try:
            self._driver.reset_device(self._selected_device.alias)
        except Exception as cause:
            self._state = DaqLifecycleState.FAULTED
            error = self._translate(cause, "reset")
            self._record_error(error.event)
            raise error from cause
        self._state = DaqLifecycleState.IDLE
        self._selected_device = None
        self._selected_module = None
        self._validated_experiment = None
        self._clear_runtime()
        self._device_available = True
        self._cancel_requested.clear()
        event = self._event(
            "DAQ.ACQUISITION.RESET_COMPLETED",
            ErrorSeverity.INFO,
            "reset",
            "The explicitly selected NI device was reset.",
            {"source": "ni"},
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
        task = self._task
        errors = _release_task(
            task,
            stop_first=self._state is DaqLifecycleState.RUNNING,
        )
        driver_warnings = self._warning_events(task, "close")
        self._task = None
        self._buffer = None
        self._state = DaqLifecycleState.CLOSED
        self._last_operation = "close"
        if errors:
            error = self._translate(errors[0], "close", cleanup_errors=errors[1:])
            self._record_error(error.event)
            raise error from errors[0]
        if driver_warnings:
            self._last_event = driver_warnings[-1]

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

    def _device_descriptor(self, device: NiDeviceSnapshot) -> DaqDeviceDescriptor:
        return DaqDeviceDescriptor(
            device_id=_device_id(device),
            vendor="National Instruments",
            model=_canonical_model(device.product_type),
            serial_number=device.serial_number,
            alias=device.alias,
            simulated=device.simulated,
            accessible=True,
            modules=tuple(self._module_descriptor(module) for module in device.modules),
            reported_model=device.product_type,
            product_category=device.product_category,
        )

    def _module_descriptor(self, module: NiModuleSnapshot) -> DaqModuleDescriptor:
        return DaqModuleDescriptor(
            module_id=_module_id(module),
            parent_device_id=_device_id_by_alias(self._system, module.chassis_alias),
            vendor="National Instruments",
            model=_canonical_model(module.product_type),
            slot=module.reported_slot - 1,
            serial_number=module.serial_number,
            available_channels=module.physical_channels,
            simultaneous_sampling=True,
            supported_measurement_quantities=module.measurement_quantities,
            supported_terminal_configurations=module.terminal_configurations,
            input_range_v=module.input_range_v,
            supported_sample_rates_hz=(
                divided_sample_rates_hz(module.maximum_sample_rate_hz, 31)
                if _canonical_model(module.product_type) == "NI-9239"
                else (module.maximum_sample_rate_hz,)
            ),
            reported_model=module.product_type,
            reported_slot=module.reported_slot,
            product_category=module.product_category,
        )

    def _ensure_buffer(self, samples_per_channel: int) -> np.ndarray:
        shape = (len(self._channel_order), samples_per_channel)
        if self._buffer is None or self._buffer.shape != shape:
            self._buffer = np.empty(shape, dtype=np.float64)
        return self._buffer

    def _remaining_count(self, requested: int) -> int:
        if self._finite_target is None:
            return requested
        return min(requested, max(0, self._finite_target - self._samples_read))

    def _read_result(
        self,
        samples: np.ndarray,
        *,
        end_of_stream: bool,
        events: tuple[DaqEvent, ...],
        read_sequence: int,
        source_start_sample_index: int,
    ) -> DaqReadResult:
        assert self._sample_rate_hz is not None
        samples.flags.writeable = False
        return DaqReadResult(
            samples=samples,
            samples_read_per_channel=samples.shape[1],
            ordered_logical_channel_ids=self._channel_order,
            actual_sample_rate_hz=self._sample_rate_hz,
            end_of_stream=end_of_stream,
            events=events,
            read_sequence=read_sequence,
            source_start_sample_index=source_start_sample_index,
        )

    def _clear_runtime(self) -> None:
        self._task = None
        self._buffer = None
        self._channel_order = ()
        self._physical_order = ()
        self._sample_rate_hz = None
        self._finite_target = None
        self._samples_read = 0
        self._read_sequence = 0

    def _configuration_error(
        self,
        message: str,
        context: dict[str, object],
        remediation: str,
    ) -> DaqConfigurationError:
        event = self._event(
            "DAQ.HARDWARE.CONFIGURATION_UNSUPPORTED",
            ErrorSeverity.ERROR,
            "configure",
            message,
            context,
        )
        return DaqConfigurationError(message, event=event, remediation=remediation)

    def _warning_events(
        self,
        task: NiTask | None,
        operation: str,
        *,
        read_sequence: int | None = None,
    ) -> tuple[DaqEvent, ...]:
        if task is None:
            return ()
        return tuple(
            self._event(
                "DAQ.HARDWARE.DRIVER_WARNING",
                ErrorSeverity.WARNING,
                operation,
                "NI-DAQmx reported a driver warning.",
                {
                    "warning_type": warning.warning_type,
                    "ni_warning_code": warning.error_code,
                    "ni_message": warning.message,
                },
                read_sequence=read_sequence,
            )
            for warning in task.pop_warnings()
        )

    def _translate(
        self,
        cause: BaseException,
        operation: str,
        *,
        cleanup_errors: tuple[BaseException, ...] = (),
    ) -> DaqError:
        error_code = getattr(cause, "error_code", None)
        message = str(cause)
        folded = message.casefold()
        context: dict[str, object] = {
            "operation": operation,
            "ni_error_code": error_code,
            "ni_message": message,
            "state": self._state.value,
            "device_alias": self._selected_device.alias if self._selected_device else None,
            "module_alias": self._selected_module.alias if self._selected_module else None,
            "requested_rate_hz": (
                self._validated_experiment.acquisition.sample_rate_hz
                if self._validated_experiment
                else None
            ),
            "channel_count": len(self._channel_order),
            "cleanup_errors": [
                {
                    "type": type(error).__name__,
                    "ni_error_code": getattr(error, "error_code", None),
                    "message": str(error),
                }
                for error in cleanup_errors
            ],
        }
        if operation == "discover":
            code = "DAQ.HARDWARE.DRIVER_UNAVAILABLE"
            cls: type[DaqError] = DaqDriverUnavailableError
            remediation = "Verify the NI-DAQmx runtime and NI services, then retry discovery."
        elif error_code in RESOURCE_RESERVED_CODES or "reserved" in folded:
            code = "DAQ.HARDWARE.RESOURCE_RESERVED"
            cls = DaqResourceReservedError
            remediation = "Close other NI applications or tasks that own the device."
        elif (
            error_code in READ_TIMEOUT_CODES
            or "samples are not yet available" in folded
            or "timeout" in folded
        ):
            code = "DAQ.ACQUISITION.READ_TIMEOUT"
            cls = DaqReadTimeoutError
            remediation = "Check timing and wiring or use a longer finite read timeout."
        elif error_code in DEVICE_UNAVAILABLE_CODES or any(
            text in folded for text in ("disconnected", "removed", "not present")
        ):
            code = "DAQ.HARDWARE.DEVICE_DISCONNECTED"
            cls = DaqDisconnectedError
            remediation = "Reconnect the selected chassis and explicitly rediscover it."
        elif operation == "verify":
            code = "DAQ.HARDWARE.TASK_VERIFICATION_FAILED"
            cls = DaqVerificationError
            remediation = "Correct the channel, range, timing, or rate configuration."
        elif operation == "start":
            code = "DAQ.ACQUISITION.START_FAILED"
            cls = DaqStartError
            remediation = "Verify device availability and that no other task owns it."
        elif operation == "read":
            code = "DAQ.ACQUISITION.READ_FAILED"
            cls = DaqReadError
            remediation = "Inspect NI diagnostics and retry with a fresh adapter instance."
        elif operation == "malformed_read":
            code = "DAQ.ACQUISITION.MALFORMED_READ"
            cls = DaqMalformedReadError
            remediation = "Do not use the returned data; inspect the driver and hardware."
        elif operation == "stop":
            code = "DAQ.ACQUISITION.STOP_FAILED"
            cls = DaqStopError
            remediation = "Close the adapter and verify that NI released the task."
        elif operation == "close":
            code = "DAQ.ACQUISITION.CLEANUP_FAILED"
            cls = DaqCleanupError
            remediation = "Verify resource release in NI-MAX before another acquisition."
        elif operation == "reset":
            code = "DAQ.ACQUISITION.RESET_FAILED"
            cls = DaqResetError
            remediation = "Rediscover the selected device and retry only when safe."
        else:
            code = "DAQ.HARDWARE.CONFIGURATION_UNSUPPORTED"
            cls = DaqConfigurationError
            remediation = "Correct the hardware configuration and retry."
        event = self._event(
            code,
            ErrorSeverity.ERROR,
            operation,
            f"NI-DAQmx operation failed: {operation}.",
            context,
        )
        return cls(event.message, event=event, remediation=remediation, cause=cause)

    def _event(
        self,
        code: str,
        severity: ErrorSeverity,
        operation: str,
        message: str,
        context: dict[str, object],
        *,
        read_sequence: int | None = None,
    ) -> DaqEvent:
        return DaqEvent(
            code=code,
            severity=severity,
            backend_id=BACKEND_ID,
            operation=operation,
            message=message,
            timestamp=datetime.now(UTC),
            context=context,
            read_sequence=read_sequence,
        )

    def _record_success(self, operation: str, event: DaqEvent | None = None) -> None:
        self._last_operation = operation
        if event is not None:
            self._last_event = event

    def _record_error(self, event: DaqEvent) -> None:
        self._last_event = event
        self._last_error = event.message

    def _require_not_closed(self, operation: str) -> None:
        if self._state is DaqLifecycleState.CLOSED:
            self._raise_lifecycle(operation, "an open adapter")

    def _require_state(
        self,
        operation: str,
        allowed: tuple[DaqLifecycleState, ...],
    ) -> None:
        if self._state not in allowed:
            self._raise_lifecycle(operation, ", ".join(state.value for state in allowed))

    def _raise_lifecycle(self, operation: str, expected: str) -> None:
        event = self._event(
            "DAQ.ACQUISITION.LIFECYCLE_INVALID",
            ErrorSeverity.ERROR,
            operation,
            f"Operation {operation} is invalid while the NI adapter is {self._state.value}.",
            {"state": self._state.value, "expected_state": expected, "source": "ni"},
        )
        self._record_error(event)
        raise DaqLifecycleError(
            event.message,
            event=event,
            remediation="Call DAQ operations in the documented lifecycle order.",
        )


def _release_task(task: NiTask | None, *, stop_first: bool) -> tuple[BaseException, ...]:
    if task is None:
        return ()
    errors: list[BaseException] = []
    if stop_first:
        try:
            task.stop()
        except Exception as error:
            errors.append(error)
    try:
        task.close()
    except Exception as error:
        errors.append(error)
    return tuple(errors)


def _check_rate(requested: float, actual: float, tolerance: float) -> None:
    if not rates_equivalent(requested, actual):
        raise ValueError(
            "NI-DAQmx coerced the sample rate to another discrete rate: "
            f"requested={requested}, actual={actual}, tolerance={tolerance}"
        )


def _check_range_count(
    ranges: tuple[tuple[float, float], ...],
    expected: int,
) -> None:
    if len(ranges) != expected:
        raise ValueError("NI-DAQmx returned an unexpected effective range count")


def _topology_issues(
    system: NiSystemSnapshot,
    profile: HardwareProfile,
) -> tuple[list[DaqValidationIssue], NiDeviceSnapshot | None, NiModuleSnapshot | None]:
    issues: list[DaqValidationIssue] = []
    expected_device = profile.devices[0] if profile.devices else None
    expected_module = profile.modules[0] if profile.modules else None
    if expected_device is None:
        issues.append(_mismatch("devices", "Declare the required cDAQ-9171 chassis."))
        return issues, None, None
    model_matches = [
        device
        for device in system.devices
        if _canonical_model(device.product_type) == _canonical_model(expected_device.model)
    ]
    if expected_device.alias is not None:
        model_matches = [
            device for device in model_matches if device.alias == expected_device.alias
        ]
    if expected_device.expected_serial_number is not None:
        model_matches = [
            device
            for device in model_matches
            if _serial_matches(device.serial_number, expected_device.expected_serial_number)
        ]
    if not model_matches:
        issues.append(_mismatch("devices[0]", "Connect the expected cDAQ-9171 chassis."))
        return issues, None, None
    if len(model_matches) > 1:
        issues.append(
            _mismatch(
                "devices[0]",
                "Specify a unique chassis alias or serial number in the hardware profile.",
            )
        )
        return issues, None, None
    device = model_matches[0]
    if device.simulated:
        issues.append(_mismatch("devices[0]", "Use a physical NI chassis for M03 acceptance."))
    if expected_module is None:
        issues.append(_mismatch("modules", "Declare the required NI-9239 module."))
        return issues, device, None
    module_matches = [
        module
        for module in device.modules
        if _canonical_model(module.product_type) == _canonical_model(expected_module.model)
    ]
    if expected_module.expected_serial_number is not None:
        module_matches = [
            module
            for module in module_matches
            if _serial_matches(module.serial_number, expected_module.expected_serial_number)
        ]
    if not module_matches:
        issues.append(_mismatch("modules[0]", "Install the expected NI-9239 module."))
        return issues, device, None
    slot_matches = [
        module
        for module in module_matches
        if module.reported_slot - 1 == expected_module.logical_slot
    ]
    if not slot_matches:
        issues.append(
            _mismatch(
                "modules[0].logical_slot",
                f"Install the NI-9239 in logical slot {expected_module.logical_slot}.",
            )
        )
        return issues, device, None
    if len(slot_matches) > 1:
        issues.append(_mismatch("modules[0]", "Constrain the module by serial number."))
        return issues, device, None
    module = slot_matches[0]
    if module.simulated:
        issues.append(_mismatch("modules[0]", "Use a physical NI module for M03 acceptance."))
    return issues, device, module


def _configuration_issues(
    module: NiModuleSnapshot,
    profile: HardwareProfile,
    experiment: ExperimentConfig,
) -> tuple[list[DaqValidationIssue], tuple[str, ...]]:
    issues: list[DaqValidationIssue] = []
    channels = _ordered_channels(experiment)
    ordered_physical = tuple(
        f"{module.alias}/ai{channel.physical_channel.channel_index}" for channel in channels
    )
    if len(set(ordered_physical)) != len(ordered_physical):
        issues.append(_mismatch("channels", "Use unique physical channels."))
    available = {channel.casefold() for channel in module.physical_channels}
    for index, (channel, physical) in enumerate(zip(channels, ordered_physical, strict=True)):
        if physical.casefold() not in available:
            issues.append(
                _mismatch(
                    f"channels[{index}].physical_channel",
                    f"Select an available channel on {module.alias}.",
                )
            )
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
            channel.requested_input_min < module.input_range_v[0]
            or channel.requested_input_max > module.input_range_v[1]
        ):
            issues.append(
                _mismatch(
                    f"channels[{index}].requested_input_range",
                    f"Use a range within {module.input_range_v} V.",
                )
            )
    if "voltage" not in module.measurement_quantities:
        issues.append(_mismatch("channels", "Use a module supporting voltage measurement."))
    if "differential" not in module.terminal_configurations:
        issues.append(_mismatch("required_terminal_configuration", "Use differential inputs."))
    rate = experiment.acquisition.sample_rate_hz
    if (
        rate not in profile.application_supported_sample_rates_hz
        or rate > module.maximum_sample_rate_hz
    ):
        issues.append(
            _mismatch(
                "acquisition.sample_rate_hz",
                f"Use an application-supported rate no greater than {module.maximum_sample_rate_hz} Hz.",
            )
        )
    if experiment.acquisition.sampling_mode.value not in module.sampling_modes:
        issues.append(_mismatch("acquisition.sampling_mode", "Use finite or continuous sampling."))
    return issues, ordered_physical


def _ordered_channels(experiment: ExperimentConfig) -> tuple[ChannelConfig, ...]:
    by_id = {channel.channel_id: channel for channel in experiment.channels}
    return tuple(by_id[channel_id] for channel_id in experiment.acquisition.channel_ids)


def _finite_target(experiment: ExperimentConfig) -> int | None:
    acquisition = experiment.acquisition
    if acquisition.sampling_mode is SamplingMode.CONTINUOUS:
        return None
    assert acquisition.requested_duration_seconds is not None
    return rounded_sample_count(
        acquisition.sample_rate_hz,
        acquisition.requested_duration_seconds,
    )


def _canonical_model(value: str) -> str:
    normalized = "".join(character for character in value.casefold() if character.isalnum())
    if normalized.startswith("ni9239"):
        return "NI-9239"
    if normalized == "cdaq9171":
        return "cDAQ-9171"
    return value.strip()


def _device_id(device: NiDeviceSnapshot) -> str:
    return f"ni:{_canonical_model(device.product_type).casefold()}:{device.serial_number or device.alias}"


def _module_id(module: NiModuleSnapshot) -> str:
    return f"ni:{_canonical_model(module.product_type).casefold()}:{module.serial_number or module.alias}"


def _device_id_by_alias(system: NiSystemSnapshot | None, alias: str) -> str:
    if system is None:
        return alias
    return next((_device_id(device) for device in system.devices if device.alias == alias), alias)


def _serial_matches(discovered: str | None, expected: str) -> bool:
    if discovered is None:
        return False
    try:
        return int(discovered, 0) == int(expected, 0)
    except ValueError:
        return discovered.casefold() == expected.casefold()


def _mismatch(path: str, correction: str) -> DaqValidationIssue:
    return DaqValidationIssue(
        code="DAQ.HARDWARE.PROFILE_MISMATCH",
        field_path=path,
        message="The requested value does not match the discovered NI hardware.",
        correction=correction,
    )
