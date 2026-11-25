"""Feedback-preview acknowledgement and local feedback dialogs."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from labdaq.build_info import get_build_info
from labdaq.errors import LabDAQError
from labdaq.feedback import FEEDBACK_CATEGORIES, FeedbackForm, create_feedback_bundle
from labdaq.version import __version__


class PreviewAcknowledgementDialog(QDialog):
    """Require explicit acknowledgement before any DAQ backend is composed."""

    def __init__(self, backend: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if backend not in {"simulated", "ni"}:
            raise ValueError(f"unsupported backend: {backend}")
        self.setWindowTitle("LabDAQ feedback preview — acknowledgement required")
        self.setModal(True)
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)

        heading = QLabel(f"LabDAQ {__version__} — UNQUALIFIED FEEDBACK PREVIEW")
        heading.setStyleSheet("font-size: 16px; font-weight: 700")
        layout.addWidget(heading)
        mode = "NI HARDWARE" if backend == "ni" else "SIMULATED HARDWARE"
        mode_label = QLabel(f"Selected launch mode: {mode}")
        mode_label.setStyleSheet("font-weight: 600")
        layout.addWidget(mode_label)

        warning = QLabel(_acknowledgement_text(backend))
        warning.setWordWrap(True)
        warning.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(warning)

        self.confirmation = QCheckBox(
            "I understand these limitations and will use this build only for supervised "
            "internal evaluation and feedback."
        )
        self.confirmation.setObjectName("previewAcknowledgement")
        layout.addWidget(self.confirmation)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        continue_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        continue_button.setText("Continue to LabDAQ")
        continue_button.setEnabled(False)
        self.confirmation.toggled.connect(continue_button.setEnabled)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)


class FeedbackBundleDialog(QDialog):
    """Collect explicit tester comments before writing a local ZIP archive."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.bundle_path: Path | None = None
        self.setWindowTitle("Create LabDAQ feedback bundle")
        self.setModal(True)
        self.resize(700, 650)
        layout = QVBoxLayout(self)
        notice = QLabel(
            "The bundle is created locally and is never transmitted automatically. "
            "Automatic diagnostics exclude sessions, raw samples, usernames, and absolute "
            "user paths."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)

        form = QFormLayout()
        self.category = QComboBox()
        self.category.addItems(FEEDBACK_CATEGORIES)
        self.summary = QLineEdit()
        self.summary.setPlaceholderText("Short description")
        self.steps = QTextEdit()
        self.expected = QTextEdit()
        self.actual = QTextEdit()
        self.impact = QTextEdit()
        for editor in (self.steps, self.expected, self.actual, self.impact):
            editor.setMaximumHeight(90)
        form.addRow("Category", self.category)
        form.addRow("Summary", self.summary)
        form.addRow("Steps to reproduce", self.steps)
        form.addRow("Expected result", self.expected)
        form.addRow("Actual result", self.actual)
        form.addRow("Impact", self.impact)
        layout.addLayout(form)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("Create bundle…")
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _save(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose feedback bundle destination",
            str(Path.home() / "Documents"),
        )
        if not selected:
            return
        try:
            bundle = create_feedback_bundle(
                Path(selected),
                form=FeedbackForm(
                    category=self.category.currentText(),
                    summary=self.summary.text().strip(),
                    steps_to_reproduce=self.steps.toPlainText().strip(),
                    expected_result=self.expected.toPlainText().strip(),
                    actual_result=self.actual.toPlainText().strip(),
                    impact=self.impact.toPlainText().strip(),
                ),
            )
        except (LabDAQError, ValueError) as error:
            QMessageBox.critical(self, "Feedback bundle failed", str(error))
            return
        self.bundle_path = bundle
        QMessageBox.information(
            self,
            "Feedback bundle created",
            f"Created:\n{bundle}\n\nReturn it through the lab's approved internal channel.",
        )
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(bundle.parent)))
        self.accept()


def show_preview_acknowledgement(backend: str, parent: QWidget | None = None) -> bool:
    """Return true only after the tester explicitly accepts the preview limits."""
    return PreviewAcknowledgementDialog(backend, parent).exec() == QDialog.DialogCode.Accepted


def show_feedback_bundle_dialog(parent: QWidget | None = None) -> Path | None:
    dialog = FeedbackBundleDialog(parent)
    dialog.exec()
    return dialog.bundle_path


def preview_title(backend: str) -> str:
    build = get_build_info()
    return f"LabDAQ {__version__} — {build.release_channel.upper()} — {backend.upper()}"


def _acknowledgement_text(backend: str) -> str:
    common = (
        "• This software and its packaged Windows build are not fully qualified.\n"
        "• Simulator results do not qualify physical hardware, wiring, calibration, or "
        "external conditioning.\n"
        "• Do not use this preview for safety-critical, unattended, or production work.\n"
        "• Preserve partial sessions and report any fault, count mismatch, or suspected "
        "sample loss.\n"
    )
    if backend == "ni":
        return common + (
            "• NI use must be supervised. Independently verify wiring, sensor power, input "
            "range, calibration provenance, and charge-conditioner compatibility before "
            "starting acquisition.\n"
            "• The NI-DAQmx Windows driver is an external prerequisite."
        )
    return common + "• This launch uses the deterministic simulator by default."
