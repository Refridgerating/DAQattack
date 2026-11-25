"""Narrow wrapper around NI-DAQmx objects owned by the NI adapter package."""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from importlib.metadata import version
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

import nidaqmx  # type: ignore[import-untyped]
from nidaqmx.constants import (  # type: ignore[import-untyped]
    AcquisitionType,
    TaskMode,
    TerminalConfiguration,
    VoltageUnits,
)
from nidaqmx.errors import (  # type: ignore[import-untyped]
    DaqFunctionNotSupportedError,
    DaqNotSupportedError,
)
from nidaqmx.stream_readers import AnalogMultiChannelReader  # type: ignore[import-untyped]
from nidaqmx.system import System  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from collections.abc import Callable

    import numpy as np

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class NiWarningSnapshot:
    warning_type: str
    message: str
    error_code: int | None


@dataclass(frozen=True, slots=True)
class NiModuleSnapshot:
    alias: str
    product_type: str
    product_category: str
    serial_number: str | None
    simulated: bool
    chassis_alias: str
    reported_slot: int
    physical_channels: tuple[str, ...]
    measurement_quantities: tuple[str, ...]
    terminal_configurations: tuple[str, ...]
    input_range_v: tuple[float, float]
    maximum_sample_rate_hz: float
    sampling_modes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NiDeviceSnapshot:
    alias: str
    product_type: str
    product_category: str
    serial_number: str | None
    simulated: bool
    modules: tuple[NiModuleSnapshot, ...]


@dataclass(frozen=True, slots=True)
class NiSystemSnapshot:
    package_version: str
    driver_version: str
    devices: tuple[NiDeviceSnapshot, ...]


@dataclass(frozen=True, slots=True)
class NiSelfTestSnapshot:
    supported: bool


class NiTask(Protocol):
    def add_voltage_channel(
        self,
        physical_channel: str,
        minimum_v: float,
        maximum_v: float,
    ) -> tuple[float, float]: ...

    def configure_timing(
        self,
        sample_rate_hz: float,
        sampling_mode: str,
        samples_per_channel: int,
    ) -> None: ...

    def verify_and_commit(self) -> float: ...

    def effective_input_ranges(self) -> tuple[tuple[float, float], ...]: ...

    def start(self) -> None: ...

    def read(
        self,
        buffer: np.ndarray,
        samples_per_channel: int,
        timeout_seconds: float,
    ) -> int: ...

    def abort(self) -> None: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...

    def pop_warnings(self) -> tuple[NiWarningSnapshot, ...]: ...


class NiDriver(Protocol):
    def discover(self) -> NiSystemSnapshot: ...

    def create_task(self) -> NiTask: ...

    def self_test_device(self, alias: str) -> NiSelfTestSnapshot: ...

    def reset_device(self, alias: str) -> None: ...


class NidaqmxTask:
    """Own exactly one NI task and its reusable stream reader."""

    def __init__(self) -> None:
        self._task = nidaqmx.Task()
        self._reader: AnalogMultiChannelReader | None = None
        self._channels: list[Any] = []
        self._warnings: list[NiWarningSnapshot] = []

    def add_voltage_channel(
        self,
        physical_channel: str,
        minimum_v: float,
        maximum_v: float,
    ) -> tuple[float, float]:
        channel = self._capture(
            lambda: self._task.ai_channels.add_ai_voltage_chan(
                physical_channel,
                terminal_config=TerminalConfiguration.DIFF,
                min_val=minimum_v,
                max_val=maximum_v,
                units=VoltageUnits.VOLTS,
            )
        )
        self._channels.append(channel)
        return (minimum_v, maximum_v)

    def configure_timing(
        self,
        sample_rate_hz: float,
        sampling_mode: str,
        samples_per_channel: int,
    ) -> None:
        mode = AcquisitionType.FINITE if sampling_mode == "finite" else AcquisitionType.CONTINUOUS
        self._capture(
            lambda: self._task.timing.cfg_samp_clk_timing(
                sample_rate_hz,
                sample_mode=mode,
                samps_per_chan=samples_per_channel,
            )
        )
        self._reader = AnalogMultiChannelReader(self._task.in_stream)
        self._reader.verify_array_shape = True

    def verify_and_commit(self) -> float:
        self._capture(lambda: self._task.control(TaskMode.TASK_VERIFY))
        self._capture(lambda: self._task.control(TaskMode.TASK_COMMIT))
        return float(self._capture(lambda: self._task.timing.samp_clk_rate))

    def effective_input_ranges(self) -> tuple[tuple[float, float], ...]:
        return self._capture(
            lambda: tuple(
                (float(channel.ai_min), float(channel.ai_max)) for channel in self._channels
            )
        )

    def start(self) -> None:
        self._capture(self._task.start)

    def read(
        self,
        buffer: np.ndarray,
        samples_per_channel: int,
        timeout_seconds: float,
    ) -> int:
        if self._reader is None:
            raise RuntimeError("NI stream reader is not configured")
        reader = self._reader
        return int(
            self._capture(
                lambda: reader.read_many_sample(
                    buffer,
                    number_of_samples_per_channel=samples_per_channel,
                    timeout=timeout_seconds,
                )
            )
        )

    def stop(self) -> None:
        self._capture(self._task.stop)

    def abort(self) -> None:
        self._capture(lambda: self._task.control(TaskMode.TASK_ABORT))

    def close(self) -> None:
        self._capture(self._task.close)

    def pop_warnings(self) -> tuple[NiWarningSnapshot, ...]:
        captured = tuple(self._warnings)
        self._warnings.clear()
        return captured

    def _capture(self, operation: Callable[[], _T]) -> _T:
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            result = operation()
        self._warnings.extend(
            NiWarningSnapshot(
                warning_type=type(item.message).__name__,
                message=str(item.message),
                error_code=getattr(item.message, "error_code", None),
            )
            for item in captured
        )
        return result


class NidaqmxDriver:
    """Translate the public nidaqmx discovery API into immutable snapshots."""

    def discover(self) -> NiSystemSnapshot:
        system = System.local()
        driver = system.driver_version
        devices = tuple(system.devices)
        by_alias = {device.name: device for device in devices}
        chassis: list[NiDeviceSnapshot] = []
        for device in devices:
            category = _enum_name(device.product_category)
            if category != "COMPACT_DAQ_CHASSIS":
                continue
            modules = tuple(
                _module_snapshot(by_alias[module.name], device.name)
                for module in device.chassis_module_devices
            )
            chassis.append(
                NiDeviceSnapshot(
                    alias=device.name,
                    product_type=str(device.product_type),
                    product_category=category,
                    serial_number=_serial_number(device.serial_num),
                    simulated=bool(device.is_simulated),
                    modules=modules,
                )
            )
        return NiSystemSnapshot(
            package_version=version("nidaqmx"),
            driver_version=(
                f"{driver.major_version}.{driver.minor_version}.{driver.update_version}"
            ),
            devices=tuple(chassis),
        )

    def create_task(self) -> NiTask:
        return NidaqmxTask()

    def self_test_device(self, alias: str) -> NiSelfTestSnapshot:
        try:
            System.local().devices[alias].self_test_device()
        except (DaqNotSupportedError, DaqFunctionNotSupportedError):
            return NiSelfTestSnapshot(supported=False)
        return NiSelfTestSnapshot(supported=True)

    def reset_device(self, alias: str) -> None:
        System.local().devices[alias].reset_device()


def _module_snapshot(device: Any, chassis_alias: str) -> NiModuleSnapshot:
    physical_channels = tuple(device.ai_physical_chans)
    terminal_configurations = tuple(
        sorted(
            {
                _normalize_terminal_configuration(configuration)
                for channel in physical_channels
                for configuration in channel.ai_term_cfgs
            }
        )
    )
    measurement_quantities = tuple(
        sorted(
            {
                _normalize_measurement_quantity(quantity)
                for channel in physical_channels
                for quantity in channel.ai_meas_types
            }
        )
    )
    ranges = tuple(float(value) for value in device.ai_voltage_rngs)
    input_range = (min(ranges), max(ranges)) if ranges else (0.0, 0.0)
    return NiModuleSnapshot(
        alias=str(device.name),
        product_type=str(device.product_type),
        product_category=_enum_name(device.product_category),
        serial_number=_serial_number(device.serial_num),
        simulated=bool(device.is_simulated),
        chassis_alias=chassis_alias,
        reported_slot=int(device.compact_daq_slot_num),
        physical_channels=tuple(channel.name for channel in physical_channels),
        measurement_quantities=measurement_quantities,
        terminal_configurations=terminal_configurations,
        input_range_v=input_range,
        maximum_sample_rate_hz=float(device.ai_max_multi_chan_rate),
        sampling_modes=tuple(_enum_name(mode).casefold() for mode in device.ai_samp_modes),
    )


def _enum_name(value: object) -> str:
    name = getattr(value, "name", None)
    return str(name if name is not None else value)


def _normalize_terminal_configuration(value: object) -> str:
    return (
        "differential"
        if _enum_name(value) in {"DIFF", "DIFFERENTIAL"}
        else _enum_name(value).casefold()
    )


def _normalize_measurement_quantity(value: object) -> str:
    return "voltage" if _enum_name(value) == "VOLTAGE" else _enum_name(value).casefold()


def _serial_number(value: int) -> str | None:
    serial = int(value)
    return None if serial == 0 else f"0x{serial:08X}"
