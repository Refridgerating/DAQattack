"""Headless DAQ discovery, validation, and bounded test-acquisition use cases."""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

import numpy as np

from labdaq.domain.enums import SamplingMode
from labdaq.domain.timing import rounded_sample_count
from labdaq.errors import ErrorSeverity
from labdaq.ports.daq import (
    DaqConfigurationError,
    DaqError,
    DaqEvent,
    DaqLifecycleState,
    DaqProfileMismatchError,
    canonical_sha256,
)

if TYPE_CHECKING:
    from labdaq.domain.models import ExperimentConfig, HardwareProfile
    from labdaq.ports.daq import (
        DaqDiscoveryReport,
        DaqHealthSnapshot,
        DaqPort,
        DaqValidationReport,
    )


@dataclass(frozen=True, slots=True)
class ChannelStatistics:
    channel_id: str
    minimum_v: float | None
    maximum_v: float | None
    mean_v: float | None
    rms_v: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            "channel_id": self.channel_id,
            "minimum_v": self.minimum_v,
            "maximum_v": self.maximum_v,
            "mean_v": self.mean_v,
            "rms_v": self.rms_v,
        }


@dataclass(frozen=True, slots=True)
class AcquisitionTestSummary:
    overall: Literal["ok", "error"]
    backend_id: str
    device_id: str
    module_id: str
    scenario_id: str | None
    scenario_revision: int | None
    seed: int | None
    generator_version: str | None
    requested_sample_rate_hz: float
    actual_sample_rate_hz: float
    requested_samples_per_channel: int
    read_chunk_size: int
    chunk_count: int
    samples_per_channel: int
    elapsed_seconds: float
    throughput_samples_per_second_per_channel: float
    array_shape: tuple[int, int]
    dtype: str
    ordered_channel_ids: tuple[str, ...]
    channel_statistics: tuple[ChannelStatistics, ...]
    canonical_sha256: str
    events: tuple[DaqEvent, ...]
    fault: DaqEvent | None
    final_health: DaqHealthSnapshot
    cleanup_succeeded: bool
    backend_details: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "overall": self.overall,
            "backend_id": self.backend_id,
            "device_id": self.device_id,
            "module_id": self.module_id,
            "scenario_id": self.scenario_id,
            "scenario_revision": self.scenario_revision,
            "seed": self.seed,
            "generator_version": self.generator_version,
            "requested_sample_rate_hz": self.requested_sample_rate_hz,
            "actual_sample_rate_hz": self.actual_sample_rate_hz,
            "requested_samples_per_channel": self.requested_samples_per_channel,
            "read_chunk_size": self.read_chunk_size,
            "chunk_count": self.chunk_count,
            "samples_per_channel": self.samples_per_channel,
            "elapsed_seconds": self.elapsed_seconds,
            "throughput_samples_per_second_per_channel": (
                self.throughput_samples_per_second_per_channel
            ),
            "array_shape": list(self.array_shape),
            "dtype": self.dtype,
            "ordered_channel_ids": list(self.ordered_channel_ids),
            "channel_statistics": [item.to_dict() for item in self.channel_statistics],
            "canonical_sha256": self.canonical_sha256,
            "events": [event.to_dict() for event in self.events],
            "fault": self.fault.to_dict() if self.fault else None,
            "final_health": self.final_health.to_dict(),
            "cleanup_succeeded": self.cleanup_succeeded,
            "backend_details": self.backend_details,
        }


class DaqService:
    """Coordinate DAQ use cases through the hardware-independent port."""

    def __init__(self, daq: DaqPort) -> None:
        self._daq = daq

    def discover(self) -> DaqDiscoveryReport:
        return self._daq.discover()

    def validate(
        self,
        profile: HardwareProfile,
        experiment: ExperimentConfig,
    ) -> DaqValidationReport:
        return self._daq.validate(profile, experiment)

    def acquire_test(
        self,
        profile: HardwareProfile,
        experiment: ExperimentConfig,
        *,
        sample_limit: int,
        chunk_size: int,
        timeout_seconds: float,
    ) -> AcquisitionTestSummary:
        _validate_bounds(experiment, sample_limit, chunk_size, timeout_seconds)
        bounded_experiment = _bounded_experiment(experiment, sample_limit)
        validation = self._daq.validate(profile, bounded_experiment)
        if not validation.accepted:
            event = DaqEvent(
                code="DAQ.HARDWARE.PROFILE_MISMATCH",
                severity=ErrorSeverity.ERROR,
                backend_id=validation.backend_id,
                operation="acquire_test",
                message="DAQ validation rejected the requested acquisition.",
                timestamp=datetime.now(UTC),
                context={"issues": [issue.to_dict() for issue in validation.errors]},
            )
            raise DaqProfileMismatchError(
                event.message,
                event=event,
                remediation="Correct the reported hardware, experiment, or scenario mismatch.",
            )
        configuration = self._daq.configure(bounded_experiment)
        events: list[DaqEvent] = []
        chunks: list[np.ndarray] = []
        fault: DaqEvent | None = None
        chunk_count = 0
        actual_rate = configuration.effective_sample_rate_hz
        cleanup_succeeded = False
        acquisition_started = time.perf_counter()
        try:
            started = self._daq.start()
            events.extend(started.events)
            acquired = 0
            while acquired < sample_limit:
                requested = min(chunk_size, sample_limit - acquired)
                result = self._daq.read(requested, timeout_seconds)
                events.extend(result.events)
                if result.samples_read_per_channel:
                    chunks.append(np.array(result.samples, copy=True, order="C"))
                    acquired += result.samples_read_per_channel
                    chunk_count += 1
                if result.end_of_stream:
                    break
        except DaqError as error:
            fault = error.event
            events.append(error.event)
        finally:
            state = self._daq.health().state
            if state in {
                DaqLifecycleState.CONFIGURED,
                DaqLifecycleState.RUNNING,
                DaqLifecycleState.FAULTED,
                DaqLifecycleState.STOPPED,
            }:
                try:
                    stopped = self._daq.stop()
                    events.extend(stopped.events)
                    cleanup_succeeded = True
                except DaqError as cleanup_error:
                    if fault is None:
                        fault = cleanup_error.event
                    events.append(cleanup_error.event)
            final_health = self._daq.health()
        elapsed_seconds = time.perf_counter() - acquisition_started
        channel_count = len(configuration.ordered_logical_channels)
        if chunks:
            samples = np.ascontiguousarray(np.concatenate(chunks, axis=1), dtype=np.float64)
        else:
            samples = np.empty((channel_count, 0), dtype=np.float64)
        statistics = _statistics(samples, configuration.ordered_logical_channels)
        details = final_health.backend_details
        return AcquisitionTestSummary(
            overall="error" if fault else "ok",
            backend_id=configuration.backend_id,
            device_id=configuration.device_id,
            module_id=configuration.module_id,
            scenario_id=_optional_str(details.get("scenario_id")),
            scenario_revision=_optional_int(details.get("scenario_revision")),
            seed=_optional_int(details.get("seed")),
            generator_version=_optional_str(details.get("generator_version")),
            requested_sample_rate_hz=configuration.requested_sample_rate_hz,
            actual_sample_rate_hz=actual_rate,
            requested_samples_per_channel=sample_limit,
            read_chunk_size=chunk_size,
            chunk_count=chunk_count,
            samples_per_channel=samples.shape[1],
            elapsed_seconds=elapsed_seconds,
            throughput_samples_per_second_per_channel=(
                samples.shape[1] / elapsed_seconds if elapsed_seconds > 0 else 0.0
            ),
            array_shape=samples.shape,
            dtype=str(samples.dtype),
            ordered_channel_ids=configuration.ordered_logical_channels,
            channel_statistics=statistics,
            canonical_sha256=canonical_sha256(samples),
            events=tuple(events),
            fault=fault,
            final_health=final_health,
            cleanup_succeeded=cleanup_succeeded,
            backend_details=dict(details),
        )

    def close(self) -> None:
        self._daq.close()


def configured_sample_count(experiment: ExperimentConfig) -> int | None:
    acquisition = experiment.acquisition
    if acquisition.sampling_mode is SamplingMode.CONTINUOUS:
        return None
    assert acquisition.requested_duration_seconds is not None
    return rounded_sample_count(
        acquisition.sample_rate_hz,
        acquisition.requested_duration_seconds,
    )


def _bounded_experiment(
    experiment: ExperimentConfig,
    sample_limit: int,
) -> ExperimentConfig:
    configured = configured_sample_count(experiment)
    if configured is None or sample_limit == configured:
        return experiment
    duration = float(Decimal(sample_limit) / Decimal(str(experiment.acquisition.sample_rate_hz)))
    return replace(
        experiment,
        acquisition=replace(
            experiment.acquisition,
            requested_duration_seconds=duration,
        ),
    )


def _validate_bounds(
    experiment: ExperimentConfig,
    sample_limit: int,
    chunk_size: int,
    timeout_seconds: float,
) -> None:
    if isinstance(sample_limit, bool) or sample_limit <= 0:
        raise _bound_error("sample_limit", sample_limit, "Use a positive sample limit.")
    if isinstance(chunk_size, bool) or chunk_size <= 0:
        raise _bound_error("chunk_size", chunk_size, "Use a positive chunk size.")
    if not np.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise _bound_error("timeout_seconds", timeout_seconds, "Use a positive finite timeout.")
    configured = configured_sample_count(experiment)
    if configured is not None and sample_limit > configured:
        raise _bound_error(
            "sample_limit",
            sample_limit,
            f"Use at most the configured finite count of {configured}.",
        )


def _bound_error(field: str, value: object, remediation: str) -> DaqConfigurationError:
    event = DaqEvent(
        code="DAQ.HARDWARE.CONFIGURATION_UNSUPPORTED",
        severity=ErrorSeverity.ERROR,
        backend_id="simulated",
        operation="acquire_test",
        message="The bounded acquisition request is invalid.",
        timestamp=datetime.now(UTC),
        context={"field": field, "value": value},
    )
    return DaqConfigurationError(event.message, event=event, remediation=remediation)


def _statistics(
    samples: np.ndarray,
    channel_ids: tuple[str, ...],
) -> tuple[ChannelStatistics, ...]:
    if samples.shape[1] == 0:
        return tuple(
            ChannelStatistics(channel_id, None, None, None, None) for channel_id in channel_ids
        )
    return tuple(
        ChannelStatistics(
            channel_id=channel_id,
            minimum_v=float(np.min(samples[index])),
            maximum_v=float(np.max(samples[index])),
            mean_v=float(np.mean(samples[index])),
            rms_v=float(np.sqrt(np.mean(np.square(samples[index])))),
        )
        for index, channel_id in enumerate(channel_ids)
    )


def _optional_str(value: object | None) -> str | None:
    return None if value is None else str(value)


def _optional_int(value: object | None) -> int | None:
    return None if value is None else int(str(value))
