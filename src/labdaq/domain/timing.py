"""Deterministic sampling-rate and finite-duration timing helpers."""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def divided_sample_rates_hz(
    maximum_rate_hz: float,
    maximum_divisor: int,
) -> tuple[float, ...]:
    """Return canonical floating-point rates ``maximum_rate_hz / n``."""
    if not math.isfinite(maximum_rate_hz) or maximum_rate_hz <= 0:
        raise ValueError("maximum_rate_hz must be positive and finite")
    if isinstance(maximum_divisor, bool) or maximum_divisor <= 0:
        raise ValueError("maximum_divisor must be a positive integer")
    return tuple(maximum_rate_hz / divisor for divisor in range(1, maximum_divisor + 1))


def rounded_sample_count(sample_rate_hz: float, duration_seconds: float) -> int:
    """Resolve a duration to the nearest whole sample using decimal half-up rounding."""
    if (
        not math.isfinite(sample_rate_hz)
        or sample_rate_hz <= 0
        or not math.isfinite(duration_seconds)
        or duration_seconds <= 0
    ):
        raise ValueError("sample rate and duration must be positive and finite")
    try:
        exact = Decimal(str(sample_rate_hz)) * Decimal(str(duration_seconds))
    except (InvalidOperation, ValueError) as cause:
        raise ValueError("sample rate and duration must be numeric") from cause
    count = int(exact.to_integral_value(rounding=ROUND_HALF_UP))
    if count < 1:
        raise ValueError("finite acquisition duration must resolve to at least one sample")
    return count


def samples_for_interval(sample_rate_hz: float, interval_seconds: float = 0.1) -> int:
    """Return the nearest whole-sample read size for a requested time interval."""
    return rounded_sample_count(sample_rate_hz, interval_seconds)


def rates_equivalent(requested_hz: float, actual_hz: float) -> bool:
    """Ignore binary representation noise without accepting another hardware rate."""
    return math.isclose(requested_hz, actual_hz, rel_tol=1e-12, abs_tol=1e-9)
