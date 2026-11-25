from __future__ import annotations

import threading
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QLabel, QListWidgetItem, QMessageBox, QSplitter

from labdaq.adapters.qt_preferences import QSettingsPreferenceAdapter
from labdaq.application.configuration_workspace import FieldIssue
from labdaq.application.readiness_service import RawDiagnosticConfirmation
from labdaq.application.session_catalog_service import SessionCatalog, SessionCatalogEntry
from labdaq.bootstrap import create_desktop_application
from labdaq.domain.enums import MetricAvailability, SignalStage, Unit
from labdaq.domain.models import ProcessingRange
from labdaq.domain.plotting import PlotRepresentation
from labdaq.errors import DependencyUnavailableError, ErrorSeverity, LabDAQError
from labdaq.gui.__main__ import main as gui_main
from labdaq.gui.background import KeyedBackgroundRunner
from labdaq.gui.main_window import _functional_display_issues
from labdaq.gui.tiles import TILE_PROPERTY
from labdaq.ports.acquisition import AcquisitionState
from labdaq.ports.daq import DaqValidationIssue
from labdaq.ports.operator import (
    OpenAcquisitionStatus,
    OpenMonitorState,
    OpenRecordingState,
    OperatorRunStatus,
)
from labdaq.ports.preferences import UserPreferences
from labdaq.ports.processing import (
    ChannelExtremaResult,
    DetectedTransientEvent,
    LocatedExtremum,
    TransientChannelThreshold,
    TransientDetectionResult,
    TransientDetectionSettings,
)
from labdaq.ports.session import SessionCompletionState, SessionFaultSummary

if TYPE_CHECKING:
    from pathlib import Path


def _preferences(tmp_path: Path) -> QSettingsPreferenceAdapter:
    settings = QSettings(str(tmp_path / "desktop.ini"), QSettings.Format.IniFormat)
    return QSettingsPreferenceAdapter(settings)


def _assert_first_splitter_handle_moves(splitter: QSplitter, qtbot: object) -> None:
    before = splitter.sizes()
    assert len(before) >= 2
    assert all(size > 0 for size in before)
    splitter.moveSplitter(before[0] + 30, 1)
    qtbot.wait(10)  # type: ignore[attr-defined]
    assert splitter.sizes() != before
    assert all(size > 0 for size in splitter.sizes())


def _assert_splitter_tiles_are_bordered(splitter: QSplitter) -> None:
    assert "border: 1px solid palette(mid)" in splitter.styleSheet()
    assert splitter.count() > 0
    assert all(
        splitter.widget(index).property(TILE_PROPERTY) is True for index in range(splitter.count())
    )


def test_sample_rate_selector_updates_batch_interval_and_recipe(
    qtbot: object,
    tmp_path: Path,
) -> None:
    _, window = create_desktop_application(
        backend="simulated",
        argv=[],
        preferences=_preferences(tmp_path),
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]

    assert not window.sample_rate_selector.isEditable()
    assert window.sample_rate_selector.count() == 31
    window.sample_rate_selector.setCurrentIndex(2)
    qtbot.wait(10)  # type: ignore[attr-defined]

    assert window.draft.normalized is not None
    assert window.draft.normalized.acquisition.sample_rate_hz == 50_000.0 / 3
    assert window.draft.normalized.acquisition.chunk_config.samples_per_channel == 1667
    assert window.draft.active_recipe_id == "baseline-low-rate"
    assert "100.020 ms" in window.read_interval_label.text()
    assert "baseline-low-rate" in window.recipe_box.toPlainText()
    assert "SHA-256" in window.recipe_box.toPlainText()


def test_review_event_selection_and_located_extrema_rendering(
    qtbot: object,
    tmp_path: Path,
) -> None:
    _, window = create_desktop_application(
        backend="simulated",
        argv=[],
        preferences=_preferences(tmp_path),
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    settings = TransientDetectionSettings()
    detection = TransientDetectionResult(
        signal_stage=SignalStage.RAW,
        sample_range=ProcessingRange(0, 1000),
        sample_rate_hz=1000.0,
        settings=settings,
        thresholds=(TransientChannelThreshold("accel-x", Unit.VOLT, 0.0, 1.0, 6.0),),
        events=(
            DetectedTransientEvent(
                channel_id="accel-x",
                unit=Unit.VOLT,
                crossing_range=ProcessingRange(100, 111),
                analysis_range=ProcessingRange(90, 121),
                peak_sample_index=105,
                peak_time_seconds=0.105,
                peak_deviation=8.0,
                normalized_peak_score=8.0,
            ),
        ),
    )

    window._transient_detection_ready(detection)  # type: ignore[attr-defined]
    window.review_detected_events.selectRow(0)
    qtbot.wait(10)  # type: ignore[attr-defined]

    assert window.review_start_seconds.text() == "0.09"
    assert window.review_stop_seconds.text() == "0.121"
    assert window.review_plot.selection_range == (0.09, 0.121)

    maximum = LocatedExtremum(MetricAvailability.AVAILABLE, 5.0, 5.0, 103, 0.103)
    minimum = LocatedExtremum(MetricAvailability.AVAILABLE, -8.0, 8.0, 105, 0.105)
    extrema = ChannelExtremaResult(
        channel_id="accel-x",
        unit=Unit.VOLT,
        signal_stage=SignalStage.RAW,
        sample_range=ProcessingRange(90, 121),
        sample_rate_hz=1000.0,
        maximum=maximum,
        minimum=minimum,
        absolute_peak=minimum,
    )
    window._review_extrema_ready((extrema,))  # type: ignore[attr-defined]

    assert window.review_extrema_table.item(0, 2).text() == "5"
    assert window.review_extrema_table.item(0, 8).text() == "-8"
    assert window.review_extrema_table.item(0, 9).text() == "105"


def test_open_acquisition_controls_render_independent_monitor_and_recording_state(
    qtbot: object,
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    _, window = create_desktop_application(
        backend="simulated",
        argv=[],
        preferences=_preferences(tmp_path),
        experiment_mode="accelerometer_commissioning",
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    window.acquisition_mode.setCurrentIndex(window.acquisition_mode.findData("open"))

    assert window.draft.normalized is not None
    assert f"All {len(window.draft.normalized.acquisition.channel_ids)} enabled channels" in (
        window.open_channel.text()
    )
    assert not window.open_start_button.isHidden()
    assert window.acquisition_start_button.isHidden()

    status = OpenAcquisitionStatus(
        monitor_state=OpenMonitorState.MONITORING,
        recording_state=OpenRecordingState.INACTIVE,
        monitor_run_id="monitor-1",
        selected_channel_id="accel-x",
        selected_channel_unit="V",
        monitor_started_at=datetime.now(UTC),
        elapsed_seconds=1.5,
        actual_sample_rate_hz=50_000.0,
        acquired_samples_per_channel=75_000,
        retained_start_sample_index=25_000,
        retained_stop_sample_index=75_000,
        display_frames_dropped=2,
        recording_session_id=None,
        recording_session_path=None,
        recording_source_start_sample_index=None,
        recording_source_stop_sample_index=None,
        recording_persisted_samples=0,
        ordered_channel_ids=window.draft.normalized.acquisition.channel_ids,
        ordered_channel_units=tuple("V" for _ in window.draft.normalized.acquisition.channel_ids),
    )
    window._render_open_status(status)
    assert window.open_record_button.text() == "Record"
    assert window.open_record_button.isEnabled()
    assert window.open_end_button.isEnabled()
    assert not window.open_channel.isEnabled()
    assert "monitored 75000" in window.acquisition_counts_label.text()

    recording = replace(
        status,
        recording_state=OpenRecordingState.RECORDING,
        recording_session_id="segment-1",
        recording_session_path=tmp_path / "segment-1",
        recording_source_start_sample_index=75_000,
        recording_persisted_samples=5_000,
    )
    window._render_open_status(recording)
    assert window.open_record_button.text() == "Stop recording"
    assert "segment persisted 5000" in window.acquisition_counts_label.text()

    current = {"status": status}
    submitted: list[str] = []

    def immediate_submit(
        key: str,
        operation: object,
        *,
        on_success: object,
        on_failure: object,
        cancel_previous: object = None,
    ) -> int:
        del cancel_previous
        submitted.append(key)
        try:
            value = operation()  # type: ignore[operator]
        except BaseException as error:
            on_failure(error)  # type: ignore[operator]
        else:
            on_success(value)  # type: ignore[operator]
        return len(submitted)

    monkeypatch.setattr(window.runner, "submit", immediate_submit)  # type: ignore[attr-defined]
    monkeypatch.setattr(window.runner, "busy", lambda _key: False)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        window.presenter,
        "open_acquisition_status",
        lambda: current["status"],
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        window.presenter,
        "start_open_recording",
        lambda _parent=None: recording,
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        window.presenter,
        "stop_open_recording",
        lambda: replace(recording, recording_state=OpenRecordingState.FINALIZING),
    )
    saved = SimpleNamespace(session_path=tmp_path / "segment-1")
    monkeypatch.setattr(  # type: ignore[attr-defined]
        window.presenter,
        "wait_for_open_recording",
        lambda: saved,
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        window.presenter,
        "open_acquisition_frame",
        lambda: None,
    )

    window._toggle_open_recording()
    assert "open-record" in submitted
    current["status"] = recording
    window._toggle_open_recording()
    assert "open-record-stop" in submitted
    assert "monitoring continues" in window.statusBar().currentMessage()

    readiness = SimpleNamespace(eligible=True)
    # Destination workers may still deliver readiness updates during this test.
    # Retain the real hardware presentation those callbacks need to inspect.
    window._open_monitor_started((object(), window.hardware, readiness, status))
    window.pause_display.setChecked(False)
    window._poll_live()
    assert "open-live-frame" in submitted

    terminal = replace(
        status,
        monitor_state=OpenMonitorState.COMPLETED,
        recording_state=OpenRecordingState.INACTIVE,
    )
    monkeypatch.setattr(window.presenter, "end_monitoring", lambda: terminal)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        window.presenter,
        "wait_for_monitor_terminal",
        lambda: terminal,
    )
    window._request_end_monitoring()
    assert "open-monitor-stop" in submitted
    assert window.acquisition_state_label.text() == "monitor completed / recording inactive"
    current["status"] = terminal
    window._open_status = terminal


def test_keyed_background_runner_ignores_superseded_results(qtbot: object) -> None:
    runner = KeyedBackgroundRunner()
    started = threading.Event()
    release = threading.Event()
    results: list[str] = []

    def old_operation() -> str:
        started.set()
        release.wait(timeout=5.0)
        return "old"

    runner.submit(
        "discovery",
        old_operation,
        on_success=results.append,
        on_failure=lambda error: (_ for _ in ()).throw(error),
    )
    assert started.wait(timeout=2.0)
    runner.submit(
        "discovery",
        lambda: "new",
        on_success=results.append,
        on_failure=lambda error: (_ for _ in ()).throw(error),
    )
    qtbot.waitUntil(lambda: results == ["new"], timeout=5_000)  # type: ignore[attr-defined]
    release.set()
    assert runner.shutdown(timeout_ms=5_000)
    assert results == ["new"]


def test_background_failure_invalidation_and_repeated_shutdown(qtbot: object) -> None:
    runner = KeyedBackgroundRunner()
    errors: list[BaseException] = []
    cancelled: list[bool] = []
    runner.submit(
        "catalog",
        lambda: (_ for _ in ()).throw(RuntimeError("catalog failed")),
        on_success=lambda value: None,
        on_failure=errors.append,
    )
    qtbot.waitUntil(lambda: len(errors) == 1, timeout=3_000)  # type: ignore[attr-defined]
    assert str(errors[0]) == "catalog failed"

    runner.invalidate("catalog", cancel=lambda: cancelled.append(True))
    assert cancelled == [True]
    assert runner.shutdown(timeout_ms=3_000)
    assert runner.shutdown(timeout_ms=3_000)


def test_gui_module_entrypoint_forwards_desktop_arguments(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    calls: dict[str, object] = {}

    def create(**kwargs: object) -> tuple[object, object]:
        calls.update(kwargs)
        return object(), object()

    monkeypatch.setattr("labdaq.bootstrap.create_desktop_application", create)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "labdaq.gui.preview.show_preview_acknowledgement", lambda backend: True
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "labdaq.gui.main_window.run_desktop_window", lambda window, application: 7
    )
    profile = tmp_path / "profile.json"
    configuration = tmp_path / "configuration.json"

    result = gui_main(
        [
            "--daq",
            "ni",
            "--profile",
            str(profile),
            "--configuration",
            str(configuration),
            "--qualification-plan",
            "full-v1",
        ]
    )

    assert result == 7
    assert calls["backend"] == "ni"
    assert calls["profile_path"] == profile
    assert calls["configuration_path"] == configuration
    assert calls["qualification_plan"] == "full-v1"


def test_gui_preview_rejection_happens_before_backend_composition(
    monkeypatch: object,
) -> None:
    composed: list[bool] = []
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "labdaq.gui.preview.show_preview_acknowledgement", lambda backend: False
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "labdaq.bootstrap.create_desktop_application", lambda **kwargs: composed.append(True)
    )

    assert gui_main(["--daq", "ni"]) == 0
    assert composed == []


def test_packaged_gui_bypasses_preview_acknowledgement_but_composes_ni(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    calls: dict[str, object] = {}

    def create(**kwargs: object) -> tuple[object, object]:
        calls.update(kwargs)
        return object(), object()

    def unexpected_acknowledgement(backend: str) -> bool:
        raise AssertionError(f"packaged launch requested acknowledgement for {backend}")

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "labdaq.gui.preview.show_preview_acknowledgement",
        unexpected_acknowledgement,
    )
    monkeypatch.setattr("labdaq.bootstrap.create_desktop_application", create)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "labdaq.gui.main_window.run_desktop_window", lambda window, application: 9
    )

    result = gui_main(
        [
            "--daq",
            "ni",
            "--profile",
            str(tmp_path / "profile.json"),
            "--configuration",
            str(tmp_path / "configuration.json"),
            "--qualification-plan",
            "full-v1",
        ],
        require_preview_acknowledgement=False,
    )

    assert result == 9
    assert calls["backend"] == "ni"


def test_packaged_feedback_launch_remains_backend_free(monkeypatch: object) -> None:
    shown: list[bool] = []

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "labdaq.gui.preview.show_feedback_bundle_dialog",
        lambda: shown.append(True),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "labdaq.bootstrap.create_desktop_application",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("DAQ backend composed")),
    )

    assert gui_main(["--feedback"], require_preview_acknowledgement=False) == 0
    assert shown == [True]


def test_functional_preview_forces_bundled_ni_commissioning_configuration(
    monkeypatch: object,
) -> None:
    calls: dict[str, object] = {}

    def create(**kwargs: object) -> tuple[object, object]:
        calls.update(kwargs)
        return object(), object()

    monkeypatch.setattr("labdaq.bootstrap.create_desktop_application", create)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "labdaq.gui.main_window.run_desktop_window", lambda window, application: 11
    )

    result = gui_main(
        [
            "--daq",
            "simulated",
            "--mode",
            "production",
            "--configuration",
            "ignored.json",
            "--functional-preview",
        ],
        require_preview_acknowledgement=False,
    )

    assert result == 11
    assert calls["backend"] == "ni"
    assert calls["experiment_mode"] == "accelerometer_commissioning"
    assert calls["configuration_path"] is None
    assert calls["functional_preview"] is True


def test_qsettings_preferences_round_trip_and_limit_recent_paths(tmp_path: Path) -> None:
    settings = QSettings(str(tmp_path / "preferences.ini"), QSettings.Format.IniFormat)
    adapter = QSettingsPreferenceAdapter(settings)
    expected = UserPreferences(
        window_geometry=b"geometry",
        recent_session_parents=tuple(tmp_path / f"destination-{index}" for index in range(8)),
        recent_browser_roots=(tmp_path,),
    )

    adapter.save(expected)
    adapter.sync()

    assert adapter.load() == expected


def test_qsettings_preferences_keep_production_and_commissioning_paths_separate(
    tmp_path: Path,
) -> None:
    settings = QSettings(str(tmp_path / "mode-preferences.ini"), QSettings.Format.IniFormat)
    adapter = QSettingsPreferenceAdapter(settings)
    expected = UserPreferences(
        production_profile_path=tmp_path / "production-profile.json",
        production_configuration_path=tmp_path / "production-config.json",
        production_qualification_plan_path=tmp_path / "production-plan.json",
        commissioning_profile_path=tmp_path / "commissioning-profile.json",
        commissioning_configuration_path=tmp_path / "commissioning-config.json",
        commissioning_qualification_plan_path=tmp_path / "commissioning-plan.json",
    )
    adapter.save(expected)
    adapter.sync()

    assert adapter.load() == expected


def test_commissioning_gui_selects_ai3_for_spectra_and_raw_time(
    qtbot: object,
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    _, window = create_desktop_application(
        backend="simulated",
        argv=[],
        preferences=_preferences(tmp_path),
        experiment_mode="accelerometer_commissioning",
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]

    assert "ai3 RAW ONLY / NOT CHARGE" in window.windowTitle()
    assert window.channel_table.rowCount() == 4
    assert window.channel_table.item(3, 3).text() == "diagnostic_raw_voltage"

    axis = QListWidgetItem("Accelerometer X")
    axis.setData(Qt.ItemDataRole.UserRole, "accel-x")
    axis.setData(int(Qt.ItemDataRole.UserRole) + 1, False)
    axis.setCheckState(Qt.CheckState.Checked)
    diagnostic = QListWidgetItem("UNPOWERED / UNQUALIFIED / NOT CHARGE")
    diagnostic.setData(Qt.ItemDataRole.UserRole, "pcb-422e03-raw-diagnostic")
    diagnostic.setData(int(Qt.ItemDataRole.UserRole) + 1, True)
    diagnostic.setCheckState(Qt.CheckState.Checked)
    window.review_channels.addItem(axis)
    window.review_channels.addItem(diagnostic)

    window.review_representation.setCurrentIndex(
        window.review_representation.findData(PlotRepresentation.RAW_TIME.value)
    )
    assert window._selected_review_channels() == (
        "accel-x",
        "pcb-422e03-raw-diagnostic",
    )
    window.review_representation.setCurrentIndex(
        window.review_representation.findData(PlotRepresentation.PSD.value)
    )
    assert window._selected_review_channels() == ("accel-x", "pcb-422e03-raw-diagnostic")
    window.review_signal_stage.setCurrentIndex(
        window.review_signal_stage.findData(SignalStage.CONDITIONED.value)
    )
    assert window._selected_review_channels() == ("accel-x",)
    assert "excluded" in window.review_channel_notice.text()
    assert window._selected_review_channels(PlotRepresentation.FFT_AMPLITUDE) == (
        "accel-x",
        "pcb-422e03-raw-diagnostic",
    )
    window._review_analysis_tab_changed(0)
    assert window._selected_review_channels() == ("accel-x", "pcb-422e03-raw-diagnostic")
    axis.setCheckState(Qt.CheckState.Unchecked)
    assert window._selected_review_channels(PlotRepresentation.FFT_AMPLITUDE) == (
        "pcb-422e03-raw-diagnostic",
    )
    assert window._selected_review_channels(PlotRepresentation.FILTERED_TIME) == ()
    submitted = []
    monkeypatch.setattr(
        window, "_submit_review_analysis_plot", lambda *args: submitted.append(args)
    )  # type: ignore[attr-defined]
    window._review_path = tmp_path
    window._request_review_analysis()
    assert [args[0] for args in submitted] == [
        PlotRepresentation.FFT_AMPLITUDE,
        PlotRepresentation.PSD,
    ]
    assert all(args[2] == ("pcb-422e03-raw-diagnostic",) for args in submitted)
    assert "No channels eligible" in window.review_plot.coordinate_label.text()
    diagnostic.setCheckState(Qt.CheckState.Unchecked)
    assert window._selected_review_channels(PlotRepresentation.PSD) == ()


def test_spectral_panels_group_units_and_clear_failed_results(
    qtbot: object,
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    _, window = create_desktop_application(
        backend="simulated",
        argv=[],
        preferences=_preferences(tmp_path),
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    panel = window.review_fft_plot
    values = (
        ("accel-x", "X", "g", [0.0, 1.0], [1.0, 2.0]),
        ("ai3", "ai3 — Raw voltage", "V", [0.0, 1.0], [0.001, 0.002]),
    )
    panel._set_series(values, x_label="Frequency", x_unit="Hz")
    assert set(panel._plots) == {"g", "V"}
    assert set(panel._curves) == {"accel-x", "ai3"}
    panel._set_series(values[1:], x_label="Frequency", x_unit="Hz")
    assert panel.channels.count() == 1
    assert set(panel._curves) == {"ai3"}
    failures = []
    monkeypatch.setattr(  # type: ignore[attr-defined]
        window.runner,
        "submit",
        lambda *args, **kwargs: failures.append(kwargs["on_failure"]),
    )
    window._review_path = tmp_path
    window._submit_review_analysis_plot(
        PlotRepresentation.FFT_AMPLITUDE, "review-fft", ("ai3",), None, None
    )
    error = LabDAQError("FFT range too large", code="DAQ.PROCESSING.SPECTRAL_SELECTION_TOO_LARGE")
    failures.pop()(error)
    assert panel._latest == {}
    assert panel.channels.count() == 0
    assert panel.coordinate_label.text() == "FFT failed; see status for details."

    item = QListWidgetItem("ai3")
    item.setData(Qt.ItemDataRole.UserRole, "ai3")
    item.setCheckState(Qt.CheckState.Checked)
    window.review_channels.addItem(item)
    panel._set_series(values, x_label="Frequency", x_unit="Hz")
    window.review_representation.setCurrentIndex(
        window.review_representation.findData(PlotRepresentation.FFT_AMPLITUDE.value)
    )
    window._request_review_plot()
    window.review_representation.setCurrentIndex(
        window.review_representation.findData(PlotRepresentation.PSD.value)
    )
    failures.pop()(error)
    assert panel._latest == {}
    assert panel.coordinate_label.text() == "FFT failed; see status for details."


def test_commissioning_gui_exposes_raw_diagnostic_readiness_and_hover_blockers(
    qtbot: object,
    tmp_path: Path,
) -> None:
    _, window = create_desktop_application(
        backend="simulated",
        argv=[],
        preferences=_preferences(tmp_path),
        experiment_mode="accelerometer_commissioning",
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    window.show()
    window.acquisition_mode.setCurrentIndex(window.acquisition_mode.findData("qualified"))
    qtbot.waitUntil(lambda: window.hardware.validation is not None, timeout=5_000)  # type: ignore[attr-defined]
    original_validation_key = window.hardware.validation_key
    window.destination_edit.setText(str(tmp_path))
    window.destination_edit.editingFinished.emit()

    assert window.discovery_label.text() == "Compatible"
    assert window.hardware.validation_key == original_validation_key
    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: (
            window.diagnostic_readiness is not None
            and window.diagnostic_readiness.eligible
            and window.hardware.validation is not None
        ),
        timeout=5_000,
    )

    assert window.diagnostic_start_button.isEnabled()
    assert not window.acquisition_start_button.isEnabled()
    assert window.continue_button.isEnabled()
    assert window.readiness_list.count() > 0
    assert "qualification.current" in window.readiness_list.item(0).text()
    assert window.diagnostic_readiness_list.count() == 1
    assert "Physical authorization" in window.diagnostic_readiness_list.item(0).text()
    assert window.global_status.text() == ("Raw diagnostic ready; qualified acquisition blocked")
    tooltip = window.global_status.toolTip()
    assert "DAQ.QUALIFICATION.REPORT_MISSING" in tooltip
    assert "Run hardware qualification or load matching current evidence" in tooltip
    assert "Raw-voltage diagnostic" in tooltip
    assert "Eligible after physical authorization" in tooltip

    qtbot.mouseClick(window.continue_button, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]
    assert window.navigation.currentRow() == 2

    window.close()
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=5_000)  # type: ignore[attr-defined]


def test_hardware_revalidation_is_serialized_and_latest_acquisition_edit_wins(
    qtbot: object,
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    _, window = create_desktop_application(
        backend="simulated",
        argv=[],
        preferences=_preferences(tmp_path),
        experiment_mode="accelerometer_commissioning",
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    window.show()
    qtbot.waitUntil(lambda: window.hardware.validation is not None, timeout=5_000)  # type: ignore[attr-defined]
    original_key = window.hardware.validation_key

    window.field_editors["experiment.notes"].setText("metadata-only edit")
    window.field_editors["experiment.notes"].editingFinished.emit()
    assert window.hardware.validation_key == original_key
    assert window.discovery_label.text() == "Compatible"

    window.channel_editor_fields["sensitivity"].setText("6.915")
    window.channel_editor_fields["sensitivity"].editingFinished.emit()
    assert window.hardware.validation_key == original_key
    assert window.discovery_label.text() == "Compatible"

    original_refresh = window.presenter.refresh_hardware
    first_started = threading.Event()
    release_first = threading.Event()
    calls: list[int] = []

    def delayed_refresh(draft: object) -> object:
        chunk_size = draft.normalized.acquisition.chunk_config.samples_per_channel  # type: ignore[attr-defined]
        calls.append(chunk_size)
        if chunk_size == 2500:
            first_started.set()
            assert release_first.wait(5.0)
        return original_refresh(draft)  # type: ignore[arg-type]

    monkeypatch.setattr(window.presenter, "refresh_hardware", delayed_refresh)  # type: ignore[attr-defined]
    window.field_editors["acquisition.chunk_size"].setText("2500")
    window.field_editors["acquisition.chunk_size"].editingFinished.emit()
    qtbot.waitUntil(first_started.is_set, timeout=5_000)  # type: ignore[attr-defined]
    window.field_editors["acquisition.chunk_size"].setText("2000")
    window.field_editors["acquisition.chunk_size"].editingFinished.emit()
    release_first.set()

    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: (
            window.discovery_label.text() == "Compatible"
            and window.hardware.validation_key
            == window.presenter.hardware_validation_key(window.draft)
        ),
        timeout=10_000,
    )
    assert calls == [2500, 2000]
    assert window.hardware.validation_key != original_key

    window.close()
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=5_000)  # type: ignore[attr-defined]


def test_functional_preview_defaults_open_and_timed_launches_without_confirmation(
    qtbot: object,
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    _, window = create_desktop_application(
        backend="simulated",
        argv=[],
        preferences=_preferences(tmp_path),
        experiment_mode="accelerometer_commissioning",
        functional_preview=True,
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "labdaq.gui.main_window.RawDiagnosticAuthorizationDialog.exec",
        lambda dialog: (_ for _ in ()).throw(AssertionError("confirmation dialog opened")),
    )
    window.show()

    assert "FUNCTIONAL NI TEST" in window.windowTitle()
    assert window.navigation.isRowHidden(1)
    assert window.qualified_readiness_group.isHidden()
    assert window.diagnostic_readiness_group.title() == "Functional-run readiness"
    assert window.diagnostic_start_button.text() == "Start Functional Run"
    assert not window.acquisition_mode.isHidden()
    assert window.acquisition_mode.currentData() == "open"
    assert not window.open_start_button.isHidden()
    assert "All 4 enabled channels" in window.open_channel.text()
    assert window.acquisition_start_button.isHidden()

    window.acquisition_mode.setCurrentIndex(window.acquisition_mode.findData("functional"))

    window.destination_edit.setText(str(tmp_path))
    window.destination_edit.editingFinished.emit()
    window.field_editors["acquisition.duration_s"].setText("0.02")
    window.field_editors["acquisition.duration_s"].editingFinished.emit()
    qtbot.mouseClick(window.discover_button, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]
    qtbot.waitUntil(lambda: window.diagnostic_start_button.isEnabled(), timeout=5_000)  # type: ignore[attr-defined]

    assert window.readiness_list.count() == 0
    assert window.diagnostic_readiness_list.count() == 1
    assert "READY" in window.diagnostic_readiness_list.item(0).text()
    assert window.global_status.text() == "Ready for unqualified functional acquisition"

    qtbot.mouseClick(window.diagnostic_start_button, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]
    qtbot.waitUntil(lambda: window._current_summary is not None, timeout=15_000)  # type: ignore[attr-defined]
    assert window._current_summary.session.session_type.value == "experiment"
    assert not window._current_summary.session.qualification_report_available
    assert not window.conditioned_live_plot.isHidden()

    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: window._review is not None and not window.runner.busy("review-plot"),
        timeout=15_000,
    )
    window.review_signal_stage.setCurrentIndex(
        window.review_signal_stage.findData(SignalStage.CONDITIONED.value)
    )
    window._request_review_analysis()
    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: (
            not any(
                window.runner.busy(key)
                for key in ("review-extrema", "review-time", "review-fft", "review-psd")
            )
        ),
        timeout=15_000,
    )
    assert len(window.review_plot._latest) == 3
    assert window.review_extrema_table.rowCount() == 3
    assert len(window.review_fft_plot._latest) == 4
    assert len(window.review_psd_plot._latest) == 4
    assert set(window.review_fft_plot._plots) == {"g", "V"}
    assert set(window.review_psd_plot._plots) == {"g^2/Hz", "V^2/Hz"}
    for index in range(3):
        window.review_channels.item(index).setCheckState(Qt.CheckState.Unchecked)
    window._request_review_analysis()
    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: not window.runner.busy("review-fft") and not window.runner.busy("review-psd"),
        timeout=15_000,
    )
    assert tuple(window.review_fft_plot._latest) == ("pcb-422e03-raw-diagnostic",)
    assert window.review_extrema_table.rowCount() == 0
    assert window.review_plot._latest == {}
    window.review_stop_seconds.setText("1000")
    window._request_review_analysis()
    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: not window.runner.busy("review-fft") and not window.runner.busy("review-psd"),
        timeout=15_000,
    )
    assert window.review_fft_plot._latest == {}
    assert window.review_psd_plot._latest == {}
    assert window.review_fft_plot.coordinate_label.text().startswith("FFT failed")
    assert window.review_psd_plot.coordinate_label.text().startswith("PSD failed")
    window.review_stop_seconds.setText("invalid")
    window._request_review_analysis()
    assert not window.runner.busy("review-fft")

    window.close()
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=5_000)  # type: ignore[attr-defined]


def test_functional_preview_open_monitor_needs_no_authorization_and_uses_selected_channels(
    qtbot: object,
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    _, window = create_desktop_application(
        backend="simulated",
        argv=[],
        preferences=_preferences(tmp_path),
        experiment_mode="accelerometer_commissioning",
        functional_preview=True,
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    window.show()
    window.destination_edit.setText(str(tmp_path))
    window.destination_edit.editingFinished.emit()
    qtbot.waitUntil(lambda: window.open_start_button.isEnabled(), timeout=5_000)  # type: ignore[attr-defined]
    assert window.draft.normalized is not None
    expected_channels = window.draft.normalized.acquisition.channel_ids
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "labdaq.gui.main_window.RawDiagnosticAuthorizationDialog.exec",
        lambda _dialog: (_ for _ in ()).throw(AssertionError("authorization dialog opened")),
    )
    qtbot.mouseClick(window.open_start_button, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]
    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: window._open_status is not None and window._open_status.active,
        timeout=10_000,
    )

    assert window._open_status is not None
    assert window._open_status.ordered_channel_ids == expected_channels
    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: (
            window.presenter.open_acquisition_frame() is not None
            and len(window.presenter.open_acquisition_frame().series) == len(expected_channels)  # type: ignore[union-attr]
        ),
        timeout=10_000,
    )

    qtbot.mouseClick(window.open_end_button, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]
    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: window._open_status is not None and not window._open_status.active,
        timeout=10_000,
    )
    window.close()
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=5_000)  # type: ignore[attr-defined]


def test_raw_monitor_gui_selects_channels_and_records_repeated_segments(
    qtbot: object, tmp_path: Path, monkeypatch: object
) -> None:
    _, window = create_desktop_application(
        backend="simulated", argv=[], preferences=_preferences(tmp_path)
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    window.show()
    try:
        title = window.findChild(QLabel, "applicationTitle")
        assert title is not None
        assert "LAB-RELEASE" in title.text()
        assert "FEEDBACK PREVIEW" not in title.text()
        monkeypatch.setattr(  # type: ignore[attr-defined]
            "labdaq.gui.main_window.RawDiagnosticAuthorizationDialog.exec",
            lambda _dialog: (_ for _ in ()).throw(AssertionError("authorization dialog opened")),
        )
        assert window.acquisition_mode.currentData() == "open"
        window.channel_table.item(1, 1).setCheckState(Qt.CheckState.Unchecked)
        window.channel_table.item(3, 1).setCheckState(Qt.CheckState.Unchecked)
        qtbot.waitUntil(lambda: window.open_start_button.isEnabled(), timeout=10_000)  # type: ignore[attr-defined]
        assert window.draft.normalized is not None
        expected = window.draft.normalized.acquisition.channel_ids
        assert len(expected) == 2
        assert all(c.sensor_id is None for c in window.draft.normalized.channels)
        qtbot.mouseClick(window.open_start_button, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]
        qtbot.waitUntil(  # type: ignore[attr-defined]
            lambda: window._open_status is not None and window._open_status.active,
            timeout=10_000,
        )
        qtbot.waitUntil(  # type: ignore[attr-defined]
            lambda: window.presenter.open_acquisition_frame() is not None, timeout=10_000
        )
        frame = window.presenter.open_acquisition_frame()
        assert frame is not None
        assert tuple(series.channel_id for series in frame.series) == expected
        assert all(series.unit == "V" for series in frame.series)
        assert window._open_status is not None
        assert window._open_status.ordered_channel_ids == expected
        assert not list(tmp_path.glob("*/manifest.json"))

        window.destination_edit.setText(str(tmp_path))
        paths = []
        for _ in range(2):
            qtbot.waitUntil(lambda: window.open_record_button.isEnabled(), timeout=10_000)  # type: ignore[attr-defined]
            qtbot.mouseClick(window.open_record_button, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]
            qtbot.waitUntil(  # type: ignore[attr-defined]
                lambda: (
                    window._open_status is not None
                    and window._open_status.recording_persisted_samples > 0
                ),
                timeout=15_000,
            )
            qtbot.waitUntil(lambda: window.open_record_button.isEnabled(), timeout=10_000)  # type: ignore[attr-defined]
            qtbot.mouseClick(window.open_record_button, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]
            qtbot.waitUntil(  # type: ignore[attr-defined]
                lambda: (
                    window._open_status is not None
                    and window._open_status.recording_state is OpenRecordingState.INACTIVE
                    and window._open_status.last_recording is not None
                ),
                timeout=15_000,
            )
            assert (
                window._open_status is not None and window._open_status.last_recording is not None
            )
            paths.append(window._open_status.last_recording.session_path)
            assert window._open_status.active
        assert paths[0] != paths[1]
        for path in paths:
            assert path.parent == tmp_path
            review = window.presenter.load_session_review(path)
            assert review.summary.ordered_channel_ids == expected

        qtbot.waitUntil(lambda: window.open_end_button.isEnabled(), timeout=10_000)  # type: ignore[attr-defined]
        qtbot.mouseClick(window.open_end_button, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]
        qtbot.waitUntil(  # type: ignore[attr-defined]
            lambda: window._open_status is not None and not window._open_status.active,
            timeout=10_000,
        )
        window.close()
        qtbot.waitUntil(lambda: not window.isVisible(), timeout=5_000)  # type: ignore[attr-defined]

    finally:
        window.presenter.close()
        window._force_close = True
        window.close()


def test_raw_monitor_gui_reports_missing_discovered_channel(qtbot: object, tmp_path: Path) -> None:
    _, window = create_desktop_application(
        backend="simulated", argv=[], preferences=_preferences(tmp_path)
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    qtbot.waitUntil(lambda: window.hardware.validation is not None, timeout=10_000)  # type: ignore[attr-defined]
    discovery = window.hardware.discovery
    validation = window.hardware.validation
    assert discovery is not None and validation is not None
    device = discovery.devices[0]
    module = device.modules[0]
    missing = module.available_channels[-1]
    discovery = replace(
        discovery,
        devices=(
            replace(
                device,
                modules=(replace(module, available_channels=module.available_channels[:-1]),),
            ),
        ),
    )
    validation = replace(
        validation,
        accepted=False,
        errors=(
            DaqValidationIssue(
                "DAQ.CHANNEL.UNAVAILABLE",
                "channels[3].physical_channel",
                f"{missing} is unavailable",
                "Select an available channel.",
            ),
        ),
    )
    window._hardware_ready(replace(window.hardware, discovery=discovery, validation=validation))
    assert "unavailable" in window.channel_table.item(3, 4).text()
    assert not window.open_start_button.isEnabled()
    assert any(
        "Select an available channel" in window.diagnostic_readiness_list.item(row).text()
        for row in range(window.diagnostic_readiness_list.count())
    )
    window.close()


def test_functional_preview_collapses_noneditable_template_defects() -> None:
    issues = (
        FieldIssue(
            "DAQ.CONFIG.FIELD_INVALID",
            "conditioning_chains[0].charge_converter.sensitivity_unit",
            ErrorSeverity.ERROR,
            "invalid unit",
            "supply a unit",
        ),
        FieldIssue(
            "DAQ.CONFIG.FIELD_INVALID",
            "conditioning_chains[0].polarity",
            ErrorSeverity.ERROR,
            "invalid polarity",
            "supply polarity",
        ),
        FieldIssue(
            "DAQ.HARDWARE.DISCOVERY_REQUIRED",
            "hardware.profile_id",
            ErrorSeverity.ERROR,
            "refresh required",
            "refresh hardware",
        ),
    )

    displayed = _functional_display_issues(issues)

    assert [issue.code for issue in displayed] == [
        "DAQ.HARDWARE.DISCOVERY_REQUIRED",
        "DAQ.CONFIGURATION.BUNDLED_TEMPLATE_INVALID",
    ]


def test_commissioning_gui_launches_and_reopens_raw_diagnostic(
    qtbot: object,
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    _, window = create_desktop_application(
        backend="simulated",
        argv=[],
        preferences=_preferences(tmp_path),
        experiment_mode="accelerometer_commissioning",
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    window.show()
    window.acquisition_mode.setCurrentIndex(window.acquisition_mode.findData("qualified"))
    window.destination_edit.setText(str(tmp_path))
    window.destination_edit.editingFinished.emit()
    window.field_editors["acquisition.duration_s"].setText("0.02")
    window.field_editors["acquisition.duration_s"].editingFinished.emit()
    qtbot.mouseClick(window.discover_button, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]
    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: window.diagnostic_start_button.isEnabled(),
        timeout=5_000,
    )
    confirmation = RawDiagnosticConfirmation(
        operator="operator",
        confirmed_at=datetime.now(UTC),
        measured_supply_voltage_v=5.0,
        polarity_confirmed=True,
        ai3_connection="disconnected",
    )

    def accept_dialog(dialog: object) -> object:
        dialog.confirmation = confirmation  # type: ignore[attr-defined]
        return 1

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "labdaq.gui.main_window.RawDiagnosticAuthorizationDialog.exec",
        accept_dialog,
    )
    qtbot.mouseClick(window.diagnostic_start_button, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]
    qtbot.waitUntil(lambda: window._current_summary is not None, timeout=15_000)  # type: ignore[attr-defined]
    qtbot.waitUntil(lambda: window._review is not None, timeout=15_000)  # type: ignore[attr-defined]
    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: (
            not window.runner.busy("review-plot")
            and "samples [0, 1000)" in window.review_plot_metadata.text()
        ),
        timeout=10_000,
    )

    assert window._current_summary.session.session_type.value == "raw-diagnostic"
    assert window._current_summary.recorded.persisted_samples_per_channel == 1000
    assert not window.conditioned_live_plot.isVisible()
    assert window.review_representation.isEnabled()
    assert not window.review_signal_stage.isEnabled()
    assert window.review_analysis_tabs.isTabEnabled(0)
    assert window.review_analysis_tabs.isTabEnabled(1)
    model = window.review_representation.model()
    assert not model.item(
        window.review_representation.findData(PlotRepresentation.CALIBRATED_TIME.value)
    ).isEnabled()
    window._request_review_analysis()
    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: (
            not any(
                window.runner.busy(key)
                for key in ("review-extrema", "review-time", "review-fft", "review-psd")
            )
        ),
        timeout=15_000,
    )
    assert len(window.review_fft_plot._latest) == 4
    assert len(window.review_psd_plot._latest) == 4
    assert all(values[2] == "V" for values in window.review_fft_plot._latest.values())
    assert "Raw voltage" in window.review_fft_plot._latest["pcb-422e03-raw-diagnostic"][3]
    assert window.review_representation.currentData() == PlotRepresentation.RAW_TIME.value
    assert "RAW DIAGNOSTIC SESSION" in window.review_artifacts.toPlainText()
    assert window.review_start_seconds.text() == ""
    assert window.review_stop_seconds.text() == ""

    review = window._review
    assert review is not None
    automatic_requests: list[bool] = []
    monkeypatch.setattr(  # type: ignore[attr-defined]
        window,
        "_request_review_plot",
        lambda: automatic_requests.append(True),
    )
    for state in (SessionCompletionState.STOPPED, SessionCompletionState.FAULTED):
        window._review_ready(  # type: ignore[attr-defined]
            replace(review, summary=replace(review.summary, completion_state=state)),
            automatic=True,
        )
    assert automatic_requests == [True, True]

    window._review_ready(  # type: ignore[attr-defined]
        replace(
            review,
            summary=replace(
                review.summary,
                persisted_samples_per_channel=0,
                raw_data_available=False,
            ),
        ),
        automatic=True,
    )
    assert automatic_requests == [True, True]
    assert "No persisted raw samples" in window.review_plot_metadata.text()

    window.close()
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=5_000)  # type: ignore[attr-defined]


def test_simulated_setup_is_scrollable_and_reports_actionable_initial_blockers(
    qtbot: object,
    tmp_path: Path,
) -> None:
    application, window = create_desktop_application(
        backend="simulated",
        argv=[],
        preferences=_preferences(tmp_path),
    )
    del application
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    window.resize(1760, 776)
    window.show()
    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: window.hardware.validation is not None,
        timeout=5_000,
    )

    assert window.height() == 776
    assert window.setup_scroll_area.verticalScrollBar().maximum() > 0
    for control in (window.destination_edit, window.readiness_list, window.continue_button):
        window.setup_scroll_area.ensureWidgetVisible(control)
        qtbot.wait(10)  # type: ignore[attr-defined]
        assert not control.visibleRegion().isEmpty()
    window.setup_scroll_area.ensureWidgetVisible(window.channel_editor_fields["ni_source"])
    qtbot.wait(10)  # type: ignore[attr-defined]
    assert not window.channel_editor_fields["ni_source"].visibleRegion().isEmpty()

    assert [
        window.channel_table.item(row, 3).text() for row in range(window.channel_table.rowCount())
    ] == ["diagnostic_raw_voltage"] * 4

    missing_parent = tmp_path / "missing-sessions"
    window.destination_edit.setText(str(missing_parent))
    window.destination_edit.editingFinished.emit()
    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: window.destination is not None and not window.destination.valid,
        timeout=5_000,
    )
    assert not missing_parent.exists()
    assert window.continue_button.isEnabled()
    assert window.global_status.text() == "Ready for open raw-voltage monitoring"

    window.close()
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=5_000)  # type: ignore[attr-defined]


def test_plot_workspaces_are_dominant_resizable_and_navigation_collapses(
    qtbot: object,
    tmp_path: Path,
) -> None:
    application, window = create_desktop_application(
        backend="simulated",
        argv=[],
        preferences=_preferences(tmp_path),
    )
    del application
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    window.resize(1760, 776)
    window.show()
    window.acquisition_mode.setCurrentIndex(window.acquisition_mode.findData("qualified"))
    qtbot.wait(50)  # type: ignore[attr-defined]

    window.navigation.setCurrentRow(2)
    qtbot.wait(20)  # type: ignore[attr-defined]
    selected_page = window.navigation.currentRow()
    expanded_page_width = window.pages.width()
    assert window.navigation.isVisible()
    assert window.navigation_toggle.text() == "Hide navigation"
    qtbot.mouseClick(window.navigation_toggle, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]
    qtbot.wait(20)  # type: ignore[attr-defined]
    assert not window.navigation.isVisible()
    assert window.navigation_toggle.text() == "Show navigation"
    assert window.pages.width() > expanded_page_width
    assert window.navigation.currentRow() == selected_page
    qtbot.mouseClick(window.navigation_toggle, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]
    qtbot.wait(20)  # type: ignore[attr-defined]
    assert window.navigation.isVisible()
    assert window.navigation.currentRow() == selected_page

    summary_height, plot_height = window.acquisition_workspace_splitter.sizes()
    assert plot_height >= summary_height * 2
    status_width, metrics_width = window.acquisition_summary_splitter.sizes()
    assert min(status_width, metrics_width) >= (status_width + metrics_width) * 0.3
    raw_height, conditioned_height = window.live_plot_splitter.sizes()
    assert raw_height > 0 and conditioned_height > 0
    assert abs(raw_height - conditioned_height) <= window.live_plot_splitter.handleWidth()
    channel_width, canvas_width = window.raw_live_plot.channel_plot_splitter.sizes()
    assert canvas_width >= channel_width * 3
    assert window.raw_live_plot.isVisible()
    assert window.conditioned_live_plot.isVisible()
    for splitter in (
        window.shell_splitter,
        window.readiness_splitter,
        window.acquisition_workspace_splitter,
        window.acquisition_summary_splitter,
        window.live_plot_splitter,
        window.raw_live_plot.channel_plot_splitter,
        window.conditioned_live_plot.channel_plot_splitter,
    ):
        _assert_splitter_tiles_are_bordered(splitter)
    for splitter in (
        window.shell_splitter,
        window.acquisition_workspace_splitter,
        window.acquisition_summary_splitter,
        window.live_plot_splitter,
        window.raw_live_plot.channel_plot_splitter,
    ):
        assert splitter.handleWidth() == 8
        _assert_first_splitter_handle_moves(splitter, qtbot)

    window.raw_live_plot.set_live_frame(
        SimpleNamespace(
            series=(
                SimpleNamespace(
                    channel_id="voltage",
                    display_name="Voltage",
                    unit="V",
                    relative_time_seconds=[0.0, 1.0],
                    values=[0.0, 1.0],
                ),
                SimpleNamespace(
                    channel_id="acceleration",
                    display_name="Acceleration",
                    unit="g",
                    relative_time_seconds=[0.0, 1.0],
                    values=[0.0, -1.0],
                ),
            )
        )
    )
    qtbot.wait(20)  # type: ignore[attr-defined]
    assert window.raw_live_plot.plot_stack_splitter.count() == 2
    _assert_splitter_tiles_are_bordered(window.raw_live_plot.plot_stack_splitter)
    assert window.raw_live_plot.plot_stack_splitter.handleWidth() == 8
    _assert_first_splitter_handle_moves(window.raw_live_plot.plot_stack_splitter, qtbot)

    window.navigation.setCurrentRow(3)
    qtbot.wait(20)  # type: ignore[attr-defined]
    browser_height, review_plot_height, diagnostics_height = (
        window.review_workspace_splitter.sizes()
    )
    assert review_plot_height > browser_height
    assert review_plot_height > diagnostics_height
    for splitter in (
        window.review_workspace_splitter,
        window.session_browser_splitter,
        window.review_plot_splitter,
        window.review_analysis_splitter,
        window.review_details_splitter,
        window.review_plot.channel_plot_splitter,
        window.review_fft_plot.channel_plot_splitter,
        window.review_psd_plot.channel_plot_splitter,
    ):
        _assert_splitter_tiles_are_bordered(splitter)
    for splitter in (
        window.review_workspace_splitter,
        window.session_browser_splitter,
        window.review_plot_splitter,
        window.review_details_splitter,
        window.review_plot.channel_plot_splitter,
    ):
        assert splitter.handleWidth() == 8
        _assert_first_splitter_handle_moves(splitter, qtbot)

    window.close()
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=5_000)  # type: ignore[attr-defined]


def test_plot_workspace_layout_survives_unavailable_plotting_dependency(
    qtbot: object,
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    def unavailable_plot(*_args: object, **_kwargs: object) -> object:
        raise DependencyUnavailableError(
            "Plotting unavailable for layout test.",
            code="DAQ.DEPENDENCY.UNAVAILABLE",
        )

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "labdaq.gui.main_window.SignalPlotPanel",
        unavailable_plot,
    )
    application, window = create_desktop_application(
        backend="simulated",
        argv=[],
        preferences=_preferences(tmp_path),
    )
    del application
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    window.resize(1760, 776)
    window.show()
    qtbot.wait(20)  # type: ignore[attr-defined]

    assert isinstance(window.raw_live_plot, QLabel)
    assert isinstance(window.conditioned_live_plot, QLabel)
    assert isinstance(window.review_plot, QLabel)
    for splitter in (
        window.live_plot_splitter,
        window.review_workspace_splitter,
        window.review_plot_splitter,
    ):
        _assert_splitter_tiles_are_bordered(splitter)
    assert all(size > 0 for size in window.live_plot_splitter.sizes())
    window.navigation.setCurrentRow(3)
    qtbot.wait(20)  # type: ignore[attr-defined]
    assert window.review_workspace_splitter.sizes()[1] > 0
    for key in ("plot", "analysis"):
        window.review_layout.minimize(key)
        window.review_layout.restore(key)
        window.review_layout.toggle_maximize(key)
        assert window.review_layout.panels[key].isVisible()
        window.review_layout.restore_maximized()

    window.close()
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=5_000)  # type: ignore[attr-defined]


def test_simulated_window_edits_configuration_previews_destination_and_closes(
    qtbot: object,
    tmp_path: Path,
) -> None:
    application, window = create_desktop_application(
        backend="simulated",
        argv=[],
        preferences=_preferences(tmp_path),
    )
    del application
    window.draft = window.presenter.initial_draft(template_id="four-channel-vibration-synthetic-v2")
    window._render_draft()
    window._submit_discovery()
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    window.show()

    window.destination_edit.setText(str(tmp_path))
    window.destination_edit.editingFinished.emit()
    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: (
            window.destination is not None and window.destination.plan.parent_directory == tmp_path
        ),
        timeout=5_000,
    )
    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: window.hardware.validation is not None,
        timeout=5_000,
    )

    assert window.draft.valid
    assert window.channel_table.rowCount() == 4
    assert window.destination is not None and window.destination.valid
    assert not window.destination.plan.session_path.exists()
    assert window.presenter.services.recipe.recipe_id == "baseline"

    window.channel_table.selectRow(3)
    sensitivity = window.channel_editor_fields["sensitivity"]
    sensitivity.setText("0.02")
    sensitivity.editingFinished.emit()
    assert window.draft.raw_fields["calibrations"][3]["sensitivity_value"] == 0.02  # type: ignore[index]
    assert window.channel_editor_fields["converter_model"].text() == "SYNTHETIC-CONVERTER"
    assert window.channel_editor_fields["ni_bias_removed"].text() == "True"

    window.plan_selector.setCurrentText("throughput-v1")
    assert window.presenter.services.qualification_plan.plan_id == "throughput-v1"
    assert window.test_table.rowCount() == len(window.presenter.services.qualification_plan.tests)

    window.close()
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=5_000)  # type: ignore[attr-defined]


def test_window_shutdown_during_active_qualification_releases_workers(
    qtbot: object,
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    _, window = create_desktop_application(
        backend="simulated",
        argv=[],
        preferences=_preferences(tmp_path),
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    window.show()
    window.destination_edit.setText(str(tmp_path))
    window.destination_edit.editingFinished.emit()
    monkeypatch.setattr(  # type: ignore[attr-defined]
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    qtbot.mouseClick(window.qualification_start, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]
    qtbot.waitUntil(lambda: window.runner.busy("qualification"), timeout=3_000)  # type: ignore[attr-defined]
    window.close()

    qtbot.waitUntil(lambda: not window.runner.busy("qualification"), timeout=20_000)  # type: ignore[attr-defined]
    assert not window.isVisible()


def test_window_active_acquisition_close_requests_stop_and_waits_for_finalization(
    qtbot: object,
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    _, window = create_desktop_application(
        backend="simulated",
        argv=[],
        preferences=_preferences(tmp_path),
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    window.show()
    running = OperatorRunStatus(
        state=AcquisitionState.RUNNING,
        finalization_state=SessionCompletionState.RECORDING,
        session_id="session",
        session_path=tmp_path / "session",
        experiment_name="close-test",
        started_at=datetime.now(UTC),
        elapsed_seconds=1.0,
        requested_duration_seconds=10.0,
        actual_sample_rate_hz=50_000.0,
        ordered_channel_ids=("a",),
        ordered_channel_units=("V",),
        acquired_samples_per_channel=100,
        persisted_samples_per_channel=100,
        recording_queue_occupancy=0,
        recording_queue_capacity=2,
        display_queue_occupancy=0,
        display_queue_capacity=2,
        display_frames_produced=1,
        display_frames_dropped=0,
        acquisition_gap_count=0,
        persistence_loss_samples=None,
        warning_count=0,
    )
    stopped = threading.Event()
    finalized = threading.Event()
    monkeypatch.setattr(window.presenter, "acquisition_status", lambda: running)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        window.presenter,
        "stop_acquisition",
        lambda: stopped.set() or running,
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        window.presenter,
        "wait_for_acquisition",
        lambda timeout=None: finalized.set() or object(),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    window.close()

    qtbot.waitUntil(stopped.is_set, timeout=3_000)  # type: ignore[attr-defined]
    qtbot.waitUntil(finalized.is_set, timeout=3_000)  # type: ignore[attr-defined]
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=5_000)  # type: ignore[attr-defined]


def test_complete_gui_qualification_launch_intent_and_catalog_workflow(
    qtbot: object,
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    _, window = create_desktop_application(
        backend="simulated",
        argv=[],
        preferences=_preferences(tmp_path),
    )
    window.draft = window.presenter.initial_draft(template_id="four-channel-vibration-synthetic-v2")
    window._render_draft()
    window._submit_discovery()
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    window.show()
    window.acquisition_mode.setCurrentIndex(window.acquisition_mode.findData("qualified"))
    window.destination_edit.setText(str(tmp_path))
    window.destination_edit.editingFinished.emit()
    monkeypatch.setattr(  # type: ignore[attr-defined]
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    qtbot.mouseClick(window.qualification_start, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]
    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: window.qualification.report is not None,
        timeout=30_000,
    )
    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: window.readiness is not None and window.readiness.eligible,
        timeout=8_000,
    )

    assert "completed" in window.qualification_status.text()
    assert window.open_report_button.isEnabled()
    assert window.continue_button.isEnabled()
    existing = set(tmp_path.iterdir())
    with qtbot.waitSignal(window.acquisitionLaunchRequested, timeout=5_000) as emitted:  # type: ignore[attr-defined]
        qtbot.mouseClick(window.continue_button, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]
    assert emitted.args[0].qualification_report_id == window.qualification.report.report_id
    qtbot.waitUntil(lambda: window._current_summary is not None, timeout=15_000)  # type: ignore[attr-defined]
    qtbot.waitUntil(lambda: window._review is not None, timeout=15_000)  # type: ignore[attr-defined]
    created = set(tmp_path.iterdir()) - existing
    assert len(created) == 1
    assert window._current_summary.recorded.persisted_samples_per_channel == 500_000
    assert window._review.summary.session_id == window._current_summary.recorded.session_id
    assert window.acquisition_counts_label.text().startswith("acquired 500000 / persisted 500000")
    assert window.navigation.currentRow() == 3
    assert window.review_channels.count() == 4
    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: (
            not window.runner.busy("review-plot")
            and window.review_plot_metadata.text().startswith("raw")
            and "samples [0, 500000)" in window.review_plot_metadata.text()
        ),
        timeout=15_000,
    )
    assert window.review_representation.currentData() == PlotRepresentation.RAW_TIME.value
    assert window.review_start_seconds.text() == ""
    assert window.review_stop_seconds.text() == ""
    assert len(window.review_plot._latest) == 4

    window.review_start_seconds.setText("0")
    window.review_stop_seconds.setText("0.01")
    qtbot.mouseClick(window.review_plot_button, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]
    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: (
            not window.runner.busy("review-plot")
            and "samples [0, 500)" in window.review_plot_metadata.text()
        ),
        timeout=10_000,
    )
    assert window.review_plot.coordinate_label.text().startswith("Move over")

    window.review_stop_seconds.setText("0.1")
    for index, representation in enumerate(("raw", "calibrated", "filtered", "fft", "psd")):
        window.review_representation.setCurrentIndex(index)
        qtbot.mouseClick(window.review_plot_button, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]
        qtbot.waitUntil(  # type: ignore[attr-defined]
            lambda expected=representation: (
                not window.runner.busy("review-plot")
                and window.review_plot_metadata.text().startswith(expected)
            ),
            timeout=15_000,
        )

    window.review_representation.setCurrentIndex(0)
    window.review_start_seconds.setText("0")
    window.review_stop_seconds.setText("0.01")
    qtbot.mouseClick(window.review_analyze_button, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]
    window.review_layout.minimize("analysis")
    window.review_layout.toggle_maximize("plot")
    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: (
            not any(
                window.runner.busy(key)
                for key in ("review-extrema", "review-time", "review-fft", "review-psd")
            )
        ),
        timeout=20_000,
    )
    assert window.review_extrema_table.rowCount() == 4
    assert len(window.review_fft_plot._latest) == 4
    assert len(window.review_psd_plot._latest) == 4

    assert not window.review_layout.panels["analysis"].isVisible()
    window.review_layout.restore("analysis")
    assert window.review_layout.maximized is None
    assert window.review_layout.panels["analysis"].isVisible()
    window.review_analysis_tabs.setCurrentIndex(1)
    plot = window.review_plot
    plot.set_selection_range(0.002, 0.008)
    canvas = next(iter(plot._plots.values()))
    canvas.setXRange(0.003, 0.007, padding=0)
    selected_channels = tuple(
        window.review_channels.item(index).checkState()
        for index in range(window.review_channels.count())
    )
    latest = plot._latest
    window.review_layout.minimize("plot")
    window.review_layout.restore("plot")
    window.review_layout.toggle_maximize("plot")
    window.review_layout.restore_maximized()
    assert plot._latest is latest
    assert next(iter(plot._plots.values())) is canvas
    assert plot.selection_range == (0.002, 0.008)
    assert canvas.viewRange()[0] == [0.003, 0.007]
    assert window.review_analysis_tabs.currentIndex() == 1
    assert selected_channels == tuple(
        window.review_channels.item(index).checkState()
        for index in range(window.review_channels.count())
    )
    window.review_analysis_tabs.setCurrentIndex(0)

    qtbot.mouseClick(window.review_detect_button, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]
    qtbot.waitUntil(lambda: not window.runner.busy("review-detect"), timeout=20_000)  # type: ignore[attr-defined]
    assert window.review_detection_status.text().startswith("Detected")

    qtbot.mouseClick(window.export_csv_button, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]
    qtbot.waitUntil(lambda: not window.runner.busy("export"), timeout=15_000)  # type: ignore[attr-defined]
    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: window._review is not None and len(window._review.export_history) == 1,
        timeout=10_000,
    )
    assert "completed" in window.review_artifacts.toPlainText()
    for button, expected_count in (
        (window.export_png_button, 2),
        (window.export_svg_button, 3),
    ):
        qtbot.waitUntil(lambda current=button: current.isEnabled(), timeout=5_000)  # type: ignore[attr-defined]
        qtbot.mouseClick(button, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]
        qtbot.waitUntil(lambda: not window.runner.busy("export"), timeout=15_000)  # type: ignore[attr-defined]
        qtbot.waitUntil(  # type: ignore[attr-defined]
            lambda count=expected_count: (
                window._review is not None and len(window._review.export_history) == count
            ),
            timeout=10_000,
        )

    faulted = replace(
        window._current_summary,
        recorded=replace(
            window._current_summary.recorded,
            fault=SessionFaultSummary(
                "DAQ.STORAGE.TEST_FAILURE",
                "Injected finalized fault presentation.",
            ),
        ),
    )
    monkeypatch.setattr(QMessageBox, "exec", lambda *args, **kwargs: 0)  # type: ignore[attr-defined]
    window._show_run_fault(faulted)

    corrupt = tmp_path / "2026-08-13_120000_corrupt_12345678"
    corrupt.mkdir()
    (corrupt / "manifest.json").write_text("{broken", encoding="utf-8")
    window.browser_root.setText(str(tmp_path))
    window._scan_sessions()
    qtbot.waitUntil(lambda: window.session_table.rowCount() == 3, timeout=8_000)  # type: ignore[attr-defined]
    valid_row = next(index for index, entry in enumerate(window._catalog_entries) if entry.valid)
    invalid_row = next(
        index for index, entry in enumerate(window._catalog_entries) if not entry.valid
    )
    window.session_table.selectRow(valid_row)
    assert "Hardware:" in window.session_details.toPlainText()
    previous_review = window._review
    window.review_layout.minimize("session_details")
    window.review_layout.toggle_maximize("analysis")
    window._review_selected_session()
    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: not window.runner.busy("review") and window._review is not previous_review,
        timeout=15_000,
    )
    assert window.review_layout.minimized == {"session_details"}
    assert window.review_layout.maximized == "analysis"
    window.review_layout.reset()
    window.session_table.selectRow(invalid_row)
    assert "Invalid catalog entry" in window.session_details.toPlainText()

    valid_entry = next(entry for entry in window._catalog_entries if entry.valid)
    assert valid_entry.summary is not None
    state_entries = tuple(
        replace(
            valid_entry,
            session_path=tmp_path / state.value,
            summary=replace(valid_entry.summary, completion_state=state),
        )
        for state in (
            SessionCompletionState.COMPLETED,
            SessionCompletionState.STOPPED,
            SessionCompletionState.INTERRUPTED,
            SessionCompletionState.FAULTED,
        )
    )
    invalid_entries = tuple(
        SessionCatalogEntry(
            tmp_path / name,
            False,
            None,
            code,
            message,
            "Preserve and inspect the candidate.",
        )
        for name, code, message in (
            ("missing", "DAQ.STORAGE.SESSION_FILE_MISSING", "A required file is missing."),
            ("corrupt", "DAQ.STORAGE.SESSION_CORRUPT", "The manifest is corrupt."),
            (
                "unsupported",
                "DAQ.CONFIG.UNSUPPORTED_SCHEMA_VERSION",
                "The schema is unsupported.",
            ),
        )
    )
    window._catalog_ready(
        SessionCatalog(tmp_path, (*state_entries, *invalid_entries), datetime.now(UTC))
    )
    assert window.session_table.rowCount() == 7
    assert [window.session_table.item(row, 2).text() for row in range(4)] == [
        "completed",
        "stopped",
        "interrupted",
        "faulted",
    ]

    window._handle_failure(
        LabDAQError(
            "Expected GUI operation failure.",
            code="DAQ.GUI.EXPECTED_FAILURE",
            remediation="Continue the test.",
        )
    )
    assert "DAQ.GUI.EXPECTED_FAILURE" in window.statusBar().currentMessage()
    window._new_destination_identity()
    window.close()
