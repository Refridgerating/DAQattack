from types import MappingProxyType

import pytest

from labdaq.errors import (
    AcquisitionError,
    ApplicationStateError,
    ConfigurationError,
    DependencyUnavailableError,
    ErrorSeverity,
    ExportError,
    HardwareError,
    LabDAQError,
    ProcessingError,
    StorageError,
)


@pytest.mark.parametrize(
    "error_type",
    [
        LabDAQError,
        ConfigurationError,
        DependencyUnavailableError,
        HardwareError,
        AcquisitionError,
        StorageError,
        ProcessingError,
        ExportError,
        ApplicationStateError,
    ],
)
def test_error_hierarchy_preserves_serialization_contract(
    error_type: type[LabDAQError],
) -> None:
    error = error_type(
        "Something failed.",
        code="DAQ.CORE.INVALID_ARGUMENT",
        severity=ErrorSeverity.WARNING,
        context={"channel": "accel_x"},
    )

    assert error.to_dict() == {
        "code": "DAQ.CORE.INVALID_ARGUMENT",
        "message": "Something failed.",
        "severity": "warning",
        "context": {"channel": "accel_x"},
        "remediation": None,
    }
    assert str(error) == "[DAQ.CORE.INVALID_ARGUMENT] Something failed."


def test_context_is_deeply_copied_and_frozen() -> None:
    source = {"nested": {"values": [1, 2]}}
    error = LabDAQError("Failed.", code="DAQ.CORE.INVALID_ARGUMENT", context=source)
    source["nested"] = {}

    assert error.to_dict()["context"] == {"nested": {"values": [1, 2]}}
    assert isinstance(error.context, MappingProxyType)
    nested = error.context["nested"]
    assert isinstance(nested, MappingProxyType)
    with pytest.raises(TypeError):
        nested["value"] = 3  # type: ignore[index]


def test_cause_is_preserved_but_not_serialized() -> None:
    cause = RuntimeError("driver details")
    error = LabDAQError(
        "Operation failed.",
        code="DAQ.CORE.INVALID_ARGUMENT",
        cause=cause,
    )

    assert error.cause is cause
    assert error.__cause__ is cause
    assert "cause" not in error.to_dict()


def test_sensitive_context_is_redacted() -> None:
    error = LabDAQError(
        "Operation failed.",
        code="DAQ.CORE.INVALID_ARGUMENT",
        context={"api_token": "do-not-serialize", "safe": "visible"},
    )

    assert error.to_dict()["context"] == {"api_token": "[REDACTED]", "safe": "visible"}


def test_raw_payloads_and_array_like_values_are_rejected() -> None:
    class ArrayLike:
        shape = (4, 100)
        dtype = "float64"

    with pytest.raises(ValueError, match="Raw sample payload"):
        LabDAQError(
            "Operation failed.",
            code="DAQ.CORE.INVALID_ARGUMENT",
            context={"samples": [1, 2, 3]},
        )
    with pytest.raises(ValueError, match="Array-like"):
        LabDAQError(
            "Operation failed.",
            code="DAQ.CORE.INVALID_ARGUMENT",
            context={"payload": ArrayLike()},
        )


@pytest.mark.parametrize(
    "code",
    ["CORE.INVALID", "DAQ.core.INVALID", "DAQ.CORE.invalid", "DAQ.CORE"],
)
def test_invalid_error_codes_are_rejected(code: str) -> None:
    with pytest.raises(ValueError, match="Invalid LabDAQ error code"):
        LabDAQError("Invalid.", code=code)


def test_empty_messages_and_unsupported_context_values_are_rejected() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        LabDAQError("", code="DAQ.CORE.INVALID_ARGUMENT")
    with pytest.raises(TypeError, match="Bytes"):
        LabDAQError(
            "Invalid.",
            code="DAQ.CORE.INVALID_ARGUMENT",
            context={"payload": b"bytes"},
        )
    with pytest.raises(TypeError, match="Unsupported"):
        LabDAQError(
            "Invalid.",
            code="DAQ.CORE.INVALID_ARGUMENT",
            context={"payload": object()},
        )


def test_nested_sensitive_values_are_redacted_and_long_sequences_are_bounded() -> None:
    error = LabDAQError(
        "Invalid.",
        code="DAQ.CORE.INVALID_ARGUMENT",
        context={
            "nested": {"password": "hidden"},
            "history": list(range(100)),
            "description": "x" * 1100,
        },
    )
    context = error.to_dict()["context"]

    assert context["nested"] == {"password": "[REDACTED]"}
    assert "length=100" in context["history"]
    assert context["description"].endswith("<truncated>")


def test_non_string_context_keys_are_rejected() -> None:
    with pytest.raises(TypeError, match="keys must be strings"):
        LabDAQError(
            "Invalid.",
            code="DAQ.CORE.INVALID_ARGUMENT",
            context={1: "value"},  # type: ignore[dict-item]
        )
