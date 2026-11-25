"""JSON serialization adapter for DAQ-M06 scientific records."""

from __future__ import annotations

import json
from datetime import date
from importlib.resources import files
from typing import TYPE_CHECKING, Never, cast

from jsonschema import Draft202012Validator, FormatChecker

from labdaq.domain.enums import (
    CalibrationQuality,
    CalibrationSourceKind,
    ChannelRole,
    ProcessingMode,
    Unit,
)
from labdaq.domain.models import (
    ProcessingNotice,
    ProcessingProvenance,
    ProcessingRange,
    ResolvedChannelCalibration,
)
from labdaq.domain.serialization import canonical_sha256_excluding, to_canonical_primitive
from labdaq.errors import ProcessingError

if TYPE_CHECKING:
    from collections.abc import Mapping


class JsonProcessingRecordAdapter:
    """Round-trip version-1 calibration and provenance records."""

    def dumps_calibration(self, value: ResolvedChannelCalibration) -> str:
        payload = _record_dict(value)
        _validate(payload, "calibration-record.schema.json")
        return json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"

    def loads_calibration(self, payload: str) -> ResolvedChannelCalibration:
        raw = _load_object(payload, "CALIBRATION_INVALID")
        _validate(raw, "calibration-record.schema.json")
        try:
            record = ResolvedChannelCalibration(
                schema_version=_integer(raw, "schema_version"),
                calibration_id=_string(raw, "calibration_id"),
                calibration_hash=_string(raw, "calibration_hash"),
                logical_channel_id=_string(raw, "logical_channel_id"),
                channel_role=ChannelRole(_string(raw, "channel_role")),
                sensor_id=_string(raw, "sensor_id"),
                sensor_model=_string(raw, "sensor_model"),
                sensor_serial_number=_string(raw, "sensor_serial_number"),
                input_unit=Unit(_string(raw, "input_unit")),
                output_unit=Unit(_string(raw, "output_unit")),
                sensitivity_v_per_output_unit=_number(raw, "sensitivity_v_per_output_unit"),
                input_offset_v=_number(raw, "input_offset_v"),
                polarity_multiplier=_integer(raw, "polarity_multiplier"),
                quality=CalibrationQuality(_string(raw, "quality")),
                source_kind=CalibrationSourceKind(_string(raw, "source_kind")),
                source_reference=_string(raw, "source_reference"),
                effective_date=(
                    date.fromisoformat(_string(raw, "effective_date"))
                    if raw.get("effective_date") is not None
                    else None
                ),
                valid_input_min_v=_optional_number(raw, "valid_input_min_v"),
                valid_input_max_v=_optional_number(raw, "valid_input_max_v"),
                valid_output_min=_optional_number(raw, "valid_output_min"),
                valid_output_max=_optional_number(raw, "valid_output_max"),
                validated_frequency_min_hz=_optional_number(raw, "validated_frequency_min_hz"),
                validated_frequency_max_hz=_optional_number(raw, "validated_frequency_max_hz"),
                extended_frequency_min_hz=_optional_number(raw, "extended_frequency_min_hz"),
                extended_frequency_max_hz=_optional_number(raw, "extended_frequency_max_hz"),
                uncertainty=_optional_string(raw, "uncertainty"),
                notes=_optional_string(raw, "notes"),
                expiration_date=_optional_date(raw, "expiration_date"),
                certificate_identifier=_optional_string(raw, "certificate_identifier"),
                conditions=_optional_string(raw, "conditions"),
            )
        except (KeyError, TypeError, ValueError) as cause:
            _decode_fault("CALIBRATION_INVALID", cause)
        if record.calibration_hash != canonical_sha256_excluding(record, "calibration_hash"):
            _hash_fault("CALIBRATION_INVALID", "calibration_hash")
        return record

    def dumps_provenance(self, value: ProcessingProvenance) -> str:
        payload = _record_dict(value)
        _validate(payload, "processing-provenance.schema.json")
        return json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"

    def loads_provenance(self, payload: str) -> ProcessingProvenance:
        raw = _load_object(payload, "PROVENANCE_INVALID")
        _validate(raw, "processing-provenance.schema.json")
        try:
            range_value = _object(raw, "source_range")
            notices = tuple(_notice(item) for item in _object_array(raw, "notices"))
            details = _object(raw, "algorithm_details")
            record = ProcessingProvenance(
                schema_version=_integer(raw, "schema_version"),
                provenance_hash=_string(raw, "provenance_hash"),
                source_session_id=_string(raw, "source_session_id"),
                source_completion_state=_string(raw, "source_completion_state"),
                source_raw_digest=_string(raw, "source_raw_digest"),
                source_range=ProcessingRange(
                    _integer(range_value, "start_sample_index"),
                    _integer(range_value, "stop_sample_index"),
                ),
                ordered_channel_ids=tuple(_string_array(raw, "ordered_channel_ids")),
                sample_rate_hz=_number(raw, "sample_rate_hz"),
                calibration_ids=tuple(_string_array(raw, "calibration_ids")),
                calibration_hashes=tuple(_string_array(raw, "calibration_hashes")),
                calibration_qualities=tuple(
                    CalibrationQuality(value)
                    for value in _string_array(raw, "calibration_qualities")
                ),
                recipe_id=_string(raw, "recipe_id"),
                recipe_version=_string(raw, "recipe_version"),
                recipe_hash=_string(raw, "recipe_hash"),
                processing_mode=ProcessingMode(_string(raw, "processing_mode")),
                application_version=_string(raw, "application_version"),
                numpy_version=_string(raw, "numpy_version"),
                scipy_version=_string(raw, "scipy_version"),
                output_units=tuple(Unit(value) for value in _string_array(raw, "output_units")),
                notices=notices,
                algorithm_details=details,
            )
        except (KeyError, TypeError, ValueError) as cause:
            _decode_fault("PROVENANCE_INVALID", cause)
        if record.provenance_hash != canonical_sha256_excluding(record, "provenance_hash"):
            _hash_fault("PROVENANCE_INVALID", "provenance_hash")
        return record


def _record_dict(value: object) -> dict[str, object]:
    result = to_canonical_primitive(value)
    if not isinstance(result, dict):
        raise TypeError("processing record must encode to an object")
    return cast("dict[str, object]", result)


def _load_object(payload: str, suffix: str) -> dict[str, object]:
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as cause:
        _decode_fault(suffix, cause)
    if not isinstance(value, dict):
        _decode_fault(suffix, TypeError("top-level JSON must be an object"))
    return cast("dict[str, object]", value)


def _validate(value: Mapping[str, object], schema_name: str) -> None:
    resource = files("labdaq.resources").joinpath("schemas", "v1", schema_name)
    schema = json.loads(resource.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        raise ProcessingError(
            "A processing record does not match its versioned JSON schema.",
            code="DAQ.PROCESSING.RECORD_SCHEMA_INVALID",
            context={"schema": schema_name, "issues": [error.message for error in errors[:16]]},
            remediation="Use an unmodified version-1 processing record.",
        )


def _decode_fault(suffix: str, cause: BaseException) -> Never:
    raise ProcessingError(
        "A processing record could not be decoded.",
        code=f"DAQ.PROCESSING.{suffix}",
        context={"exception_type": type(cause).__name__},
        remediation="Use an unmodified version-1 processing record.",
        cause=cause,
    ) from cause


def _hash_fault(suffix: str, field: str) -> Never:
    raise ProcessingError(
        "A processing record's deterministic hash does not match its content.",
        code=f"DAQ.PROCESSING.{suffix}",
        context={"field": field},
        remediation="Recreate the record from its verified scientific inputs.",
    )


def _string(value: Mapping[str, object], key: str) -> str:
    item = value[key]
    if not isinstance(item, str):
        raise TypeError(f"{key} must be a string")
    return item


def _integer(value: Mapping[str, object], key: str) -> int:
    item = value[key]
    if isinstance(item, bool) or not isinstance(item, int):
        raise TypeError(f"{key} must be an integer")
    return item


def _number(value: Mapping[str, object], key: str) -> float:
    item = value[key]
    if isinstance(item, bool) or not isinstance(item, (int, float)):
        raise TypeError(f"{key} must be numeric")
    return float(item)


def _optional_number(value: Mapping[str, object], key: str) -> float | None:
    item = value.get(key)
    if item is None:
        return None
    if isinstance(item, bool) or not isinstance(item, (int, float)):
        raise TypeError(f"{key} must be numeric or null")
    return float(item)


def _object(value: Mapping[str, object], key: str) -> Mapping[str, object]:
    item = value[key]
    if not isinstance(item, dict):
        raise TypeError(f"{key} must be an object")
    return cast("Mapping[str, object]", item)


def _string_array(value: Mapping[str, object], key: str) -> list[str]:
    item = value[key]
    if not isinstance(item, list) or not all(isinstance(member, str) for member in item):
        raise TypeError(f"{key} must be a string array")
    return cast("list[str]", item)


def _object_array(value: Mapping[str, object], key: str) -> list[Mapping[str, object]]:
    item = value[key]
    if not isinstance(item, list) or not all(isinstance(member, dict) for member in item):
        raise TypeError(f"{key} must be an object array")
    return cast("list[Mapping[str, object]]", item)


def _notice(value: Mapping[str, object]) -> ProcessingNotice:
    return ProcessingNotice(
        code=_string(value, "code"),
        severity=_string(value, "severity"),
        message=_string(value, "message"),
        channel_id=_optional_string(value, "channel_id"),
        start_sample_index=_optional_integer(value, "start_sample_index"),
        stop_sample_index=_optional_integer(value, "stop_sample_index"),
    )


def _optional_string(value: Mapping[str, object], key: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str):
        raise TypeError(f"{key} must be a string or null")
    return item


def _optional_date(value: Mapping[str, object], key: str) -> date | None:
    item = _optional_string(value, key)
    return date.fromisoformat(item) if item is not None else None


def _optional_integer(value: Mapping[str, object], key: str) -> int | None:
    item = value.get(key)
    if item is None:
        return None
    if isinstance(item, bool) or not isinstance(item, int):
        raise TypeError(f"{key} must be an integer or null")
    return item
