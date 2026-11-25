"""Versioned logical-data and file digest helpers."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Final

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

LOGICAL_DIGEST_VERSION: Final = 1
LOGICAL_DIGEST_PREFIX: Final = b"LABDAQ-RAW-F64LE-INTERLEAVED-V1\0"


class LogicalDataDigest:
    """Chunk-boundary-independent digest of ordered synchronized sample frames."""

    def __init__(self) -> None:
        self._digest = hashlib.sha256()
        self._digest.update(LOGICAL_DIGEST_PREFIX)

    def update(self, samples: np.ndarray) -> None:
        if not isinstance(samples, np.ndarray) or samples.ndim != 2:
            raise ValueError("logical digest input must be a two-dimensional NumPy array")
        if samples.dtype != np.dtype(np.float64):
            raise ValueError("logical digest input must have float64 dtype")
        frame_major = np.ascontiguousarray(samples.T, dtype=np.dtype("<f8"))
        self._digest.update(frame_major.tobytes(order="C"))

    def hexdigest(self) -> str:
        return self._digest.hexdigest()


def logical_sha256(arrays: Iterable[np.ndarray]) -> str:
    digest = LogicalDataDigest()
    for samples in arrays:
        digest.update(samples)
    return digest.hexdigest()


def file_sha256(path: Path, *, block_size: int = 1024 * 1024) -> str:
    if block_size <= 0:
        raise ValueError("block_size must be positive")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(block_size):
            digest.update(block)
    return digest.hexdigest()
