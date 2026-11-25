"""Safe conversion helpers for operational context."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Final

_SENSITIVE_MARKERS: Final = ("password", "passwd", "secret", "token", "credential", "api_key")
_RAW_PAYLOAD_KEYS: Final = frozenset(
    {"array", "data_array", "raw_data", "raw_samples", "sample_array", "samples"}
)
_MAX_SEQUENCE_ITEMS: Final = 32
_MAX_STRING_LENGTH: Final = 1024
_MAX_DEPTH: Final = 8


def sanitize_context(
    context: Mapping[str, object],
    *,
    reject_arrays: bool,
    strict: bool,
) -> dict[str, object]:
    """Copy context into safe, bounded, JSON-compatible values."""
    sanitized: dict[str, object] = {}
    for key, value in context.items():
        if not isinstance(key, str):
            raise TypeError("Context keys must be strings")
        normalized_key = key.casefold()
        if any(marker in normalized_key for marker in _SENSITIVE_MARKERS):
            sanitized[key] = "[REDACTED]"
            continue
        if normalized_key in _RAW_PAYLOAD_KEYS:
            if reject_arrays:
                raise ValueError(f"Raw sample payload is not allowed in context: {key}")
            sanitized[key] = _summarize(value)
            continue
        sanitized[key] = _sanitize_value(
            value,
            reject_arrays=reject_arrays,
            strict=strict,
            depth=0,
        )
    return sanitized


def freeze_mapping(context: Mapping[str, object]) -> Mapping[str, object]:
    """Recursively freeze a sanitized context mapping."""
    return MappingProxyType({key: _freeze_value(value) for key, value in context.items()})


def thaw_mapping(context: Mapping[str, object]) -> dict[str, object]:
    """Convert frozen context back to ordinary JSON-compatible containers."""
    return {key: _thaw_value(value) for key, value in context.items()}


def _sanitize_value(
    value: object,
    *,
    reject_arrays: bool,
    strict: bool,
    depth: int,
) -> object:
    if depth >= _MAX_DEPTH:
        return "<omitted: maximum context depth exceeded>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) <= _MAX_STRING_LENGTH:
            return value
        return f"{value[:_MAX_STRING_LENGTH]}… <truncated>"
    if isinstance(value, bytes):
        if strict:
            raise TypeError("Bytes are not supported in structured context")
        return _summarize(value)
    if _looks_like_array(value):
        if reject_arrays:
            raise ValueError("Array-like values are not allowed in structured context")
        return _summarize(value)
    if isinstance(value, Mapping):
        nested: dict[str, object] = {}
        for nested_key, nested_value in value.items():
            if not isinstance(nested_key, str):
                raise TypeError("Context keys must be strings")
            if any(marker in nested_key.casefold() for marker in _SENSITIVE_MARKERS):
                nested[nested_key] = "[REDACTED]"
            else:
                nested[nested_key] = _sanitize_value(
                    nested_value,
                    reject_arrays=reject_arrays,
                    strict=strict,
                    depth=depth + 1,
                )
        return nested
    if isinstance(value, Sequence):
        if len(value) > _MAX_SEQUENCE_ITEMS:
            return _summarize(value)
        return [
            _sanitize_value(
                item,
                reject_arrays=reject_arrays,
                strict=strict,
                depth=depth + 1,
            )
            for item in value
        ]
    if strict:
        raise TypeError(f"Unsupported structured context value: {type(value).__name__}")
    return f"<{type(value).__name__} omitted>"


def _looks_like_array(value: object) -> bool:
    return hasattr(value, "shape") and hasattr(value, "dtype")


def _summarize(value: object) -> str:
    length = _safe_length(value)
    suffix = f", length={length}" if length is not None else ""
    shape = getattr(value, "shape", None)
    shape_suffix = f", shape={shape}" if shape is not None else ""
    return f"<{type(value).__name__} omitted{suffix}{shape_suffix}>"


def _safe_length(value: object) -> int | None:
    try:
        return len(value)  # type: ignore[arg-type]
    except (TypeError, AttributeError):
        return None


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    return value


def _thaw_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_value(item) for item in value]
    return value
