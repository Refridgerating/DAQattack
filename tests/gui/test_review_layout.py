from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from PySide6.QtCore import QSettings, Qt

from labdaq.adapters.qt_preferences import QSettingsPreferenceAdapter
from labdaq.bootstrap import create_desktop_application

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from pytestqt.qtbot import QtBot

    from labdaq.gui.main_window import LabDAQMainWindow


PANEL_KEYS = (
    "sessions",
    "session_details",
    "channels",
    "plot",
    "analysis",
    "events",
    "detected_events",
    "exports",
)


@pytest.fixture
def window(qtbot: QtBot, tmp_path: Path) -> Iterator[LabDAQMainWindow]:
    preferences = QSettingsPreferenceAdapter(
        QSettings(str(tmp_path / "layout.ini"), QSettings.Format.IniFormat)
    )
    _, window = create_desktop_application(backend="simulated", argv=[], preferences=preferences)
    qtbot.addWidget(window)
    window.resize(1760, 1100)
    window.show()
    window.navigation.setCurrentRow(3)
    qtbot.waitExposed(window)
    yield window
    window.close()


@pytest.mark.parametrize("key", PANEL_KEYS)
def test_each_panel_minimizes_maximizes_and_restores(
    window: LabDAQMainWindow,
    qtbot: QtBot,
    key: str,
) -> None:
    controller = window.review_layout
    panel = controller.panels[key]
    assert panel.isVisible()
    assert panel.minimize_button.accessibleName().startswith("Minimize ")
    parent = panel.parentWidget()
    qtbot.mouseClick(panel.minimize_button, Qt.MouseButton.LeftButton)
    assert not panel.isVisible()
    assert controller.restore_buttons[key].isVisible()
    qtbot.mouseClick(controller.restore_buttons[key], Qt.MouseButton.LeftButton)
    assert panel.isVisible()
    qtbot.mouseClick(panel.maximize_button, Qt.MouseButton.LeftButton)
    assert [key for key, value in controller.panels.items() if value.isVisible()] == [key]
    qtbot.waitUntil(lambda: panel.width() >= controller.root.width() - 15)
    qtbot.waitUntil(lambda: panel.height() >= controller.root.height() - 15)
    assert window.review_analyze_button.isVisible()
    assert window.review_detect_button.isVisible()
    assert window.export_csv_button.isVisible()
    assert window.export_progress.isVisible()
    assert controller.reset_button.isVisible()
    assert panel.maximize_button.accessibleName().startswith("Restore ")
    qtbot.mouseClick(panel.maximize_button, Qt.MouseButton.LeftButton)
    assert all(panel.isVisible() for panel in controller.panels.values())
    assert panel.parentWidget() is parent


def test_nested_visibility_and_all_minimized_can_be_reset(
    window: LabDAQMainWindow,
    qtbot: QtBot,
) -> None:
    controller = window.review_layout
    for key in PANEL_KEYS:
        controller.minimize(key)
    assert all(not panel.isVisible() for panel in controller.panels.values())
    assert not window.session_browser_splitter.isVisible()
    assert not window.review_analysis_splitter.isVisible()
    assert not window.review_details_splitter.isVisible()
    assert all(button.isVisible() for button in controller.restore_buttons.values())
    controller.restore_scroll.setFixedWidth(260)
    qtbot.waitUntil(lambda: controller.restore_scroll.horizontalScrollBar().maximum() > 0)
    controller.restore_scroll.ensureWidgetVisible(controller.restore_buttons["exports"])
    qtbot.mouseClick(controller.restore_buttons["exports"], Qt.MouseButton.LeftButton)
    assert controller.panels["exports"].isVisible()
    assert not window.session_browser_splitter.isVisible()
    qtbot.mouseClick(controller.reset_button, Qt.MouseButton.LeftButton)
    assert all(panel.isVisible() for panel in controller.panels.values())
    assert not controller.minimized
    assert controller.maximized is None
    assert all(not button.isVisible() for button in controller.restore_buttons.values())
    browser, plot, details = controller.root.sizes()
    assert plot > browser and plot > details


def test_maximized_transitions_preserve_minimized_panels_and_escape(
    window: LabDAQMainWindow,
    qtbot: QtBot,
) -> None:
    controller = window.review_layout
    controller.minimize("sessions")
    controller.toggle_maximize("analysis")
    qtbot.keyClick(controller.panels["analysis"].maximize_button, Qt.Key.Key_Escape)
    assert controller.maximized is None
    assert controller.minimized == {"sessions"}
    assert controller.panels["plot"].isVisible()
    controller.toggle_maximize("plot")
    controller.minimize("plot")
    assert controller.maximized is None
    assert controller.minimized == {"sessions", "plot"}
    assert controller.panels["analysis"].isVisible()
    controller.toggle_maximize("analysis")
    controller.restore("sessions")
    assert controller.maximized is None
    assert controller.minimized == {"plot"}
    assert controller.panels["sessions"].isVisible()


def test_restore_keeps_user_proportions_after_resize_and_navigation(
    window: LabDAQMainWindow,
    qtbot: QtBot,
) -> None:
    controller = window.review_layout
    splitter = window.session_browser_splitter
    splitter.setSizes([1000, 700])
    before = splitter.sizes()
    ratio = before[0] / sum(before)
    controller.minimize("events")
    controller.toggle_maximize("analysis")
    window.resize(1950, 1200)
    window.navigation.setCurrentRow(2)
    window.navigation.setCurrentRow(3)
    assert controller.maximized == "analysis"
    assert controller.minimized == {"events"}
    controller.restore_maximized()
    qtbot.wait(20)
    after = splitter.sizes()
    assert after[0] / sum(after) == pytest.approx(ratio, abs=0.01)
    controller.minimize("sessions")
    controller.restore("sessions")
    after = splitter.sizes()
    assert after[0] / sum(after) == pytest.approx(ratio, abs=0.01)


def test_layout_does_not_persist_to_preferences(
    window: LabDAQMainWindow,
    qtbot: QtBot,
) -> None:
    preferences = window.presenter.services.preferences
    window.review_layout.minimize("plot")
    window.review_layout.toggle_maximize("analysis")
    window.close()
    _, reopened = create_desktop_application(backend="simulated", argv=[], preferences=preferences)
    qtbot.addWidget(reopened)
    reopened.show()
    reopened.navigation.setCurrentRow(3)
    assert not reopened.review_layout.minimized
    assert reopened.review_layout.maximized is None
    assert all(panel.isVisible() for panel in reopened.review_layout.panels.values())
    reopened.close()
