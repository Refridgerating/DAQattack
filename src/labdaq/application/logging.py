"""Explicit, idempotent operational logging configuration."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TextIO

from labdaq._context import sanitize_context

_LOGGER_NAME = "labdaq"


class _LabDAQHandler(logging.StreamHandler[TextIO]):
    """Handler type used to identify LabDAQ-owned logging configuration."""


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = _base_payload(record)
        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


class _HumanFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = _base_payload(record)
        context = json.dumps(payload["context"], sort_keys=True, separators=(",", ":"))
        rendered = (
            f"{payload['timestamp']} {payload['severity']} {payload['logger']} "
            f"[{payload['event_code']}] {payload['component']}: {payload['message']} "
            f"context={context}"
        )
        if record.exc_info is not None:
            rendered = f"{rendered}\n{self.formatException(record.exc_info)}"
        return rendered


def configure_logging(
    *,
    level: int | str = logging.INFO,
    json_output: bool = False,
    stream: TextIO | None = None,
) -> logging.Logger:
    """Configure the LabDAQ logger without modifying global root handlers."""
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    for handler in tuple(logger.handlers):
        if isinstance(handler, _LabDAQHandler):
            logger.removeHandler(handler)
            handler.close()

    owned_handler = _LabDAQHandler(stream or sys.stderr)
    owned_handler.setLevel(level)
    owned_handler.setFormatter(_JsonFormatter() if json_output else _HumanFormatter())
    logger.addHandler(owned_handler)
    return logger


def _base_payload(record: logging.LogRecord) -> dict[str, object]:
    raw_context = getattr(record, "context", {})
    context: Mapping[str, object]
    if isinstance(raw_context, Mapping):
        context = raw_context
    else:
        context = {"value": raw_context}
    sanitized_context = sanitize_context(
        context,
        reject_arrays=False,
        strict=False,
    )
    return {
        "timestamp": _utc_timestamp(record.created),
        "logger": record.name,
        "severity": record.levelname.lower(),
        "message": record.getMessage(),
        "event_code": getattr(record, "event_code", "DAQ.CORE.LOG"),
        "component": getattr(record, "component", record.name),
        "context": sanitized_context,
        "session_id": getattr(record, "session_id", None),
        "device_id": getattr(record, "device_id", None),
        "channel_id": getattr(record, "channel_id", None),
    }


def _utc_timestamp(epoch_seconds: float) -> str:
    return (
        datetime.fromtimestamp(epoch_seconds, tz=UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
