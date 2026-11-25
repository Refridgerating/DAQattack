"""Version-1 JSON codecs and atomic repositories for DAQ qualification."""

from __future__ import annotations

import json
import os
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, cast

from jsonschema import Draft202012Validator, FormatChecker

from labdaq.domain.qualification import (
    ConfirmationDocument,
    EvidenceLink,
    EvidenceRetention,
    LimitComparison,
    LimitSourceCategory,
    MetricEvaluation,
    MetricValueKind,
    OperatorConfirmation,
    OverrideDisposition,
    QualificationClassification,
    QualificationExecutionMode,
    QualificationFingerprintInput,
    QualificationHardwareIdentity,
    QualificationLifecycle,
    QualificationLimit,
    QualificationMetric,
    QualificationOverride,
    QualificationPlan,
    QualificationReport,
    QualificationSampleCounts,
    QualificationStatus,
    QualificationTelemetry,
    QualificationTestDefinition,
    QualificationTestResult,
    SignalStage,
)
from labdaq.domain.serialization import canonical_json_bytes, to_canonical_primitive
from labdaq.errors import ConfigurationValidationError, QualificationError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from importlib.resources.abc import Traversable

_BUNDLED_PLANS = {
    "preflight-v1": "config/qualification-plans/preflight-v1.json",
    "full-v1": "config/qualification-plans/full-v1.json",
    "throughput-v1": "config/qualification-plans/throughput-v1.json",
}


class QualificationJsonCodec:
    """Decode and encode strict qualification documents without infrastructure leakage."""

    def load_plan(self, plan_or_path: str | Path) -> QualificationPlan:
        if str(plan_or_path) in _BUNDLED_PLANS:
            resource = _resource(_BUNDLED_PLANS[str(plan_or_path)])
            raw = _load_json_text(resource.read_text(encoding="utf-8"), str(resource), "plan")
        else:
            path = Path(plan_or_path)
            raw = _read_json(path, "plan")
        return self.decode_plan(raw, document=str(plan_or_path))

    def load_report(self, path: Path) -> QualificationReport:
        return self.decode_report(_read_json(path, "report"), document=str(path))

    def load_confirmations(self, path: Path) -> ConfirmationDocument:
        return self.decode_confirmations(_read_json(path, "confirmations"), document=str(path))

    def encode(self, value: object) -> dict[str, object]:
        primitive = to_canonical_primitive(value)
        if not isinstance(primitive, dict):
            raise TypeError("qualification JSON documents must encode to objects")
        return cast("dict[str, object]", primitive)

    def decode_plan(
        self,
        raw: Mapping[str, object],
        *,
        document: str | None = None,
    ) -> QualificationPlan:
        _validate(raw, "qualification-plan.schema.json", "qualification_plan", document)
        try:
            return QualificationPlan(
                schema_version=_integer(raw, "schema_version"),
                plan_id=_string(raw, "plan_id"),
                plan_revision=_integer(raw, "plan_revision"),
                hardware_profile_id=_optional_string(raw, "hardware_profile_id"),
                hardware_profile_revision=_optional_integer(raw, "hardware_profile_revision"),
                experiment_config_hash=_optional_string(raw, "experiment_config_hash"),
                tests=tuple(_decode_test(item) for item in _objects(raw, "tests")),
                acquisition_rate_hz=_number(raw, "acquisition_rate_hz"),
                powered_idle_duration_seconds=_number(raw, "powered_idle_duration_seconds"),
                assisted_window_duration_seconds=_number(raw, "assisted_window_duration_seconds"),
                throughput_duration_seconds=_number(raw, "throughput_duration_seconds"),
                maximum_report_age_seconds=_optional_number(raw, "maximum_report_age_seconds"),
                evidence_retention=EvidenceRetention(_string(raw, "evidence_retention")),
                assisted_settling_duration_seconds=(
                    _number(raw, "assisted_settling_duration_seconds")
                    if raw.get("assisted_settling_duration_seconds") is not None
                    else 0.0
                ),
            )
        except (KeyError, TypeError, ValueError) as cause:
            raise _construction_error("qualification_plan", document, cause) from cause

    def decode_confirmations(
        self,
        raw: Mapping[str, object],
        *,
        document: str | None = None,
    ) -> ConfirmationDocument:
        _validate(
            raw,
            "qualification-confirmations.schema.json",
            "qualification_confirmations",
            document,
        )
        try:
            return ConfirmationDocument(
                schema_version=_integer(raw, "schema_version"),
                confirmations=tuple(
                    _decode_confirmation(item) for item in _objects(raw, "confirmations")
                ),
            )
        except (KeyError, TypeError, ValueError) as cause:
            raise _construction_error("qualification_confirmations", document, cause) from cause

    def decode_report(
        self,
        raw: Mapping[str, object],
        *,
        document: str | None = None,
    ) -> QualificationReport:
        _validate(raw, "qualification-report.schema.json", "qualification_report", document)
        try:
            return QualificationReport(
                schema_version=_integer(raw, "schema_version"),
                report_id=_string(raw, "report_id"),
                lifecycle=QualificationLifecycle(_string(raw, "lifecycle")),
                created_at=_datetime(raw, "created_at"),
                updated_at=_datetime(raw, "updated_at"),
                operator=_string(raw, "operator"),
                application_version=_string(raw, "application_version"),
                operating_system=_string(raw, "operating_system"),
                plan_id=_string(raw, "plan_id"),
                plan_hash=_string(raw, "plan_hash"),
                hardware_profile_id=_string(raw, "hardware_profile_id"),
                hardware_profile_revision=_integer(raw, "hardware_profile_revision"),
                hardware_profile_hash=_string(raw, "hardware_profile_hash"),
                experiment_config_hash=_string(raw, "experiment_config_hash"),
                qualification_fingerprint=_string(raw, "qualification_fingerprint"),
                fingerprint_input=_decode_fingerprint(_object(raw, "fingerprint_input")),
                hardware_identity=_decode_hardware_identity(_object(raw, "hardware_identity")),
                results=tuple(_decode_result(item) for item in _objects(raw, "results")),
                overall_status=QualificationStatus(_string(raw, "overall_status")),
                blocking_reason_codes=_strings(raw, "blocking_reason_codes"),
                warning_reason_codes=_strings(raw, "warning_reason_codes"),
                skipped_test_ids=_strings(raw, "skipped_test_ids"),
                recommended_actions=_strings(raw, "recommended_actions"),
                sample_counts=_decode_counts(_object(raw, "sample_counts")),
                telemetry=_decode_telemetry(_object(raw, "telemetry")),
                evidence=tuple(_decode_evidence(item) for item in _objects(raw, "evidence")),
                session_relative_path=_string(raw, "session_relative_path"),
                override=_decode_override(_optional_object(raw, "override")),
                recovered_from_checkpoint=_boolean(raw, "recovered_from_checkpoint"),
            )
        except (KeyError, TypeError, ValueError) as cause:
            raise _construction_error("qualification_report", document, cause) from cause


class AtomicQualificationReportRepository:
    """Persist the exact plan once and atomically replace report checkpoints."""

    def __init__(self, codec: QualificationJsonCodec | None = None) -> None:
        self._codec = codec or QualificationJsonCodec()

    def initialize(self, session_path: Path, plan: QualificationPlan) -> None:
        target = session_path / "qualification-plan.json"
        if target.exists():
            existing = self._codec.decode_plan(_read_json(target, "plan"), document=str(target))
            if existing.content_hash != plan.content_hash:
                raise QualificationError(
                    "The qualification session already contains a different plan.",
                    code="DAQ.QUALIFICATION.PLAN_MISMATCH",
                    context={"path": str(target)},
                )
            return
        _atomic_write(target, canonical_json_bytes(plan), replace_existing=False)

    def checkpoint(self, session_path: Path, report: QualificationReport) -> Path:
        target = session_path / "qualification-report.json"
        _atomic_write(target, canonical_json_bytes(report), replace_existing=True)
        return target

    def load(self, report_path: Path) -> QualificationReport:
        return self._codec.load_report(report_path)

    def load_plan(self, plan_path: Path) -> QualificationPlan:
        return self._codec.load_plan(plan_path)


def _decode_test(raw: Mapping[str, object]) -> QualificationTestDefinition:
    return QualificationTestDefinition(
        test_id=_string(raw, "test_id"),
        version=_integer(raw, "version"),
        display_name=_string(raw, "display_name"),
        purpose=_string(raw, "purpose"),
        required=_boolean(raw, "required"),
        classification=QualificationClassification(_string(raw, "classification")),
        execution_mode=QualificationExecutionMode(_string(raw, "execution_mode")),
        required_inputs=_strings(raw, "required_inputs"),
        prerequisites=_strings(raw, "prerequisites"),
        declared_metrics=_strings(raw, "declared_metrics"),
        limits=tuple(_decode_limit(item) for item in _objects(raw, "limits")),
        required_confirmation_ids=_strings(raw, "required_confirmation_ids"),
        advisory_reason_codes=_strings(raw, "advisory_reason_codes"),
    )


def _decode_limit(raw: Mapping[str, object]) -> QualificationLimit:
    value = raw["value"]
    if isinstance(value, list):
        value = tuple(_number_value(item) for item in value)
    if value is not None and not isinstance(value, (float, int, bool, str, tuple)):
        raise TypeError("limit value has an unsupported type")
    return QualificationLimit(
        limit_id=_string(raw, "limit_id"),
        comparison=LimitComparison(_string(raw, "comparison")),
        value=cast("float | bool | str | tuple[float, float] | None", value),
        unit=_string(raw, "unit"),
        signal_stage=SignalStage(_string(raw, "signal_stage")),
        source_category=LimitSourceCategory(_string(raw, "source_category")),
        source_reference=_string(raw, "source_reference"),
        rationale=_string(raw, "rationale"),
        consequence=QualificationStatus(_string(raw, "consequence")),
        metric_name=_optional_string(raw, "metric_name"),
    )


def _decode_confirmation(raw: Mapping[str, object]) -> OperatorConfirmation:
    return OperatorConfirmation(
        confirmation_id=_string(raw, "confirmation_id"),
        confirmed=_boolean(raw, "confirmed"),
        confirmed_at=_datetime(raw, "confirmed_at"),
        operator=_string(raw, "operator"),
        statement=_string(raw, "statement"),
        evidence_reference=_optional_string(raw, "evidence_reference"),
        sample_index=_optional_integer(raw, "sample_index"),
        sample_count=_optional_integer(raw, "sample_count"),
    )


def _decode_result(raw: Mapping[str, object]) -> QualificationTestResult:
    return QualificationTestResult(
        test_id=_string(raw, "test_id"),
        version=_integer(raw, "version"),
        started_at=_datetime(raw, "started_at"),
        completed_at=_datetime(raw, "completed_at"),
        status=QualificationStatus(_string(raw, "status")),
        classification=QualificationClassification(_string(raw, "classification")),
        required=_boolean(raw, "required"),
        summary=_string(raw, "summary"),
        reason_codes=_strings(raw, "reason_codes"),
        metrics=tuple(_decode_metric(item) for item in _objects(raw, "metrics")),
        event_codes=_strings(raw, "event_codes"),
        evidence=tuple(_decode_evidence(item) for item in _objects(raw, "evidence")),
        confirmations=tuple(_decode_confirmation(item) for item in _objects(raw, "confirmations")),
        assumptions=_strings(raw, "assumptions"),
    )


def _decode_metric(raw: Mapping[str, object]) -> QualificationMetric:
    value = raw["value"]
    if not isinstance(value, (float, int, bool, str)):
        raise TypeError("metric value has an unsupported type")
    limit_raw = _optional_object(raw, "limit")
    return QualificationMetric(
        name=_string(raw, "name"),
        value_kind=MetricValueKind(_string(raw, "value_kind")),
        value=cast("float | bool | str", value),
        unit=_string(raw, "unit"),
        signal_stage=SignalStage(_string(raw, "signal_stage")),
        evaluation=MetricEvaluation(_string(raw, "evaluation")),
        method=_string(raw, "method"),
        software_version=_string(raw, "software_version"),
        channel_id=_optional_string(raw, "channel_id"),
        first_sample_index=_optional_integer(raw, "first_sample_index"),
        sample_count=_optional_integer(raw, "sample_count"),
        limit=None if limit_raw is None else _decode_limit(limit_raw),
    )


def _decode_evidence(raw: Mapping[str, object]) -> EvidenceLink:
    return EvidenceLink(
        artifact_type=_string(raw, "artifact_type"),
        relative_path=_string(raw, "relative_path"),
        sha256=_optional_string(raw, "sha256"),
        disposition=_string(raw, "disposition"),
    )


def _decode_hardware_identity(raw: Mapping[str, object]) -> QualificationHardwareIdentity:
    return QualificationHardwareIdentity(
        backend_id=_string(raw, "backend_id"),
        device_id=_optional_string(raw, "device_id"),
        device_alias=_optional_string(raw, "device_alias"),
        device_model=_optional_string(raw, "device_model"),
        device_serial_number=_optional_string(raw, "device_serial_number"),
        module_id=_optional_string(raw, "module_id"),
        module_model=_optional_string(raw, "module_model"),
        module_serial_number=_optional_string(raw, "module_serial_number"),
        ordered_physical_channels=_strings(raw, "ordered_physical_channels"),
        driver_version=_optional_string(raw, "driver_version"),
    )


def _decode_fingerprint(raw: Mapping[str, object]) -> QualificationFingerprintInput:
    ranges = tuple(
        cast("tuple[float, float]", tuple(_number_value(value) for value in item))
        for item in _arrays(raw, "requested_input_ranges_v")
    )
    return QualificationFingerprintInput(
        plan_hash=_string(raw, "plan_hash"),
        hardware_profile_hash=_string(raw, "hardware_profile_hash"),
        hardware_profile_id=_string(raw, "hardware_profile_id"),
        hardware_profile_revision=_integer(raw, "hardware_profile_revision"),
        experiment_config_hash=_string(raw, "experiment_config_hash"),
        acquisition_rate_hz=_number(raw, "acquisition_rate_hz"),
        acquisition_duration_seconds=_number(raw, "acquisition_duration_seconds"),
        samples_per_read=_integer(raw, "samples_per_read"),
        recording_queue_capacity_chunks=_integer(raw, "recording_queue_capacity_chunks"),
        storage_location=_string(raw, "storage_location"),
        ordered_logical_channels=_strings(raw, "ordered_logical_channels"),
        ordered_physical_channels=_strings(raw, "ordered_physical_channels"),
        requested_input_ranges_v=ranges,
        sensor_identities=_strings(raw, "sensor_identities"),
        calibration_identities=_strings(raw, "calibration_identities"),
        conditioning_identities=_strings(raw, "conditioning_identities"),
        hardware_identity=_decode_hardware_identity(_object(raw, "hardware_identity")),
    )


def _decode_counts(raw: Mapping[str, object]) -> QualificationSampleCounts:
    return QualificationSampleCounts(
        expected_per_channel=_integer(raw, "expected_per_channel"),
        acquired_per_channel=_integer(raw, "acquired_per_channel"),
        persisted_per_channel=_integers(raw, "persisted_per_channel"),
        replayed_per_channel=_integers(raw, "replayed_per_channel"),
        missing=_integer(raw, "missing"),
        duplicated=_integer(raw, "duplicated"),
        reordered=_integer(raw, "reordered"),
        display_dropped=_integer(raw, "display_dropped"),
        invalid=_integer(raw, "invalid"),
    )


def _decode_telemetry(raw: Mapping[str, object]) -> QualificationTelemetry:
    return QualificationTelemetry(
        read_count=_integer(raw, "read_count"),
        read_duration_total_seconds=_number(raw, "read_duration_total_seconds"),
        read_duration_max_seconds=_number(raw, "read_duration_max_seconds"),
        write_duration_total_seconds=_number(raw, "write_duration_total_seconds"),
        write_duration_max_seconds=_number(raw, "write_duration_max_seconds"),
        recording_queue_high_water_chunks=_integer(raw, "recording_queue_high_water_chunks"),
        recording_queue_capacity_chunks=_integer(raw, "recording_queue_capacity_chunks"),
        recording_queue_max_utilization=_number(raw, "recording_queue_max_utilization"),
        backpressure_events=_integer(raw, "backpressure_events"),
        disk_free_before_bytes=_optional_integer(raw, "disk_free_before_bytes"),
        disk_free_after_bytes=_optional_integer(raw, "disk_free_after_bytes"),
        stop_latency_seconds=_optional_number(raw, "stop_latency_seconds"),
        cleanup_succeeded=_optional_boolean(raw, "cleanup_succeeded"),
    )


def _decode_override(raw: Mapping[str, object] | None) -> QualificationOverride | None:
    if raw is None:
        return None
    return QualificationOverride(
        reason_code=_string(raw, "reason_code"),
        operator=_string(raw, "operator"),
        rationale=_string(raw, "rationale"),
        acknowledged_at=_datetime(raw, "acknowledged_at"),
        disposition=OverrideDisposition(_string(raw, "disposition")),
    )


def _atomic_write(target: Path, payload: bytes, *, replace_existing: bool) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    if temporary.exists():
        temporary.unlink()
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        if not replace_existing and target.exists():
            raise FileExistsError(target)
        os.replace(temporary, target)
    except OSError as cause:
        if temporary.exists():
            temporary.unlink()
        raise QualificationError(
            "The qualification checkpoint could not be written atomically.",
            code="DAQ.QUALIFICATION.REPORT_WRITE_FAILED",
            context={"path": str(target)},
            cause=cause,
        ) from cause


def _validate(
    raw: Mapping[str, object],
    schema_name: str,
    document_type: str,
    document: str | None,
) -> None:
    version = raw.get("schema_version")
    if version != 1 or isinstance(version, bool):
        raise ConfigurationValidationError(
            "The qualification document uses an unsupported schema version.",
            document_type=document_type,
            document=document,
            issues=[{"field_path": "schema_version", "rule": "must equal integer 1"}],
        )
    schema = json.loads(_resource(f"schemas/v1/{schema_name}").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(raw), key=lambda error: list(error.absolute_path))
    if errors:
        raise ConfigurationValidationError(
            "The qualification document does not match its JSON Schema.",
            document_type=document_type,
            document=document,
            issues=[
                {
                    "field_path": "$" + "".join(f"[{part}]" for part in error.absolute_path),
                    "rule": error.message,
                }
                for error in errors[:32]
            ],
        )


def _construction_error(
    document_type: str,
    document: str | None,
    cause: BaseException,
) -> ConfigurationValidationError:
    return ConfigurationValidationError(
        "The qualification document contains an invalid domain value.",
        document_type=document_type,
        document=document,
        issues=[{"field_path": "$", "rule": str(cause)}],
        cause=cause,
    )


def _read_json(path: Path, kind: str) -> dict[str, object]:
    try:
        return _load_json_text(path.read_text(encoding="utf-8"), str(path), kind)
    except OSError as cause:
        raise QualificationError(
            "The qualification document could not be read.",
            code="DAQ.QUALIFICATION.REPORT_READ_FAILED",
            context={"path": str(path), "document_kind": kind},
            cause=cause,
        ) from cause


def _load_json_text(payload: str, document: str, kind: str) -> dict[str, object]:
    try:
        raw = json.loads(payload)
    except json.JSONDecodeError as cause:
        raise QualificationError(
            "The qualification document is not valid JSON.",
            code="DAQ.QUALIFICATION.REPORT_INVALID",
            context={"path": document, "document_kind": kind},
            cause=cause,
        ) from cause
    if not isinstance(raw, dict):
        raise QualificationError(
            "The qualification document must contain a JSON object.",
            code="DAQ.QUALIFICATION.REPORT_INVALID",
            context={"path": document, "document_kind": kind},
        )
    return cast("dict[str, object]", raw)


def _resource(relative_path: str) -> Traversable:
    resource: Traversable = files("labdaq.resources")
    for part in relative_path.split("/"):
        resource = resource.joinpath(part)
    return resource


def _object(raw: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = raw[key]
    if not isinstance(value, dict):
        raise TypeError(f"{key} must be an object")
    return cast("Mapping[str, object]", value)


def _optional_object(raw: Mapping[str, object], key: str) -> Mapping[str, object] | None:
    value = raw.get(key)
    return None if value is None else _object(raw, key)


def _objects(raw: Mapping[str, object], key: str) -> tuple[Mapping[str, object], ...]:
    return tuple(_mapping(item, key) for item in _array(raw, key))


def _mapping(value: object, key: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"{key} values must be objects")
    return cast("Mapping[str, object]", value)


def _array(raw: Mapping[str, object], key: str) -> list[object]:
    value = raw[key]
    if not isinstance(value, list):
        raise TypeError(f"{key} must be an array")
    return cast("list[object]", value)


def _arrays(raw: Mapping[str, object], key: str) -> tuple[list[object], ...]:
    values = _array(raw, key)
    if any(not isinstance(item, list) for item in values):
        raise TypeError(f"{key} values must be arrays")
    return cast("tuple[list[object], ...]", tuple(values))


def _string(raw: Mapping[str, object], key: str) -> str:
    value = raw[key]
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _optional_string(raw: Mapping[str, object], key: str) -> str | None:
    value = raw.get(key)
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{key} must be a string or null")
    return value


def _strings(raw: Mapping[str, object], key: str) -> tuple[str, ...]:
    values = _array(raw, key)
    if any(not isinstance(item, str) for item in values):
        raise TypeError(f"{key} values must be strings")
    return cast("tuple[str, ...]", tuple(values))


def _integer(raw: Mapping[str, object], key: str) -> int:
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an integer")
    return value


def _optional_integer(raw: Mapping[str, object], key: str) -> int | None:
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an integer or null")
    return value


def _integers(raw: Mapping[str, object], key: str) -> tuple[int, ...]:
    values = _array(raw, key)
    if any(isinstance(item, bool) or not isinstance(item, int) for item in values):
        raise TypeError(f"{key} values must be integers")
    return cast("tuple[int, ...]", tuple(values))


def _number(raw: Mapping[str, object], key: str) -> float:
    return _number_value(raw[key])


def _number_value(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("value must be numeric")
    return float(value)


def _optional_number(raw: Mapping[str, object], key: str) -> float | None:
    value = raw.get(key)
    return None if value is None else _number_value(value)


def _boolean(raw: Mapping[str, object], key: str) -> bool:
    value = raw[key]
    if not isinstance(value, bool):
        raise TypeError(f"{key} must be a boolean")
    return value


def _optional_boolean(raw: Mapping[str, object], key: str) -> bool | None:
    value = raw.get(key)
    if value is not None and not isinstance(value, bool):
        raise TypeError(f"{key} must be a boolean or null")
    return value


def _datetime(raw: Mapping[str, object], key: str) -> datetime:
    value = _string(raw, key)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
