"""PySide6 widgets for the DAQ-M10 desktop workflow."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING, cast

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from labdaq.application.configuration_workspace import FieldIssue, field_value
from labdaq.application.operator_messages import (
    OperatorMessage,
    operator_message_from_error,
    unexpected_operator_message,
)
from labdaq.application.readiness_service import RawDiagnosticConfirmation
from labdaq.domain.enums import SignalStage
from labdaq.domain.plotting import ExportFormat, ExportProduct, PlotRepresentation
from labdaq.domain.serialization import canonical_sha256
from labdaq.errors import DependencyUnavailableError, ErrorSeverity, LabDAQError
from labdaq.gui.background import KeyedBackgroundRunner
from labdaq.gui.field_help import (
    bind_field_help,
    bind_table_help,
    set_described_table_item,
    set_field_notice,
)
from labdaq.gui.presentation import (
    DesktopPresenter,
    HardwarePresentation,
    QualificationPresentation,
)
from labdaq.gui.preview import preview_title, show_feedback_bundle_dialog
from labdaq.gui.review_layout import ReviewLayoutController, ReviewPanel
from labdaq.gui.signal_plot import SignalPlotPanel
from labdaq.gui.tiles import style_splitter_tiles
from labdaq.ports.processing import TransientDetectionSettings

if TYPE_CHECKING:
    from labdaq.application.configuration_workspace import ConfigurationDraftSnapshot
    from labdaq.application.destination_service import SessionDestinationPreview
    from labdaq.application.readiness_service import (
        DiagnosticReadinessDecision,
        FunctionalPreviewLaunchIntent,
        OpenAcquisitionIntent,
        RawDiagnosticLaunchIntent,
        ReadinessDecision,
    )
    from labdaq.application.session_catalog_service import SessionCatalog, SessionCatalogEntry
    from labdaq.domain.plotting import ExportManifest, ExportProgress, SampleRange
    from labdaq.ports.acquisition import AcquisitionFaultSummary
    from labdaq.ports.operator import (
        LiveMetricSnapshot,
        LivePlotFrame,
        OpenAcquisitionStatus,
        OperatorRunStatus,
        OperatorRunSummary,
        SessionReviewSnapshot,
    )
    from labdaq.ports.plotting import PlotBundle
    from labdaq.ports.processing import (
        ChannelExtremaResult,
        DetectedTransientEvent,
        MetricValue,
        TransientDetectionResult,
    )
    from labdaq.ports.qualification import QualificationProgress
    from labdaq.ports.session import RecordedSessionCompletion, SessionFaultSummary

_LOG = logging.getLogger(__name__)


class _ProgressRelay(QObject):
    received = Signal(object)

    def publish(self, progress: QualificationProgress) -> None:
        self.received.emit(progress)


class _ObjectRelay(QObject):
    received = Signal(object)

    def publish(self, value: object) -> None:
        self.received.emit(value)


class RawDiagnosticAuthorizationDialog(QDialog):
    """Collect explicit physical confirmations without granting qualification."""

    def __init__(self, operator: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._operator = operator
        self.confirmation: RawDiagnosticConfirmation | None = None
        self.setWindowTitle("Authorize raw-voltage diagnostic")
        self.setModal(True)
        layout = QVBoxLayout(self)
        warning = QLabel(
            "This records immutable raw volts only. It does not authorize calibrated "
            "acceleration, charge interpretation, or production use."
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)
        form = QFormLayout()
        self.supply_voltage = QDoubleSpinBox()
        self.supply_voltage.setObjectName("diagnosticSupplyVoltage")
        self.supply_voltage.setRange(0.0, 10.0)
        self.supply_voltage.setDecimals(3)
        self.supply_voltage.setValue(5.0)
        self.supply_polarity = QCheckBox("Polarity was measured and confirmed")
        self.supply_polarity.setObjectName("diagnosticSupplyPolarity")
        form.addRow("Measured board supply (V)", self.supply_voltage)
        form.addRow("Supply check", self.supply_polarity)
        layout.addLayout(form)

        self.ai3_disconnected = QRadioButton("ai3 is disconnected")
        self.ai3_disconnected.setObjectName("diagnosticAi3Disconnected")
        self.ai3_connected = QRadioButton(
            "ai3 is connected after an independent high-impedance measurement"
        )
        self.ai3_connected.setObjectName("diagnosticAi3Connected")
        self.ai3_disconnected.setChecked(True)
        layout.addWidget(self.ai3_disconnected)
        layout.addWidget(self.ai3_connected)
        bounds = QFormLayout()
        self.ai3_minimum = QDoubleSpinBox()
        self.ai3_minimum.setObjectName("diagnosticAi3Minimum")
        self.ai3_minimum.setRange(-20.0, 20.0)
        self.ai3_minimum.setDecimals(4)
        self.ai3_maximum = QDoubleSpinBox()
        self.ai3_maximum.setObjectName("diagnosticAi3Maximum")
        self.ai3_maximum.setRange(-20.0, 20.0)
        self.ai3_maximum.setDecimals(4)
        self.ai3_instrument = QCheckBox("High-impedance scope or meter was used")
        self.ai3_instrument.setObjectName("diagnosticAi3Instrument")
        bounds.addRow("Observed ai3 minimum (V)", self.ai3_minimum)
        bounds.addRow("Observed ai3 maximum (V)", self.ai3_maximum)
        bounds.addRow("Instrument check", self.ai3_instrument)
        layout.addLayout(bounds)
        self.ai3_connected.toggled.connect(self._update_ai3_controls)
        self._update_ai3_controls(False)

        for widget, help_id in (
            (self.supply_voltage, "diagnostic.supply_voltage"),
            (self.supply_polarity, "diagnostic.supply_polarity"),
            (self.ai3_disconnected, "diagnostic.ai3_disconnected"),
            (self.ai3_connected, "diagnostic.ai3_connected"),
            (self.ai3_minimum, "diagnostic.ai3_minimum"),
            (self.ai3_maximum, "diagnostic.ai3_maximum"),
            (self.ai3_instrument, "diagnostic.ai3_instrument"),
        ):
            bind_field_help(widget, help_id)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_confirmation)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_ai3_controls(self, connected: bool) -> None:
        for control in (self.ai3_minimum, self.ai3_maximum, self.ai3_instrument):
            control.setEnabled(connected)

    def _accept_confirmation(self) -> None:
        connected = self.ai3_connected.isChecked()
        try:
            confirmation = RawDiagnosticConfirmation(
                operator=self._operator,
                confirmed_at=datetime.now(UTC),
                measured_supply_voltage_v=self.supply_voltage.value(),
                polarity_confirmed=self.supply_polarity.isChecked(),
                ai3_connection="connected-verified" if connected else "disconnected",
                ai3_observed_min_v=self.ai3_minimum.value() if connected else None,
                ai3_observed_max_v=self.ai3_maximum.value() if connected else None,
                high_impedance_instrument_confirmed=(
                    self.ai3_instrument.isChecked() if connected else False
                ),
            )
        except ValueError as error:
            QMessageBox.warning(self, "Diagnostic authorization incomplete", str(error))
            return
        self.confirmation = confirmation
        self.accept()


class LabDAQMainWindow(QMainWindow):
    """Presentation-only shell over M10 application services."""

    acquisitionLaunchRequested = Signal(object)

    def __init__(
        self,
        presenter: DesktopPresenter,
        *,
        template_id: str,
        configuration_path: Path | None = None,
        profile_path: Path | None = None,
        qualification_plan: str | Path = "preflight-v1",
    ) -> None:
        super().__init__()
        self.presenter = presenter
        self.functional_preview = presenter.services.functional_preview
        self._launch_profile_path = profile_path
        self._launch_configuration_path = configuration_path
        self._launch_qualification_plan_path = (
            qualification_plan if isinstance(qualification_plan, Path) else None
        )
        self.runner = KeyedBackgroundRunner(self)
        self.runner.busyChanged.connect(self._background_busy_changed)
        self.progress_relay = _ProgressRelay(self)
        self.progress_relay.received.connect(self._render_progress)
        self.export_progress_relay = _ObjectRelay(self)
        self.export_progress_relay.received.connect(self._render_export_progress)
        self.preferences = presenter.services.preferences.load()
        self.draft = presenter.initial_draft(
            template_id=template_id,
            configuration_path=configuration_path,
        )
        self.destination: SessionDestinationPreview | None = None
        self.hardware = HardwarePresentation(unavailable=presenter.services.startup_message)
        self.qualification = QualificationPresentation()
        self.readiness: ReadinessDecision | None = None
        self.diagnostic_readiness: DiagnosticReadinessDecision | None = None
        self.open_readiness: DiagnosticReadinessDecision | None = None
        self._active_raw_diagnostic = False
        self._current_status: OperatorRunStatus | None = None
        self._current_summary: OperatorRunSummary | None = None
        self._open_status: OpenAcquisitionStatus | None = None
        self._open_record_wait_submitted = False
        self._review: SessionReviewSnapshot | None = None
        self._review_path: Path | None = None
        self._transient_result: TransientDetectionResult | None = None
        self._export_cancel = Event()
        self._review_detection_cancel = Event()
        self._review_analysis_cancel = Event()
        self._terminal_wait_submitted = False
        self._close_after_finalization = False
        self._force_close = False
        self._closing = False
        self._hardware_refresh_pending = False
        self._hardware_refresh_timer = QTimer(self)
        self._hardware_refresh_timer.setSingleShot(True)
        self._hardware_refresh_timer.setInterval(300)
        self._hardware_refresh_timer.timeout.connect(self._run_pending_hardware_refresh)
        self._build_shell()
        self.setMinimumHeight(776)
        self._render_draft()
        self._restore_preferences()
        if presenter.services.startup_message is not None:
            self._show_message(presenter.services.startup_message)
        for message in presenter.services.capability_messages:
            self._show_message(message)
        self.live_timer = QTimer(self)
        self.live_timer.setInterval(100)
        self.live_timer.timeout.connect(self._poll_live)
        self.live_timer.start()
        self._submit_destination_check()
        self._submit_discovery()

    def _build_shell(self) -> None:
        self.setWindowTitle(preview_title(self.presenter.services.mode))
        self.resize(1180, 760)

        title = QLabel(preview_title(self.presenter.services.mode))
        title.setObjectName("applicationTitle")
        title.setStyleSheet("font-size: 18px; font-weight: 600; padding: 6px")
        self.global_status = QLabel("Checking readiness…")
        self.global_status.setAlignment(Qt.AlignmentFlag.AlignRight)

        self.navigation_toggle = QToolButton()
        self.navigation_toggle.setText("Hide navigation")
        self.navigation_toggle.setToolTip("Hide or show the page navigation (Ctrl+Shift+N)")
        self.navigation_toggle.setShortcut("Ctrl+Shift+N")
        self.navigation_toggle.clicked.connect(self._toggle_navigation)

        self.navigation = QListWidget()
        self.navigation.addItems(
            ["Experiment Setup", "Hardware Tests", "Acquisition", "Sessions / Review"]
        )
        if self.functional_preview:
            self.navigation.setRowHidden(1, True)
        self.navigation.setMinimumWidth(140)
        self.navigation.setMaximumWidth(360)
        self.navigation.setCurrentRow(0)
        self._navigation_width = 175
        self.pages = QStackedWidget()
        self.pages.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.pages.addWidget(self._build_setup_page())
        self.pages.addWidget(self._build_hardware_page())
        self.pages.addWidget(self._build_acquisition_page())
        self.pages.addWidget(self._build_sessions_page())
        self._bind_operator_field_help()
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)

        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.global_status)
        header.addWidget(self.navigation_toggle)
        self.shell_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.shell_splitter.setObjectName("shellWorkspaceSplitter")
        self.shell_splitter.addWidget(self.navigation)
        self.shell_splitter.addWidget(self.pages)
        _configure_splitter(self.shell_splitter, sizes=(175, 1005), stretch=(0, 1))
        self.shell_splitter.setCollapsible(0, True)
        root_layout = QVBoxLayout()
        root_layout.addLayout(header)
        root_layout.addWidget(self.shell_splitter, 1)
        root = QWidget()
        root.setLayout(root_layout)
        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar())

        quit_action = QAction("Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        self.addAction(quit_action)

        help_menu = self.menuBar().addMenu("Help")
        feedback_action = QAction("Create feedback bundle…", self)
        feedback_action.triggered.connect(lambda: show_feedback_bundle_dialog(self))
        help_menu.addAction(feedback_action)

    def _bind_operator_field_help(self) -> None:
        """Attach descriptive help to every data-bearing operator widget."""
        bindings: tuple[tuple[QWidget, str], ...] = (
            (self.global_status, "shell.readiness_status"),
            (self.profile_selector, "setup.profile"),
            (self.profile_label, "setup.profile_identity"),
            (self.discovery_label, "setup.discovery"),
            (self.sample_rate_selector, "acquisition.sample_rate_hz"),
            (self.read_interval_label, "acquisition.read_interval"),
            (self.destination_edit, "storage.parent_directory"),
            (self.destination_preview_label, "storage.destination_preview"),
            (self.recipe_box, "processing.active_recipe"),
            (self.readiness_list, "readiness.qualified"),
            (self.diagnostic_readiness_list, "readiness.diagnostic"),
            (self.plan_selector, "qualification.plan"),
            (self.plan_revision_label, "qualification.revision"),
            (self.qualification_progress, "qualification.progress"),
            (self.qualification_status, "qualification.status"),
            (self.report_viewer, "qualification.report"),
            (self.acquisition_mode, "acquisition.mode"),
            (self.open_channel, "acquisition.monitor_channels"),
            (self.pause_display, "acquisition.pause_display"),
            (self.live_window, "acquisition.live_window"),
            (self.acquisition_state_label, "acquisition.state"),
            (self.acquisition_elapsed_label, "acquisition.elapsed"),
            (self.acquisition_counts_label, "acquisition.counts"),
            (self.acquisition_warning_label, "acquisition.warnings"),
            (self.acquisition_identity_label, "acquisition.identity"),
            (self.recording_queue, "acquisition.recording_queue"),
            (self.display_queue, "acquisition.display_queue"),
            (self.raw_live_plot, "plot.panel"),
            (self.conditioned_live_plot, "plot.panel"),
            (self.browser_root, "sessions.root"),
            (self.session_details, "sessions.details"),
            (self.review_signal_stage, "review.stage"),
            (self.review_representation, "review.representation"),
            (self.review_start_seconds, "review.start"),
            (self.review_stop_seconds, "review.stop"),
            (self.review_mad_multiplier, "review.mad_multiplier"),
            (self.review_pre_padding, "review.pre_padding"),
            (self.review_post_padding, "review.post_padding"),
            (self.review_merge_gap, "review.merge_gap"),
            (self.review_detection_status, "review.detection_status"),
            (self.review_channels, "review.channels"),
            (self.review_plot, "plot.panel"),
            (self.review_fft_plot, "plot.panel"),
            (self.review_psd_plot, "plot.panel"),
            (self.review_plot_metadata, "review.plot_metadata"),
            (self.review_events, "review.events"),
            (self.review_artifacts, "review.artifacts"),
            (self.export_progress, "review.export_progress"),
        )
        for widget, help_id in bindings:
            bind_field_help(widget, help_id)
        for path, editor in self.field_editors.items():
            bind_field_help(editor, path)
        for key, editor in self.channel_editor_fields.items():
            bind_field_help(editor, f"channel.{key}")

        bind_table_help(
            self.channel_table,
            "channel.table",
            (
                "channel.column.order",
                "channel.column.enabled",
                "channel.column.logical_id",
                "channel.column.role",
                "channel.column.physical",
                "channel.column.sensor",
                "channel.column.calibration",
            ),
        )
        bind_table_help(
            self.test_table,
            "qualification.table",
            (
                "qualification.column.test",
                "qualification.column.purpose",
                "qualification.column.required",
                "qualification.column.status",
            ),
        )
        bind_table_help(
            self.live_metrics_table,
            "metrics.table",
            (
                "metrics.column.channel",
                "metrics.column.unit",
                "metrics.column.rms",
                "metrics.column.peak",
                "metrics.column.peak_to_peak",
                "metrics.column.crest",
                "metrics.column.clipping",
            ),
        )
        bind_table_help(
            self.session_table,
            "sessions.table",
            (
                "sessions.column.session",
                "sessions.column.started",
                "sessions.column.completion",
                "sessions.column.integrity",
                "sessions.column.type",
            ),
        )
        bind_table_help(
            self.review_extrema_table,
            "review.extrema_table",
            (
                "review.extrema.channel",
                "review.extrema.unit",
                "review.extrema.maximum",
                "review.extrema.max_sample",
                "review.extrema.max_time",
                "review.extrema.minimum",
                "review.extrema.min_sample",
                "review.extrema.min_time",
                "review.extrema.absolute_peak",
                "review.extrema.peak_sample",
                "review.extrema.peak_time",
            ),
        )
        bind_table_help(
            self.review_detected_events,
            "review.detected_table",
            (
                "review.detected.channel",
                "review.detected.start",
                "review.detected.stop",
                "review.detected.duration",
                "review.detected.peak",
                "review.detected.score",
            ),
        )

    def _toggle_navigation(self) -> None:
        if self.navigation.isVisible():
            self._navigation_width = max(self.navigation.width(), self.navigation.minimumWidth())
            self.navigation.hide()
            self.navigation_toggle.setText("Show navigation")
            return
        self.navigation.show()
        available = max(self.shell_splitter.width() - self._navigation_width, 1)
        self.shell_splitter.setSizes([self._navigation_width, available])
        self.navigation_toggle.setText("Hide navigation")

    def _build_setup_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        identity_group = QGroupBox("Profile and hardware")
        identity_layout = QHBoxLayout(identity_group)
        self.profile_label = QLabel()
        self.profile_selector = QComboBox()
        self.profile_selector.addItem(
            self.presenter.services.profile.hardware_profile_id,
            self.presenter.services.profile.hardware_profile_id,
        )
        self.discovery_label = QLabel("Not checked")
        self.discover_button = QPushButton("Refresh hardware")
        self.discover_button.clicked.connect(self._submit_discovery)
        identity_layout.addWidget(self.profile_selector, 1)
        identity_layout.addWidget(self.profile_label)
        identity_layout.addWidget(self.discovery_label)
        identity_layout.addWidget(self.discover_button)
        layout.addWidget(identity_group)

        form_group = QGroupBox("Experiment and acquisition")
        form = QGridLayout(form_group)
        self.field_editors: dict[str, QLineEdit] = {}
        fields = (
            ("experiment.name", "Experiment name"),
            ("experiment.operator", "Operator"),
            ("experiment.sample_id", "Specimen / sample ID"),
            ("experiment.description", "Description"),
            ("experiment.notes", "Notes"),
            ("acquisition.duration_s", "Duration (s)"),
            ("acquisition.chunk_size", "Samples / read / channel"),
        )
        for index, (path, label) in enumerate(fields):
            editor = QLineEdit()
            editor.setObjectName(path.replace(".", "_"))
            editor.editingFinished.connect(
                lambda field_path=path, control=editor: self._edit_field(field_path, control.text())
            )
            self.field_editors[path] = editor
            row, column = divmod(index, 2)
            form.addWidget(QLabel(label), row * 2, column)
            form.addWidget(editor, row * 2 + 1, column)
        self.sample_rate_selector = QComboBox()
        self.sample_rate_selector.setObjectName("acquisition_sample_rate_hz")
        self.sample_rate_selector.setEditable(False)
        for rate in self.presenter.services.profile.application_supported_sample_rates_hz:
            self.sample_rate_selector.addItem(f"{rate:,.6f}".rstrip("0").rstrip("."), rate)
        self.sample_rate_selector.currentIndexChanged.connect(self._edit_sample_rate)
        form.addWidget(QLabel("Sample rate (Hz)"), 6, 1)
        form.addWidget(self.sample_rate_selector, 7, 1)
        self.read_interval_label = QLabel()
        form.addWidget(self.read_interval_label, 8, 0, 1, 2)
        layout.addWidget(form_group)

        destination_group = QGroupBox("Session destination")
        destination_layout = QGridLayout(destination_group)
        self.destination_edit = QLineEdit()
        self.destination_edit.editingFinished.connect(
            lambda: self._edit_field("storage.parent_directory", self.destination_edit.text())
        )
        destination_button = QPushButton("Choose…")
        destination_button.clicked.connect(self._choose_destination)
        self.destination_preview_label = QLabel("Not previewed")
        self.destination_preview_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        new_identity = QPushButton("New preview ID")
        new_identity.clicked.connect(self._new_destination_identity)
        destination_layout.addWidget(self.destination_edit, 0, 0)
        destination_layout.addWidget(destination_button, 0, 1)
        destination_layout.addWidget(self.destination_preview_label, 1, 0)
        destination_layout.addWidget(new_identity, 1, 1)
        layout.addWidget(destination_group)

        self.readiness_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.recipe_box = QTextEdit()
        self.recipe_box.setReadOnly(True)
        self.recipe_box.setPlainText(
            f"Active baseline recipe\n{self.presenter.services.recipe.recipe_id} "
            f"@ {self.presenter.services.recipe.recipe_version}"
        )
        readiness_panels = QWidget()
        readiness_panels_layout = QVBoxLayout(readiness_panels)
        readiness_panels_layout.setContentsMargins(0, 0, 0, 0)

        self.qualified_readiness_group = QGroupBox("Qualified-acquisition readiness")
        qualified_readiness_layout = QVBoxLayout(self.qualified_readiness_group)
        self.readiness_list = QListWidget()
        qualified_readiness_layout.addWidget(self.readiness_list)

        self.diagnostic_readiness_group = QGroupBox("Raw monitoring / diagnostic readiness")
        if self.functional_preview:
            self.diagnostic_readiness_group.setTitle("Functional-run readiness")
        diagnostic_readiness_layout = QVBoxLayout(self.diagnostic_readiness_group)
        self.diagnostic_readiness_list = QListWidget()
        diagnostic_readiness_layout.addWidget(self.diagnostic_readiness_list)

        readiness_panels_layout.addWidget(self.qualified_readiness_group)
        readiness_panels_layout.addWidget(self.diagnostic_readiness_group)
        self.qualified_readiness_group.setVisible(not self.functional_preview)
        self.readiness_splitter.addWidget(self.recipe_box)
        self.readiness_splitter.addWidget(readiness_panels)
        style_splitter_tiles(self.readiness_splitter)
        layout.addWidget(self.readiness_splitter)

        self.continue_button = QPushButton("Continue to Acquisition")
        self.continue_button.setEnabled(False)
        self.continue_button.clicked.connect(self._continue_to_acquisition)
        layout.addWidget(self.continue_button)

        self.channel_table = QTableWidget(0, 7)
        self.channel_table.setHorizontalHeaderLabels(
            ["Order", "Enabled", "Logical ID", "Role", "Physical", "Sensor", "Calibration"]
        )
        self.channel_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.channel_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.channel_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.channel_table.itemSelectionChanged.connect(self._render_selected_channel)
        self.channel_table.itemChanged.connect(self._channel_item_changed)
        layout.addWidget(self.channel_table, 1)

        calibration_group = QGroupBox("Selected channel calibration and conditioning")
        calibration_form = QFormLayout(calibration_group)
        self.channel_editor_fields: dict[str, QLineEdit] = {}
        for key, label in (
            ("input_min", "Input minimum (V)"),
            ("input_max", "Input maximum (V)"),
            ("sensor", "Sensor reference"),
            ("sensor_manufacturer", "Sensor manufacturer"),
            ("sensor_model", "Sensor model"),
            ("sensor_serial", "Sensor serial"),
            ("sensor_mounting", "Sensor mounting"),
            ("sensor_certificate", "Sensor certificate"),
            ("calibration", "Calibration reference"),
            ("sensitivity", "Sensitivity"),
            ("sensitivity_unit", "Sensitivity unit"),
            ("output_unit", "Engineering output unit"),
            ("polarity", "Polarity"),
            ("source_reference", "Calibration source"),
            ("effective_date", "Effective date"),
            ("input_offset", "Input offset"),
            ("conditioning", "Conditioning reference"),
            ("conditioning_components", "Chain components (; separated)"),
            ("conditioning_input_unit", "Chain input unit"),
            ("conditioning_supply", "Chain supply"),
            ("conditioning_polarity", "Chain polarity"),
            ("conditioning_evidence", "Conditioning evidence"),
            ("converter_manufacturer", "Converter manufacturer"),
            ("converter_model", "Converter model"),
            ("converter_serial", "Converter serial"),
            ("converter_sensitivity", "Converter sensitivity"),
            ("converter_unit", "Converter sensitivity unit"),
            ("converter_polarity", "Converter polarity"),
            ("converter_excitation", "Converter excitation"),
            ("converter_current", "Excitation current (mA)"),
            ("converter_source", "Converter evidence source"),
            ("ni_stage", "NI-terminal stage"),
            ("ni_bias_removed", "Bias removal documented (true/false)"),
            ("ni_expected_bias", "Expected bias (V)"),
            ("ni_bias_tolerance", "Bias tolerance (V)"),
            ("ni_dynamic_min", "Dynamic minimum (V)"),
            ("ni_dynamic_max", "Dynamic maximum (V)"),
            ("ni_source", "NI-terminal evidence source"),
        ):
            control = QLineEdit()
            control.editingFinished.connect(
                lambda name=key, editor=control: self._edit_selected_channel(name, editor.text())
            )
            self.channel_editor_fields[key] = control
            calibration_form.addRow(label, control)
        self._selected_channel_paths: dict[str, str] = {}
        layout.addWidget(calibration_group)

        self.setup_scroll_area = QScrollArea()
        self.setup_scroll_area.setObjectName("experimentSetupScrollArea")
        self.setup_scroll_area.setWidgetResizable(True)
        self.setup_scroll_area.setSizeAdjustPolicy(
            QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored
        )
        self.setup_scroll_area.setWidget(page)
        return self.setup_scroll_area

    def _build_hardware_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        plan = self.presenter.services.qualification_plan
        plan_row = QHBoxLayout()
        plan_row.addWidget(QLabel("Qualification plan"))
        self.plan_selector = QComboBox()
        plan_ids = tuple(
            dict.fromkeys(
                (
                    plan.plan_id,
                    *self.presenter.services.available_qualification_plan_ids,
                )
            )
        )
        self.plan_selector.addItems(plan_ids)
        self.plan_selector.setCurrentText(plan.plan_id)
        self.plan_selector.currentTextChanged.connect(self._select_qualification_plan)
        self.plan_revision_label = QLabel()
        plan_row.addWidget(self.plan_selector)
        plan_row.addWidget(self.plan_revision_label)
        plan_row.addStretch(1)
        layout.addLayout(plan_row)
        self.test_table = QTableWidget(0, 4)
        self.test_table.setHorizontalHeaderLabels(["Test", "Purpose", "Required", "Status"])
        self.test_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.test_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._populate_test_table()
        layout.addWidget(self.test_table, 1)

        buttons = QHBoxLayout()
        self.qualification_start = QPushButton("Start hardware tests")
        self.qualification_cancel = QPushButton("Cancel")
        self.qualification_cancel.setEnabled(False)
        self.qualification_start.clicked.connect(self._confirm_and_start_qualification)
        self.qualification_cancel.clicked.connect(self.presenter.cancel_qualification)
        buttons.addWidget(self.qualification_start)
        buttons.addWidget(self.qualification_cancel)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.qualification_progress = QProgressBar()
        self.qualification_progress.setRange(0, 1)
        self.qualification_progress.setValue(0)
        self.qualification_status = QLabel("No qualification report")
        layout.addWidget(self.qualification_progress)
        layout.addWidget(self.qualification_status)
        self.report_viewer = QTextEdit()
        self.report_viewer.setReadOnly(True)
        layout.addWidget(self.report_viewer)
        self.open_report_button = QPushButton("Open report folder")
        self.open_report_button.setEnabled(False)
        self.open_report_button.clicked.connect(self._open_report_folder)
        layout.addWidget(self.open_report_button)
        return page

    def _build_acquisition_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        controls = QHBoxLayout()
        self.acquisition_mode = QComboBox()
        if self.functional_preview:
            self.acquisition_mode.addItem("Open monitoring and recording", "open")
            self.acquisition_mode.addItem("Timed functional run", "functional")
        else:
            self.acquisition_mode.addItem("Open monitoring and recording", "open")
            self.acquisition_mode.addItem("Qualified acquisition", "qualified")
        self.acquisition_mode.currentIndexChanged.connect(self._acquisition_mode_changed)
        self.open_channel = QLabel("All enabled channels")
        self.diagnostic_start_button = QPushButton("Start raw diagnostic")
        self.diagnostic_start_button.setEnabled(False)
        self.diagnostic_start_button.clicked.connect(self._request_raw_diagnostic)
        self.acquisition_start_button = QPushButton("Start qualified acquisition")
        self.acquisition_stop_button = QPushButton("Stop")
        self.acquisition_stop_button.setEnabled(False)
        self.acquisition_start_button.clicked.connect(self._request_launch)
        self.acquisition_stop_button.clicked.connect(self._request_stop)
        self.open_start_button = QPushButton("Start monitoring")
        self.open_record_button = QPushButton("Record")
        self.open_end_button = QPushButton("End monitoring")
        self.open_start_button.clicked.connect(self._request_open_monitor)
        self.open_record_button.clicked.connect(self._toggle_open_recording)
        self.open_end_button.clicked.connect(self._request_end_monitoring)
        self.pause_display = QCheckBox("Pause display")
        self.live_window = QComboBox()
        self.live_window.addItems(["1 s", "2 s", "5 s", "10 s"])
        self.live_window.setCurrentText("5 s")
        self.live_window.currentTextChanged.connect(
            lambda value: self.presenter.set_live_window(float(value.split()[0]))
        )
        self.acquisition_mode_label = QLabel("Mode")
        controls.addWidget(self.acquisition_mode_label)
        controls.addWidget(self.acquisition_mode)
        self.open_channel_label = QLabel("Monitor channel")
        controls.addWidget(self.open_channel_label)
        controls.addWidget(self.open_channel)
        controls.addWidget(self.diagnostic_start_button)
        controls.addWidget(self.acquisition_start_button)
        controls.addWidget(self.acquisition_stop_button)
        controls.addWidget(self.open_start_button)
        controls.addWidget(self.open_record_button)
        controls.addWidget(self.open_end_button)
        controls.addWidget(self.pause_display)
        controls.addWidget(QLabel("Window"))
        controls.addWidget(self.live_window)
        controls.addStretch(1)
        layout.addLayout(controls)
        if self.functional_preview:
            self.diagnostic_start_button.setText("Start Functional Run")
            self.diagnostic_start_button.clicked.disconnect()
            self.diagnostic_start_button.clicked.connect(self._request_functional_preview)
            self.acquisition_start_button.setVisible(False)

        status_group = QGroupBox("Run and recording status")
        status_layout = QGridLayout(status_group)
        self.acquisition_state_label = QLabel("idle")
        self.acquisition_elapsed_label = QLabel("0.000 s")
        self.acquisition_counts_label = QLabel("acquired 0 / persisted 0")
        self.acquisition_warning_label = QLabel("warnings 0 / gaps 0 / display drops 0")
        self.acquisition_identity_label = QLabel("No active session")
        self.acquisition_identity_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        for label in (
            self.acquisition_state_label,
            self.acquisition_elapsed_label,
            self.acquisition_counts_label,
            self.acquisition_warning_label,
            self.acquisition_identity_label,
        ):
            label.setWordWrap(True)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.recording_queue = QProgressBar()
        self.recording_queue.setRange(0, 1)
        self.display_queue = QProgressBar()
        self.display_queue.setRange(0, 1)
        status_layout.addWidget(QLabel("State"), 0, 0)
        status_layout.addWidget(self.acquisition_state_label, 0, 1)
        status_layout.addWidget(QLabel("Elapsed"), 0, 2)
        status_layout.addWidget(self.acquisition_elapsed_label, 0, 3)
        status_layout.addWidget(self.acquisition_counts_label, 1, 0, 1, 2)
        status_layout.addWidget(self.acquisition_warning_label, 1, 2, 1, 2)
        status_layout.addWidget(QLabel("Recording queue"), 2, 0)
        status_layout.addWidget(self.recording_queue, 2, 1)
        status_layout.addWidget(QLabel("Display queue"), 2, 2)
        status_layout.addWidget(self.display_queue, 2, 3)
        status_layout.addWidget(self.acquisition_identity_label, 3, 0, 1, 4)
        self.live_metrics_table = QTableWidget(0, 7)
        self.live_metrics_table.setHorizontalHeaderLabels(
            ["Channel", "Unit", "RMS", "Peak", "Peak-to-peak", "Crest", "Clipping"]
        )
        self.live_metrics_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.acquisition_summary_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.acquisition_summary_splitter.setObjectName("acquisitionSummarySplitter")
        self.acquisition_summary_splitter.addWidget(status_group)
        self.acquisition_summary_splitter.addWidget(self.live_metrics_table)
        _configure_splitter(
            self.acquisition_summary_splitter,
            sizes=(800, 900),
            stretch=(1, 1),
        )

        self.live_plot_splitter = QSplitter(Qt.Orientation.Vertical)
        self.live_plot_splitter.setObjectName("livePlotSplitter")
        self.raw_live_plot = self._create_plot_panel("Raw voltage")
        self.conditioned_live_plot = self._create_plot_panel("Conditioned (selected recipe)")
        self.live_plot_splitter.addWidget(self.raw_live_plot)
        self.live_plot_splitter.addWidget(self.conditioned_live_plot)
        _configure_splitter(self.live_plot_splitter, sizes=(1, 1), stretch=(1, 1))

        self.acquisition_workspace_splitter = QSplitter(Qt.Orientation.Vertical)
        self.acquisition_workspace_splitter.setObjectName("acquisitionWorkspaceSplitter")
        self.acquisition_workspace_splitter.addWidget(self.acquisition_summary_splitter)
        self.acquisition_workspace_splitter.addWidget(self.live_plot_splitter)
        _configure_splitter(
            self.acquisition_workspace_splitter,
            sizes=(220, 660),
            stretch=(1, 3),
        )
        layout.addWidget(self.acquisition_workspace_splitter, 1)
        available = self.presenter.services.live is not None
        self.acquisition_start_button.setEnabled(available)
        if not available:
            self.acquisition_state_label.setText("Unavailable: install storage and analysis extras")
        self._acquisition_mode_changed()
        return page

    def _build_sessions_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        controls = QHBoxLayout()
        self.browser_root = QLineEdit()
        choose = QPushButton("Choose root…")
        self.session_refresh_button = QPushButton("Refresh")
        self.session_review_button = QPushButton("Review selected")
        self.session_review_button.setEnabled(False)
        choose.clicked.connect(self._choose_browser_root)
        self.session_refresh_button.clicked.connect(self._scan_sessions)
        self.session_review_button.clicked.connect(self._review_selected_session)
        controls.addWidget(self.browser_root, 1)
        controls.addWidget(choose)
        controls.addWidget(self.session_refresh_button)
        controls.addWidget(self.session_review_button)
        layout.addLayout(controls)
        self.session_browser_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.session_browser_splitter.setObjectName("sessionBrowserSplitter")
        self.session_table = QTableWidget(0, 5)
        self.session_table.setHorizontalHeaderLabels(
            ["Session", "Started", "Completion", "Integrity", "Type"]
        )
        self.session_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.session_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.session_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.session_table.itemSelectionChanged.connect(self._render_session_details)
        self.session_details = QTextEdit()
        self.session_details.setReadOnly(True)
        review_panels = {
            "sessions": ReviewPanel("Sessions", self.session_table),
            "session_details": ReviewPanel("Session details", self.session_details),
        }
        self.session_browser_splitter.addWidget(review_panels["sessions"])
        self.session_browser_splitter.addWidget(review_panels["session_details"])
        _configure_splitter(
            self.session_browser_splitter,
            sizes=(3, 2),
            stretch=(3, 2),
        )

        review_controls = QHBoxLayout()
        self.review_signal_stage = QComboBox()
        for label, stage in (
            ("Raw", SignalStage.RAW),
            ("Calibrated", SignalStage.CALIBRATED),
            ("Conditioned (selected recipe)", SignalStage.CONDITIONED),
        ):
            self.review_signal_stage.addItem(label, stage.value)
        self.review_signal_stage.currentIndexChanged.connect(self._review_stage_changed)
        self.review_representation = QComboBox()
        for label, value in (
            ("Raw", PlotRepresentation.RAW_TIME),
            ("Calibrated", PlotRepresentation.CALIBRATED_TIME),
            ("Conditioned (session recipe)", PlotRepresentation.FILTERED_TIME),
            ("FFT amplitude", PlotRepresentation.FFT_AMPLITUDE),
            ("PSD", PlotRepresentation.PSD),
        ):
            self.review_representation.addItem(label, value.value)
        self.review_representation.currentIndexChanged.connect(self._review_representation_changed)
        self.review_start_seconds = QLineEdit()
        self.review_start_seconds.setPlaceholderText("start seconds (inclusive)")
        self.review_stop_seconds = QLineEdit()
        self.review_stop_seconds.setPlaceholderText("stop seconds (exclusive)")
        self.review_start_seconds.textEdited.connect(self._review_selection_inputs_changed)
        self.review_stop_seconds.textEdited.connect(self._review_selection_inputs_changed)
        self.review_plot_button = QPushButton("Plot range")
        self.review_plot_button.setEnabled(False)
        self.review_plot_button.clicked.connect(self._request_review_plot)
        self.review_analyze_button = QPushButton("Analyze selection")
        self.review_analyze_button.setEnabled(False)
        self.review_analyze_button.clicked.connect(self._request_review_analysis)
        self.export_csv_button = QPushButton("Export CSV")
        self.export_png_button = QPushButton("Export PNG")
        self.export_svg_button = QPushButton("Export SVG")
        self.export_tdms_button = QPushButton("Export TDMS")
        self.export_tdms_button.setEnabled(False)
        self.export_tdms_button.clicked.connect(self._request_tdms_export)
        for button, output_format in (
            (self.export_csv_button, ExportFormat.CSV),
            (self.export_png_button, ExportFormat.PNG),
            (self.export_svg_button, ExportFormat.SVG),
        ):
            button.setEnabled(False)
            button.clicked.connect(
                lambda _checked=False, selected=output_format: self._request_export(selected)
            )
        review_controls.addWidget(self.review_signal_stage)
        review_controls.addWidget(self.review_representation)
        review_controls.addWidget(self.review_start_seconds)
        review_controls.addWidget(self.review_stop_seconds)
        review_controls.addWidget(self.review_plot_button)
        review_controls.addWidget(self.review_analyze_button)
        review_controls.addWidget(self.export_csv_button)
        review_controls.addWidget(self.export_png_button)
        review_controls.addWidget(self.export_svg_button)
        review_controls.addWidget(self.export_tdms_button)
        layout.addLayout(review_controls)
        self.review_channel_notice = QLabel("")
        self.review_channel_notice.setWordWrap(True)
        layout.addWidget(self.review_channel_notice)

        detector_controls = QHBoxLayout()
        detector_controls.addWidget(QLabel("Transient detection:"))
        self.review_mad_multiplier = QDoubleSpinBox()
        self.review_mad_multiplier.setRange(0.1, 1000.0)
        self.review_mad_multiplier.setDecimals(2)
        self.review_mad_multiplier.setValue(6.0)
        self.review_mad_multiplier.setSuffix(" MAD")
        self.review_pre_padding = QDoubleSpinBox()
        self.review_post_padding = QDoubleSpinBox()
        self.review_merge_gap = QDoubleSpinBox()
        for control, detector_value, label in (
            (self.review_pre_padding, 0.05, " pre s"),
            (self.review_post_padding, 0.05, " post s"),
            (self.review_merge_gap, 0.02, " merge s"),
        ):
            control.setRange(0.0, 60.0)
            control.setDecimals(4)
            control.setSingleStep(0.01)
            control.setValue(detector_value)
            control.setSuffix(label)
            control.valueChanged.connect(self._review_detector_inputs_changed)
            detector_controls.addWidget(control)
        self.review_mad_multiplier.valueChanged.connect(self._review_detector_inputs_changed)
        detector_controls.addWidget(self.review_mad_multiplier)
        self.review_detect_button = QPushButton("Detect events")
        self.review_detect_button.setEnabled(False)
        self.review_detect_button.clicked.connect(self._request_transient_detection)
        detector_controls.addWidget(self.review_detect_button)
        self.review_detection_status = QLabel("No waveform events detected")
        self.review_detection_status.setWordWrap(True)
        detector_controls.addWidget(self.review_detection_status, 1)
        layout.addLayout(detector_controls)

        self.review_channels = QListWidget()
        self.review_channels.itemChanged.connect(self._review_channel_selection_changed)
        self.review_plot = self._create_plot_panel("Disk-backed session review")
        review_plot_panel = _signal_plot_panel(self.review_plot)
        if review_plot_panel is not None:
            review_plot_panel.selectionChanged.connect(self._review_cursor_changed)
        self.review_plot_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.review_plot_splitter.setObjectName("reviewPlotSplitter")
        review_panels["channels"] = ReviewPanel("Channels", self.review_channels)
        review_panels["plot"] = ReviewPanel("Session plot", self.review_plot)
        self.review_plot_splitter.addWidget(review_panels["channels"])
        self.review_plot_splitter.addWidget(review_panels["plot"])
        _configure_splitter(
            self.review_plot_splitter,
            sizes=(1, 5),
            stretch=(1, 5),
        )
        self.review_plot_metadata = QLabel("No review plot generated")
        self.review_plot_metadata.setWordWrap(True)
        self.review_plot_metadata.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        review_plot_tile = QWidget()
        review_plot_layout = QVBoxLayout(review_plot_tile)
        review_plot_layout.setContentsMargins(0, 0, 0, 0)
        self.review_analysis_tabs = QTabWidget()
        self.review_fft_plot = self._create_plot_panel("FFT amplitude")
        self.review_psd_plot = self._create_plot_panel("Welch PSD")
        self.review_extrema_table = QTableWidget(0, 11)
        self.review_extrema_table.setHorizontalHeaderLabels(
            [
                "Channel",
                "Unit",
                "Maximum",
                "Max sample",
                "Max time (s)",
                "Minimum",
                "Min sample",
                "Min time (s)",
                "Absolute peak",
                "Peak sample",
                "Peak time (s)",
            ]
        )
        self.review_extrema_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.review_extrema_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.review_analysis_tabs.addTab(self.review_fft_plot, "FFT")
        self.review_analysis_tabs.addTab(self.review_psd_plot, "PSD")
        self.review_analysis_tabs.addTab(self.review_extrema_table, "Extrema")
        self.review_analysis_tabs.currentChanged.connect(self._review_analysis_tab_changed)
        self.review_analysis_splitter = QSplitter(Qt.Orientation.Vertical)
        self.review_analysis_splitter.setObjectName("reviewAnalysisSplitter")
        self.review_analysis_splitter.addWidget(self.review_plot_splitter)
        review_panels["analysis"] = ReviewPanel("FFT / PSD / Extrema", self.review_analysis_tabs)
        self.review_analysis_splitter.addWidget(review_panels["analysis"])
        _configure_splitter(self.review_analysis_splitter, sizes=(3, 2), stretch=(3, 2))
        review_plot_layout.addWidget(self.review_analysis_splitter, 1)
        review_panels["plot"].content_layout.addWidget(self.review_plot_metadata)

        self.review_details_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.review_details_splitter.setObjectName("reviewDetailsSplitter")
        self.review_events = QTextEdit()
        self.review_events.setReadOnly(True)
        self.review_events.setPlaceholderText("Structured events and faults")
        self.review_artifacts = QTextEdit()
        self.review_artifacts.setReadOnly(True)
        self.review_artifacts.setPlaceholderText("Export artifact history and diagnostics")
        self.review_detected_events = QTableWidget(0, 6)
        self.review_detected_events.setHorizontalHeaderLabels(
            ["Channel", "Start (s)", "Stop (s)", "Duration (s)", "Peak (s)", "Score"]
        )
        self.review_detected_events.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.review_detected_events.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.review_detected_events.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.review_detected_events.itemSelectionChanged.connect(self._select_detected_event)
        review_panels["events"] = ReviewPanel("Structured events", self.review_events)
        review_panels["detected_events"] = ReviewPanel(
            "Detected events", self.review_detected_events
        )
        review_panels["exports"] = ReviewPanel("Export history", self.review_artifacts)
        for key in ("events", "detected_events", "exports"):
            self.review_details_splitter.addWidget(review_panels[key])
        _configure_splitter(
            self.review_details_splitter,
            sizes=(1, 1, 1),
            stretch=(1, 1, 1),
        )

        self.review_workspace_splitter = QSplitter(Qt.Orientation.Vertical)
        self.review_workspace_splitter.setObjectName("reviewWorkspaceSplitter")
        self.review_workspace_splitter.addWidget(self.session_browser_splitter)
        self.review_workspace_splitter.addWidget(review_plot_tile)
        self.review_workspace_splitter.addWidget(self.review_details_splitter)
        _configure_splitter(
            self.review_workspace_splitter,
            sizes=(180, 540, 180),
            stretch=(1, 3, 1),
        )
        layout.addWidget(self.review_workspace_splitter, 1)
        self.review_layout = ReviewLayoutController(
            page,
            self.review_workspace_splitter,
            review_panels,
            {
                self.review_workspace_splitter: (180, 540, 180),
                self.session_browser_splitter: (3, 2),
                self.review_plot_splitter: (1, 5),
                self.review_analysis_splitter: (3, 2),
                self.review_details_splitter: (1, 1, 1),
            },
        )
        layout.addWidget(self.review_layout.toolbar)
        export_status = QHBoxLayout()
        self.export_progress = QProgressBar()
        self.export_progress.setRange(0, 1)
        self.export_cancel_button = QPushButton("Cancel export")
        self.export_cancel_button.setEnabled(False)
        self.export_cancel_button.clicked.connect(self._export_cancel.set)
        export_status.addWidget(self.export_progress, 1)
        export_status.addWidget(self.export_cancel_button)
        layout.addLayout(export_status)
        self._catalog_entries: tuple[SessionCatalogEntry, ...] = ()
        return page

    def _create_plot_panel(self, title: str) -> QWidget:
        try:
            return SignalPlotPanel(title)
        except DependencyUnavailableError as error:
            unavailable = QLabel(f"{title} unavailable: {error.message} {error.remediation or ''}")
            unavailable.setWordWrap(True)
            return unavailable

    def _render_draft(self) -> None:
        profile = self.presenter.services.profile
        self.profile_label.setText(f"{profile.hardware_profile_id} rev {profile.profile_revision}")
        if (
            self.functional_preview
            and self.draft.normalized is not None
            and self.draft.normalized.experiment_mode.value == "accelerometer_commissioning"
        ):
            self.setWindowTitle(
                f"{preview_title(self.presenter.services.mode)} - "
                "FUNCTIONAL NI TEST (X/Y/Z UNQUALIFIED; ai3 RAW ONLY / NOT CHARGE)"
            )
        elif (
            self.draft.normalized is not None
            and self.draft.normalized.experiment_mode.value == "accelerometer_commissioning"
        ):
            self.setWindowTitle(
                f"{preview_title(self.presenter.services.mode)} — "
                "COMMISSIONING (ai3 RAW ONLY / NOT CHARGE)"
            )
        else:
            self.setWindowTitle(
                f"{preview_title(self.presenter.services.mode)} — RAW VOLTAGE MONITORING"
            )
        for path, editor in self.field_editors.items():
            value = field_value(self.draft.raw_fields, path)
            editor.setText("" if value is None else str(value))
            self._mark_field(editor, path)
        selected_rate = field_value(self.draft.raw_fields, "acquisition.sample_rate_hz")
        self.sample_rate_selector.blockSignals(True)
        if isinstance(selected_rate, (int, float)) and not isinstance(selected_rate, bool):
            for index in range(self.sample_rate_selector.count()):
                if float(self.sample_rate_selector.itemData(index)) == float(selected_rate):
                    self.sample_rate_selector.setCurrentIndex(index)
                    break
        self.sample_rate_selector.blockSignals(False)
        self._mark_field(self.sample_rate_selector, "acquisition.sample_rate_hz")
        chunk_size = field_value(self.draft.raw_fields, "acquisition.chunk_size")
        if (
            isinstance(selected_rate, (int, float))
            and not isinstance(selected_rate, bool)
            and isinstance(chunk_size, int)
            and not isinstance(chunk_size, bool)
            and selected_rate > 0
        ):
            interval_ms = 1000.0 * chunk_size / float(selected_rate)
            self.read_interval_label.setText(
                f"Derived read interval: {interval_ms:.3f} ms "
                f"({chunk_size:,} samples per channel at {float(selected_rate):,.6f} Hz)"
            )
        else:
            self.read_interval_label.setText("Derived read interval unavailable")
        recipe = self.presenter.recipe_for_draft(self.draft)
        self.recipe_box.setPlainText(
            f"Automatically selected recipe\n{recipe.recipe_id} @ {recipe.recipe_version}\n"
            f"SHA-256 {canonical_sha256(recipe)}"
        )
        destination = field_value(self.draft.raw_fields, "storage.parent_directory")
        self.destination_edit.setText("" if destination is None else str(destination))
        self._mark_field(self.destination_edit, "storage.parent_directory")
        raw_channels = field_value(self.draft.raw_fields, "channels")
        channels = raw_channels if isinstance(raw_channels, tuple) else ()
        self.channel_table.blockSignals(True)
        self.channel_table.setRowCount(len(channels))
        for row, raw in enumerate(channels):
            channel = cast("dict[str, object]", raw)
            physical = cast("dict[str, object]", channel.get("physical_channel", {}))
            physical_label = (
                f"{physical.get('module_id', '')}/ai{physical.get('channel_index', '')}"
            )
            if self.hardware.discovery is not None:
                modules = [
                    module
                    for device in self.hardware.discovery.devices
                    for module in device.modules
                    if any(
                        module.model == configured.model
                        for configured in self.presenter.services.profile.modules
                    )
                ]
                if len(modules) == 1:
                    suffix = f"/ai{physical.get('channel_index', '')}".casefold()
                    available = [
                        name
                        for name in modules[0].available_channels
                        if name.casefold().endswith(suffix)
                    ]
                    physical_label = (
                        available[0] if len(available) == 1 else f"{physical_label} (unavailable)"
                    )
            values = (
                channel.get("channel_order"),
                "Yes" if channel.get("enabled") else "No",
                channel.get("channel_id"),
                channel.get("channel_role"),
                physical_label,
                channel.get("sensor_id"),
                channel.get("calibration_id"),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value or ""))
                if column == 1:
                    item.setText("")
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(
                        Qt.CheckState.Checked if channel.get("enabled") else Qt.CheckState.Unchecked
                    )
                set_described_table_item(self.channel_table, row, column, item)
        self.channel_table.blockSignals(False)
        self._render_selected_channel()
        if self.channel_table.rowCount() and self.channel_table.currentRow() < 0:
            self.channel_table.selectRow(0)
        if hasattr(self, "open_channel"):
            if self.draft.normalized is not None:
                by_id = {channel.channel_id: channel for channel in self.draft.normalized.channels}
                labels = tuple(
                    f"{by_id[channel_id].logical_name} [{by_id[channel_id].raw_unit.value}]"
                    for channel_id in self.draft.normalized.acquisition.channel_ids
                )
                self.open_channel.setText(
                    f"All {len(labels)} enabled channels: " + ", ".join(labels)
                )
            else:
                self.open_channel.setText("All enabled channels (configuration invalid)")
        self._update_readiness()

    def _channel_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 1 or self._closing:
            return
        path = f"channels[{item.row()}].enabled"
        self.draft = self.presenter.update_field(path, item.checkState() == Qt.CheckState.Checked)
        self._queue_hardware_refresh()
        self._render_draft()
        self._submit_destination_check()

    def _render_selected_channel(self) -> None:
        row = self.channel_table.currentRow()
        raw_channels = field_value(self.draft.raw_fields, "channels")
        if row < 0 or not isinstance(raw_channels, tuple) or row >= len(raw_channels):
            return
        channel = cast("Mapping[str, object]", raw_channels[row])
        sensor_id = str(channel.get("sensor_id") or "")
        calibration_id = str(channel.get("calibration_id") or "")
        conditioning_id = str(channel.get("conditioning_chain_id") or "")
        paths: dict[str, str] = {
            "input_min": f"channels[{row}].requested_input_min",
            "input_max": f"channels[{row}].requested_input_max",
            "sensor": f"channels[{row}].sensor_id",
            "calibration": f"channels[{row}].calibration_id",
            "conditioning": f"channels[{row}].conditioning_chain_id",
        }
        sensors = field_value(self.draft.raw_fields, "sensors")
        calibrations = field_value(self.draft.raw_fields, "calibrations")
        chains = field_value(self.draft.raw_fields, "conditioning_chains")
        sensor_index = _mapping_index(sensors, "sensor_id", sensor_id)
        calibration_index = _mapping_index(calibrations, "calibration_id", calibration_id)
        chain_index = _mapping_index(chains, "conditioning_chain_id", conditioning_id)
        if sensor_index is not None:
            paths.update(
                {
                    "sensor_manufacturer": f"sensors[{sensor_index}].manufacturer",
                    "sensor_model": f"sensors[{sensor_index}].model",
                    "sensor_serial": f"sensors[{sensor_index}].serial_number",
                    "sensor_mounting": f"sensors[{sensor_index}].mounting_description",
                    "sensor_certificate": f"sensors[{sensor_index}].certificate_reference",
                }
            )
        if calibration_index is not None:
            paths.update(
                {
                    "sensitivity": f"calibrations[{calibration_index}].sensitivity_value",
                    "sensitivity_unit": f"calibrations[{calibration_index}].sensitivity_unit",
                    "output_unit": f"calibrations[{calibration_index}].output_unit",
                    "polarity": f"calibrations[{calibration_index}].polarity",
                    "source_reference": f"calibrations[{calibration_index}].source_reference",
                    "effective_date": f"calibrations[{calibration_index}].effective_date",
                    "input_offset": f"calibrations[{calibration_index}].input_offset",
                }
            )
        if chain_index is not None:
            prefix = f"conditioning_chains[{chain_index}]"
            paths.update(
                {
                    "conditioning_components": f"{prefix}.components",
                    "conditioning_input_unit": f"{prefix}.input_unit",
                    "conditioning_supply": f"{prefix}.supply_description",
                    "conditioning_polarity": f"{prefix}.polarity",
                    "conditioning_evidence": f"{prefix}.calibration_reference",
                    "converter_manufacturer": f"{prefix}.charge_converter.manufacturer",
                    "converter_model": f"{prefix}.charge_converter.model",
                    "converter_serial": f"{prefix}.charge_converter.serial_number",
                    "converter_sensitivity": (
                        f"{prefix}.charge_converter.nominal_charge_sensitivity"
                    ),
                    "converter_unit": f"{prefix}.charge_converter.sensitivity_unit",
                    "converter_polarity": f"{prefix}.charge_converter.output_polarity",
                    "converter_excitation": f"{prefix}.charge_converter.excitation_source",
                    "converter_current": f"{prefix}.charge_converter.excitation_current_ma",
                    "converter_source": f"{prefix}.charge_converter.source_reference",
                    "ni_stage": f"{prefix}.ni_terminal_conditioning.stage_description",
                    "ni_bias_removed": (
                        f"{prefix}.ni_terminal_conditioning.bias_removal_documented"
                    ),
                    "ni_expected_bias": f"{prefix}.ni_terminal_conditioning.expected_bias_v",
                    "ni_bias_tolerance": f"{prefix}.ni_terminal_conditioning.bias_tolerance_v",
                    "ni_dynamic_min": f"{prefix}.ni_terminal_conditioning.dynamic_min_v",
                    "ni_dynamic_max": f"{prefix}.ni_terminal_conditioning.dynamic_max_v",
                    "ni_source": f"{prefix}.ni_terminal_conditioning.source_reference",
                }
            )
        self._selected_channel_paths = paths
        for key, editor in self.channel_editor_fields.items():
            path = paths.get(key)
            value = field_value(self.draft.raw_fields, path) if path is not None else None
            editor.setEnabled(path is not None)
            editor.setText(
                "; ".join(str(item) for item in value)
                if isinstance(value, (list, tuple))
                else ""
                if value is None
                else str(value)
            )
            if path is not None:
                self._mark_field(editor, path)

    def _background_busy_changed(self, key: str, busy: bool) -> None:
        if key == "discovery":
            self.discover_button.setEnabled(
                not busy and self.presenter.services.daq is not None and not self._closing
            )
            if not busy and self._hardware_refresh_pending:
                self._hardware_refresh_timer.start()
        elif key == "qualification":
            self.qualification_cancel.setEnabled(busy and not self._closing)
            self.plan_selector.setEnabled(not busy and not self._closing)
            if busy:
                self.continue_button.setEnabled(False)
                self.acquisition_start_button.setEnabled(False)
                self.diagnostic_start_button.setEnabled(False)
            else:
                self._update_readiness()
                if self._hardware_refresh_pending:
                    self._hardware_refresh_timer.start()
        elif key == "catalog":
            self.session_refresh_button.setEnabled(not busy and not self._closing)
        elif key in {"launch", "diagnostic-launch", "functional-launch"}:
            self.acquisition_start_button.setEnabled(
                not busy
                and self.readiness is not None
                and self.readiness.eligible
                and self.presenter.services.live is not None
                and not self._closing
            )
            self.diagnostic_start_button.setEnabled(
                not busy
                and self.diagnostic_readiness is not None
                and self.diagnostic_readiness.eligible
                and not self._closing
            )
            self.continue_button.setEnabled(
                not busy
                and (
                    (self.readiness is not None and self.readiness.eligible)
                    or (
                        self.diagnostic_readiness is not None and self.diagnostic_readiness.eligible
                    )
                )
                and not self._closing
            )
        elif key in {"open-monitor-start", "open-record", "open-record-stop", "open-monitor-stop"}:
            if busy:
                self.open_start_button.setEnabled(False)
                self.open_record_button.setEnabled(False)
                self.open_end_button.setEnabled(False)
                self.acquisition_mode.setEnabled(False)
                self.open_channel.setEnabled(False)
            elif self._open_status is not None:
                self._render_open_status(
                    self.presenter.open_acquisition_status() or self._open_status
                )
            else:
                self._update_readiness()
        elif key == "review":
            self.review_plot_button.setEnabled(not busy and self._review is not None)
        elif key == "review-plot":
            self.review_plot_button.setEnabled(not busy and self._review is not None)
        elif key == "review-detect":
            self.review_detect_button.setEnabled(not busy and self._review is not None)
        elif key in {"review-extrema", "review-time", "review-fft", "review-psd"}:
            analysis_busy = any(
                self.runner.busy(item)
                for item in ("review-extrema", "review-time", "review-fft", "review-psd")
            )
            self.review_analyze_button.setEnabled(not analysis_busy and self._review is not None)
        elif key == "export":
            self.export_cancel_button.setEnabled(busy and not self._closing)
            for button in (
                self.export_csv_button,
                self.export_png_button,
                self.export_svg_button,
            ):
                button.setEnabled(not busy and self._review is not None and not self._closing)
            self.export_tdms_button.setEnabled(
                not busy
                and self._review is not None
                and not self._closing
                and self.presenter.services.raw_export is not None
            )

    def _edit_selected_channel(self, key: str, text: str) -> None:
        path = self._selected_channel_paths.get(key)
        if path is None:
            return
        value: object = text
        if key in {
            "input_min",
            "input_max",
            "sensitivity",
            "input_offset",
            "converter_sensitivity",
            "converter_current",
            "ni_expected_bias",
            "ni_bias_tolerance",
            "ni_dynamic_min",
            "ni_dynamic_max",
        }:
            try:
                value = float(text)
            except ValueError:
                pass
        elif key == "ni_bias_removed":
            if text.strip().casefold() in {"true", "yes", "1"}:
                value = True
            elif text.strip().casefold() in {"false", "no", "0"}:
                value = False
        elif key == "conditioning_components":
            value = [item.strip() for item in text.split(";") if item.strip()]
        self.draft = self.presenter.update_field(path, value)
        if self.presenter.field_affects_hardware_validation(path):
            self._queue_hardware_refresh()
        self._render_draft()
        self._submit_destination_check()

    def _select_qualification_plan(self, plan_id: str) -> None:
        if not plan_id:
            return
        self.presenter.select_qualification_plan(plan_id)
        self.qualification = QualificationPresentation()
        self._populate_test_table()
        self._update_readiness()

    def _populate_test_table(self) -> None:
        plan = self.presenter.services.qualification_plan
        self.plan_revision_label.setText(f"revision {plan.plan_revision}")
        self.test_table.setRowCount(len(plan.tests))
        for row, definition in enumerate(plan.tests):
            set_described_table_item(
                self.test_table, row, 0, QTableWidgetItem(definition.display_name)
            )
            set_described_table_item(self.test_table, row, 1, QTableWidgetItem(definition.purpose))
            set_described_table_item(
                self.test_table,
                row,
                2,
                QTableWidgetItem("Yes" if definition.required else "No"),
            )
            set_described_table_item(self.test_table, row, 3, QTableWidgetItem("Pending"))

    def _mark_field(self, editor: QWidget, path: str) -> None:
        issue = next((item for item in self.draft.issues if item.field_path == path), None)
        if issue is None:
            editor.setStyleSheet("")
            set_field_notice(editor, None)
        else:
            editor.setStyleSheet("border: 1px solid #b42318")
            set_field_notice(editor, f"{issue.message}\n{issue.remediation}")

    def _edit_sample_rate(self, index: int) -> None:
        if self._closing or index < 0:
            return
        value = self.sample_rate_selector.itemData(index)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return
        self.draft = self.presenter.update_field("acquisition.sample_rate_hz", float(value))
        self.qualification = QualificationPresentation()
        self._queue_hardware_refresh()
        self._render_draft()
        self._submit_destination_check()

    def _edit_field(self, field_path: str, text: str) -> None:
        if self._closing:
            return
        value: object = text
        if field_path in {"acquisition.sample_rate_hz", "acquisition.duration_s"}:
            try:
                value = float(text)
            except ValueError:
                pass
        elif field_path == "acquisition.chunk_size":
            try:
                value = int(text)
            except ValueError:
                pass
        self.draft = self.presenter.update_field(field_path, value)
        if self.presenter.field_affects_hardware_validation(field_path):
            self._queue_hardware_refresh()
        self._render_draft()
        self._submit_destination_check()

    def _choose_destination(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Choose session parent", self.destination_edit.text()
        )
        if selected:
            self.destination_edit.setText(selected)
            self._edit_field("storage.parent_directory", selected)
            self._remember_path(Path(selected), browser=False)

    def _new_destination_identity(self) -> None:
        self.presenter.refresh_destination_identity()
        self._submit_destination_check()

    def _submit_destination_check(self) -> None:
        draft = self.draft
        self.runner.submit(
            "destination",
            lambda: self.presenter.check_destination(draft),
            on_success=self._destination_ready,
            on_failure=self._handle_failure,
        )

    def _destination_ready(self, preview: object) -> None:
        self.destination = cast("SessionDestinationPreview | None", preview)
        if self.destination is None:
            self.destination_preview_label.setText("Configuration must be valid before preview")
        else:
            status = "ready" if self.destination.valid else "blocked"
            self.destination_preview_label.setText(
                f"{self.destination.plan.session_path}  •  {status}  •  "
                f"{self.destination.free_bytes if self.destination.free_bytes is not None else 'unknown'} bytes free"
            )
        self._update_readiness()

    def _submit_discovery(self) -> None:
        self._hardware_refresh_pending = True
        self._hardware_refresh_timer.stop()
        self._run_pending_hardware_refresh()

    def _queue_hardware_refresh(self) -> None:
        self.hardware = HardwarePresentation(unavailable=self.presenter.services.startup_message)
        self._hardware_refresh_pending = True
        if self.draft.normalized is None:
            self.discovery_label.setText("Waiting for valid acquisition settings")
            return
        self.discovery_label.setText("Hardware recheck scheduled")
        self._hardware_refresh_timer.start()

    def _hardware_is_owned(self) -> bool:
        run_active = self._current_status is not None and self._current_status.state.value in {
            "running",
            "stopping",
        }
        monitor_active = self._open_status is not None and self._open_status.active
        return run_active or monitor_active or self.runner.busy("qualification")

    def _run_pending_hardware_refresh(self) -> None:
        if not self._hardware_refresh_pending or self._closing:
            return
        if self.presenter.services.daq is None:
            self.discovery_label.setText("Driver unavailable")
            self.discover_button.setEnabled(False)
            self._update_readiness()
            return
        if self.draft.normalized is None:
            self.discovery_label.setText("Waiting for valid acquisition settings")
            self._update_readiness()
            return
        if self._hardware_is_owned():
            self.discovery_label.setText("Hardware recheck deferred until idle")
            return
        if self.runner.busy("discovery"):
            self.discovery_label.setText("Hardware recheck queued")
            return
        draft = self.draft
        self._hardware_refresh_pending = False
        self.discovery_label.setText("Checking…")
        self.runner.submit(
            "discovery",
            lambda: self.presenter.refresh_hardware(draft),
            on_success=self._hardware_ready,
            on_failure=self._handle_failure,
        )

    def _hardware_ready(self, presentation: object) -> None:
        refreshed = cast("HardwarePresentation", presentation)
        if refreshed.validation_key != self.presenter.hardware_validation_key(self.draft):
            self._hardware_refresh_pending = True
            self.discovery_label.setText("Configuration changed; rechecking hardware")
            self._hardware_refresh_timer.start()
            return
        self.hardware = refreshed
        validation = self.hardware.validation
        self.discovery_label.setText(
            "Compatible"
            if validation is not None and validation.accepted
            else "Configuration mismatch"
        )
        if validation is not None and validation.accepted and self.presenter.services.mode == "ni":
            self._remember_validated_paths()
        self._render_draft()

    def _remember_validated_paths(self) -> None:
        mode = (
            "commissioning"
            if self.draft.normalized is not None
            and self.draft.normalized.experiment_mode.value == "accelerometer_commissioning"
            else "production"
        )
        if not any(
            value is not None
            for value in (
                self._launch_profile_path,
                self._launch_configuration_path,
                self._launch_qualification_plan_path,
            )
        ):
            return
        try:
            if mode == "commissioning":
                self.preferences = replace(
                    self.preferences,
                    commissioning_profile_path=self._launch_profile_path,
                    commissioning_configuration_path=self._launch_configuration_path,
                    commissioning_qualification_plan_path=self._launch_qualification_plan_path,
                )
            else:
                self.preferences = replace(
                    self.preferences,
                    production_profile_path=self._launch_profile_path,
                    production_configuration_path=self._launch_configuration_path,
                    production_qualification_plan_path=self._launch_qualification_plan_path,
                )
            self.presenter.services.preferences.save(self.preferences)
            self.presenter.services.preferences.sync()
        except Exception as error:  # noqa: BLE001 - preferences must not block acquisition
            _LOG.warning("Could not remember validated GUI paths: %s", error)

    def _update_readiness(self) -> None:
        self.open_readiness = self.presenter.evaluate_open_readiness(self.draft, self.hardware)
        if self.functional_preview:
            self._update_functional_readiness()
            return
        self.readiness = self.presenter.evaluate_readiness(
            self.draft, self.destination, self.hardware, self.qualification
        )
        self.diagnostic_readiness = self.presenter.evaluate_raw_diagnostic_readiness(
            self.draft,
            self.destination,
            self.hardware,
        )
        self.readiness_list.clear()
        for issue in self.readiness.issues:
            self.readiness_list.addItem(
                f"{issue.severity.value.upper()} · {issue.field_path} · {issue.message}"
            )
        self.diagnostic_readiness_list.clear()
        open_mode = self.acquisition_mode.currentData() == "open"
        display_readiness = self.open_readiness if open_mode else self.diagnostic_readiness
        if display_readiness.eligible:
            self.diagnostic_readiness_list.addItem(
                "READY ? Raw voltage monitoring is available."
                if open_mode
                else "READY ? Physical authorization will be captured immediately before launch."
            )
        else:
            for issue in display_readiness.issues:
                self.diagnostic_readiness_list.addItem(
                    f"{issue.severity.value.upper()} ? {issue.field_path} ? "
                    f"{issue.message} {issue.remediation}"
                )
        active = self._current_status is not None and self._current_status.state.value in {
            "running",
            "stopping",
        }
        open_active = self._open_status is not None and self._open_status.active
        active = active or open_active
        self.continue_button.setEnabled(
            (
                self.readiness.eligible
                or self.diagnostic_readiness.eligible
                or self.open_readiness.eligible
            )
            and not active
            and not self.runner.busy("qualification")
            and not self._closing
        )
        self.acquisition_start_button.setEnabled(
            self.readiness.eligible
            and self.presenter.services.live is not None
            and not active
            and not self.runner.busy("qualification")
            and not self._closing
        )
        commissioning = (
            self.draft.normalized is not None
            and self.draft.normalized.experiment_mode.value == "accelerometer_commissioning"
        )
        self.diagnostic_start_button.setVisible(commissioning)
        self.diagnostic_start_button.setEnabled(
            commissioning
            and self.diagnostic_readiness.eligible
            and not active
            and not self.runner.busy("qualification")
            and not self._closing
        )
        if hasattr(self, "open_start_button"):
            open_mode = self.acquisition_mode.currentData() == "open"
            self.open_start_button.setEnabled(
                open_mode
                and self.open_readiness.eligible
                and self.presenter.services.open_acquisition is not None
                and not active
                and not self.runner.busy("qualification")
                and not self._closing
            )
            self.open_record_button.setEnabled(False)
            self.open_end_button.setEnabled(False)
            self.open_channel.setEnabled(not active and not self._closing)
            self.acquisition_mode.setEnabled(not active and not self._closing)
        self.qualification_start.setEnabled(
            self.draft.valid
            and self.presenter.services.qualification_runner_factory is not None
            and not self.runner.busy("qualification")
            and not active
            and not self._closing
        )
        self.global_status.setText(
            "Ready for acquisition handoff"
            if self.readiness.eligible
            else f"Not ready · {len(self.readiness.blocking_reasons)} blocking finding(s)"
        )
        qualified_blockers = self.readiness.blocking_reasons
        diagnostic_blockers = self.diagnostic_readiness.blocking_reasons
        if self.acquisition_mode.currentData() == "open" and self.open_readiness.eligible:
            self.global_status.setText("Ready for open raw-voltage monitoring")
            self.global_status.setStyleSheet("color: #188038")
        elif self.acquisition_mode.currentData() == "open":
            self.global_status.setText(
                f"Monitoring blocked ? {len(self.open_readiness.blocking_reasons)} finding(s)"
            )
            self.global_status.setStyleSheet("color: #b42318")
        elif self.readiness.eligible:
            self.global_status.setText("Ready for qualified acquisition")
            self.global_status.setStyleSheet("color: #188038")
        elif commissioning and self.diagnostic_readiness.eligible:
            self.global_status.setText("Raw diagnostic ready; qualified acquisition blocked")
            self.global_status.setStyleSheet("color: #b26a00")
        else:
            self.global_status.setStyleSheet("color: #b42318")
        if self.acquisition_mode.currentData() == "open":
            set_field_notice(
                self.global_status,
                _readiness_tooltip(
                    (),
                    self.open_readiness.blocking_reasons,
                    diagnostic_eligible=self.open_readiness.eligible,
                ),
            )
        else:
            set_field_notice(
                self.global_status,
                _readiness_tooltip(
                    qualified_blockers,
                    diagnostic_blockers if commissioning else (),
                    diagnostic_eligible=commissioning and self.diagnostic_readiness.eligible,
                ),
            )

    def _update_functional_readiness(self) -> None:
        self.readiness = None
        self.diagnostic_readiness = self.presenter.evaluate_raw_diagnostic_readiness(
            self.draft,
            self.destination,
            self.hardware,
        )
        self.readiness_list.clear()
        self.diagnostic_readiness_list.clear()
        open_mode = self.acquisition_mode.currentData() == "open"
        decision = self.open_readiness if open_mode else self.diagnostic_readiness
        assert decision is not None
        display_issues = _functional_display_issues(decision.issues)
        if decision.eligible:
            self.diagnostic_readiness_list.addItem(
                "READY - acquisition settings and hardware are valid."
            )
        else:
            for issue in display_issues:
                self.diagnostic_readiness_list.addItem(
                    f"{issue.severity.value.upper()} - {issue.field_path} - {issue.message}"
                )
        active = self._current_status is not None and self._current_status.state.value in {
            "running",
            "stopping",
        }
        active = active or (self._open_status is not None and self._open_status.active)
        eligible = decision.eligible
        enabled = eligible and not active and not self._closing
        self.continue_button.setEnabled(enabled)
        open_mode = self.acquisition_mode.currentData() == "open"
        self.diagnostic_start_button.setVisible(not open_mode)
        self.diagnostic_start_button.setEnabled(enabled and not open_mode)
        self.acquisition_start_button.setEnabled(False)
        self.qualification_start.setEnabled(False)
        for widget in (
            self.open_channel_label,
            self.open_channel,
            self.open_start_button,
            self.open_record_button,
            self.open_end_button,
        ):
            widget.setVisible(open_mode)
        self.open_start_button.setEnabled(
            enabled and open_mode and self.presenter.services.open_acquisition is not None
        )
        self.open_record_button.setEnabled(False)
        self.open_end_button.setEnabled(False)
        self.open_channel.setEnabled(not active and not self._closing)
        self.acquisition_mode.setEnabled(not active and not self._closing)
        if eligible:
            self.global_status.setText(
                "Ready for open raw-voltage monitoring"
                if open_mode
                else "Ready for unqualified functional acquisition"
            )
            self.global_status.setStyleSheet("color: #b26a00")
        else:
            count = sum(
                issue.severity in {ErrorSeverity.ERROR, ErrorSeverity.CRITICAL}
                for issue in display_issues
            )
            label = "Monitoring" if open_mode else "Functional run"
            self.global_status.setText(f"{label} blocked - {count} required item(s)")
            self.global_status.setStyleSheet("color: #b42318")
        set_field_notice(
            self.global_status,
            _readiness_tooltip(
                (),
                tuple(
                    issue
                    for issue in display_issues
                    if issue.severity in {ErrorSeverity.ERROR, ErrorSeverity.CRITICAL}
                ),
                diagnostic_eligible=eligible,
            ),
        )

    def _required_confirmation_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                confirmation_id
                for test in self.presenter.services.qualification_plan.tests
                for confirmation_id in test.required_confirmation_ids
            )
        )

    def _confirm_and_start_qualification(self) -> None:
        confirmations = self._required_confirmation_ids()
        if confirmations:
            result = QMessageBox.question(
                self,
                "Required synthetic/physical confirmations",
                "Confirm that the following statements have been checked:\n\n"
                + "\n".join(f"• {item}" for item in confirmations)
                + "\n\nThis confirmation is retained in the qualification report.",
            )
            if result is not QMessageBox.StandardButton.Yes:
                return
        draft = self.draft
        self.presenter.prepare_qualification()
        self.qualification_start.setEnabled(False)
        self.qualification_cancel.setEnabled(True)
        self.qualification_progress.setRange(0, 0)
        self.runner.submit(
            "qualification",
            lambda: self.presenter.run_qualification(
                draft,
                self.progress_relay,
                approved_confirmation_ids=confirmations,
            ),
            on_success=self._qualification_ready,
            on_failure=self._handle_qualification_failure,
            cancel_previous=self.presenter.cancel_qualification,
        )

    def _render_progress(self, value: object) -> None:
        progress = cast("QualificationProgress", value)
        self.qualification_status.setText(f"{progress.phase}: {progress.message}")
        if progress.indeterminate:
            self.qualification_progress.setRange(0, 0)
        else:
            self.qualification_progress.setRange(0, max(progress.total_test_count, 1))
            self.qualification_progress.setValue(len(progress.completed_test_ids))
        completed = set(progress.completed_test_ids)
        for row, definition in enumerate(self.presenter.services.qualification_plan.tests):
            status = "Complete" if definition.test_id in completed else "Pending"
            if definition.test_id == progress.current_test_id:
                status = progress.phase.title()
            status_item = self.test_table.item(row, 3)
            if status_item is not None:
                status_item.setText(status)

    def _qualification_ready(self, presentation: object) -> None:
        self.qualification = cast("QualificationPresentation", presentation)
        report = self.qualification.report
        self.qualification_cancel.setEnabled(False)
        self.open_report_button.setEnabled(self.qualification.report_path is not None)
        if report is not None:
            self.qualification_status.setText(
                f"{report.lifecycle.value} · {report.overall_status.value} · "
                f"fingerprint {report.qualification_fingerprint[:12]}…"
            )
            definitions = {
                item.test_id: item for item in self.presenter.services.qualification_plan.tests
            }
            lines: list[str] = []
            for result in report.results:
                lines.append(f"{result.test_id}: {result.status.value} — {result.summary}")
                lines.extend(
                    f"  measurement {metric.name}"
                    f"[{metric.channel_id or 'all'}] = {metric.value} {metric.unit} "
                    f"({metric.evaluation.value})"
                    for metric in result.metrics
                )
                definition = definitions.get(result.test_id)
                if definition is not None:
                    lines.extend(
                        f"  limit {limit.limit_id}: {limit.comparison.value} "
                        f"{limit.value if limit.value is not None else 'unresolved'} "
                        f"{limit.unit} → {limit.consequence.value}"
                        for limit in definition.limits
                    )
            self.report_viewer.setPlainText("\n".join(lines))
        self._submit_discovery()
        self._update_readiness()

    def _handle_qualification_failure(self, error: BaseException) -> None:
        self.qualification_cancel.setEnabled(False)
        self.qualification_progress.setRange(0, 1)
        self._handle_failure(error)
        self._update_readiness()

    def _open_report_folder(self) -> None:
        if self.qualification.report_path is not None:
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self.qualification.report_path.parent.absolute()))
            )

    def _choose_browser_root(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Choose session catalog root", self.browser_root.text()
        )
        if selected:
            self.browser_root.setText(selected)
            self._remember_path(Path(selected), browser=True)
            self._scan_sessions()

    def _scan_sessions(self) -> None:
        root = Path(self.browser_root.text())
        self.runner.submit(
            "catalog",
            lambda: self.presenter.scan_sessions(root),
            on_success=self._catalog_ready,
            on_failure=self._handle_failure,
        )

    def _catalog_ready(self, catalog_value: object) -> None:
        catalog = cast("SessionCatalog", catalog_value)
        self._catalog_entries = catalog.entries
        self.session_table.setRowCount(len(catalog.entries))
        for row, entry in enumerate(catalog.entries):
            summary = entry.summary
            if summary is None:
                values = (entry.session_path.name, "—", "Invalid", "Invalid", "Unknown")
            else:
                values = (
                    summary.experiment_name,
                    summary.started_at.isoformat() if summary.started_at else "Unknown",
                    summary.completion_state.value,
                    summary.integrity.overall.value,
                    summary.session_type.value,
                )
            for column, value in enumerate(values):
                set_described_table_item(
                    self.session_table, row, column, QTableWidgetItem(str(value))
                )
        if catalog.entries:
            self.session_table.selectRow(0)

    def _render_session_details(self) -> None:
        row = self.session_table.currentRow()
        if row < 0 or row >= len(self._catalog_entries):
            self.session_review_button.setEnabled(False)
            return
        entry = self._catalog_entries[row]
        summary = entry.summary
        if summary is None:
            self.session_review_button.setEnabled(False)
            self.session_details.setPlainText(
                f"Invalid catalog entry\n{entry.error_code}\n{entry.error_message}\n"
                f"{entry.remediation or ''}\n\n{entry.session_path}"
            )
            return
        self.session_review_button.setEnabled(
            self.presenter.services.review is not None and not self._closing
        )
        hardware = summary.hardware
        recipe = summary.recipe
        qualification = summary.qualification
        channel_lines = [
            f"  {item.channel_id}: {item.physical_channel or 'unmapped'} · "
            f"sensor {item.sensor_model or 'unknown'} / "
            f"{item.sensor_serial_number or 'unknown'} · "
            f"calibration {item.calibration_id or 'none'}"
            + (f" · {item.warning}" if item.warning else "")
            for item in summary.channels
        ]
        self.session_details.setPlainText(
            "\n".join(
                [
                    f"Session: {summary.session_id}",
                    f"Experiment: {summary.experiment_name}",
                    (
                        "RAW DIAGNOSTIC: immutable volts only; no calibrated measurement release."
                        if summary.session_type.value == "raw-diagnostic"
                        else "COMMISSIONING: X/Y/Z only; ai3 is UNPOWERED / UNQUALIFIED / "
                        "NOT CHARGE; production qualification incomplete."
                        if summary.experiment_mode == "accelerometer_commissioning"
                        else f"Experiment mode: {summary.experiment_mode}"
                    ),
                    f"Specimen: {summary.sample_id or 'not recorded'}",
                    f"Samples: {summary.common_sample_count}",
                    f"Hardware: {hardware.device_id if hardware else 'unknown'} / "
                    f"{hardware.module_id if hardware else 'unknown'}",
                    f"Recipe: {recipe.recipe_id if recipe else 'unknown'} "
                    f"{recipe.recipe_version if recipe else ''}",
                    f"Qualification: {qualification.report_id if qualification else 'none'}",
                    "Channels:",
                    *channel_lines,
                    f"Warnings: {summary.warning_count}",
                    f"Faults: {summary.fault_count}",
                ]
            )
        )

    def _continue_to_acquisition(self) -> None:
        if self.acquisition_mode.currentData() == "open":
            if self.open_readiness is not None and self.open_readiness.eligible:
                self.navigation.setCurrentRow(2)
        elif self.readiness is not None and self.readiness.eligible:
            self._request_launch()
        elif self.diagnostic_readiness is not None and self.diagnostic_readiness.eligible:
            self.navigation.setCurrentRow(2)

    def _request_launch(self) -> None:
        if self.destination is None:
            return
        draft = self.draft
        destination = self.destination
        qualification = self.qualification
        self.navigation.setCurrentRow(2)
        self.runner.submit(
            "launch",
            lambda: self._launch_in_background(draft, destination, qualification),
            on_success=self._acquisition_started,
            on_failure=self._handle_failure,
        )

    def _request_raw_diagnostic(self) -> None:
        if (
            self.destination is None
            or self.draft.normalized is None
            or self.runner.busy("qualification")
        ):
            return
        dialog = RawDiagnosticAuthorizationDialog(
            self.draft.normalized.session.operator,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.confirmation is None:
            return
        draft = self.draft
        destination = self.destination
        confirmation = dialog.confirmation
        self.navigation.setCurrentRow(2)
        self.runner.submit(
            "diagnostic-launch",
            lambda: self._raw_diagnostic_launch_in_background(
                draft,
                destination,
                confirmation,
            ),
            on_success=self._diagnostic_acquisition_started,
            on_failure=self._handle_failure,
        )

    def _raw_diagnostic_launch_in_background(
        self,
        draft: ConfigurationDraftSnapshot,
        destination: SessionDestinationPreview,
        confirmation: RawDiagnosticConfirmation,
    ) -> tuple[
        RawDiagnosticLaunchIntent,
        HardwarePresentation,
        DiagnosticReadinessDecision,
        OperatorRunStatus,
    ]:
        intent, hardware, readiness = self.presenter.request_raw_diagnostic_launch(
            draft,
            destination,
            confirmation,
        )
        status = self.presenter.start_acquisition(intent)
        return intent, hardware, readiness, status

    def _request_functional_preview(self) -> None:
        if self.destination is None or self.draft.normalized is None:
            return
        draft = self.draft
        destination = self.destination
        self.navigation.setCurrentRow(2)
        self.runner.submit(
            "functional-launch",
            lambda: self._functional_launch_in_background(draft, destination),
            on_success=self._functional_acquisition_started,
            on_failure=self._handle_failure,
        )

    def _functional_launch_in_background(
        self,
        draft: ConfigurationDraftSnapshot,
        destination: SessionDestinationPreview,
    ) -> tuple[
        FunctionalPreviewLaunchIntent,
        HardwarePresentation,
        DiagnosticReadinessDecision,
        OperatorRunStatus,
    ]:
        intent, hardware, readiness = self.presenter.request_functional_preview_launch(
            draft,
            destination,
        )
        status = self.presenter.start_acquisition(intent)
        return intent, hardware, readiness, status

    def _launch_in_background(
        self,
        draft: ConfigurationDraftSnapshot,
        destination: SessionDestinationPreview,
        qualification: QualificationPresentation,
    ) -> tuple[object, HardwarePresentation, ReadinessDecision, OperatorRunStatus]:
        intent, hardware, readiness = self.presenter.request_launch(
            draft,
            destination,
            qualification,
        )
        status = self.presenter.start_acquisition(intent)
        return intent, hardware, readiness, status

    def _acquisition_started(self, value: object) -> None:
        intent, hardware, readiness, status = cast(
            "tuple[object, HardwarePresentation, ReadinessDecision, OperatorRunStatus]",
            value,
        )
        self.hardware = hardware
        self.readiness = readiness
        self._current_status = status
        self._current_summary = None
        self._active_raw_diagnostic = False
        self._configure_live_presentation(raw_diagnostic=False)
        self._terminal_wait_submitted = False
        self.acquisitionLaunchRequested.emit(intent)
        self._render_run_status(status)
        self.statusBar().showMessage(
            f"Acquisition started; recording session {status.session_id} is active.",
            10_000,
        )

    def _diagnostic_acquisition_started(self, value: object) -> None:
        intent, hardware, readiness, status = cast(
            "tuple[RawDiagnosticLaunchIntent, HardwarePresentation, "
            "DiagnosticReadinessDecision, OperatorRunStatus]",
            value,
        )
        self.hardware = hardware
        self.diagnostic_readiness = readiness
        self._current_status = status
        self._current_summary = None
        self._terminal_wait_submitted = False
        self._active_raw_diagnostic = True
        self._configure_live_presentation(raw_diagnostic=True)
        self.acquisitionLaunchRequested.emit(intent)
        self._render_run_status(status)
        self.statusBar().showMessage(
            f"RAW DIAGNOSTIC started; session {status.session_id} records volts only.",
            10_000,
        )

    def _functional_acquisition_started(self, value: object) -> None:
        intent, hardware, readiness, status = cast(
            "tuple[FunctionalPreviewLaunchIntent, HardwarePresentation, "
            "DiagnosticReadinessDecision, OperatorRunStatus]",
            value,
        )
        self.hardware = hardware
        self.diagnostic_readiness = readiness
        self._current_status = status
        self._current_summary = None
        self._terminal_wait_submitted = False
        self._active_raw_diagnostic = False
        self._configure_live_presentation(raw_diagnostic=False)
        self.acquisitionLaunchRequested.emit(intent)
        self._render_run_status(status)
        self.statusBar().showMessage(
            f"UNQUALIFIED FUNCTIONAL RUN started; session {status.session_id} is active.",
            10_000,
        )

    def _configure_live_presentation(self, *, raw_diagnostic: bool) -> None:
        self.conditioned_live_plot.setVisible(not raw_diagnostic)
        self.live_metrics_table.setVisible(not raw_diagnostic)
        if raw_diagnostic:
            set_field_notice(
                self.conditioned_live_plot,
                "Unavailable: raw diagnostic sessions do not authorize derived processing.",
            )
            self.live_metrics_table.setRowCount(0)
        else:
            set_field_notice(self.conditioned_live_plot, None)

    def _acquisition_mode_changed(self) -> None:
        if not hasattr(self, "acquisition_mode"):
            return
        open_mode = self.acquisition_mode.currentData() == "open"
        for widget in (
            self.open_channel_label,
            self.open_channel,
            self.open_start_button,
            self.open_record_button,
            self.open_end_button,
        ):
            widget.setVisible(open_mode)
        for widget in (
            self.diagnostic_start_button,
            self.acquisition_start_button,
            self.acquisition_stop_button,
        ):
            widget.setVisible(not open_mode)
        if self.functional_preview:
            self.acquisition_start_button.setVisible(False)
        self.conditioned_live_plot.setVisible(not open_mode and not self._active_raw_diagnostic)
        self.live_metrics_table.setVisible(not open_mode and not self._active_raw_diagnostic)
        self._update_readiness()

    def _request_open_monitor(self) -> None:
        if self.draft.normalized is None or self.presenter.services.open_acquisition is None:
            return
        draft = self.draft
        self.runner.submit(
            "open-monitor-start",
            lambda: self._start_open_monitor_in_background(draft),
            on_success=self._open_monitor_started,
            on_failure=self._handle_failure,
        )

    def _start_open_monitor_in_background(
        self, draft: ConfigurationDraftSnapshot
    ) -> tuple[
        OpenAcquisitionIntent,
        HardwarePresentation,
        DiagnosticReadinessDecision,
        OpenAcquisitionStatus,
    ]:
        intent, hardware, readiness = self.presenter.request_open_acquisition_launch(draft)
        return intent, hardware, readiness, self.presenter.start_monitoring(intent)

    def _open_monitor_started(self, value: object) -> None:
        _, hardware, readiness, status = cast(
            "tuple[OpenAcquisitionIntent, HardwarePresentation, "
            "DiagnosticReadinessDecision, OpenAcquisitionStatus]",
            value,
        )
        self.hardware = hardware
        self.open_readiness = readiness
        self._open_status = status
        self._open_record_wait_submitted = False
        self._active_raw_diagnostic = True
        self._configure_live_presentation(raw_diagnostic=True)
        self._render_open_status(status)
        self.statusBar().showMessage(
            f"Monitoring {len(status.ordered_channel_ids)} channels as raw volts; "
            "no samples are recorded yet.",
            10_000,
        )

    def _toggle_open_recording(self) -> None:
        status = self.presenter.open_acquisition_status()
        if status is None:
            return
        if status.recording_state.value == "inactive":
            destination = self.destination_edit.text().strip()
            if not destination:
                destination = QFileDialog.getExistingDirectory(self, "Choose recording parent")
                if not destination:
                    return
                self.destination_edit.setText(destination)
            parent_directory = Path(destination)
            self.runner.submit(
                "open-record",
                lambda: self.presenter.start_open_recording(parent_directory),
                on_success=lambda value: self._render_open_status(
                    cast("OpenAcquisitionStatus", value)
                ),
                on_failure=self._handle_failure,
            )
            return
        if status.recording_state.value in {"preparing", "recording"}:
            self._open_record_wait_submitted = True

            def stop_and_wait() -> object:
                self.presenter.stop_open_recording()
                return self.presenter.wait_for_open_recording()

            self.runner.submit(
                "open-record-stop",
                stop_and_wait,
                on_success=self._open_recording_finalized,
                on_failure=self._handle_failure,
            )

    def _open_recording_finalized(self, value: object) -> None:
        self._open_record_wait_submitted = False
        recorded = cast("RecordedSessionCompletion | None", value)
        status = self.presenter.open_acquisition_status()
        if status is not None:
            self._open_status = status
            self._render_open_status(status)
        if recorded is not None:
            self.statusBar().showMessage(
                f"Recording finalized at {recorded.session_path}; monitoring continues.",
                10_000,
            )
        if self.browser_root.text().strip():
            self._scan_sessions()

    def _request_end_monitoring(self) -> None:
        if self.runner.busy("open-monitor-stop"):
            return

        def end_and_wait() -> object:
            self.presenter.end_monitoring()
            return self.presenter.wait_for_monitor_terminal()

        self.runner.submit(
            "open-monitor-stop",
            end_and_wait,
            on_success=lambda value: self._render_open_status(cast("OpenAcquisitionStatus", value)),
            on_failure=self._handle_failure,
        )

    def _render_open_status(self, status: OpenAcquisitionStatus) -> None:
        self._open_status = status
        self.acquisition_state_label.setText(
            f"monitor {status.monitor_state.value} / recording {status.recording_state.value}"
        )
        self.acquisition_elapsed_label.setText(f"{status.elapsed_seconds:.3f} s")
        self.acquisition_counts_label.setText(
            f"monitored {status.acquired_samples_per_channel} / "
            f"segment persisted {status.recording_persisted_samples}"
        )
        fault = f" / storage {status.storage_fault_code}" if status.storage_fault_code else ""
        if status.monitor_fault is not None:
            fault += f" / acquisition {status.monitor_fault.code}: {status.monitor_fault.message}"
        self.acquisition_warning_label.setText(
            f"display drops {status.display_frames_dropped}{fault}"
        )
        active_path = status.recording_session_path or (
            status.last_recording.session_path if status.last_recording else None
        )
        self.acquisition_identity_label.setText(
            f"Monitor {status.monitor_run_id or 'not allocated'} | "
            + ", ".join(
                f"{channel_id} [{unit}]"
                for channel_id, unit in zip(
                    status.ordered_channel_ids,
                    status.ordered_channel_units,
                    strict=True,
                )
            )
            + f" | requested {status.requested_sample_rate_hz or 0:g} Hz / "
            f"actual {status.actual_sample_rate_hz or 0:g} Hz | "
            f"{active_path or 'no recording yet'}"
        )
        active = status.active
        monitoring = status.monitor_state.value == "monitoring"
        recording = status.recording_state.value in {"preparing", "recording"}
        self.acquisition_mode.setEnabled(not active and not self._closing)
        self.open_channel.setEnabled(not active and not self._closing)
        self.channel_table.setEnabled(not active and not self._closing)
        self.open_start_button.setEnabled(
            not active
            and self.open_readiness is not None
            and self.open_readiness.eligible
            and self.presenter.services.open_acquisition is not None
            and not self._closing
        )
        self.open_record_button.setText("Stop recording" if recording else "Record")
        self.open_record_button.setEnabled(
            monitoring
            and status.recording_state.value in {"inactive", "preparing", "recording"}
            and not self._closing
        )
        self.open_end_button.setEnabled(monitoring and not self._closing)
        if (
            not active
            and self._hardware_refresh_pending
            and not self._hardware_refresh_timer.isActive()
        ):
            self._hardware_refresh_timer.start()

    def _request_stop(self) -> None:
        if self.presenter.services.live is None or self.runner.busy("stop"):
            return
        self.acquisition_stop_button.setEnabled(False)
        self.runner.submit(
            "stop",
            self.presenter.stop_acquisition,
            on_success=lambda value: self._render_run_status(cast("OperatorRunStatus", value)),
            on_failure=self._handle_failure,
        )

    def _poll_live(self) -> None:
        if self._closing:
            return
        if self.acquisition_mode.currentData() == "open" or (
            self._open_status is not None and self._open_status.active
        ):
            try:
                open_status = self.presenter.open_acquisition_status()
            except BaseException as error:
                self._handle_failure(error)
                return
            if open_status is not None:
                self._render_open_status(open_status)
                if (
                    open_status.monitor_state.value == "monitoring"
                    and not self.pause_display.isChecked()
                    and not self.runner.busy("open-live-frame")
                ):
                    self.runner.submit(
                        "open-live-frame",
                        self.presenter.open_acquisition_frame,
                        on_success=lambda value: _set_live_frame(
                            self.raw_live_plot,
                            cast("LivePlotFrame | None", value),
                        ),
                        on_failure=self._handle_failure,
                    )
            return
        if self.presenter.services.live is None:
            return
        try:
            status = self.presenter.acquisition_status()
        except BaseException as error:
            self._handle_failure(error)
            return
        if status is None:
            return
        self._current_status = status
        self._render_run_status(status)
        if status.terminal and not self._terminal_wait_submitted:
            self._terminal_wait_submitted = True
            self.runner.submit(
                "finalization",
                self.presenter.wait_for_acquisition,
                on_success=self._acquisition_finalized,
                on_failure=self._handle_failure,
            )
        if (
            status.state.value not in {"running", "stopping"}
            or self.pause_display.isChecked()
            or self.runner.busy("live-frame")
        ):
            return
        self.runner.submit(
            "live-frame",
            lambda: (
                self.presenter.live_frame(SignalStage.RAW),
                self.presenter.live_frame(SignalStage.CONDITIONED),
                self.presenter.live_metrics(),
            ),
            on_success=self._live_snapshot_ready,
            on_failure=self._handle_failure,
        )

    def _render_run_status(self, status: OperatorRunStatus) -> None:
        self.acquisition_state_label.setText(
            f"{status.state.value} / "
            f"{status.finalization_state.value if status.finalization_state else 'not finalized'}"
        )
        self.acquisition_elapsed_label.setText(f"{status.elapsed_seconds:.3f} s")
        lag = max(
            0,
            status.acquired_samples_per_channel - status.persisted_samples_per_channel,
        )
        loss = (
            "pending terminal evidence"
            if status.persistence_loss_samples is None
            else str(status.persistence_loss_samples)
        )
        self.acquisition_counts_label.setText(
            f"acquired {status.acquired_samples_per_channel} / "
            f"persisted {status.persisted_samples_per_channel} / queue lag {lag} / loss {loss}"
        )
        degradation = f" / {status.display_degradation.code}" if status.display_degradation else ""
        self.acquisition_warning_label.setText(
            f"warnings {status.warning_count} / gaps {status.acquisition_gap_count} / "
            f"display drops {status.display_frames_dropped}{degradation}"
        )
        duration = (
            f"{status.requested_duration_seconds:g} s"
            if status.requested_duration_seconds is not None
            else "continuous"
        )
        actual_duration = (
            status.acquired_samples_per_channel / status.actual_sample_rate_hz
            if status.actual_sample_rate_hz
            else 0.0
        )
        self.acquisition_identity_label.setText(
            f"Session {status.session_id or 'not allocated'} | {status.session_path or 'no path'} | "
            f"requested {status.requested_sample_rate_hz or 0:g} Hz / "
            f"actual {status.actual_sample_rate_hz or 0:g} Hz | "
            f"requested duration {duration} / actual {actual_duration:.9g} s | "
            + ", ".join(
                f"{channel_id} [{unit}]"
                for channel_id, unit in zip(
                    status.ordered_channel_ids,
                    status.ordered_channel_units,
                    strict=True,
                )
            )
        )
        self.recording_queue.setRange(0, max(1, status.recording_queue_capacity))
        self.recording_queue.setValue(status.recording_queue_occupancy)
        self.display_queue.setRange(0, max(1, status.display_queue_capacity))
        self.display_queue.setValue(status.display_queue_occupancy)
        active = status.state.value in {"running", "stopping"}
        self.acquisition_stop_button.setEnabled(
            active and status.state.value == "running" and not self._closing
        )
        self.acquisition_start_button.setEnabled(
            not active
            and self.readiness is not None
            and self.readiness.eligible
            and not self._closing
        )
        self.diagnostic_start_button.setEnabled(
            not active
            and self.diagnostic_readiness is not None
            and self.diagnostic_readiness.eligible
            and not self._closing
        )

    def _live_snapshot_ready(self, value: object) -> None:
        raw, conditioned, metrics = cast(
            "tuple[LivePlotFrame | None, LivePlotFrame | None, LiveMetricSnapshot | None]",
            value,
        )
        raw_panel = _signal_plot_panel(self.raw_live_plot)
        conditioned_panel = _signal_plot_panel(self.conditioned_live_plot)
        if raw_panel is not None:
            raw_panel.set_live_frame(raw)
        if conditioned_panel is not None:
            conditioned_panel.set_live_frame(conditioned)
        if metrics is not None:
            self._render_live_metrics(metrics)

    def _render_live_metrics(self, snapshot: LiveMetricSnapshot) -> None:
        self.live_metrics_table.setRowCount(len(snapshot.metrics))
        for row, metric in enumerate(snapshot.metrics):
            values = (
                metric.channel_id,
                metric.unit.value,
                _metric_text(metric.rms),
                _metric_text(metric.absolute_peak),
                _metric_text(metric.peak_to_peak),
                _metric_text(metric.crest_factor),
                _metric_text(metric.clipping_fraction),
            )
            for column, value in enumerate(values):
                set_described_table_item(
                    self.live_metrics_table, row, column, QTableWidgetItem(value)
                )
        suffix = " (stale)" if snapshot.stale else ""
        set_field_notice(
            self.live_metrics_table,
            f"{snapshot.window_seconds:g} s conditioned window; "
            f"{snapshot.effective_bandwidth}{suffix}",
        )

    def _acquisition_finalized(self, value: object) -> None:
        summary = cast("OperatorRunSummary", value)
        self._current_summary = summary
        status = self.presenter.acquisition_status()
        if status is not None:
            self._render_run_status(status)
        self.statusBar().showMessage(
            f"Session finalized: {summary.recorded.state.value}; "
            f"{summary.recorded.persisted_samples_per_channel} samples/channel.",
            15_000,
        )
        self._load_review(summary.recorded.session_path, automatic=True)
        if summary.recorded.fault is not None or (
            summary.recorded.acquisition_completion is not None
            and summary.recorded.acquisition_completion.fault is not None
        ):
            self._show_run_fault(summary)
        if self._close_after_finalization:
            self._force_close = True
            self.close()
        elif self._hardware_refresh_pending:
            self._hardware_refresh_timer.start()

    def _review_selected_session(self) -> None:
        row = self.session_table.currentRow()
        if row < 0 or row >= len(self._catalog_entries):
            return
        entry = self._catalog_entries[row]
        if entry.summary is not None:
            self._load_review(entry.session_path, automatic=False)

    def _load_review(self, path: Path, *, automatic: bool) -> None:
        if self.presenter.services.review is None:
            return
        if automatic:
            self.navigation.setCurrentRow(3)
        self.runner.submit(
            "review",
            lambda: self.presenter.load_session_review(path),
            on_success=lambda value: self._review_ready(value, automatic=automatic),
            on_failure=self._handle_failure,
        )

    def _review_ready(self, value: object, *, automatic: bool = False) -> None:
        review = cast("SessionReviewSnapshot", value)
        self._review = review
        self._review_path = review.summary.session_path
        self._transient_result = None
        self.review_detected_events.setRowCount(0)
        self.review_extrema_table.setRowCount(0)
        self.review_detection_status.setText("No waveform events detected")
        self.review_start_seconds.clear()
        self.review_stop_seconds.clear()
        review_plot_panel = _signal_plot_panel(self.review_plot)
        if review_plot_panel is not None:
            review_plot_panel.clear_selection_range()
        raw_diagnostic = review.summary.session_type.value == "raw-diagnostic"
        raw_stage_index = self.review_signal_stage.findData(SignalStage.RAW.value)
        self.review_signal_stage.setCurrentIndex(raw_stage_index)
        self.review_signal_stage.setEnabled(not raw_diagnostic)
        self.review_analysis_tabs.setTabEnabled(0, True)
        self.review_analysis_tabs.setTabEnabled(1, True)
        self.review_representation.setEnabled(True)
        for representation in (
            PlotRepresentation.CALIBRATED_TIME,
            PlotRepresentation.FILTERED_TIME,
        ):
            index = self.review_representation.findData(representation.value)
            model = cast("QStandardItemModel", self.review_representation.model())
            model.item(index).setEnabled(not raw_diagnostic)
        if raw_diagnostic:
            raw_index = self.review_representation.findData(PlotRepresentation.RAW_TIME.value)
            self.review_representation.setCurrentIndex(raw_index)
            set_field_notice(
                self.review_representation,
                "RAW DIAGNOSTIC: time history, FFT, and PSD use raw voltage only.",
            )
        else:
            self.review_representation.setEnabled(True)
            set_field_notice(self.review_representation, None)
        self.review_channels.blockSignals(True)
        self.review_channels.clear()
        for channel in review.summary.channels:
            diagnostic = channel.processing_disposition == "raw_only_diagnostic"
            item = QListWidgetItem(
                f"{channel.display_name} ({channel.channel_id}) "
                f"[{channel.raw_unit} -> {channel.engineering_unit}]"
                + (" — RAW VOLTAGE ONLY" if diagnostic else "")
            )
            item.setData(Qt.ItemDataRole.UserRole, channel.channel_id)
            item.setData(int(Qt.ItemDataRole.UserRole) + 1, diagnostic)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.review_channels.addItem(item)
        self.review_channels.blockSignals(False)
        self._mark_review_results_stale(detection=False)
        self.review_events.setPlainText(
            "\n".join(
                f"{event.timestamp.isoformat()} [{event.severity}] {event.code} {event.message}"
                for event in review.events
            )
            or "No persisted events."
        )
        artifact_lines = [
            f"Session {review.summary.session_id}",
            f"Path: {review.summary.session_path}",
            f"Experiment / operator: {review.summary.experiment_name} / "
            f"{review.summary.operator or 'not recorded'}",
            (
                "RAW DIAGNOSTIC SESSION: immutable volts only; no calibrated measurement release."
                if raw_diagnostic
                else "COMMISSIONING SESSION: X/Y/Z only; ai3 is UNPOWERED / UNQUALIFIED / "
                "NOT CHARGE; production qualification is incomplete."
                if review.summary.experiment_mode == "accelerometer_commissioning"
                else f"Experiment mode: {review.summary.experiment_mode}"
            ),
            f"Completion: {review.summary.completion_state.value} / "
            f"{review.summary.completion_reason.value if review.summary.completion_reason else 'unknown'}",
            f"Start / end: {review.summary.started_at or 'unknown'} / "
            f"{review.summary.ended_at or 'unknown'}",
            f"Requested / acquired duration: "
            f"{review.summary.requested_duration_seconds or 'continuous'} / "
            f"{review.summary.acquired_duration_seconds:g} s",
            f"Sample rate: {review.summary.actual_sample_rate_hz:g} Hz",
            "Channels: "
            + ", ".join(
                f"{channel_id} [{unit}]"
                for channel_id, unit in zip(
                    review.summary.ordered_channel_ids,
                    review.summary.ordered_channel_units,
                    strict=True,
                )
            ),
            f"Acquired / persisted: {review.summary.acquired_samples_per_channel} / "
            f"{review.summary.persisted_samples_per_channel}",
            f"Gaps: {review.summary.acquisition_gap_count}; "
            f"persistence loss: {review.summary.persistence_loss_samples}; "
            f"display drops: {review.summary.display_frames_dropped}",
            f"Warnings / faults: {review.summary.warning_count} / {review.summary.fault_count}",
            f"Integrity: {review.summary.integrity.overall.value}",
            f"Hardware profile: {review.summary.hardware_profile_id}",
            f"Qualification: "
            f"{review.summary.qualification.report_id if review.summary.qualification else 'none'}",
            f"Recipe: {review.summary.recipe.recipe_id if review.summary.recipe else 'none'}",
            f"Source artifacts manifest/event/raw: {review.summary.manifest_available}/"
            f"{review.summary.event_log_available}/{review.summary.raw_data_available}",
            "Export history:",
        ]
        artifact_lines.extend(
            (
                f"  {entry.manifest_path.name}: "
                f"{entry.manifest.final_state.value if entry.valid and entry.manifest else entry.error_code}"
            )
            for entry in review.export_history
        )
        self.review_artifacts.setPlainText("\n".join(artifact_lines))
        for button in (
            self.review_plot_button,
            self.review_analyze_button,
            self.review_detect_button,
            self.export_csv_button,
            self.export_png_button,
            self.export_svg_button,
        ):
            button.setEnabled(True)
        self.export_tdms_button.setEnabled(self.presenter.services.raw_export is not None)
        if automatic:
            raw_index = self.review_representation.findData(PlotRepresentation.RAW_TIME.value)
            self.review_representation.setCurrentIndex(raw_index)
            self.review_signal_stage.setCurrentIndex(raw_stage_index)
            self.review_start_seconds.clear()
            self.review_stop_seconds.clear()
            if (
                review.summary.raw_data_available
                and review.summary.persisted_samples_per_channel > 0
                and self.review_channels.count() > 0
            ):
                self._request_review_plot()
            else:
                self.review_plot_metadata.setText(
                    "No persisted raw samples are available for automatic plotting."
                )

    def _selected_review_channels(
        self, representation: PlotRepresentation | None = None
    ) -> tuple[str, ...]:
        if representation is None:
            representation = PlotRepresentation(str(self.review_representation.currentData()))
        includes_diagnostic = representation in {
            PlotRepresentation.RAW_TIME,
            PlotRepresentation.FFT_AMPLITUDE,
            PlotRepresentation.PSD,
        }
        selected = tuple(
            str(item.data(Qt.ItemDataRole.UserRole))
            for index in range(self.review_channels.count())
            if (item := self.review_channels.item(index)).checkState() == Qt.CheckState.Checked
            and (includes_diagnostic or not bool(item.data(int(Qt.ItemDataRole.UserRole) + 1)))
        )
        return selected

    def _review_stage(self) -> SignalStage:
        return SignalStage(str(self.review_signal_stage.currentData()))

    def _review_time_representation(self) -> PlotRepresentation:
        return {
            SignalStage.RAW: PlotRepresentation.RAW_TIME,
            SignalStage.CALIBRATED: PlotRepresentation.CALIBRATED_TIME,
            SignalStage.CONDITIONED: PlotRepresentation.FILTERED_TIME,
        }[self._review_stage()]

    def _review_stage_changed(self) -> None:
        if not hasattr(self, "review_representation"):
            return
        representation = self._review_time_representation()
        index = self.review_representation.findData(representation.value)
        if index >= 0:
            self.review_representation.setCurrentIndex(index)
        self._mark_review_results_stale(detection=True)

    def _review_representation_changed(self) -> None:
        representation = PlotRepresentation(str(self.review_representation.currentData()))
        stage = {
            PlotRepresentation.RAW_TIME: SignalStage.RAW,
            PlotRepresentation.CALIBRATED_TIME: SignalStage.CALIBRATED,
            PlotRepresentation.FILTERED_TIME: SignalStage.CONDITIONED,
        }.get(representation)
        if stage is None or self._review_stage() is stage:
            return
        self.review_signal_stage.setCurrentIndex(self.review_signal_stage.findData(stage.value))

    def _review_channel_selection_changed(self) -> None:
        self._mark_review_results_stale(detection=True)

    def _review_detector_inputs_changed(self) -> None:
        self._mark_review_results_stale(detection=True)

    def _review_selection_inputs_changed(self) -> None:
        self._mark_review_results_stale(detection=False)

    def _mark_review_results_stale(self, *, detection: bool) -> None:
        if not hasattr(self, "review_extrema_table"):
            return
        diagnostic_selected = any(
            item.checkState() == Qt.CheckState.Checked
            and bool(item.data(int(Qt.ItemDataRole.UserRole) + 1))
            for index in range(self.review_channels.count())
            if (item := self.review_channels.item(index)) is not None
        )
        self.review_channel_notice.setText(
            "Raw-only channels use raw voltage for FFT/PSD. "
            "They are excluded from calibrated/conditioned time plots, extrema, and detection."
            if diagnostic_selected and self._review_stage() is not SignalStage.RAW
            else "Raw-only channels use raw voltage for FFT/PSD."
            if diagnostic_selected
            else ""
        )
        if self.review_extrema_table.rowCount():
            set_field_notice(
                self.review_extrema_table,
                "Results are stale; analyze the selection again.",
            )
        for plot in (self.review_fft_plot, self.review_psd_plot):
            panel = _signal_plot_panel(plot)
            if panel is not None:
                panel.coordinate_label.setText("Results are stale; analyze the selection again.")
                set_field_notice(
                    panel.coordinate_label,
                    "Results are stale; analyze the selection again.",
                )
        if detection and self._transient_result is not None:
            self.review_detection_status.setText(
                "Detected event suggestions are stale; run detection again."
            )

    def _review_cursor_changed(self, start: float, stop: float) -> None:
        self.review_start_seconds.setText(f"{start:.9g}")
        self.review_stop_seconds.setText(f"{stop:.9g}")
        self._mark_review_results_stale(detection=False)

    def _review_detector_settings(self) -> TransientDetectionSettings:
        return TransientDetectionSettings(
            mad_multiplier=self.review_mad_multiplier.value(),
            pre_padding_seconds=self.review_pre_padding.value(),
            post_padding_seconds=self.review_post_padding.value(),
            merge_gap_seconds=self.review_merge_gap.value(),
        )

    def _review_analysis_tab_changed(self, index: int) -> None:
        representation = {
            0: PlotRepresentation.FFT_AMPLITUDE,
            1: PlotRepresentation.PSD,
        }.get(index)
        if representation is None or not self.review_representation.isEnabled():
            return
        selected = self.review_representation.findData(representation.value)
        if selected >= 0:
            self.review_representation.setCurrentIndex(selected)

    def _review_seconds(self) -> tuple[float | None, float | None]:
        start_text = self.review_start_seconds.text().strip()
        stop_text = self.review_stop_seconds.text().strip()
        return (
            float(start_text) if start_text else None,
            float(stop_text) if stop_text else None,
        )

    def _request_transient_detection(self) -> None:
        if self._review_path is None or self.runner.busy("review-detect"):
            return
        channels = self._selected_review_channels(self._review_time_representation())
        if not channels:
            self.statusBar().showMessage("Select at least one detection channel.")
            return
        path = self._review_path
        stage = self._review_stage()
        settings = self._review_detector_settings()
        self._review_detection_cancel.clear()
        self.review_detection_status.setText(
            "Scanning the verified session for waveform transients..."
        )
        self.runner.submit(
            "review-detect",
            lambda: self.presenter.detect_review_transients(
                path,
                channels,
                stage,
                settings,
                cancelled=self._review_detection_cancel.is_set,
            ),
            on_success=self._transient_detection_ready,
            on_failure=self._transient_detection_failed,
            cancel_previous=self._review_detection_cancel.set,
        )

    def _transient_detection_ready(self, value: object) -> None:
        result = cast("TransientDetectionResult", value)
        self._transient_result = result
        self.review_detected_events.setRowCount(len(result.events))
        for row, event in enumerate(result.events):
            start = event.analysis_range.start_sample_index / result.sample_rate_hz
            stop = event.analysis_range.stop_sample_index / result.sample_rate_hz
            score = (
                "inf"
                if event.normalized_peak_score == float("inf")
                else f"{event.normalized_peak_score:.4g}"
            )
            values = (
                event.channel_id,
                f"{start:.9g}",
                f"{stop:.9g}",
                f"{stop - start:.9g}",
                f"{event.peak_time_seconds:.9g}",
                score,
            )
            for column, text_value in enumerate(values):
                item = QTableWidgetItem(text_value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, event)
                set_described_table_item(self.review_detected_events, row, column, item)
        unavailable = [item.channel_id for item in result.thresholds if item.reason is not None]
        self.review_detection_status.setText(
            f"Detected {len(result.events)} event(s) on {len(result.thresholds)} channel(s)."
            + (f" Unavailable: {', '.join(unavailable)}." if unavailable else "")
        )

    def _transient_detection_failed(self, error: BaseException) -> None:
        self.review_detection_status.setText("Transient detection failed; see status for details.")
        self._handle_failure(error)

    def _select_detected_event(self) -> None:
        row = self.review_detected_events.currentRow()
        if row < 0 or self._transient_result is None:
            return
        item = self.review_detected_events.item(row, 0)
        if item is None:
            return
        event = cast("DetectedTransientEvent", item.data(Qt.ItemDataRole.UserRole))
        sample_rate = self._transient_result.sample_rate_hz
        start = event.analysis_range.start_sample_index / sample_rate
        stop = event.analysis_range.stop_sample_index / sample_rate
        self.review_start_seconds.setText(f"{start:.9g}")
        self.review_stop_seconds.setText(f"{stop:.9g}")
        review_plot_panel = _signal_plot_panel(self.review_plot)
        if review_plot_panel is not None:
            full_stop = self._transient_result.sample_range.stop_sample_index / sample_rate
            review_plot_panel.set_selection_range(
                start,
                stop,
                bounds=(0.0, full_stop),
            )
        self._mark_review_results_stale(detection=False)

    def _request_review_analysis(self) -> None:
        if self._review_path is None:
            return
        channels = self._selected_review_channels(PlotRepresentation.FFT_AMPLITUDE)
        if not channels:
            self.statusBar().showMessage("Select at least one analysis channel.")
            return
        try:
            start, stop = self._review_seconds()
        except ValueError as error:
            self._handle_failure(error)
            return
        path = self._review_path
        stage = self._review_stage()
        self._review_analysis_cancel.clear()

        def selected_range() -> SampleRange:
            return self.presenter.resolve_review_range(
                path,
                start_seconds=start,
                stop_seconds=stop,
            )

        time_channels = self._selected_review_channels(self._review_time_representation())
        if time_channels:
            self.runner.submit(
                "review-extrema",
                lambda: self.presenter.compute_review_extrema(
                    path,
                    time_channels,
                    stage,
                    selected_range(),
                    cancelled=self._review_analysis_cancel.is_set,
                ),
                on_success=self._review_extrema_ready,
                on_failure=lambda error: self._review_analysis_failed("Extrema", error),
                cancel_previous=self._review_analysis_cancel.set,
            )
            self._submit_review_analysis_plot(
                self._review_time_representation(),
                "review-time",
                time_channels,
                start,
                stop,
            )
        else:
            self.runner.invalidate("review-extrema")
            self.runner.invalidate("review-time")
            self.review_extrema_table.setRowCount(0)
            panel = _signal_plot_panel(self.review_plot)
            if panel is not None:
                panel.clear_series()
                panel.coordinate_label.setText(
                    "No channels eligible at this time-domain stage; select Raw to inspect ai3."
                )
        self._submit_review_analysis_plot(
            PlotRepresentation.FFT_AMPLITUDE,
            "review-fft",
            channels,
            start,
            stop,
        )
        self._submit_review_analysis_plot(
            PlotRepresentation.PSD,
            "review-psd",
            channels,
            start,
            stop,
        )

    def _submit_review_analysis_plot(
        self,
        representation: PlotRepresentation,
        key: str,
        channels: tuple[str, ...],
        start: float | None,
        stop: float | None,
    ) -> None:
        assert self._review_path is not None
        path = self._review_path
        stage = self._review_stage()

        def build() -> PlotBundle:
            selected_range = self.presenter.resolve_review_range(
                path,
                start_seconds=start,
                stop_seconds=stop,
            )
            return self.presenter.build_review_plot(
                path,
                representation,
                channels,
                selected_range,
                signal_stage=stage,
            )

        self.runner.submit(
            key,
            build,
            on_success=self._review_plot_ready,
            on_failure=lambda error: self._review_analysis_failed(
                "FFT"
                if representation is PlotRepresentation.FFT_AMPLITUDE
                else representation.value.upper(),
                error,
            ),
        )

    def _review_extrema_ready(self, value: object) -> None:
        results = cast("tuple[ChannelExtremaResult, ...]", value)
        self.review_extrema_table.setRowCount(len(results))
        set_field_notice(self.review_extrema_table, None)
        for row, result in enumerate(results):
            extrema = (result.maximum, result.minimum, result.absolute_peak)
            values = [result.channel_id, result.unit.value]
            for extremum in extrema:
                if extremum.value is None:
                    values.extend((extremum.reason or "unavailable", "", ""))
                else:
                    values.extend(
                        (
                            f"{extremum.value:.9g}",
                            str(extremum.sample_index),
                            f"{extremum.relative_time_seconds:.9g}",
                        )
                    )
            for column, text_value in enumerate(values):
                set_described_table_item(
                    self.review_extrema_table,
                    row,
                    column,
                    QTableWidgetItem(text_value),
                )

    def _review_analysis_failed(self, product: str, error: BaseException) -> None:
        if product == "Extrema":
            set_field_notice(
                self.review_extrema_table,
                "Extrema analysis failed; see status.",
            )
        elif product in {"FFT", "PSD"}:
            plot = self.review_fft_plot if product == "FFT" else self.review_psd_plot
            panel = _signal_plot_panel(plot)
            if panel is not None:
                panel.clear_series()
                panel.coordinate_label.setText(f"{product} failed; see status for details.")
                set_field_notice(
                    panel.coordinate_label,
                    f"{product} failed; see status for details.",
                )
        self._handle_failure(error)

    def _request_review_plot(self) -> None:
        if self._review_path is None or self.runner.busy("review-plot"):
            return
        representation = PlotRepresentation(str(self.review_representation.currentData()))
        channels = self._selected_review_channels(representation)
        if not channels:
            self.statusBar().showMessage("Select at least one review channel.")
            return
        try:
            start, stop = self._review_seconds()
        except ValueError as error:
            self._handle_failure(error)
            return
        path = self._review_path
        stage = self._review_stage()
        self.review_plot_metadata.setText("Loading review plot from the saved raw session...")

        def build() -> PlotBundle:
            selected_range = self.presenter.resolve_review_range(
                path,
                start_seconds=start,
                stop_seconds=stop,
            )
            return self.presenter.build_review_plot(
                path,
                representation,
                channels,
                selected_range,
                signal_stage=stage,
            )

        self.runner.submit(
            "review-plot",
            build,
            on_success=self._review_plot_ready,
            on_failure=lambda error: self._review_plot_failed(error, representation=representation),
        )

    def _review_plot_failed(
        self, error: BaseException, *, representation: PlotRepresentation
    ) -> None:
        self.review_plot_metadata.setText("Review plot generation failed; see status for details.")
        self._review_analysis_failed(
            "FFT"
            if representation is PlotRepresentation.FFT_AMPLITUDE
            else representation.value.upper(),
            error,
        )

    def _review_plot_ready(self, value: object) -> None:
        bundle = cast("PlotBundle", value)
        target = self.review_plot
        if bundle.representation is PlotRepresentation.FFT_AMPLITUDE:
            target = self.review_fft_plot
        elif bundle.representation is PlotRepresentation.PSD:
            target = self.review_psd_plot
        target_panel = _signal_plot_panel(target)
        if target_panel is not None:
            target_panel.set_plot_bundle(bundle)
            target_panel.coordinate_label.setText("Move over a plot to inspect coordinates")
            set_field_notice(target_panel.coordinate_label, None)
        selected_range = bundle.series[0].source_range
        if (
            bundle.representation
            in {
                PlotRepresentation.RAW_TIME,
                PlotRepresentation.CALIBRATED_TIME,
                PlotRepresentation.FILTERED_TIME,
            }
            and self._review is not None
        ):
            review_plot_panel = _signal_plot_panel(self.review_plot)
            if review_plot_panel is not None:
                full_stop = (
                    self._review.summary.persisted_samples_per_channel
                    / selected_range.sample_rate_hz
                )
                review_plot_panel.set_selection_range(
                    selected_range.start_index / selected_range.sample_rate_hz,
                    selected_range.stop_index / selected_range.sample_rate_hz,
                    bounds=(0.0, full_stop),
                )
        spectral = dict(bundle.spectral_details or {})
        spectral_text = ", ".join(f"{key}={item}" for key, item in spectral.items())
        self.review_plot_metadata.setText(
            f"{bundle.representation.value} | samples "
            f"[{selected_range.start_index}, {selected_range.stop_index}) | "
            f"{selected_range.sample_rate_hz:g} Hz | recipe {bundle.recipe_id or 'none'} | "
            f"units {', '.join(series.y_unit for series in bundle.series)}"
            + (f" | {spectral_text}" if spectral_text else "")
            + (f" | warnings {', '.join(bundle.warnings)}" if bundle.warnings else "")
        )

    def _request_export(self, output_format: ExportFormat) -> None:
        if self._review_path is None or self.runner.busy("export"):
            return
        representation = PlotRepresentation(str(self.review_representation.currentData()))
        channels = self._selected_review_channels(representation)
        if not channels:
            self.statusBar().showMessage("Select at least one export channel.")
            return
        try:
            start, stop = self._review_seconds()
        except ValueError as error:
            self._handle_failure(error)
            return
        path = self._review_path
        stage = self._review_stage()
        self._export_cancel.clear()

        def export() -> object:
            selected_range = self.presenter.resolve_review_range(
                path,
                start_seconds=start,
                stop_seconds=stop,
            )
            product = _export_product(representation, output_format, len(channels))
            request = self.presenter.build_export_request(
                path,
                product,
                output_format,
                representation,
                channels,
                selected_range,
                signal_stage=stage,
            )
            return self.presenter.run_export(
                (request,),
                cancelled=self._export_cancel.is_set,
                progress=self.export_progress_relay.publish,
            )

        self.runner.submit(
            "export",
            export,
            on_success=self._export_ready,
            on_failure=self._handle_failure,
            cancel_previous=self._export_cancel.set,
        )

    def _request_tdms_export(self) -> None:
        if (
            self._review_path is None
            or self.runner.busy("export")
            or self.presenter.services.raw_export is None
        ):
            return
        path = self._review_path
        self._export_cancel.clear()

        def export() -> object:
            return self.presenter.run_tdms_export(
                path,
                cancelled=self._export_cancel.is_set,
                progress=lambda processed, total: self.export_progress_relay.publish(
                    (processed, total)
                ),
            )

        self.runner.submit(
            "export",
            export,
            on_success=self._tdms_export_ready,
            on_failure=self._handle_failure,
            cancel_previous=self._export_cancel.set,
        )

    def _render_export_progress(self, value: object) -> None:
        if isinstance(value, tuple) and len(value) == 2:
            processed, total = (int(value[0]), int(value[1]))
            self.export_progress.setRange(0, max(1, total))
            self.export_progress.setValue(processed)
            set_field_notice(self.export_progress, "writing verified TDMS raw export")
            return
        progress = cast("ExportProgress", value)
        processed = progress.processed_samples
        total = progress.total_samples
        self.export_progress.setRange(0, max(1, total))
        self.export_progress.setValue(processed)
        set_field_notice(self.export_progress, progress.operation)

    def _export_ready(self, value: object) -> None:
        state = cast("ExportManifest", value).final_state
        self.statusBar().showMessage(f"Export job {state.value}; artifact history refreshed.")
        if self._review_path is not None:
            self._load_review(self._review_path, automatic=False)

    def _tdms_export_ready(self, value: object) -> None:
        output_path = getattr(value, "output_path", None)
        self.statusBar().showMessage(f"TDMS interoperability export completed: {output_path}")
        if self._review_path is not None:
            self._load_review(self._review_path, automatic=False)

    def _show_run_fault(self, summary: OperatorRunSummary) -> None:
        fault: SessionFaultSummary | AcquisitionFaultSummary | None = summary.recorded.fault
        if fault is None and summary.recorded.acquisition_completion is not None:
            fault = summary.recorded.acquisition_completion.fault
        if fault is None:
            return
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Acquisition fault finalized")
        dialog.setIcon(QMessageBox.Icon.Critical)
        dialog.setText(f"[{fault.code}] {fault.message}")
        dialog.setInformativeText(
            "The session was finalized. Review retained data and diagnostics before another run. "
            f"Acquired/persisted: {summary.recorded.acquired_samples_per_channel}/"
            f"{summary.recorded.persisted_samples_per_channel}. "
            f"Raw/manifest available: {summary.session.raw_data_available}/"
            f"{summary.session.manifest_available}."
        )
        details = str(fault.to_dict())
        dialog.setDetailedText(details)
        copy_button = dialog.addButton("Copy details", QMessageBox.ButtonRole.ActionRole)
        review_button = dialog.addButton("Review session", QMessageBox.ButtonRole.ActionRole)
        folder_button = dialog.addButton("Open folder", QMessageBox.ButtonRole.ActionRole)
        diagnostics_button = dialog.addButton(
            "View diagnostics",
            QMessageBox.ButtonRole.ActionRole,
        )
        dialog.addButton(QMessageBox.StandardButton.Close)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is copy_button:
            QApplication.clipboard().setText(details)
        elif clicked is review_button:
            self.navigation.setCurrentRow(3)
        elif clicked is folder_button:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(summary.recorded.session_path)))
        elif clicked is diagnostics_button:
            self.navigation.setCurrentRow(3)
            self.review_events.setFocus()

    def _handle_failure(self, error: BaseException) -> None:
        if isinstance(error, LabDAQError):
            message = operator_message_from_error(error)
        else:
            message = unexpected_operator_message()
            _LOG.exception(
                "Unexpected GUI operation failure correlation_id=%s",
                message.correlation_id,
                exc_info=error,
            )
        self._show_message(message)

    def _show_message(self, message: OperatorMessage) -> None:
        detail = message.message
        if message.remediation:
            detail += f" {message.remediation}"
        if message.correlation_id:
            detail += f" Correlation ID: {message.correlation_id}"
        self.statusBar().showMessage(f"[{message.code}] {detail}")
        color = (
            "#b42318"
            if message.severity in {ErrorSeverity.ERROR, ErrorSeverity.CRITICAL}
            else "#8a5a00"
        )
        self.global_status.setStyleSheet(f"color: {color}")

    def _remember_path(self, path: Path, *, browser: bool) -> None:
        resolved = path.absolute()
        if browser:
            values = (
                resolved,
                *(item for item in self.preferences.recent_browser_roots if item != resolved),
            )[:8]
            self.preferences = replace(self.preferences, recent_browser_roots=values)
        else:
            values = (
                resolved,
                *(item for item in self.preferences.recent_session_parents if item != resolved),
            )[:8]
            self.preferences = replace(self.preferences, recent_session_parents=values)

    def _restore_preferences(self) -> None:
        if self.preferences.window_geometry:
            self.restoreGeometry(self.preferences.window_geometry)
        if self.preferences.recent_browser_roots:
            self.browser_root.setText(str(self.preferences.recent_browser_roots[0]))
        elif self.preferences.recent_session_parents:
            self.browser_root.setText(str(self.preferences.recent_session_parents[0]))

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._closing:
            event.accept()
            return
        open_status = self.presenter.open_acquisition_status()
        if not self._force_close and open_status is not None and open_status.active:
            answer = QMessageBox.question(
                self,
                "Open acquisition is active",
                "Finalize any recording, end monitoring, and close?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer is not QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.setEnabled(False)

            def end_monitor_and_wait() -> object:
                self.presenter.end_monitoring()
                return self.presenter.wait_for_monitor_terminal()

            self.runner.submit(
                "close-open-monitor",
                end_monitor_and_wait,
                on_success=self._close_monitor_finalized,
                on_failure=self._close_failed,
            )
            event.ignore()
            return
        status = self.presenter.acquisition_status()
        if (
            not self._force_close
            and status is not None
            and status.state.value in {"running", "stopping"}
        ):
            answer = QMessageBox.question(
                self,
                "Acquisition is active",
                "Stop and close after the session is finalized?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer is not QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._close_after_finalization = True
            self._terminal_wait_submitted = True
            self.setEnabled(False)

            def stop_and_wait() -> object:
                self.presenter.stop_acquisition()
                return self.presenter.wait_for_acquisition()

            self.runner.submit(
                "close-acquisition",
                stop_and_wait,
                on_success=self._close_finalized,
                on_failure=self._close_failed,
            )
            event.ignore()
            return
        self._closing = True
        self.live_timer.stop()
        self.setEnabled(False)

        def cancel_background() -> None:
            self.presenter.cancel_qualification()
            self._export_cancel.set()
            self._review_detection_cancel.set()
            self._review_analysis_cancel.set()

        completed = self.runner.shutdown(
            cancel=cancel_background,
            timeout_ms=15_000,
        )
        self.preferences = replace(
            self.preferences,
            window_geometry=cast("bytes", self.saveGeometry().data()),
        )
        self.presenter.services.preferences.save(self.preferences)
        try:
            self.presenter.close()
        except LabDAQError as error:
            _LOG.warning("Desktop shutdown warning: %s", error)
        logging.shutdown()
        if completed:
            event.accept()
        else:
            _LOG.error("Desktop shutdown timed out waiting for background workers")
            event.ignore()
            self._closing = False
            self.setEnabled(True)
            self.live_timer.start()

    def _close_finalized(self, value: object) -> None:
        self._current_summary = cast("OperatorRunSummary", value)
        self._force_close = True
        self.close()

    def _close_monitor_finalized(self, value: object) -> None:
        self._open_status = cast("OpenAcquisitionStatus", value)
        self._force_close = True
        self.close()

    def _close_failed(self, error: BaseException) -> None:
        self._close_after_finalization = False
        self._terminal_wait_submitted = False
        self.setEnabled(True)
        self._handle_failure(error)


def _configure_splitter(
    splitter: QSplitter,
    *,
    sizes: tuple[int, ...],
    stretch: tuple[int, ...],
) -> None:
    style_splitter_tiles(splitter)
    splitter.setHandleWidth(8)
    splitter.setChildrenCollapsible(False)
    splitter.setSizes(list(sizes))
    for index, factor in enumerate(stretch):
        splitter.setStretchFactor(index, factor)


def _signal_plot_panel(widget: QWidget) -> SignalPlotPanel | None:
    return None if isinstance(widget, QLabel) else cast("SignalPlotPanel", widget)


def _readiness_tooltip(
    qualified_blockers: tuple[FieldIssue, ...],
    diagnostic_blockers: tuple[FieldIssue, ...],
    *,
    diagnostic_eligible: bool,
) -> str:
    def section(title: str, blockers: tuple[FieldIssue, ...], ready_text: str) -> list[str]:
        lines = [title]
        if not blockers:
            lines.append(ready_text)
            return lines
        for index, issue in enumerate(blockers, start=1):
            lines.extend(
                (
                    f"{index}. [{issue.code}] {issue.field_path}",
                    issue.message,
                    f"Resolve: {issue.remediation}",
                )
            )
        return lines

    lines = section(
        "Qualified acquisition",
        qualified_blockers,
        "No blocking findings.",
    )
    if diagnostic_blockers or diagnostic_eligible:
        lines.append("")
        lines.extend(
            section(
                "Raw-voltage diagnostic",
                diagnostic_blockers,
                "Eligible after physical authorization is entered.",
            )
        )
    return "\n".join(lines)


def _functional_display_issues(issues: tuple[FieldIssue, ...]) -> tuple[FieldIssue, ...]:
    """Keep tester-actionable gates and collapse impossible bundled-template defects."""
    editable_prefixes = (
        "session.",
        "experiment.",
        "acquisition.",
        "storage.",
        "channels[",
        "sensors[",
        "calibrations[",
        "hardware.",
    )
    displayed = []
    unexpected = []
    for issue in issues:
        if issue.field_path.startswith(editable_prefixes) or issue.code.startswith(
            ("DAQ.HARDWARE.", "DAQ.DEPENDENCY.", "DAQ.STORAGE.")
        ):
            displayed.append(issue)
        else:
            unexpected.append(issue)
    if unexpected:
        displayed.append(
            FieldIssue(
                "DAQ.CONFIGURATION.BUNDLED_TEMPLATE_INVALID",
                "configuration.template",
                ErrorSeverity.ERROR,
                "The packaged functional configuration is internally invalid.",
                "Reinstall this build and attach a feedback bundle; these fields are not tester setup items.",
            )
        )
    return tuple(displayed)


def run_desktop_window(window: LabDAQMainWindow, application: QApplication) -> int:
    window.show()
    return application.exec()


def _mapping_index(value: object, key: str, expected: str) -> int | None:
    if not isinstance(value, tuple):
        return None
    return next(
        (
            index
            for index, item in enumerate(value)
            if isinstance(item, Mapping) and str(item.get(key) or "") == expected
        ),
        None,
    )


def _set_live_frame(widget: QWidget, frame: LivePlotFrame | None) -> None:
    panel = _signal_plot_panel(widget)
    if panel is not None:
        panel.set_live_frame(frame)


def _metric_text(metric: MetricValue) -> str:
    return f"{metric.value:.6g}" if metric.value is not None else (metric.reason or "unavailable")


def _export_product(
    representation: PlotRepresentation,
    output_format: ExportFormat,
    channel_count: int,
) -> ExportProduct:
    if output_format is ExportFormat.CSV:
        return {
            PlotRepresentation.RAW_TIME: ExportProduct.RAW_TIME_DATA,
            PlotRepresentation.CALIBRATED_TIME: ExportProduct.CALIBRATED_TIME_DATA,
            PlotRepresentation.FILTERED_TIME: ExportProduct.FILTERED_TIME_DATA,
            PlotRepresentation.FFT_AMPLITUDE: ExportProduct.FFT_AMPLITUDE_DATA,
            PlotRepresentation.PSD: ExportProduct.PSD_DATA,
        }[representation]
    if representation is PlotRepresentation.FFT_AMPLITUDE:
        return ExportProduct.FFT_FIGURE
    if representation is PlotRepresentation.PSD:
        return ExportProduct.PSD_FIGURE
    return ExportProduct.TIME_HISTORY_FIGURE if channel_count == 1 else ExportProduct.OVERLAY_FIGURE
