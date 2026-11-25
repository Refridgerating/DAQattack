"""PyQtGraph panels for immutable live and replay plot bundles."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from labdaq.errors import DependencyUnavailableError
from labdaq.gui.field_help import bind_field_help
from labdaq.gui.tiles import add_splitter_tile, style_splitter_tiles

if TYPE_CHECKING:
    from collections.abc import Iterable

    from labdaq.ports.operator import LivePlotFrame
    from labdaq.ports.plotting import PlotBundle


def axis_unit_groups(
    channels: Iterable[tuple[str, str]],
    *,
    independent: bool,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Group stable channel identities by compatible unit or independently."""
    groups: dict[str, list[str]] = defaultdict(list)
    for channel_id, unit in channels:
        key = channel_id if independent else unit
        groups[key].append(channel_id)
    return tuple((key, tuple(values)) for key, values in groups.items())


class SignalPlotPanel(QWidget):
    """Small reusable operator plot supporting unit grouping and bounded frames."""

    selectionChanged = Signal(float, float)

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        try:
            import pyqtgraph as pg  # type: ignore[import-untyped]
        except ModuleNotFoundError as cause:
            raise DependencyUnavailableError(
                "Responsive operator plots require PyQtGraph.",
                code="DAQ.DEPENDENCY.UNAVAILABLE",
                context={"dependency": "pyqtgraph", "feature": "live_plotting"},
                remediation='Install LabDAQ with the "gui" extra.',
                cause=cause,
            ) from cause
        self._pg = pg
        self._title = title
        self._latest: dict[str, tuple[Any, Any, str, str]] = {}
        self._plots: dict[str, Any] = {}
        self._curves: dict[str, Any] = {}
        self._proxies: list[Any] = []
        self._signature: tuple[object, ...] = ()
        self._selection_regions: list[Any] = []
        self._selection_range: tuple[float, float] | None = None
        self._selection_bounds: tuple[float, float] | None = None
        self._selection_syncing = False

        root = QVBoxLayout(self)
        controls = QHBoxLayout()
        controls.addWidget(QLabel(title))
        self.scale_mode = QComboBox()
        self.scale_mode.addItems(["Compatible-unit shared", "Independent", "Manual limits"])
        self.scale_mode.currentIndexChanged.connect(self._rebuild)
        controls.addWidget(self.scale_mode)
        self.minimum = QDoubleSpinBox()
        self.maximum = QDoubleSpinBox()
        for control in (self.minimum, self.maximum):
            control.setRange(-1.0e12, 1.0e12)
            control.setDecimals(6)
            control.valueChanged.connect(self._apply_ranges)
        self.minimum.setValue(-1.0)
        self.maximum.setValue(1.0)
        controls.addWidget(self.minimum)
        controls.addWidget(self.maximum)
        autoscale = QPushButton("Autoscale")
        autoscale.clicked.connect(self._autoscale)
        controls.addWidget(autoscale)
        controls.addStretch(1)
        root.addLayout(controls)

        self.channels = QListWidget()
        self.channels.setMinimumWidth(120)
        self.channels.itemChanged.connect(self._rebuild)
        self.plot_stack_splitter = QSplitter(Qt.Orientation.Vertical)
        self.plot_stack_splitter.setObjectName("plotStackSplitter")
        self.plot_stack_splitter.setHandleWidth(8)
        self.plot_stack_splitter.setChildrenCollapsible(False)
        self.plot_host = QWidget()
        plot_host_layout = QVBoxLayout(self.plot_host)
        plot_host_layout.setContentsMargins(0, 0, 0, 0)
        plot_host_layout.addWidget(self.plot_stack_splitter)
        self.channel_plot_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.channel_plot_splitter.setObjectName("channelPlotSplitter")
        self.channel_plot_splitter.setHandleWidth(8)
        self.channel_plot_splitter.setChildrenCollapsible(False)
        add_splitter_tile(self.channel_plot_splitter, self.channels)
        add_splitter_tile(self.channel_plot_splitter, self.plot_host)
        style_splitter_tiles(self.channel_plot_splitter)
        style_splitter_tiles(self.plot_stack_splitter)
        self.channel_plot_splitter.setSizes([180, 820])
        self.channel_plot_splitter.setStretchFactor(0, 1)
        self.channel_plot_splitter.setStretchFactor(1, 5)
        root.addWidget(self.channel_plot_splitter, 1)
        self.coordinate_label = QLabel("Move over a plot to inspect coordinates")
        root.addWidget(self.coordinate_label)
        for widget, help_id in (
            (self, "plot.panel"),
            (self.scale_mode, "plot.scale_mode"),
            (self.minimum, "plot.minimum"),
            (self.maximum, "plot.maximum"),
            (self.channels, "plot.channels"),
            (self.coordinate_label, "plot.coordinate"),
        ):
            bind_field_help(widget, help_id)

    def set_live_frame(self, frame: LivePlotFrame | None) -> None:
        if frame is None:
            return
        self._set_series(
            (
                (
                    series.channel_id,
                    series.display_name,
                    series.unit,
                    series.relative_time_seconds,
                    series.values,
                )
                for series in frame.series
            ),
            x_label="Relative time",
            x_unit="s",
        )

    def set_plot_bundle(self, bundle: PlotBundle) -> None:
        self._set_series(
            (
                (
                    series.channel_id,
                    series.label,
                    series.y_unit,
                    series.x_values,
                    series.y_values,
                )
                for series in bundle.series
            ),
            x_label=("Frequency" if bundle.series[0].x_unit == "Hz" else "Relative time"),
            x_unit=bundle.series[0].x_unit,
        )

    def clear_series(self) -> None:
        """Remove obsolete results when an analysis fails or has no eligible channels."""
        self._set_series((), x_label="", x_unit="")

    def set_selection_range(
        self,
        start_seconds: float,
        stop_seconds: float,
        *,
        bounds: tuple[float, float] | None = None,
        emit: bool = False,
    ) -> None:
        if start_seconds < 0 or stop_seconds <= start_seconds:
            raise ValueError("selection range must be non-empty and non-negative")
        if bounds is not None:
            lower, upper = bounds
            if lower < 0 or upper <= lower or start_seconds < lower or stop_seconds > upper:
                raise ValueError("selection range must be contained by its bounds")
            self._selection_bounds = bounds
        self._selection_range = (start_seconds, stop_seconds)
        self._install_selection_regions()
        self._synchronize_selection(None, emit=emit)

    def clear_selection_range(self) -> None:
        self._selection_range = None
        self._selection_bounds = None
        for region in self._selection_regions:
            for plot in self._plots.values():
                plot.removeItem(region)
        self._selection_regions.clear()

    @property
    def selection_range(self) -> tuple[float, float] | None:
        return self._selection_range

    def _set_series(
        self,
        series_values: Iterable[tuple[str, str, str, Any, Any]],
        *,
        x_label: str,
        x_unit: str,
    ) -> None:
        incoming = tuple(series_values)
        incoming_labels = {
            channel_id: f"{name} [{unit}]" for channel_id, name, unit, _, _ in incoming
        }
        self.channels.blockSignals(True)
        for index in reversed(range(self.channels.count())):
            item = self.channels.item(index)
            channel_id = item.data(Qt.ItemDataRole.UserRole)
            if channel_id not in incoming_labels:
                self.channels.takeItem(index)
            else:
                item.setText(incoming_labels[channel_id])
        known = {
            self.channels.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self.channels.count())
        }
        self.channels.blockSignals(True)
        for channel_id, name, _unit, _x, _y in incoming:
            if channel_id not in known:
                item = QListWidgetItem(f"{name} [{_unit}]")
                item.setData(Qt.ItemDataRole.UserRole, channel_id)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked)
                self.channels.addItem(item)
        self.channels.blockSignals(False)
        self._latest = {channel_id: (x, y, unit, name) for channel_id, name, unit, x, y in incoming}
        self._x_label = x_label
        self._x_unit = x_unit
        signature = self._group_signature()
        if signature != self._signature:
            self._build_plots(signature)
        for channel_id, curve in self._curves.items():
            values = self._latest.get(channel_id)
            if values is not None:
                curve.setData(values[0], values[1])
        self._apply_ranges()

    def _visible_ids(self) -> tuple[str, ...]:
        return tuple(
            str(item.data(Qt.ItemDataRole.UserRole))
            for index in range(self.channels.count())
            if (item := self.channels.item(index)).checkState() is Qt.CheckState.Checked
            and item.data(Qt.ItemDataRole.UserRole) in self._latest
        )

    def _group_signature(self) -> tuple[object, ...]:
        visible = self._visible_ids()
        independent = self.scale_mode.currentIndex() == 1
        groups = axis_unit_groups(
            ((channel_id, str(self._latest[channel_id][2])) for channel_id in visible),
            independent=independent,
        )
        return (groups, getattr(self, "_x_label", "x"), getattr(self, "_x_unit", ""))

    def _rebuild(self) -> None:
        if not self._latest:
            return
        self._build_plots(self._group_signature())
        for channel_id, curve in self._curves.items():
            values = self._latest[channel_id]
            curve.setData(values[0], values[1])
        self._apply_ranges()

    def _build_plots(self, signature: tuple[object, ...]) -> None:
        while self.plot_stack_splitter.count():
            widget = self.plot_stack_splitter.widget(0)
            if widget is None:
                break
            widget.setParent(None)
            widget.deleteLater()
        self._plots.clear()
        self._curves.clear()
        self._proxies.clear()
        groups = cast("tuple[tuple[str, tuple[str, ...]], ...]", signature[0])
        colors = ("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00")
        for group_index, (group_key, channel_ids) in enumerate(groups):
            plot = self._pg.PlotWidget()
            plot.setLabel("bottom", str(signature[1]), units=str(signature[2]))
            unit = str(self._latest[channel_ids[0]][2])
            plot.setLabel("left", str(group_key), units=unit)
            plot.showGrid(x=True, y=True, alpha=0.2)
            plot.addLegend()
            for channel_index, channel_id in enumerate(channel_ids):
                curve = plot.plot(
                    name=str(self._latest[channel_id][3]),
                    pen=self._pg.mkPen(
                        colors[(group_index + channel_index) % len(colors)], width=1
                    ),
                )
                self._curves[channel_id] = curve
            proxy = self._pg.SignalProxy(
                plot.scene().sigMouseMoved,
                rateLimit=30,
                slot=lambda event, current=plot: self._inspect_coordinate(current, event),
            )
            self._proxies.append(proxy)
            self._plots[str(group_key)] = plot
            add_splitter_tile(self.plot_stack_splitter, plot)
            self.plot_stack_splitter.setStretchFactor(group_index, 1)
        if groups:
            self.plot_stack_splitter.setSizes([1] * len(groups))
        self._signature = signature
        self._selection_regions.clear()
        self._install_selection_regions()

    def _install_selection_regions(self) -> None:
        if self._selection_range is None or len(self._selection_regions) == len(self._plots):
            return
        self._selection_regions.clear()
        for plot in self._plots.values():
            region = self._pg.LinearRegionItem(values=self._selection_range, movable=True)
            if self._selection_bounds is not None:
                region.setBounds(self._selection_bounds)
            region.setZValue(10)
            region.sigRegionChangeFinished.connect(
                lambda current=region: self._synchronize_selection(current, emit=True)
            )
            plot.addItem(region)
            self._selection_regions.append(region)

    def _synchronize_selection(self, source: Any | None, *, emit: bool) -> None:
        if self._selection_range is None or self._selection_syncing:
            return
        self._selection_syncing = True
        try:
            if source is not None:
                start, stop = (float(value) for value in source.getRegion())
                self._selection_range = (start, stop)
            for region in self._selection_regions:
                if region is not source:
                    region.setRegion(self._selection_range)
        finally:
            self._selection_syncing = False
        if emit:
            self.selectionChanged.emit(*self._selection_range)

    def _inspect_coordinate(self, plot: Any, event: object) -> None:
        position = event[0] if isinstance(event, tuple) else event
        if plot.sceneBoundingRect().contains(position):
            coordinate = plot.getPlotItem().vb.mapSceneToView(position)
            self.coordinate_label.setText(f"x={coordinate.x():.6g}, y={coordinate.y():.6g}")

    def _apply_ranges(self) -> None:
        manual = self.scale_mode.currentIndex() == 2
        valid = self.minimum.value() < self.maximum.value()
        for plot in self._plots.values():
            if manual and valid:
                plot.setYRange(self.minimum.value(), self.maximum.value(), padding=0)
            elif not manual:
                plot.enableAutoRange(axis="y", enable=True)

    def _autoscale(self) -> None:
        for plot in self._plots.values():
            plot.enableAutoRange()
