"""Deterministic bounded min/max display decimation."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np


class StreamingMinMaxDecimator:
    """Collect at most ``target_points`` source extrema per channel."""

    def __init__(
        self,
        *,
        channel_count: int,
        start_index: int,
        stop_index: int,
        target_points: int,
    ) -> None:
        import numpy as np

        if channel_count <= 0 or stop_index <= start_index or target_points <= 0:
            raise ValueError("decimation dimensions must be positive")
        self._channel_count = channel_count
        self._start = start_index
        self._stop = stop_index
        self._count = stop_index - start_index
        self._target = target_points
        self._passthrough = self._count <= target_points
        if not self._passthrough and target_points < 4:
            raise ValueError("min/max decimation requires at least four target points")
        self._buckets = 0 if self._passthrough else (target_points - 2) // 2
        self._interior_count = max(0, self._count - 2)
        self._indices: list[list[int]] = [[] for _ in range(channel_count)]
        self._values: list[list[float]] = [[] for _ in range(channel_count)]
        self._bucket = -1
        self._minimum = np.empty(channel_count, dtype=np.float64)
        self._maximum = np.empty(channel_count, dtype=np.float64)
        self._minimum_index = np.empty(channel_count, dtype=np.int64)
        self._maximum_index = np.empty(channel_count, dtype=np.int64)
        self._received = 0

    def append(self, start_index: int, values: np.ndarray) -> None:
        import numpy as np

        if values.dtype != np.float64 or values.ndim != 2:
            raise ValueError("decimation values must be a channels-by-samples float64 array")
        if values.shape[0] != self._channel_count or start_index != self._start + self._received:
            raise ValueError("decimation chunks must be aligned and contiguous")
        if start_index + values.shape[1] > self._stop:
            raise ValueError("decimation chunk exceeds the selected range")
        if not np.all(np.isfinite(values)):
            raise ValueError("decimation cannot bridge nonfinite source values")
        for offset in range(values.shape[1]):
            index = start_index + offset
            column = values[:, offset]
            if self._passthrough or index in {self._start, self._stop - 1}:
                if not self._passthrough and index == self._stop - 1:
                    self._flush_bucket()
                self._append_column(index, column)
                continue
            bucket = min(
                ((index - self._start - 1) * self._buckets) // self._interior_count,
                self._buckets - 1,
            )
            if bucket != self._bucket:
                self._flush_bucket()
                self._bucket = bucket
                self._minimum[:] = column
                self._maximum[:] = column
                self._minimum_index[:] = index
                self._maximum_index[:] = index
            else:
                lower = column < self._minimum
                upper = column > self._maximum
                self._minimum[lower] = column[lower]
                self._minimum_index[lower] = index
                self._maximum[upper] = column[upper]
                self._maximum_index[upper] = index
        self._received += values.shape[1]

    def finalize(self) -> tuple[tuple[np.ndarray, np.ndarray], ...]:
        import numpy as np

        if self._received != self._count:
            raise ValueError("decimation did not receive the complete selected range")
        self._flush_bucket()
        result = []
        for indices, values in zip(self._indices, self._values, strict=True):
            index_array = np.asarray(indices, dtype=np.int64)
            value_array = np.asarray(values, dtype=np.float64)
            index_array.flags.writeable = False
            value_array.flags.writeable = False
            if len(index_array) > self._target:
                raise AssertionError("decimation exceeded its point bound")
            result.append((index_array, value_array))
        return tuple(result)

    def _append_column(self, index: int, values: np.ndarray) -> None:
        for channel in range(self._channel_count):
            self._append_one(channel, index, float(values[channel]))

    def _append_one(self, channel: int, index: int, value: float) -> None:
        if self._indices[channel] and self._indices[channel][-1] == index:
            return
        self._indices[channel].append(index)
        self._values[channel].append(value)

    def _flush_bucket(self) -> None:
        if self._bucket < 0:
            return
        for channel in range(self._channel_count):
            extrema = sorted(
                {
                    int(self._minimum_index[channel]): float(self._minimum[channel]),
                    int(self._maximum_index[channel]): float(self._maximum[channel]),
                }.items()
            )
            for index, value in extrema:
                self._append_one(channel, index, value)
        self._bucket = -1


def decimate_xy(
    x_values: np.ndarray,
    y_values: np.ndarray,
    *,
    target_points: int,
) -> tuple[np.ndarray, np.ndarray, bool]:
    """Apply the same position-bucket algorithm to an already computed product."""
    import numpy as np

    if x_values.dtype != np.float64 or y_values.dtype != np.float64:
        raise ValueError("plot vectors must use float64")
    if x_values.ndim != 1 or y_values.ndim != 1 or len(x_values) != len(y_values):
        raise ValueError("plot vectors must be aligned")
    if len(x_values) == 0 or not np.all(np.isfinite(x_values)) or not np.all(np.isfinite(y_values)):
        raise ValueError("plot vectors must be finite and non-empty")
    values = np.ascontiguousarray(y_values.reshape(1, -1))
    values.flags.writeable = False
    collector = StreamingMinMaxDecimator(
        channel_count=1,
        start_index=0,
        stop_index=len(y_values),
        target_points=target_points,
    )
    collector.append(0, values)
    positions, reduced = collector.finalize()[0]
    x_result = np.asarray(x_values[positions], dtype=np.float64)
    x_result.flags.writeable = False
    return x_result, reduced, len(reduced) != len(y_values)
