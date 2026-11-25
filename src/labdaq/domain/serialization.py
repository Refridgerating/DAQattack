"""Deterministic serialization for scientific identities."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path


def to_canonical_primitive(value: object) -> object:
    """Convert supported records to JSON-compatible deterministic values."""
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: to_canonical_primitive(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(key): to_canonical_primitive(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [to_canonical_primitive(item) for item in value]
    if isinstance(value, float) and not __import__("math").isfinite(value):
        raise ValueError("canonical JSON forbids non-finite numbers")
    return value


def canonical_json_bytes(value: object) -> bytes:
    """Return the stable UTF-8 JSON representation used for content hashes."""
    return json.dumps(
        to_canonical_primitive(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    """Hash canonical scientific content with SHA-256."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def canonical_sha256_excluding(value: object, *field_names: str) -> str:
    """Hash a record after excluding named top-level identity/volatile fields."""
    primitive = to_canonical_primitive(value)
    if not isinstance(primitive, dict):
        raise TypeError("excluded-field canonical hashing requires an object")
    excluded = set(field_names)
    return canonical_sha256({key: item for key, item in primitive.items() if key not in excluded})
