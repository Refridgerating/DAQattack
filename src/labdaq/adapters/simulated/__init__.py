"""Lazy integration boundary for deterministic simulated DAQ support."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from labdaq.application.daq_service import DaqService


def create_service(*, scenario_path: Path | None = None) -> DaqService:
    """Create the simulated service without importing NumPy at package import time."""
    try:
        from labdaq.adapters.simulated.adapter import SimulatedDaqAdapter
        from labdaq.adapters.simulated.scenario import JsonSimulatorScenarioCodec
        from labdaq.application.daq_service import DaqService
    except ModuleNotFoundError as cause:
        if cause.name != "numpy":
            raise
        from labdaq.errors import DependencyUnavailableError

        raise DependencyUnavailableError(
            "The simulated DAQ requires NumPy.",
            code="DAQ.DEPENDENCY.UNAVAILABLE",
            context={"dependency": "numpy", "feature": "simulated_daq"},
            remediation='Install LabDAQ with the "simulated" extra.',
            cause=cause,
        ) from cause

    codec = JsonSimulatorScenarioCodec()
    scenario = (
        codec.load(scenario_path)
        if scenario_path is not None
        else codec.load_bundled("clean-four-channel")
    )
    return DaqService(SimulatedDaqAdapter(scenario))


__all__ = ["create_service"]
