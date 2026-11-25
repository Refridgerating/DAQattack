"""Command-line entry point for the M10 desktop shell."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Launch the LabDAQ M10 desktop")
    parser.add_argument(
        "--daq",
        choices=("simulated", "ni"),
        default="simulated",
        help="DAQ backend; simulated is the safe default",
    )
    parser.add_argument(
        "--mode",
        choices=("production", "accelerometer_commissioning"),
        default="production",
        help="experiment scope; commissioning intentionally excludes the charge channel",
    )
    parser.add_argument("--profile", type=Path, help="external hardware profile")
    parser.add_argument("--configuration", type=Path, help="external experiment configuration")
    parser.add_argument(
        "--qualification-plan",
        default="preflight-v1",
        help="bundled plan ID or external plan path",
    )
    parser.add_argument(
        "--scenario",
        type=Path,
        help="developer-only simulator scenario; not exposed in normal widgets",
    )
    parser.add_argument(
        "--feedback",
        action="store_true",
        help="create a local feedback bundle without initializing a DAQ backend",
    )
    parser.add_argument("--functional-preview", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    require_preview_acknowledgement: bool = True,
) -> int:
    arguments = _parser().parse_args(argv)
    qt_argv = sys.argv if argv is None else [sys.argv[0], *argv]
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication(qt_argv)
    from labdaq.gui.preview import show_feedback_bundle_dialog

    if arguments.feedback:
        show_feedback_bundle_dialog()
        return 0
    if arguments.functional_preview:
        arguments.daq = "ni"
        arguments.mode = "accelerometer_commissioning"
        arguments.profile = None
        arguments.configuration = None
        arguments.qualification_plan = "preflight-v1"
        arguments.scenario = None
    if require_preview_acknowledgement:
        from labdaq.gui.preview import show_preview_acknowledgement

        if not show_preview_acknowledgement(arguments.daq):
            return 0
    if arguments.daq == "ni" and not arguments.functional_preview:
        explicit_profile = arguments.profile is not None
        explicit_configuration = arguments.configuration is not None
        explicit_plan = isinstance(arguments.qualification_plan, Path)
        from labdaq.adapters.qt_preferences import QSettingsPreferenceAdapter

        saved = QSettingsPreferenceAdapter().load()
        prefix = (
            "commissioning" if arguments.mode == "accelerometer_commissioning" else "production"
        )
        if arguments.profile is None:
            arguments.profile = getattr(saved, f"{prefix}_profile_path")
        if arguments.configuration is None:
            arguments.configuration = getattr(saved, f"{prefix}_configuration_path")
        if arguments.qualification_plan == "preflight-v1":
            saved_plan = getattr(saved, f"{prefix}_qualification_plan_path")
            if saved_plan is not None:
                arguments.qualification_plan = saved_plan
        for label, path, was_explicit in (
            ("profile", arguments.profile, explicit_profile),
            ("configuration", arguments.configuration, explicit_configuration),
            (
                "qualification plan",
                arguments.qualification_plan
                if isinstance(arguments.qualification_plan, Path)
                else None,
                explicit_plan,
            ),
        ):
            if path is not None and not was_explicit and not Path(path).is_file():
                raise SystemExit(f"Saved NI {label} path does not exist: {path}")
    from labdaq.bootstrap import create_desktop_application
    from labdaq.gui.main_window import run_desktop_window

    created_application, window = create_desktop_application(
        backend=arguments.daq,
        profile_path=arguments.profile,
        configuration_path=arguments.configuration,
        qualification_plan=arguments.qualification_plan,
        scenario_path=arguments.scenario,
        argv=qt_argv,
        experiment_mode=arguments.mode,
        functional_preview=arguments.functional_preview,
    )
    return run_desktop_window(window, created_application)


if __name__ == "__main__":
    raise SystemExit(main())
