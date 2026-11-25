"""Minimal LabDAQ command-line interface."""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, TextIO, cast

from labdaq.bootstrap import (
    create_configuration_service,
    create_daq_service,
    create_export_coordinator,
    create_plot_data_service,
    create_qualification_recovery_service,
    create_qualification_repository,
    create_qualification_runner,
    create_raw_container_export_service,
    create_recorded_session_processing_service,
    create_session_inspection_service,
    current_qualification_fingerprint,
    load_bundled_processing_recipe,
    load_daq_configuration,
    load_qualification_confirmations,
    load_qualification_plan,
)
from labdaq.diagnostics import collect_diagnostics
from labdaq.errors import (
    ConfigurationError,
    ExportError,
    LabDAQError,
    PlotError,
    ProcessingError,
    QualificationError,
    StorageError,
)
from labdaq.feedback import FeedbackForm, create_feedback_bundle
from labdaq.version import get_version_label

if TYPE_CHECKING:
    from labdaq.application.configuration_service import ValidationSummary
    from labdaq.application.plotting_service import PlotDataService
    from labdaq.domain.models import ProcessingRecipeEnvelope
    from labdaq.domain.plotting import PlotRequest

EXIT_SUCCESS = 0
EXIT_USAGE = 2
EXIT_DIAGNOSTIC_FAILURE = 3
EXIT_INTERNAL_ERROR = 4
EXIT_CONFIGURATION_FAILURE = 5
EXIT_DAQ_FAILURE = 6
EXIT_SESSION_FAILURE = 7
EXIT_PROCESSING_FAILURE = 8
EXIT_EXPORT_FAILURE = 9
EXIT_QUALIFICATION_FAILURE = 10


def main(argv: Sequence[str] | None = None) -> int:
    """Run the LabDAQ CLI and return its documented process exit code."""
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command is None:
        parser.error("a command is required")

    json_output = bool(getattr(arguments, "json_output", False))
    try:
        if arguments.command == "diagnostics":
            report = collect_diagnostics()
        elif arguments.command == "feedback-bundle":
            bundle = create_feedback_bundle(
                Path(arguments.output),
                form=FeedbackForm(
                    category=str(arguments.category),
                    summary=str(arguments.summary or ""),
                    steps_to_reproduce=str(arguments.steps or ""),
                    expected_result=str(arguments.expected or ""),
                    actual_result=str(arguments.actual or ""),
                    impact=str(arguments.impact or ""),
                ),
            )
            report = {
                "overall": "ok",
                "feedback_bundle": str(bundle),
                "transmitted": False,
            }
            if json_output:
                print(json.dumps(report, sort_keys=True, separators=(",", ":")))
            else:
                print(
                    f"Feedback bundle created: {bundle}\n"
                    "It was not transmitted. Return it through the approved internal channel."
                )
            return EXIT_SUCCESS
        elif arguments.command == "hardware":
            report, exit_code = _run_hardware(arguments)
            if json_output:
                print(json.dumps(report, sort_keys=True, separators=(",", ":")))
            else:
                print(_render_hardware_report(arguments.action, report))
            return exit_code
        elif arguments.command == "session":
            report, exit_code = _run_session(arguments)
            if json_output:
                print(json.dumps(report, sort_keys=True, separators=(",", ":")))
            else:
                print(_render_session_summary(report))
            return exit_code
        elif arguments.command == "process-session":
            report = _run_process_session(arguments)
            if json_output:
                print(json.dumps(report, sort_keys=True, separators=(",", ":")))
            else:
                print(_render_processing_summary(report))
            return EXIT_SUCCESS
        elif arguments.command == "plot-data":
            report = _run_plot_data(arguments)
            if json_output:
                print(json.dumps(report, sort_keys=True, separators=(",", ":")))
            else:
                print(_render_plot_summary(report))
            return EXIT_SUCCESS
        elif arguments.command == "export":
            report, exit_code = _run_export(arguments)
            if json_output:
                print(json.dumps(report, sort_keys=True, separators=(",", ":")))
            else:
                print(_render_export_summary(report))
            return exit_code
        elif arguments.command == "qualify":
            report, exit_code = _run_qualify(arguments)
            if json_output:
                print(json.dumps(report, sort_keys=True, separators=(",", ":")))
            else:
                print(_render_qualification_summary(report))
            return exit_code
        elif arguments.command == "qualification":
            report, exit_code = _run_qualification(arguments)
            if json_output:
                print(json.dumps(report, sort_keys=True, separators=(",", ":")))
            else:
                print(_render_qualification_summary(report))
            return exit_code
        elif arguments.command == "recipe" and arguments.action == "show":
            report = _show_recipe(str(arguments.recipe_id))
            if json_output:
                print(json.dumps(report, sort_keys=True, separators=(",", ":")))
            else:
                print(json.dumps(report, indent=2, sort_keys=True))
            return EXIT_SUCCESS
        else:
            summary = _validate_configuration(arguments.command, Path(arguments.path))
            if json_output:
                print(json.dumps(summary.to_dict(), sort_keys=True, separators=(",", ":")))
            else:
                print(_render_validation_summary(summary))
            return EXIT_SUCCESS
    except ConfigurationError as structured_error:
        _render_error(structured_error, json_output=json_output, stream=sys.stdout)
        _debug_cause(structured_error)
        return EXIT_CONFIGURATION_FAILURE
    except StorageError as structured_error:
        _render_error(structured_error, json_output=json_output, stream=sys.stdout)
        _debug_cause(structured_error)
        return EXIT_SESSION_FAILURE
    except ProcessingError as structured_error:
        _render_error(structured_error, json_output=json_output, stream=sys.stdout)
        _debug_cause(structured_error)
        return EXIT_PROCESSING_FAILURE
    except (PlotError, ExportError) as structured_error:
        _render_error(structured_error, json_output=json_output, stream=sys.stdout)
        _debug_cause(structured_error)
        return EXIT_EXPORT_FAILURE
    except QualificationError as structured_error:
        _render_error(structured_error, json_output=json_output, stream=sys.stdout)
        _debug_cause(structured_error)
        return EXIT_QUALIFICATION_FAILURE
    except LabDAQError as structured_error:
        _render_error(structured_error, json_output=json_output, stream=sys.stdout)
        _debug_cause(structured_error)
        if arguments.command == "hardware":
            return EXIT_DAQ_FAILURE
        if arguments.command == "session":
            return EXIT_SESSION_FAILURE
        if arguments.command in {"plot-data", "export"}:
            return EXIT_EXPORT_FAILURE
        if arguments.command in {"qualify", "qualification"}:
            return EXIT_QUALIFICATION_FAILURE
        return EXIT_DIAGNOSTIC_FAILURE
    except Exception as cause:
        internal_error = LabDAQError(
            "An unexpected internal error occurred.",
            code="DAQ.CLI.INTERNAL_ERROR",
            context={"exception_type": type(cause).__name__},
            remediation="Run with debug logging and report the diagnostic details.",
            cause=cause,
        )
        _render_error(internal_error, json_output=json_output, stream=sys.stdout)
        _debug_cause(internal_error)
        return EXIT_INTERNAL_ERROR

    if json_output:
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    else:
        print(_render_human_report(report))
    return EXIT_SUCCESS


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="labdaq", description="LabDAQ software diagnostics")
    parser.add_argument("--version", action="version", version=get_version_label())
    subparsers = parser.add_subparsers(dest="command")
    diagnostics_parser = subparsers.add_parser(
        "diagnostics",
        help="report the software environment without accessing DAQ hardware",
    )
    diagnostics_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="emit deterministic machine-readable JSON",
    )
    feedback_parser = subparsers.add_parser(
        "feedback-bundle",
        help="create a local privacy-bounded preview feedback ZIP",
    )
    feedback_parser.add_argument("--output", required=True, help="existing destination directory")
    feedback_parser.add_argument(
        "--category",
        default="other",
        choices=(
            "installation",
            "simulator",
            "ni-hardware",
            "acquisition",
            "review-export",
            "other",
        ),
    )
    feedback_parser.add_argument("--summary")
    feedback_parser.add_argument("--steps")
    feedback_parser.add_argument("--expected")
    feedback_parser.add_argument("--actual")
    feedback_parser.add_argument("--impact")
    _add_json_argument(feedback_parser)
    for command, help_text in (
        ("config", "validate an experiment configuration"),
        ("profile", "validate a hardware profile"),
    ):
        command_parser = subparsers.add_parser(command, help=help_text)
        action_parsers = command_parser.add_subparsers(dest="action", required=True)
        validate_parser = action_parsers.add_parser("validate", help=help_text)
        validate_parser.add_argument("path", help="path to the JSON document")
        validate_parser.add_argument(
            "--json",
            action="store_true",
            dest="json_output",
            help="emit deterministic machine-readable JSON",
        )
    recipe_parser = subparsers.add_parser("recipe", help="inspect processing recipes")
    recipe_actions = recipe_parser.add_subparsers(dest="action", required=True)
    recipe_validate = recipe_actions.add_parser("validate", help="validate a processing recipe")
    recipe_validate.add_argument("path", help="path to the JSON document")
    _add_json_argument(recipe_validate)
    recipe_show = recipe_actions.add_parser("show", help="show a bundled processing recipe")
    recipe_show.add_argument("recipe_id", help="bundled recipe name, normally baseline-v1")
    _add_json_argument(recipe_show)
    hardware_parser = subparsers.add_parser(
        "hardware",
        help="discover, validate, and exercise an explicitly selected DAQ backend",
    )
    hardware_actions = hardware_parser.add_subparsers(dest="action", required=True)
    for action in ("list", "inspect"):
        action_parser = hardware_actions.add_parser(
            action,
            help=f"{action} available DAQ hardware",
        )
        _add_backend_argument(action_parser)
        if action == "inspect":
            action_parser.add_argument(
                "--device",
                help="optional local alias or stable device identifier",
            )
        _add_json_argument(action_parser)
    validate_parser = hardware_actions.add_parser(
        "validate",
        help="validate a profile, experiment, and simulator scenario",
    )
    _add_backend_argument(validate_parser)
    validate_parser.add_argument("--profile", required=True, help="hardware profile JSON path")
    validate_parser.add_argument(
        "--experiment",
        required=True,
        help="experiment configuration JSON path",
    )
    validate_parser.add_argument("--scenario", help="simulator scenario JSON path")
    _add_json_argument(validate_parser)
    acquire_parser = hardware_actions.add_parser(
        "acquire-test",
        help="run a bounded non-recorded acquisition",
    )
    _add_backend_argument(acquire_parser)
    acquire_parser.add_argument("--profile", help="optional hardware profile JSON path")
    acquire_parser.add_argument(
        "--experiment",
        required=True,
        help="experiment configuration JSON path",
    )
    acquire_parser.add_argument(
        "--scenario",
        help="simulator scenario JSON path",
    )
    bound = acquire_parser.add_mutually_exclusive_group()
    bound.add_argument("--duration", type=float, help="bounded duration in seconds")
    bound.add_argument("--samples", type=int, help="bounded samples per channel")
    acquire_parser.add_argument("--chunk-size", type=int, help="samples per read")
    acquire_parser.add_argument("--timeout", type=float, help="read timeout in seconds")
    _add_json_argument(acquire_parser)
    session_parser = subparsers.add_parser(
        "session",
        help="inspect and verify durable acquisition sessions",
    )
    session_actions = session_parser.add_subparsers(dest="action", required=True)
    summary_parser = session_actions.add_parser(
        "summary",
        help="summarize an existing durable session",
    )
    summary_parser.add_argument("path", help="path to the session directory")
    summary_parser.add_argument(
        "--verify",
        action="store_true",
        help="perform full raw-data, event-log, and checksum verification",
    )
    _add_json_argument(summary_parser)
    processing_parser = subparsers.add_parser(
        "process-session",
        help="process a verified recorded session with its recorded recipe",
    )
    processing_parser.add_argument("path", help="path to the session directory")
    processing_parser.add_argument("--recipe")
    processing_parser.add_argument("--summary", action="store_true", required=True)
    processing_parser.add_argument("--start-sample", type=int, default=0)
    processing_parser.add_argument("--stop-sample", type=int)
    processing_parser.add_argument("--chunk-size", type=int, default=8192)
    _add_json_argument(processing_parser)
    plot_parser = subparsers.add_parser(
        "plot-data",
        help="build bounded headless plot data from a verified session",
    )
    plot_parser.add_argument("path", help="path to the session directory")
    _add_plot_arguments(plot_parser)
    _add_json_argument(plot_parser)
    export_parser = subparsers.add_parser(
        "export",
        help="export traceable CSV data or static figures",
    )
    export_actions = export_parser.add_subparsers(dest="action", required=True)
    csv_parser = export_actions.add_parser("csv", help="export numeric CSV and metadata")
    csv_parser.add_argument("path", help="path to the session directory")
    _add_plot_arguments(csv_parser)
    _add_export_arguments(csv_parser)
    _add_json_argument(csv_parser)
    figure_parser = export_actions.add_parser("figure", help="export a static PNG or SVG")
    figure_parser.add_argument("path", help="path to the session directory")
    _add_plot_arguments(figure_parser)
    _add_export_arguments(figure_parser)
    figure_parser.add_argument(
        "--kind",
        required=True,
        choices=("time-history", "overlay", "fft", "psd"),
    )
    figure_parser.add_argument("--format", required=True, choices=("png", "svg"))
    figure_parser.add_argument("--title")
    figure_parser.add_argument("--log-y", action="store_true")
    _add_json_argument(figure_parser)
    tdms_parser = export_actions.add_parser(
        "tdms", help="export a verified full raw session for NI interoperability"
    )
    tdms_parser.add_argument("path", help="path to the session directory")
    _add_export_arguments(tdms_parser)
    tdms_parser.add_argument("--chunk-size", type=int, default=8192)
    _add_json_argument(tdms_parser)
    qualify_parser = subparsers.add_parser(
        "qualify",
        help="run headless hardware qualification through the production recording path",
    )
    qualify_actions = qualify_parser.add_subparsers(dest="action", required=True)
    qualify_hardware = qualify_actions.add_parser(
        "hardware",
        help="qualify the configured DAQ hardware or deterministic simulator",
    )
    qualify_hardware.add_argument("--backend", required=True, choices=("ni", "simulated"))
    qualify_hardware.add_argument("--profile", required=True)
    qualify_hardware.add_argument("--config", required=True)
    qualify_hardware.add_argument("--plan", required=True)
    qualify_hardware.add_argument("--scenario")
    qualify_hardware.add_argument("--confirmations")
    qualify_hardware.add_argument("--duration", type=float)
    _add_json_argument(qualify_hardware)
    qualification_parser = subparsers.add_parser(
        "qualification",
        help="inspect, check, or recover a qualification report",
    )
    qualification_actions = qualification_parser.add_subparsers(
        dest="action",
        required=True,
    )
    qualification_show = qualification_actions.add_parser("show")
    qualification_show.add_argument("report")
    _add_json_argument(qualification_show)
    qualification_check = qualification_actions.add_parser("check")
    qualification_check.add_argument("report")
    qualification_check.add_argument("--profile", required=True)
    qualification_check.add_argument("--config", required=True)
    qualification_check.add_argument("--backend", choices=("ni", "simulated"))
    qualification_check.add_argument("--scenario")
    _add_json_argument(qualification_check)
    qualification_recover = qualification_actions.add_parser("recover")
    qualification_recover.add_argument("session")
    _add_json_argument(qualification_recover)
    return parser


def _add_plot_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--view",
        required=True,
        choices=("raw", "calibrated", "filtered", "fft", "psd"),
    )
    parser.add_argument("--channels", help="comma-separated logical channel IDs")
    parser.add_argument(
        "--start",
        "--start-seconds",
        dest="start",
        type=float,
        help="inclusive start time in seconds",
    )
    parser.add_argument(
        "--stop",
        "--stop-seconds",
        dest="stop",
        type=float,
        help="exclusive stop time in seconds",
    )
    parser.add_argument("--start-sample", type=int)
    parser.add_argument("--stop-sample", type=int)
    parser.add_argument(
        "--spectral-input",
        choices=("raw", "calibrated", "filtered", "conditioned"),
    )
    parser.add_argument("--recipe")
    parser.add_argument("--max-points", type=int, default=2000)
    parser.add_argument("--chunk-size", type=int, default=8192)


def _add_export_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", help="existing destination directory")
    parser.add_argument("--name", help="safe lower-kebab output base name")


def _add_backend_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--backend",
        required=True,
        help="explicit backend identifier: simulated or ni",
    )


def _add_json_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="emit deterministic machine-readable JSON",
    )


def _validate_configuration(command: str, path: Path) -> ValidationSummary:
    service = create_configuration_service()
    if command == "config":
        return service.validate_experiment_file(path)
    if command == "profile":
        return service.validate_profile_file(path)
    if command == "recipe":
        return service.validate_recipe_file(path)
    raise ValueError(f"Unsupported configuration command: {command}")


def _show_recipe(recipe_id: str) -> dict[str, object]:
    from labdaq.domain.serialization import canonical_sha256

    recipe, payload = load_bundled_processing_recipe(recipe_id)
    payload["recipe_hash"] = canonical_sha256(recipe)
    return payload


def _run_process_session(arguments: argparse.Namespace) -> dict[str, object]:
    service = create_recorded_session_processing_service()
    path = Path(arguments.path)
    summary = service.process(
        path,
        recipe_id=(str(arguments.recipe) if arguments.recipe else _session_recipe_id(path)),
        start_sample=int(arguments.start_sample),
        stop_sample=(int(arguments.stop_sample) if arguments.stop_sample is not None else None),
        chunk_size=int(arguments.chunk_size),
    )
    return summary.to_dict()


def _plot_request_from_arguments(
    arguments: argparse.Namespace,
) -> tuple[PlotDataService, PlotRequest]:
    from labdaq.domain.enums import SignalStage
    from labdaq.domain.plotting import (
        ChannelSelection,
        PlotRepresentation,
        PlotRequest,
        PlotViewport,
    )

    service = create_plot_data_service()
    path = Path(arguments.path)
    metadata = service.source_metadata(path)
    channels = (
        tuple(part.strip() for part in str(arguments.channels).split(",") if part.strip())
        if arguments.channels
        else metadata.ordered_channel_ids
    )
    selected_range = service.resolve_range(
        path,
        start_index=arguments.start_sample,
        stop_index=arguments.stop_sample,
        start_seconds=arguments.start,
        stop_seconds=arguments.stop,
    )
    representation = PlotRepresentation(str(arguments.view))
    requires_recipe = representation in {
        PlotRepresentation.FILTERED_TIME,
        PlotRepresentation.FFT_AMPLITUDE,
        PlotRepresentation.PSD,
    }
    recipe_id = arguments.recipe or (_session_recipe_id(path) if requires_recipe else None)
    stage_value = arguments.spectral_input
    stage = (
        {
            "raw": SignalStage.RAW,
            "calibrated": SignalStage.CALIBRATED,
            "filtered": SignalStage.CONDITIONED,
            "conditioned": SignalStage.CONDITIONED,
        }[stage_value]
        if stage_value is not None
        else (
            SignalStage.CONDITIONED
            if representation in {PlotRepresentation.FFT_AMPLITUDE, PlotRepresentation.PSD}
            else None
        )
    )
    return service, PlotRequest(
        source_session=path,
        representation=representation,
        channels=ChannelSelection(channels),
        sample_range=selected_range,
        viewport=PlotViewport(int(arguments.max_points)),
        recipe_id=recipe_id,
        spectral_input_stage=stage,
    )


def _run_plot_data(arguments: argparse.Namespace) -> dict[str, object]:
    service, request = _plot_request_from_arguments(arguments)
    return service.build_plot_data(request, chunk_size=int(arguments.chunk_size)).to_dict()


def _run_export(arguments: argparse.Namespace) -> tuple[dict[str, object], int]:
    if arguments.action == "tdms":
        receipt = create_raw_container_export_service().export_tdms(
            Path(arguments.path),
            destination=Path(arguments.output) if arguments.output else None,
            base_name=arguments.name,
            chunk_size=int(arguments.chunk_size),
        )
        return receipt.to_dict(), EXIT_SUCCESS
    from labdaq.domain.plotting import (
        ExportFormat,
        ExportJobState,
        ExportProduct,
        ExportRequest,
        FigureOptionsV1,
    )

    _, plot_request = _plot_request_from_arguments(arguments)
    if arguments.action == "csv":
        products = {
            "raw": ExportProduct.RAW_TIME_DATA,
            "calibrated": ExportProduct.CALIBRATED_TIME_DATA,
            "filtered": ExportProduct.FILTERED_TIME_DATA,
            "fft": ExportProduct.FFT_AMPLITUDE_DATA,
            "psd": ExportProduct.PSD_DATA,
        }
        output_format = ExportFormat.CSV
        figure_options = FigureOptionsV1()
    else:
        products = {
            "time-history": ExportProduct.TIME_HISTORY_FIGURE,
            "overlay": ExportProduct.OVERLAY_FIGURE,
            "fft": ExportProduct.FFT_FIGURE,
            "psd": ExportProduct.PSD_FIGURE,
        }
        output_format = ExportFormat(str(arguments.format))
        figure_options = FigureOptionsV1(
            title=arguments.title,
            logarithmic_y=bool(arguments.log_y),
        )
    product_key = str(arguments.view) if arguments.action == "csv" else str(arguments.kind)
    request = ExportRequest(
        source_session=plot_request.source_session,
        product=products[product_key],
        output_format=output_format,
        channels=plot_request.channels,
        sample_range=plot_request.sample_range,
        representation=plot_request.representation,
        recipe_id=plot_request.recipe_id,
        spectral_input_stage=plot_request.spectral_input_stage,
        destination_directory=Path(arguments.output) if arguments.output else None,
        base_name=arguments.name,
        figure_options=figure_options,
        max_points=int(arguments.max_points),
        chunk_size=int(arguments.chunk_size),
    )
    manifest = create_export_coordinator().run_export_job((request,))
    return (
        manifest.to_dict(),
        EXIT_SUCCESS if manifest.final_state is ExportJobState.COMPLETED else EXIT_EXPORT_FAILURE,
    )


def _run_qualify(arguments: argparse.Namespace) -> tuple[dict[str, object], int]:
    from labdaq.application.qualification_service import BuildQualificationPlan
    from labdaq.domain.serialization import to_canonical_primitive

    if arguments.action != "hardware":
        raise ValueError(f"Unsupported qualification target: {arguments.action}")
    duration = arguments.duration
    if duration is not None and (not math.isfinite(duration) or duration <= 0):
        raise QualificationError(
            "Qualification duration must be finite and positive.",
            code="DAQ.QUALIFICATION.PLAN_INVALID",
            context={"duration_seconds": duration},
        )
    profile, experiment = load_daq_configuration(
        experiment_path=Path(arguments.config),
        profile_path=Path(arguments.profile),
    )
    template = load_qualification_plan(str(arguments.plan))
    plan = BuildQualificationPlan().build(
        template,
        profile,
        experiment,
        throughput_duration_seconds=duration,
    )
    confirmations = (
        load_qualification_confirmations(Path(arguments.confirmations))
        if arguments.confirmations is not None
        else None
    )
    recipe_value, _ = load_bundled_processing_recipe(
        experiment.draft_processing_recipe_id or "baseline-v1"
    )
    recipe = cast("ProcessingRecipeEnvelope", recipe_value)
    runner = create_qualification_runner(
        backend=str(arguments.backend),
        scenario_path=Path(arguments.scenario) if arguments.scenario else None,
        confirmations=confirmations,
        interactive=not bool(arguments.json_output),
        operator=experiment.session.operator,
    )
    try:
        outcome = runner.run(plan, profile, experiment, recipe)
    finally:
        runner.close()
    encoded = to_canonical_primitive(outcome.report)
    if not isinstance(encoded, dict):
        raise TypeError("qualification report did not encode to an object")
    payload: dict[str, object] = {
        "report_path": str(outcome.report_path),
        "session_path": str(outcome.session.session_path),
        "report": encoded,
        "eligibility": outcome.eligibility.to_dict(),
    }
    return (
        payload,
        EXIT_SUCCESS if outcome.eligibility.eligible else EXIT_QUALIFICATION_FAILURE,
    )


def _run_qualification(arguments: argparse.Namespace) -> tuple[dict[str, object], int]:
    from labdaq.application.qualification_service import CheckQualificationEligibility
    from labdaq.domain.serialization import to_canonical_primitive

    if arguments.action == "recover":
        session_path = Path(arguments.session)
        report = create_qualification_recovery_service().recover(session_path)
        encoded = to_canonical_primitive(report)
        assert isinstance(encoded, dict)
        return {
            "report_path": str(session_path / "qualification-report.json"),
            "report": encoded,
        }, EXIT_SUCCESS

    report_path = Path(arguments.report)
    repository = create_qualification_repository()
    report = repository.load(report_path)
    encoded = to_canonical_primitive(report)
    assert isinstance(encoded, dict)
    payload: dict[str, object] = {"report_path": str(report_path), "report": encoded}
    if arguments.action == "show":
        return payload, EXIT_SUCCESS
    if arguments.action != "check":
        raise ValueError(f"Unsupported qualification action: {arguments.action}")
    plan_path = report_path.parent / "qualification-plan.json"
    plan = repository.load_plan(plan_path)
    if plan.content_hash != report.plan_hash:
        raise QualificationError(
            "The qualification report and saved plan hashes disagree.",
            code="DAQ.QUALIFICATION.PLAN_MISMATCH",
            context={"report_path": str(report_path), "plan_path": str(plan_path)},
        )
    profile, experiment = load_daq_configuration(
        experiment_path=Path(arguments.config),
        profile_path=Path(arguments.profile),
    )
    current_fingerprint = None
    if arguments.backend is not None:
        current_fingerprint = current_qualification_fingerprint(
            backend=str(arguments.backend),
            scenario_path=Path(arguments.scenario) if arguments.scenario else None,
            plan=plan,
            profile=profile,
            experiment=experiment,
        )
    eligibility = CheckQualificationEligibility().check(
        report,
        plan,
        current_fingerprint=current_fingerprint,
        evaluated_at=datetime.now(UTC),
    )
    payload["eligibility"] = eligibility.to_dict()
    return payload, EXIT_SUCCESS if eligibility.eligible else EXIT_QUALIFICATION_FAILURE


def _run_hardware(arguments: argparse.Namespace) -> tuple[dict[str, object], int]:
    scenario_path = (
        Path(arguments.scenario) if getattr(arguments, "scenario", None) is not None else None
    )
    service = create_daq_service(backend=str(arguments.backend), scenario_path=scenario_path)
    try:
        if arguments.action in {"list", "inspect"}:
            payload = _discovery_to_dict(service.discover())
            if arguments.action == "inspect" and arguments.device is not None:
                payload = _select_discovery_device(payload, str(arguments.device))
            return payload, EXIT_SUCCESS
        profile, experiment = load_daq_configuration(
            experiment_path=Path(arguments.experiment),
            profile_path=(
                Path(arguments.profile) if getattr(arguments, "profile", None) is not None else None
            ),
        )
        if arguments.action == "validate":
            report = service.validate(profile, experiment)
            payload = _validation_to_dict(report)
            return payload, EXIT_SUCCESS if report.accepted else EXIT_DAQ_FAILURE
        sample_limit = _resolve_sample_limit(
            experiment,
            samples=getattr(arguments, "samples", None),
            duration=getattr(arguments, "duration", None),
        )
        chunk_size = (
            int(arguments.chunk_size)
            if arguments.chunk_size is not None
            else experiment.acquisition.chunk_config.samples_per_channel
        )
        timeout_seconds = (
            float(arguments.timeout)
            if arguments.timeout is not None
            else experiment.acquisition.chunk_config.read_timeout_seconds
        )
        summary = service.acquire_test(
            profile,
            experiment,
            sample_limit=sample_limit,
            chunk_size=chunk_size,
            timeout_seconds=timeout_seconds,
        )
        return (
            summary.to_dict(),
            EXIT_SUCCESS if summary.overall == "ok" else EXIT_DAQ_FAILURE,
        )
    finally:
        service.close()


def _run_session(arguments: argparse.Namespace) -> tuple[dict[str, object], int]:
    from labdaq.ports.session import IntegrityStatus, VerificationLevel

    if arguments.action != "summary":
        raise ValueError(f"Unsupported session action: {arguments.action}")
    level = VerificationLevel.FULL if bool(arguments.verify) else VerificationLevel.FAST
    summary = create_session_inspection_service().summary(Path(arguments.path), level=level)
    payload = summary.to_dict()
    exit_code = (
        EXIT_SESSION_FAILURE if summary.integrity.overall is IntegrityStatus.FAIL else EXIT_SUCCESS
    )
    return payload, exit_code


def _session_recipe_id(path: Path) -> str:
    from labdaq.ports.session import VerificationLevel

    summary = create_session_inspection_service().summary(
        path,
        level=VerificationLevel.FULL,
    )
    if summary.recipe is None:
        raise ProcessingError(
            "The recorded session does not contain processing recipe provenance.",
            code="DAQ.PROCESSING.RECIPE_REQUIRED",
            context={"session_path": str(path)},
            remediation="Use a compatible legacy session or explicitly provide --recipe.",
        )
    return summary.recipe.recipe_id


def _resolve_sample_limit(
    experiment: object,
    *,
    samples: int | None,
    duration: float | None,
) -> int:
    from datetime import UTC, datetime

    from labdaq.application.daq_service import configured_sample_count
    from labdaq.domain.models import ExperimentConfig
    from labdaq.domain.timing import rounded_sample_count
    from labdaq.errors import ErrorSeverity
    from labdaq.ports.daq import DaqConfigurationError, DaqEvent

    if not isinstance(experiment, ExperimentConfig):
        raise TypeError("Expected an experiment configuration")
    configured = configured_sample_count(experiment)
    value: int | None
    if duration is not None:
        if not math.isfinite(duration) or duration <= 0:
            value = 0
        else:
            try:
                value = rounded_sample_count(experiment.acquisition.sample_rate_hz, duration)
            except ValueError:
                value = 0
    elif samples is not None:
        value = samples
    else:
        value = configured
    if value is not None and value > 0:
        return value
    event = DaqEvent(
        code="DAQ.HARDWARE.CONFIGURATION_UNSUPPORTED",
        severity=ErrorSeverity.ERROR,
        backend_id="simulated",
        operation="acquire_test",
        message="The acquisition test requires a positive sample bound.",
        timestamp=datetime.now(UTC),
        context={"requested_samples": samples, "duration_seconds": duration},
    )
    raise DaqConfigurationError(
        event.message,
        event=event,
        remediation="Supply --samples or an exactly representable --duration.",
    )


def _discovery_to_dict(report: object) -> dict[str, object]:
    from labdaq.ports.daq import DaqDiscoveryReport

    if not isinstance(report, DaqDiscoveryReport):
        raise TypeError("Expected a DAQ discovery report")
    devices: list[dict[str, object]] = []
    for device in report.devices:
        modules = [
            {
                "module_id": module.module_id,
                "parent_device_id": module.parent_device_id,
                "vendor": module.vendor,
                "model": module.model,
                "slot": module.slot,
                "serial_number": module.serial_number,
                "available_channels": list(module.available_channels),
                "simultaneous_sampling": module.simultaneous_sampling,
                "supported_measurement_quantities": list(module.supported_measurement_quantities),
                "supported_terminal_configurations": list(module.supported_terminal_configurations),
                "input_range_v": list(module.input_range_v),
                "supported_sample_rates_hz": list(module.supported_sample_rates_hz),
                "reported_model": module.reported_model,
                "reported_slot": module.reported_slot,
                "product_category": module.product_category,
            }
            for module in device.modules
        ]
        devices.append(
            {
                "device_id": device.device_id,
                "vendor": device.vendor,
                "model": device.model,
                "serial_number": device.serial_number,
                "alias": device.alias,
                "simulated": device.simulated,
                "accessible": device.accessible,
                "modules": modules,
                "reported_model": device.reported_model,
                "product_category": device.product_category,
            }
        )
    return {
        "overall": "ok",
        "backend_id": report.backend_id,
        "adapter_version": report.adapter_version,
        "discovered_at": report.discovered_at.isoformat(),
        "devices": devices,
        "warnings": [event.to_dict() for event in report.warnings],
        "driver": {
            "provider": report.driver.provider,
            "package_name": report.driver.package_name,
            "package_version": report.driver.package_version,
            "driver_version": report.driver.driver_version,
        },
    }


def _validation_to_dict(report: object) -> dict[str, object]:
    from labdaq.ports.daq import DaqValidationReport

    if not isinstance(report, DaqValidationReport):
        raise TypeError("Expected a DAQ validation report")
    return {
        "overall": "ok" if report.accepted else "error",
        "accepted": report.accepted,
        "hardware_profile_id": report.hardware_profile_id,
        "profile_revision": report.profile_revision,
        "experiment_config_id": report.experiment_config_id,
        "config_revision": report.config_revision,
        "backend_id": report.backend_id,
        "scenario_id": report.scenario_id,
        "scenario_schema_version": report.scenario_schema_version,
        "generator_version": report.generator_version,
        "seed": report.seed,
        "selected_device_id": report.selected_device_id,
        "selected_module_id": report.selected_module_id,
        "ordered_logical_channels": list(report.ordered_logical_channels),
        "ordered_physical_channels": list(report.ordered_physical_channels),
        "requested_sample_rate_hz": report.requested_sample_rate_hz,
        "supported_input_range_v": (
            list(report.supported_input_range_v)
            if report.supported_input_range_v is not None
            else None
        ),
        "errors": [issue.to_dict() for issue in report.errors],
        "warnings": [issue.to_dict() for issue in report.warnings],
    }


def _select_discovery_device(
    report: dict[str, object],
    requested: str,
) -> dict[str, object]:
    devices = report["devices"]
    if not isinstance(devices, list):
        raise TypeError("Invalid device report")
    selected = [
        device
        for device in devices
        if isinstance(device, Mapping)
        and requested in {str(device.get("device_id")), str(device.get("alias"))}
    ]
    if len(selected) != 1:
        raise LabDAQError(
            "The requested DAQ device was not found uniquely.",
            code="DAQ.HARDWARE.DEVICE_NOT_FOUND",
            context={"requested_device": requested, "match_count": len(selected)},
            remediation="Use an alias or stable identifier reported by hardware list.",
        )
    filtered = dict(report)
    filtered["devices"] = selected
    return filtered


def _render_validation_summary(summary: ValidationSummary) -> str:
    values = summary.to_dict()
    lines = [
        f"Document type: {values['document_type']}",
        f"Document ID: {values['document_id']}",
        f"Schema version: {values['schema_version']}",
        f"Revision: {values['revision']}",
        f"Hardware profile: {values['hardware_profile_id'] or 'n/a'}",
        f"Channel count: {values['channel_count'] if values['channel_count'] is not None else 'n/a'}",
        f"Sample rate: {values['sample_rate_hz'] if values['sample_rate_hz'] is not None else 'n/a'}",
        "Overall: ok",
    ]
    return "\n".join(lines)


def _render_hardware_report(action: str, report: Mapping[str, object]) -> str:
    if action in {"list", "inspect"}:
        devices = report["devices"]
        if not isinstance(devices, list) or not devices:
            return f"Backend: {report['backend_id']}\nDevices: none"
        device = devices[0]
        if not isinstance(device, Mapping):
            raise TypeError("Invalid device report")
        modules = device["modules"]
        if not isinstance(modules, list) or not modules or not isinstance(modules[0], Mapping):
            raise TypeError("Invalid module report")
        module = modules[0]
        driver = report["driver"]
        if not isinstance(driver, Mapping):
            raise TypeError("Invalid driver report")
        return "\n".join(
            [
                f"Backend: {report['backend_id']}",
                f"Adapter version: {report['adapter_version']}",
                f"Driver version: {driver['driver_version'] or 'n/a'}",
                f"Python package: {driver['package_name'] or 'n/a'} "
                f"{driver['package_version'] or ''}".rstrip(),
                f"Device: {device['device_id']} ({device['model']})",
                f"Simulated: {str(device['simulated']).lower()}",
                f"Accessible: {str(device['accessible']).lower()}",
                f"Module: {module['module_id']} ({module['model']})",
                f"Reported module: {module['reported_model'] or module['model']}",
                f"Reported slot: "
                f"{module['reported_slot'] if module['reported_slot'] is not None else 'n/a'}",
                f"Channels: {', '.join(str(item) for item in module['available_channels'])}",
                f"Supported rates: {module['supported_sample_rates_hz']}",
                f"Input range: {module['input_range_v']} V",
                "Overall: ok",
            ]
        )
    if action == "validate":
        return "\n".join(
            [
                f"Backend: {report['backend_id']}",
                f"Hardware profile: {report['hardware_profile_id']}",
                f"Experiment: {report['experiment_config_id']}",
                f"Scenario: {report['scenario_id'] or 'n/a'} "
                f"({report['generator_version'] or 'n/a'})",
                f"Accepted: {str(report['accepted']).lower()}",
                f"Channels: {report['ordered_logical_channels']}",
                f"Sample rate: {report['requested_sample_rate_hz']}",
                f"Errors: {len(report['errors'])}",  # type: ignore[arg-type]
                f"Overall: {report['overall']}",
            ]
        )
    return "\n".join(
        [
            f"Backend: {report['backend_id']}",
            f"Scenario: {report['scenario_id'] or 'n/a'} ({report['generator_version'] or 'n/a'})",
            f"Requested rate: {report['requested_sample_rate_hz']}",
            f"Actual rate: {report['actual_sample_rate_hz']}",
            f"Channel order: {report['ordered_channel_ids']}",
            f"Requested samples/channel: {report['requested_samples_per_channel']}",
            f"Acquired samples/channel: {report['samples_per_channel']}",
            f"Shape: {report['array_shape']}",
            f"Dtype: {report['dtype']}",
            f"Chunks: {report['chunk_count']}",
            (
                "Throughput: "
                f"{report['throughput_samples_per_second_per_channel']} samples/s/channel"
            ),
            f"SHA-256: {report['canonical_sha256']}",
            f"Cleanup succeeded: {str(report['cleanup_succeeded']).lower()}",
            f"Overall: {report['overall']}",
        ]
    )


def _render_human_report(report: Mapping[str, object]) -> str:
    application = _require_mapping(report, "application")
    python = _require_mapping(report, "python")
    platform = _require_mapping(report, "platform")
    optional_dependencies = _require_mapping(report, "optional_dependencies")
    build = _require_mapping(application, "build")

    lines = [
        f"{application['name']} {application['version']}",
        f"Build: {build['release_channel']} / {build['build_id']}",
        (f"Python: {python['implementation']} {python['version']} ({python['executable']})"),
        f"Operating system: {platform['system']} {platform['release']} ({platform['machine']})",
        f"Working directory: {report['working_directory']}",
        "Optional dependencies:",
    ]
    for name, raw_status in optional_dependencies.items():
        if not isinstance(raw_status, Mapping):
            raise TypeError(f"Invalid dependency status for {name}")
        status = raw_status["status"]
        discovered_version = raw_status["version"]
        version_suffix = f" ({discovered_version})" if discovered_version is not None else ""
        lines.append(f"  - {name}: {status}{version_suffix}")
    lines.append(f"Temporary-file probe: {_require_mapping(report, 'temporary_file')['status']}")
    lines.append(f"Overall: {report['overall']}")
    return "\n".join(lines)


def _render_session_summary(report: Mapping[str, object]) -> str:
    integrity = _require_mapping(report, "integrity")
    channel_ids = report["ordered_channel_ids"]
    channel_units = report["ordered_channel_units"]
    channel_counts = _require_mapping(report, "per_channel_sample_counts")
    if not isinstance(channel_ids, list) or not isinstance(channel_units, list):
        raise TypeError("Invalid session channel summary")
    lines = [
        f"Session: {report['session_id']}",
        f"Experiment: {report['experiment_name']}",
        f"Path: {report['session_path']}",
        f"Completion: {report['completion_state']} ({report['completion_reason']})",
        f"Start: {report['started_at']}",
        f"End: {report['ended_at']}",
        f"Hardware profile: {report['hardware_profile_id']}",
        f"Requested rate: {report['requested_sample_rate_hz']} Hz",
        f"Actual rate: {report['actual_sample_rate_hz']} Hz",
        "Channels:",
    ]
    for channel_id, unit in zip(channel_ids, channel_units, strict=True):
        lines.append(f"  - {channel_id}: {channel_counts[channel_id]} samples [{unit}]")
    lines.extend(
        [
            f"Common samples: {report['common_sample_count']}",
            f"Acquired duration: {report['acquired_duration_seconds']} s",
            (
                "Events: "
                f"{report['event_count']} total, {report['warning_count']} warnings, "
                f"{report['fault_count']} faults"
            ),
            f"Integrity: {integrity['overall']} ({integrity['verification_level']})",
            f"Recoverability: {integrity['recoverability']}",
        ]
    )
    issues = integrity.get("issues")
    if isinstance(issues, list) and issues:
        lines.append("Integrity findings:")
        for raw_issue in issues:
            if isinstance(raw_issue, Mapping):
                lines.append(
                    f"  - {raw_issue.get('status')}: {raw_issue.get('code')} - "
                    f"{raw_issue.get('message')}"
                )
    return "\n".join(lines)


def _render_processing_summary(report: Mapping[str, object]) -> str:
    provenance = report.get("provenance")
    metrics = report.get("metrics")
    fft = report.get("fft")
    psd = report.get("psd")
    if not isinstance(provenance, Mapping):
        return json.dumps(report, indent=2, sort_keys=True)
    lines = [
        "LabDAQ processing summary",
        f"  session: {provenance.get('source_session_id')}",
        f"  source state: {provenance.get('source_completion_state')}",
        f"  range: [{provenance.get('start_sample_index')}, {provenance.get('stop_sample_index')})",
        f"  recipe: {provenance.get('recipe_id')}@{provenance.get('recipe_version')}",
        f"  recipe hash: {provenance.get('recipe_hash')}",
        f"  provenance hash: {provenance.get('provenance_hash')}",
        f"  metric channels: {len(metrics) if isinstance(metrics, list) else 0}",
        f"  FFT channels: {len(fft) if isinstance(fft, list) else 0}",
        f"  PSD channels: {len(psd) if isinstance(psd, list) else 0}",
    ]
    return "\n".join(lines)


def _render_plot_summary(report: Mapping[str, object]) -> str:
    series = report.get("series")
    values = series if isinstance(series, list) else []
    lines = [
        "LabDAQ plot data",
        f"  session: {report.get('source_session_id')}",
        f"  representation: {report.get('representation')}",
        f"  series: {len(values)}",
    ]
    for raw_series in values:
        if isinstance(raw_series, Mapping):
            lines.append(
                f"  - {raw_series.get('channel_id')}: "
                f"{raw_series.get('output_point_count')} points "
                f"[{raw_series.get('y_unit')}]"
            )
    return "\n".join(lines)


def _render_export_summary(report: Mapping[str, object]) -> str:
    if "output_path" in report:
        return "\n".join(
            (
                "LabDAQ TDMS interoperability export",
                f"  source session: {report.get('source_session_id')}",
                f"  source format: {report.get('source_format')}",
                f"  samples/channel: {report.get('sample_count')}",
                f"  output: {report.get('output_path')}",
                f"  sidecar: {report.get('sidecar_path')}",
            )
        )
    artifacts = report.get("artifacts")
    values = artifacts if isinstance(artifacts, list) else []
    lines = [
        "LabDAQ export job",
        f"  job: {report.get('export_job_id')}",
        f"  state: {report.get('final_state')}",
        f"  manifest: {report.get('manifest_path')}",
    ]
    for artifact in values:
        if isinstance(artifact, Mapping):
            lines.append(f"  - {artifact.get('relative_path')} ({artifact.get('sha256')})")
    return "\n".join(lines)


def _render_qualification_summary(payload: Mapping[str, object]) -> str:
    raw_report = payload.get("report")
    if not isinstance(raw_report, Mapping):
        return json.dumps(payload, indent=2, sort_keys=True)
    lines = [
        "LabDAQ hardware qualification",
        f"  report: {raw_report.get('report_id')}",
        f"  plan: {raw_report.get('plan_id')}",
        f"  lifecycle: {raw_report.get('lifecycle')}",
        f"  status: {raw_report.get('overall_status')}",
        f"  report path: {payload.get('report_path')}",
        "  tests:",
    ]
    results = raw_report.get("results")
    if isinstance(results, list):
        for result in results:
            if isinstance(result, Mapping):
                reasons = result.get("reason_codes")
                suffix = f" [{', '.join(str(item) for item in reasons)}]" if reasons else ""
                lines.append(
                    f"    {result.get('test_id')}: {result.get('status')} - "
                    f"{result.get('summary')}{suffix}"
                )
    eligibility = payload.get("eligibility")
    if isinstance(eligibility, Mapping):
        lines.append(f"  start eligible: {str(eligibility.get('eligible')).lower()}")
        reasons = eligibility.get("reasons")
        if isinstance(reasons, list) and reasons:
            lines.append("  eligibility findings:")
            for reason in reasons:
                if isinstance(reason, Mapping):
                    lines.append(f"    {reason.get('code')}: {reason.get('message')}")
    return "\n".join(lines)


def _render_error(error: LabDAQError, *, json_output: bool, stream: TextIO) -> None:
    if json_output:
        payload: dict[str, object] = {"overall": "error", "error": error.to_dict()}
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")), file=stream)
        return
    print(str(error), file=sys.stderr)
    if error.remediation is not None:
        print(f"Remediation: {error.remediation}", file=sys.stderr)


def _debug_cause(error: LabDAQError) -> None:
    if error.cause is None:
        return
    logging.getLogger("labdaq.cli").debug(
        "CLI failure cause",
        exc_info=(type(error.cause), error.cause, error.cause.__traceback__),
        extra={
            "event_code": error.code,
            "component": "cli",
            "context": error.context,
        },
    )


def _require_mapping(report: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = report[key]
    if not isinstance(value, Mapping):
        raise TypeError(f"Invalid diagnostics field: {key}")
    return value
