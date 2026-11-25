from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QLabel, QTableWidget, QTableWidgetItem

from labdaq.adapters.qt_preferences import QSettingsPreferenceAdapter
from labdaq.application.configuration_workspace import FieldIssue
from labdaq.bootstrap import create_desktop_application
from labdaq.errors import ErrorSeverity
from labdaq.gui.field_help import (
    FIELD_HELP,
    FieldHelp,
    bind_field_help,
    bind_table_help,
    field_help_id,
    set_described_table_item,
    set_field_notice,
    tooltip_text,
)
from labdaq.gui.main_window import RawDiagnosticAuthorizationDialog
from labdaq.gui.signal_plot import SignalPlotPanel

if TYPE_CHECKING:
    from pathlib import Path


def _preferences(tmp_path: Path) -> QSettingsPreferenceAdapter:
    settings = QSettings(str(tmp_path / "field-help.ini"), QSettings.Format.IniFormat)
    return QSettingsPreferenceAdapter(settings)


def test_field_help_catalog_requires_description_and_usage() -> None:
    assert FIELD_HELP
    for help_id, entry in FIELD_HELP.items():
        rendered = tooltip_text(help_id)
        assert entry.what in rendered
        assert entry.usage in rendered
        assert "Current status:" not in rendered

    with pytest.raises(ValueError, match="description"):
        FieldHelp("", "Used for a test.")
    with pytest.raises(ValueError, match="usage"):
        FieldHelp("A test value.", "")
    with pytest.raises(ValueError, match="guidance"):
        FieldHelp("A test value.", "Used for a test.", "  ")
    with pytest.raises(KeyError):
        tooltip_text("missing.help.id")


def test_widget_notice_appends_and_clears_without_erasing_help(qtbot: object) -> None:
    label = QLabel()
    qtbot.addWidget(label)  # type: ignore[attr-defined]
    bind_field_help(label, "acquisition.counts")
    base = label.toolTip()

    set_field_notice(label, "Persisted samples currently lag acquired samples.")

    assert label.toolTip().startswith(base)
    assert "Current status:" in label.toolTip()
    assert "Persisted samples currently lag" in label.toolTip()

    set_field_notice(label, "  ")

    assert label.toolTip() == base
    assert field_help_id(label) == "acquisition.counts"


def test_table_headers_and_dynamic_cells_share_column_help(qtbot: object) -> None:
    table = QTableWidget(1, 2)
    table.setHorizontalHeaderLabels(["Channel", "Unit"])
    qtbot.addWidget(table)  # type: ignore[attr-defined]
    bind_table_help(
        table,
        "metrics.table",
        ("metrics.column.channel", "metrics.column.unit"),
    )
    set_described_table_item(table, 0, 0, QTableWidgetItem("accel-x"))
    set_described_table_item(table, 0, 1, QTableWidgetItem("g"))

    assert table.horizontalHeaderItem(0).toolTip() == table.item(0, 0).toolTip()
    assert "stable identity" in table.item(0, 0).toolTip()
    assert "engineering unit" in table.item(0, 1).toolTip()

    set_field_notice(table, "Metrics are stale.")

    assert "Current status:" in table.horizontalHeaderItem(0).toolTip()
    assert "Metrics are stale." in table.item(0, 1).toolTip()

    set_field_notice(table, None)

    assert "Current status:" not in table.item(0, 0).toolTip()


def test_table_help_rejects_incomplete_or_unknown_column_catalog(qtbot: object) -> None:
    table = QTableWidget(0, 2)
    table.setHorizontalHeaderLabels(["Channel", "Unit"])
    qtbot.addWidget(table)  # type: ignore[attr-defined]

    with pytest.raises(ValueError, match="every column"):
        bind_table_help(table, "metrics.table", ("metrics.column.channel",))
    with pytest.raises(KeyError, match="missing.column"):
        bind_table_help(
            table,
            "metrics.table",
            ("metrics.column.channel", "missing.column"),
        )


def test_main_window_and_authorization_dialog_cover_operator_data_fields(
    qtbot: object,
    tmp_path: Path,
) -> None:
    _, window = create_desktop_application(
        backend="simulated",
        argv=[],
        preferences=_preferences(tmp_path),
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]

    named_widgets = (
        "global_status",
        "profile_selector",
        "profile_label",
        "discovery_label",
        "sample_rate_selector",
        "read_interval_label",
        "destination_edit",
        "destination_preview_label",
        "recipe_box",
        "readiness_list",
        "diagnostic_readiness_list",
        "plan_selector",
        "plan_revision_label",
        "qualification_progress",
        "qualification_status",
        "report_viewer",
        "acquisition_mode",
        "open_channel",
        "pause_display",
        "live_window",
        "acquisition_state_label",
        "acquisition_elapsed_label",
        "acquisition_counts_label",
        "acquisition_warning_label",
        "acquisition_identity_label",
        "recording_queue",
        "display_queue",
        "browser_root",
        "session_details",
        "review_signal_stage",
        "review_representation",
        "review_start_seconds",
        "review_stop_seconds",
        "review_mad_multiplier",
        "review_pre_padding",
        "review_post_padding",
        "review_merge_gap",
        "review_detection_status",
        "review_channels",
        "review_plot_metadata",
        "review_events",
        "review_artifacts",
        "export_progress",
    )
    widgets = [getattr(window, name) for name in named_widgets]
    widgets.extend(window.field_editors.values())
    widgets.extend(window.channel_editor_fields.values())
    for widget in widgets:
        assert field_help_id(widget) is not None
        assert len(widget.toolTip().splitlines()) >= 2

    tables = (
        window.channel_table,
        window.test_table,
        window.live_metrics_table,
        window.session_table,
        window.review_extrema_table,
        window.review_detected_events,
    )
    for table in tables:
        assert field_help_id(table) is not None
        assert table.toolTip()
        for column in range(table.columnCount()):
            assert table.horizontalHeaderItem(column).toolTip()

    for table in (window.channel_table, window.test_table):
        assert table.rowCount() > 0
        for column in range(table.columnCount()):
            assert table.item(0, column).toolTip() == table.horizontalHeaderItem(column).toolTip()

    plot_widgets = (
        window.raw_live_plot,
        window.conditioned_live_plot,
        window.review_plot,
        window.review_fft_plot,
        window.review_psd_plot,
    )
    for plot in plot_widgets:
        assert field_help_id(plot) == "plot.panel"
        if isinstance(plot, SignalPlotPanel):
            for control in (
                plot.scale_mode,
                plot.minimum,
                plot.maximum,
                plot.channels,
                plot.coordinate_label,
            ):
                assert field_help_id(control) is not None
                assert control.toolTip()

    dialog = RawDiagnosticAuthorizationDialog("Operator")
    qtbot.addWidget(dialog)  # type: ignore[attr-defined]
    for control in (
        dialog.supply_voltage,
        dialog.supply_polarity,
        dialog.ai3_disconnected,
        dialog.ai3_connected,
        dialog.ai3_minimum,
        dialog.ai3_maximum,
        dialog.ai3_instrument,
    ):
        assert field_help_id(control) is not None
        assert control.toolTip()

    window.close()


def test_main_window_validation_notice_preserves_field_description(
    qtbot: object,
    tmp_path: Path,
) -> None:
    _, window = create_desktop_application(
        backend="simulated",
        argv=[],
        preferences=_preferences(tmp_path),
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    editor = window.field_editors["experiment.name"]
    base = tooltip_text("experiment.name")
    original_issues = window.draft.issues
    window.draft = replace(
        window.draft,
        issues=(
            FieldIssue(
                "DAQ.CONFIG.FIELD_INVALID",
                "experiment.name",
                ErrorSeverity.ERROR,
                "The experiment name is invalid.",
                "Enter a Windows-safe session name.",
            ),
        ),
    )

    window._mark_field(editor, "experiment.name")

    assert editor.toolTip().startswith(base)
    assert "Current status:" in editor.toolTip()
    assert "Enter a Windows-safe session name." in editor.toolTip()

    window.draft = replace(window.draft, issues=original_issues)
    window._mark_field(editor, "experiment.name")

    assert editor.toolTip() == base
    window.close()
