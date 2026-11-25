from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from labdaq.adapters.ni.adapter import NIDaqmxAdapter
from labdaq.adapters.ni.driver import (
    NiDeviceSnapshot,
    NiModuleSnapshot,
    NiSystemSnapshot,
)
from labdaq.application.daq_service import DaqService
from labdaq.cli.main import EXIT_SUCCESS, main

if TYPE_CHECKING:
    import numpy as np

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROFILE = PROJECT_ROOT / (
    "src/labdaq/resources/config/hardware-profiles/cdaq-9171-ni-9239-four-channel-v1.json"
)
EXPERIMENT = PROJECT_ROOT / "tests/fixtures/configuration/valid-four-channel-synthetic-v1.json"


class FakeTask:
    def add_voltage_channel(
        self,
        physical_channel: str,
        minimum_v: float,
        maximum_v: float,
    ) -> tuple[float, float]:
        del physical_channel, minimum_v, maximum_v
        return (-10.0, 10.0)

    def configure_timing(
        self,
        sample_rate_hz: float,
        sampling_mode: str,
        samples_per_channel: int,
    ) -> None:
        del sample_rate_hz, sampling_mode, samples_per_channel

    def verify_and_commit(self) -> float:
        return 50_000.0

    def effective_input_ranges(self) -> tuple[tuple[float, float], ...]:
        return ((-10.0, 10.0),) * 4

    def start(self) -> None:
        pass

    def read(
        self,
        buffer: np.ndarray,
        samples_per_channel: int,
        timeout_seconds: float,
    ) -> int:
        del timeout_seconds
        buffer[:, :samples_per_channel] = 0.0
        return samples_per_channel

    def stop(self) -> None:
        pass

    def abort(self) -> None:
        pass

    def close(self) -> None:
        pass

    def pop_warnings(self):
        return ()


class FakeDriver:
    def discover(self) -> NiSystemSnapshot:
        module = NiModuleSnapshot(
            alias="cDAQ1Mod1",
            product_type="NI 9239 (BNC)",
            product_category="C_SERIES_MODULE",
            serial_number="0x2",
            simulated=False,
            chassis_alias="cDAQ1",
            reported_slot=1,
            physical_channels=tuple(f"cDAQ1Mod1/ai{index}" for index in range(4)),
            measurement_quantities=("voltage",),
            terminal_configurations=("differential",),
            input_range_v=(-10.0, 10.0),
            maximum_sample_rate_hz=50_000.0,
            sampling_modes=("finite", "continuous"),
        )
        device = NiDeviceSnapshot(
            alias="cDAQ1",
            product_type="cDAQ-9171",
            product_category="COMPACT_DAQ_CHASSIS",
            serial_number="0x1",
            simulated=False,
            modules=(module,),
        )
        return NiSystemSnapshot("1.6.0", "24.5.0", (device,))

    def create_task(self) -> FakeTask:
        return FakeTask()

    def reset_device(self, alias: str) -> None:
        del alias


def _service(**kwargs: object) -> DaqService:
    del kwargs
    return DaqService(NIDaqmxAdapter(FakeDriver()))


def test_fake_ni_cli_list_validate_and_acquire(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("labdaq.cli.main.create_daq_service", _service)

    listed = main(["hardware", "list", "--backend", "ni", "--json"])
    list_payload = json.loads(capsys.readouterr().out)
    validated = main(
        [
            "hardware",
            "validate",
            "--backend",
            "ni",
            "--profile",
            str(PROFILE),
            "--experiment",
            str(EXPERIMENT),
            "--json",
        ]
    )
    validation_payload = json.loads(capsys.readouterr().out)
    acquired = main(
        [
            "hardware",
            "acquire-test",
            "--backend",
            "ni",
            "--profile",
            str(PROFILE),
            "--experiment",
            str(EXPERIMENT),
            "--samples",
            "1000",
            "--chunk-size",
            "257",
            "--json",
        ]
    )
    acquisition_payload = json.loads(capsys.readouterr().out)

    assert listed == validated == acquired == EXIT_SUCCESS
    assert list_payload["driver"]["driver_version"] == "24.5.0"
    assert list_payload["devices"][0]["modules"][0]["reported_model"] == "NI 9239 (BNC)"
    assert validation_payload["scenario_id"] is None
    assert validation_payload["accepted"] is True
    assert acquisition_payload["backend_id"] == "ni"
    assert acquisition_payload["actual_sample_rate_hz"] == 50_000.0
    assert acquisition_payload["samples_per_channel"] == 1000
    assert acquisition_payload["array_shape"] == [4, 1000]
    assert acquisition_payload["cleanup_succeeded"] is True
