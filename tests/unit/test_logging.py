import io
import json
import logging
from collections.abc import Iterator

import pytest

from labdaq.application.logging import configure_logging


@pytest.fixture
def clean_labdaq_logger() -> Iterator[logging.Logger]:
    logger = logging.getLogger("labdaq")
    original_handlers = list(logger.handlers)
    original_level = logger.level
    original_propagate = logger.propagate
    logger.handlers.clear()
    yield logger
    for handler in logger.handlers:
        handler.close()
    logger.handlers[:] = original_handlers
    logger.setLevel(original_level)
    logger.propagate = original_propagate


def test_configuration_is_idempotent_and_does_not_modify_root(
    clean_labdaq_logger: logging.Logger,
) -> None:
    root_handlers = list(logging.getLogger().handlers)
    first_stream = io.StringIO()
    second_stream = io.StringIO()

    first = configure_logging(stream=first_stream)
    second = configure_logging(stream=second_stream)

    assert first is clean_labdaq_logger
    assert second is clean_labdaq_logger
    assert len(clean_labdaq_logger.handlers) == 1
    assert logging.getLogger().handlers == root_handlers


def test_json_logging_has_stable_fields_and_safe_context(
    clean_labdaq_logger: logging.Logger,
) -> None:
    stream = io.StringIO()
    logger = configure_logging(json_output=True, stream=stream)

    logger.warning(
        "Probe warning",
        extra={
            "event_code": "DAQ.CORE.PROBE_WARNING",
            "component": "diagnostics",
            "context": {
                "api_token": "secret",
                "samples": list(range(100)),
                "channel": "accel_x",
            },
            "session_id": "session-1",
        },
    )

    payload = json.loads(stream.getvalue())
    assert payload["event_code"] == "DAQ.CORE.PROBE_WARNING"
    assert payload["component"] == "diagnostics"
    assert payload["severity"] == "warning"
    assert payload["session_id"] == "session-1"
    assert payload["device_id"] is None
    assert payload["channel_id"] is None
    assert payload["context"]["api_token"] == "[REDACTED]"
    assert "omitted" in payload["context"]["samples"]
    assert payload["context"]["channel"] == "accel_x"
    assert payload["timestamp"].endswith("Z")


def test_human_logging_is_operator_readable(clean_labdaq_logger: logging.Logger) -> None:
    stream = io.StringIO()
    logger = configure_logging(stream=stream)

    logger.info(
        "Environment ready",
        extra={
            "event_code": "DAQ.CORE.READY",
            "component": "diagnostics",
            "context": {"status": "ok"},
        },
    )

    rendered = stream.getvalue()
    assert "DAQ.CORE.READY" in rendered
    assert "diagnostics: Environment ready" in rendered
    assert '"status":"ok"' in rendered


def test_json_logging_retains_debug_traceback(clean_labdaq_logger: logging.Logger) -> None:
    stream = io.StringIO()
    logger = configure_logging(json_output=True, stream=stream)

    def fail() -> None:
        raise RuntimeError("debug details")

    try:
        fail()
    except RuntimeError:
        logger.exception(
            "Probe failed",
            extra={"event_code": "DAQ.CORE.PROBE_FAILURE", "component": "diagnostics"},
        )

    payload = json.loads(stream.getvalue())
    assert "RuntimeError: debug details" in payload["exception"]


def test_non_mapping_and_unsupported_log_context_is_safely_omitted(
    clean_labdaq_logger: logging.Logger,
) -> None:
    stream = io.StringIO()
    logger = configure_logging(json_output=True, stream=stream)

    logger.info(
        "Context test",
        extra={"context": object(), "component": "test"},
    )

    payload = json.loads(stream.getvalue())
    assert payload["context"] == {"value": "<object omitted>"}
