from PySide6.QtWidgets import QDialogButtonBox

from labdaq.gui.preview import PreviewAcknowledgementDialog, _acknowledgement_text


def test_preview_acknowledgement_requires_checkbox(qtbot: object) -> None:
    dialog = PreviewAcknowledgementDialog("simulated")
    qtbot.addWidget(dialog)  # type: ignore[attr-defined]
    continue_button = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)

    assert not continue_button.isEnabled()
    dialog.confirmation.setChecked(True)
    assert continue_button.isEnabled()


def test_ni_preview_warning_is_mode_specific() -> None:
    ni_text = _acknowledgement_text("ni")
    simulated_text = _acknowledgement_text("simulated")

    assert "NI use must be supervised" in ni_text
    assert "NI-DAQmx Windows driver" in ni_text
    assert "deterministic simulator" in simulated_text
    assert "NI use must be supervised" not in simulated_text
