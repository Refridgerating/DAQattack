"""Absolute-sample-index signal generation for the simulated DAQ."""

from __future__ import annotations

import math

import numpy as np

from labdaq.adapters.simulated.scenario import (
    ChannelSignal,
    ChirpComponent,
    DriftComponent,
    ImpulseComponent,
    NoiseComponent,
    SignalComponent,
    SimulatorScenario,
    SineComponent,
)

_UINT64_MASK = np.uint64(0xFFFFFFFFFFFFFFFF)
_SPLITMIX_INCREMENT = np.uint64(0x9E3779B97F4A7C15)
_SPLITMIX_MULTIPLIER_1 = np.uint64(0xBF58476D1CE4E5B9)
_SPLITMIX_MULTIPLIER_2 = np.uint64(0x94D049BB133111EB)
_NOISE_LANE_2 = np.uint64(0xD1B54A32D192ED03)
_TWO_POW_53 = float(1 << 53)


def generate_samples(
    scenario: SimulatorScenario,
    channel_order: tuple[str, ...],
    *,
    start_sample: int,
    sample_count: int,
    sample_rate_hz: float,
) -> np.ndarray:
    """Evaluate ordered channel signals without retaining read history."""
    if start_sample < 0 or sample_count < 0:
        raise ValueError("Sample bounds must be non-negative")
    by_id = {channel.channel_id: channel for channel in scenario.channel_signals}
    indices = np.arange(start_sample, start_sample + sample_count, dtype=np.int64)
    output = np.empty((len(channel_order), sample_count), dtype=np.float64, order="C")
    for row, channel_id in enumerate(channel_order):
        output[row] = _evaluate_channel(
            by_id[channel_id],
            scenario=scenario,
            indices=indices,
            sample_rate_hz=sample_rate_hz,
        )
    return output


def _evaluate_channel(
    channel: ChannelSignal,
    *,
    scenario: SimulatorScenario,
    indices: np.ndarray,
    sample_rate_hz: float,
) -> np.ndarray:
    values = np.full(indices.shape, channel.bias_v, dtype=np.float64)
    for component in channel.components:
        values += _evaluate_component(
            component,
            scenario=scenario,
            channel_id=channel.channel_id,
            indices=indices,
            sample_rate_hz=sample_rate_hz,
        )
    return values


def _evaluate_component(
    component: SignalComponent,
    *,
    scenario: SimulatorScenario,
    channel_id: str,
    indices: np.ndarray,
    sample_rate_hz: float,
) -> np.ndarray:
    values = np.zeros(indices.shape, dtype=np.float64)
    mask = _active_mask(component, indices)
    if not np.any(mask):
        return values
    active_indices = indices[mask]
    if isinstance(component, SineComponent):
        phase = (
            2.0 * math.pi * component.frequency_hz * active_indices / sample_rate_hz
            + component.phase_rad
        )
        values[mask] = component.offset_v + component.amplitude_v * np.sin(phase)
    elif isinstance(component, ImpulseComponent):
        values[mask] = component.amplitude_v
    elif isinstance(component, ChirpComponent):
        elapsed = (active_indices - component.start_sample) / sample_rate_hz
        duration_seconds = component.duration_samples / sample_rate_hz
        chirp_rate = (component.end_frequency_hz - component.start_frequency_hz) / duration_seconds
        phase = (
            2.0 * math.pi * (component.start_frequency_hz * elapsed + 0.5 * chirp_rate * elapsed**2)
            + component.initial_phase_rad
        )
        values[mask] = component.amplitude_v * np.sin(phase)
    elif isinstance(component, NoiseComponent):
        values[mask] = component.rms_v * _indexed_gaussian(
            scenario=scenario,
            channel_id=channel_id,
            component_id=component.component_id,
            indices=active_indices,
        )
    elif isinstance(component, DriftComponent):
        elapsed = (active_indices - component.reference_sample) / sample_rate_hz
        values[mask] = component.initial_offset_v + component.slope_v_per_second * elapsed
    return values


def _active_mask(component: SignalComponent, indices: np.ndarray) -> np.ndarray:
    if isinstance(component, ImpulseComponent):
        return (indices >= component.start_sample) & (
            indices < component.start_sample + component.width_samples
        )
    if isinstance(component, ChirpComponent):
        return (indices >= component.start_sample) & (
            indices < component.start_sample + component.duration_samples
        )
    start = component.start_sample
    stop = component.stop_sample
    mask = np.ones(indices.shape, dtype=np.bool_)
    if start is not None:
        mask &= indices >= start
    if stop is not None:
        mask &= indices < stop
    return mask


def _indexed_gaussian(
    *,
    scenario: SimulatorScenario,
    channel_id: str,
    component_id: str,
    indices: np.ndarray,
) -> np.ndarray:
    key = np.uint64(
        _stable_stream_key(
            scenario.seed,
            scenario.generator_version,
            channel_id,
            component_id,
        )
    )
    unsigned_indices = indices.astype(np.uint64, copy=False)
    pair_indices = unsigned_indices >> np.uint64(1)
    first = _splitmix64(pair_indices + key)
    second = _splitmix64(pair_indices + key + _NOISE_LANE_2)
    u1 = ((first >> np.uint64(11)).astype(np.float64) + 0.5) / _TWO_POW_53
    u2 = ((second >> np.uint64(11)).astype(np.float64) + 0.5) / _TWO_POW_53
    radius = np.sqrt(-2.0 * np.log(u1))
    angle = 2.0 * math.pi * u2
    even = radius * np.cos(angle)
    odd = radius * np.sin(angle)
    return np.where((unsigned_indices & np.uint64(1)) == 0, even, odd)


def _splitmix64(values: np.ndarray) -> np.ndarray:
    with np.errstate(over="ignore"):
        z = (values + _SPLITMIX_INCREMENT) & _UINT64_MASK
        z = ((z ^ (z >> np.uint64(30))) * _SPLITMIX_MULTIPLIER_1) & _UINT64_MASK
        z = ((z ^ (z >> np.uint64(27))) * _SPLITMIX_MULTIPLIER_2) & _UINT64_MASK
        return z ^ (z >> np.uint64(31))


def _stable_stream_key(
    seed: int,
    generator_version: str,
    channel_id: str,
    component_id: str,
) -> int:
    import hashlib

    digest = hashlib.sha256()
    digest.update(seed.to_bytes(8, "little", signed=False))
    for value in (generator_version, channel_id, component_id):
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "little"))
        digest.update(encoded)
    return int.from_bytes(digest.digest()[:8], "little")
