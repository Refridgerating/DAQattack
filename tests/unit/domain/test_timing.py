from __future__ import annotations

import pytest

from labdaq.domain.timing import (
    divided_sample_rates_hz,
    rates_equivalent,
    rounded_sample_count,
    samples_for_interval,
)


def test_ni_9239_rate_family_is_exact_and_complete() -> None:
    rates = divided_sample_rates_hz(50_000.0, 31)

    assert len(rates) == 31
    assert rates == tuple(50_000.0 / divisor for divisor in range(1, 32))
    assert rates[0] == 50_000.0
    assert rates[-1] == pytest.approx(1_612.9032258064517)


@pytest.mark.parametrize(
    ("rate", "duration", "expected"),
    [
        (50_000.0, 0.00001, 1),
        (50_000.0 / 3, 0.1, 1667),
        (50_000.0 / 31, 0.1, 161),
        (50_000.0 / 3, 1.00001, 16_667),
    ],
)
def test_sample_counts_use_decimal_half_up(rate: float, duration: float, expected: int) -> None:
    assert rounded_sample_count(rate, duration) == expected


def test_read_size_and_rate_equivalence_are_narrow() -> None:
    assert samples_for_interval(25_000.0) == 2_500
    assert rates_equivalent(50_000.0, 50_000.0 + 1e-10)
    assert not rates_equivalent(50_000.0, 49_999.75)
    with pytest.raises(ValueError, match="at least one sample"):
        rounded_sample_count(50_000.0, 0.000009)
