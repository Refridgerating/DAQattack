"""QSettings-backed user preferences, imported only by desktop composition."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from PySide6.QtCore import QByteArray, QSettings

from labdaq.errors import LabDAQError
from labdaq.ports.preferences import UserPreferences


class QSettingsPreferenceAdapter:
    def __init__(self, settings: QSettings | None = None) -> None:
        self._settings = settings or QSettings("LabDAQ", "LabDAQ")

    def load(self) -> UserPreferences:
        version = int(str(self._settings.value("preferences/schema_version", 1)))
        if version != 1:
            return UserPreferences()
        geometry_value = self._settings.value("window/geometry")
        geometry: bytes | None
        if isinstance(geometry_value, QByteArray):
            geometry = cast("bytes", geometry_value.data())
        elif isinstance(geometry_value, bytes):
            geometry = geometry_value
        else:
            geometry = None
        return UserPreferences(
            window_geometry=geometry,
            recent_session_parents=_paths(self._settings.value("recent/session_parents", [])),
            recent_browser_roots=_paths(self._settings.value("recent/browser_roots", [])),
            production_profile_path=_optional_path(
                self._settings.value("validated/production/profile")
            ),
            production_configuration_path=_optional_path(
                self._settings.value("validated/production/configuration")
            ),
            production_qualification_plan_path=_optional_path(
                self._settings.value("validated/production/qualification_plan")
            ),
            commissioning_profile_path=_optional_path(
                self._settings.value("validated/commissioning/profile")
            ),
            commissioning_configuration_path=_optional_path(
                self._settings.value("validated/commissioning/configuration")
            ),
            commissioning_qualification_plan_path=_optional_path(
                self._settings.value("validated/commissioning/qualification_plan")
            ),
        )

    def save(self, preferences: UserPreferences) -> None:
        self._settings.setValue("preferences/schema_version", preferences.schema_version)
        if preferences.window_geometry is not None:
            self._settings.setValue("window/geometry", QByteArray(preferences.window_geometry))
        self._settings.setValue(
            "recent/session_parents",
            [str(path) for path in preferences.recent_session_parents],
        )
        self._settings.setValue(
            "recent/browser_roots",
            [str(path) for path in preferences.recent_browser_roots],
        )
        for mode in ("production", "commissioning"):
            for name in ("profile", "configuration", "qualification_plan"):
                path = getattr(preferences, f"{mode}_{name}_path")
                key = f"validated/{mode}/{name}"
                if path is not None:
                    self._settings.setValue(key, str(path))
                else:
                    self._settings.remove(key)

    def sync(self) -> None:
        self._settings.sync()
        if self._settings.status() is not QSettings.Status.NoError:
            raise LabDAQError(
                "Desktop preferences could not be saved.",
                code="DAQ.GUI.PREFERENCE_WRITE_FAILED",
                context={"settings_status": self._settings.status().name},
                remediation="Check the current user's application-settings permissions.",
            )


def _paths(value: object) -> tuple[Path, ...]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = [str(item) for item in value]
    else:
        values = []
    return tuple(Path(item) for item in values[:8] if item)


def _optional_path(value: object) -> Path | None:
    if isinstance(value, str) and value.strip():
        return Path(value)
    return None
