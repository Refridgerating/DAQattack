"""Version-1 JSON codecs for DAQ-M07 requests and export records."""

from __future__ import annotations

import json
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Never, cast

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from labdaq.domain.enums import SignalStage
from labdaq.domain.plotting import (
    ChannelSelection,
    CollisionPolicy,
    DecimationAlgorithm,
    ExportArtifact,
    ExportFault,
    ExportFormat,
    ExportJobState,
    ExportManifest,
    ExportProduct,
    ExportRequest,
    FigureOptionsV1,
    PlotRepresentation,
    PlotRequest,
    PlotViewport,
    SampleRange,
)
from labdaq.errors import ExportError

if TYPE_CHECKING:
    from collections.abc import Mapping


class JsonPlotExportCodec:
    """Validate and round-trip the durable M07 JSON document families."""

    def dumps_plot_request(self, value: PlotRequest) -> str:
        return _dumps(value.to_dict(), "plot-request.schema.json")

    def loads_plot_request(self, payload: str) -> PlotRequest:
        raw = _load(payload, "plot-request.schema.json")
        try:
            return PlotRequest(
                source_session=Path(_string(raw, "source_session")),
                representation=PlotRepresentation(_string(raw, "representation")),
                channels=_channels(_mapping(raw, "channels")),
                sample_range=_range(_mapping(raw, "sample_range")),
                viewport=_viewport(_mapping(raw, "viewport")),
                recipe_id=_optional_string(raw, "recipe_id"),
                spectral_input_stage=_optional_stage(raw, "spectral_input_stage"),
                schema_version=_integer(raw, "schema_version"),
            )
        except (KeyError, TypeError, ValueError) as cause:
            _decode_error("plot request", cause)

    def dumps_export_request(self, value: ExportRequest) -> str:
        return _dumps(value.to_dict(), "export-request.schema.json")

    def loads_export_request(self, payload: str) -> ExportRequest:
        raw = _load(payload, "export-request.schema.json")
        try:
            figure = _mapping(raw, "figure_options")
            destination = _optional_string(raw, "destination_directory")
            return ExportRequest(
                source_session=Path(_string(raw, "source_session")),
                product=ExportProduct(_string(raw, "product")),
                output_format=ExportFormat(_string(raw, "output_format")),
                channels=_channels(_mapping(raw, "channels")),
                sample_range=_range(_mapping(raw, "sample_range")),
                representation=PlotRepresentation(_string(raw, "representation")),
                recipe_id=_optional_string(raw, "recipe_id"),
                spectral_input_stage=_optional_stage(raw, "spectral_input_stage"),
                destination_directory=Path(destination) if destination else None,
                base_name=_optional_string(raw, "base_name"),
                figure_options=FigureOptionsV1(
                    schema_version=_integer(figure, "schema_version"),
                    width_inches=_number(figure, "width_inches"),
                    height_inches=_number(figure, "height_inches"),
                    dpi=_integer(figure, "dpi"),
                    title=_optional_string(figure, "title"),
                    logarithmic_y=_boolean(figure, "logarithmic_y"),
                ),
                max_points=_integer(raw, "max_points"),
                chunk_size=_integer(raw, "chunk_size"),
                collision_policy=CollisionPolicy(_string(raw, "collision_policy")),
                schema_version=_integer(raw, "schema_version"),
            )
        except (KeyError, TypeError, ValueError) as cause:
            _decode_error("export request", cause)

    def dumps_sidecar(self, value: Mapping[str, object]) -> str:
        return _dumps(value, "export-sidecar.schema.json")

    def loads_sidecar(self, payload: str) -> dict[str, object]:
        return dict(_load(payload, "export-sidecar.schema.json"))

    def dumps_manifest(self, value: ExportManifest) -> str:
        return _dumps(value.to_dict(), "export-manifest.schema.json")

    def dumps_artifact_document(self, value: Mapping[str, object]) -> str:
        if "final_state" in value:
            return _dumps(value, "export-manifest.schema.json")
        if "output_sha256" in value:
            return _dumps(value, "export-sidecar.schema.json")
        raise ExportError(
            "The export JSON document family is unknown.",
            code="DAQ.EXPORT.DOCUMENT_INVALID",
        )

    def loads_manifest(self, payload: str) -> ExportManifest:
        raw = _load(payload, "export-manifest.schema.json")
        try:
            progress = _mapping(raw, "progress")
            artifacts = tuple(
                _artifact(cast("Mapping[str, object]", item))
                for item in _sequence(raw, "artifacts")
            )
            faults = tuple(
                ExportFault(
                    code=_string(cast("Mapping[str, object]", item), "code"),
                    message=_string(cast("Mapping[str, object]", item), "message"),
                    context=_mapping(cast("Mapping[str, object]", item), "context"),
                )
                for item in _sequence(raw, "faults")
            )
            return ExportManifest(
                schema_version=_integer(raw, "schema_version"),
                export_job_id=_string(raw, "export_job_id"),
                source_session_id=_string(raw, "source_session_id"),
                source_manifest_sha256=_string(raw, "source_manifest_sha256"),
                application_version=_string(raw, "application_version"),
                export_service_version=_string(raw, "export_service_version"),
                requests=tuple(
                    cast("Mapping[str, object]", item) for item in _sequence(raw, "requests")
                ),
                artifacts=artifacts,
                warnings=tuple(_strings(raw, "warnings")),
                faults=faults,
                created_at=datetime.fromisoformat(_string(raw, "created_at")),
                completed_at=datetime.fromisoformat(_string(raw, "completed_at")),
                final_state=ExportJobState(_string(raw, "final_state")),
                processed_samples=_integer(progress, "processed_samples"),
                total_samples=_integer(progress, "total_samples"),
                completed_artifacts=_integer(progress, "completed_artifacts"),
                total_artifacts=_integer(progress, "total_artifacts"),
                manifest_path=Path(_string(raw, "manifest_path")),
            )
        except (KeyError, TypeError, ValueError) as cause:
            _decode_error("export manifest", cause)


def _artifact(raw: Mapping[str, object]) -> ExportArtifact:
    return ExportArtifact(
        artifact_id=_string(raw, "artifact_id"),
        export_job_id=_string(raw, "export_job_id"),
        source_session_id=_string(raw, "source_session_id"),
        product=ExportProduct(_string(raw, "product")),
        output_format=ExportFormat(_string(raw, "format")),
        relative_path=_string(raw, "relative_path"),
        byte_count=_integer(raw, "byte_count"),
        sha256=_string(raw, "sha256"),
        sidecar_relative_path=_string(raw, "sidecar_relative_path"),
        sidecar_sha256=_string(raw, "sidecar_sha256"),
        channel_ids=tuple(_strings(raw, "channel_ids")),
        sample_range=_range(_mapping(raw, "sample_range")),
        representation=PlotRepresentation(_string(raw, "representation")),
        units=tuple(_strings(raw, "units")),
        recipe_id=_optional_string(raw, "recipe_id"),
        recipe_hash=_optional_string(raw, "recipe_hash"),
        calibration_hashes=tuple(
            cast("str | None", value) for value in _sequence(raw, "calibration_hashes")
        ),
        warnings=tuple(_strings(raw, "warnings")),
        created_at=datetime.fromisoformat(_string(raw, "created_at")),
        state=ExportJobState(_string(raw, "state")),
    )


def _channels(raw: Mapping[str, object]) -> ChannelSelection:
    names = raw.get("display_names")
    return ChannelSelection(
        tuple(_strings(raw, "channel_ids")),
        tuple(cast("list[str]", names)) if names is not None else None,
    )


def _range(raw: Mapping[str, object]) -> SampleRange:
    return SampleRange(
        start_index=_integer(raw, "start_index"),
        stop_index=_integer(raw, "stop_index"),
        sample_rate_hz=_number(raw, "sample_rate_hz"),
        requested_start_seconds=_optional_number(raw, "requested_start_seconds"),
        requested_stop_seconds=_optional_number(raw, "requested_stop_seconds"),
    )


def _viewport(raw: Mapping[str, object]) -> PlotViewport:
    return PlotViewport(
        max_points=_integer(raw, "max_points"),
        algorithm=DecimationAlgorithm(_string(raw, "algorithm")),
    )


def _dumps(value: Mapping[str, object], schema_name: str) -> str:
    _validate(value, schema_name)
    return json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"


def _load(payload: str, schema_name: str) -> Mapping[str, object]:
    try:
        value = json.loads(payload)
    except (json.JSONDecodeError, UnicodeError) as cause:
        _decode_error(schema_name, cause)
    if not isinstance(value, dict):
        _decode_error(schema_name, TypeError("document root must be an object"))
    typed = cast("Mapping[str, object]", value)
    _validate(typed, schema_name)
    return typed


def _validate(value: Mapping[str, object], schema_name: str) -> None:
    schema_root = files("labdaq.resources").joinpath("schemas/v1")
    schemas = {
        name: cast("dict[str, object]", json.loads(schema_root.joinpath(name).read_text("utf-8")))
        for name in (
            "plot-request.schema.json",
            "export-request.schema.json",
            "export-sidecar.schema.json",
            "export-manifest.schema.json",
        )
    }
    registry = Registry()
    for schema in schemas.values():
        registry = registry.with_resource(
            cast("str", schema["$id"]),
            Resource.from_contents(schema),
        )
    try:
        Draft202012Validator(
            schemas[schema_name],
            registry=registry,
            format_checker=FormatChecker(),
        ).validate(value)
    except Exception as cause:
        raise ExportError(
            "An M07 JSON document failed schema validation.",
            code="DAQ.EXPORT.DOCUMENT_INVALID",
            context={"schema": schema_name, "reason": str(cause)},
            remediation="Use an unmodified version-1 plotting or export document.",
            cause=cause,
        ) from cause


def _decode_error(document: str, cause: Exception) -> Never:
    raise ExportError(
        "An M07 JSON document could not be decoded.",
        code="DAQ.EXPORT.DOCUMENT_INVALID",
        context={"document": document, "reason": str(cause)},
        remediation="Use a valid version-1 plotting or export JSON document.",
        cause=cause,
    ) from cause


def _mapping(raw: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = raw[key]
    if not isinstance(value, dict):
        raise TypeError(f"{key} must be an object")
    return cast("Mapping[str, object]", value)


def _sequence(raw: Mapping[str, object], key: str) -> list[object]:
    value = raw[key]
    if not isinstance(value, list):
        raise TypeError(f"{key} must be an array")
    return value


def _strings(raw: Mapping[str, object], key: str) -> list[str]:
    values = _sequence(raw, key)
    if any(not isinstance(value, str) for value in values):
        raise TypeError(f"{key} must contain strings")
    return cast("list[str]", values)


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


def _integer(raw: Mapping[str, object], key: str) -> int:
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an integer")
    return value


def _number(raw: Mapping[str, object], key: str) -> float:
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{key} must be numeric")
    return float(value)


def _optional_number(raw: Mapping[str, object], key: str) -> float | None:
    return None if raw.get(key) is None else _number(raw, key)


def _boolean(raw: Mapping[str, object], key: str) -> bool:
    value = raw[key]
    if not isinstance(value, bool):
        raise TypeError(f"{key} must be a boolean")
    return value


def _optional_stage(raw: Mapping[str, object], key: str) -> SignalStage | None:
    value = _optional_string(raw, key)
    return SignalStage(value) if value is not None else None
