"""Canonical JSON-compatible conversion used by durable session adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from types import MappingProxyType


def to_primitive(value: object) -> object:
    """Convert supported immutable records to deterministic JSON values."""
    if is_dataclass(value) and not isinstance(value, type):
        return to_primitive(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (Mapping, MappingProxyType)):
        return {str(key): to_primitive(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_primitive(item) for item in value]
    return value
