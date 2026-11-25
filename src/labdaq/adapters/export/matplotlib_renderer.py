"""Lazy, noninteractive Matplotlib renderer for PNG and SVG figures."""

from __future__ import annotations

from typing import TYPE_CHECKING

from labdaq.domain.plotting import ExportFormat, FigureKind
from labdaq.errors import DependencyUnavailableError, ExportError

if TYPE_CHECKING:
    from pathlib import Path

    from labdaq.domain.plotting import FigureOptionsV1
    from labdaq.ports.plotting import FigureRenderReceipt, PlotBundle, PlotSeries


class MatplotlibStaticRenderer:
    def render(
        self,
        bundle: PlotBundle,
        *,
        kind: FigureKind,
        options: FigureOptionsV1,
        output_format: ExportFormat,
        destination: Path,
    ) -> FigureRenderReceipt:
        try:
            from matplotlib.backends.backend_agg import (
                FigureCanvasAgg,
            )
            from matplotlib.backends.backend_svg import (
                FigureCanvasSVG,
            )
            from matplotlib.figure import Figure
        except ModuleNotFoundError as cause:
            if cause.name != "matplotlib":
                raise
            raise DependencyUnavailableError(
                "Static figure export requires Matplotlib.",
                code="DAQ.DEPENDENCY.UNAVAILABLE",
                context={"dependency": "matplotlib", "feature": "figure_export"},
                remediation='Install LabDAQ with the "export" extra.',
                cause=cause,
            ) from cause
        if output_format not in {ExportFormat.PNG, ExportFormat.SVG}:
            raise ExportError(
                "The static renderer supports PNG and SVG only.",
                code="DAQ.EXPORT.RENDER_FAILED",
                context={"format": output_format.value},
            )
        groups: dict[str, list[PlotSeries]] = {}
        for series in bundle.series:
            groups.setdefault(series.y_unit, []).append(series)
        if kind is FigureKind.TIME_HISTORY and len(bundle.series) != 1:
            raise ExportError(
                "A time-history figure requires exactly one channel.",
                code="DAQ.EXPORT.REQUEST_INVALID",
                context={"channel_count": len(bundle.series)},
            )
        try:
            figure = Figure(
                figsize=(options.width_inches, options.height_inches),
                dpi=options.dpi,
                constrained_layout=True,
            )
            axes_value = figure.subplots(len(groups), 1, sharex=True, squeeze=False)
            axes = [axes_value[index, 0] for index in range(len(groups))]
            axis_labels: list[tuple[str, str]] = []
            legends: list[str] = []
            for axis, (unit, series_group) in zip(axes, groups.items(), strict=True):
                for series in series_group:
                    axis.plot(series.x_values, series.y_values, label=series.label, linewidth=0.9)
                    legends.append(series.label)
                axis.set_ylabel(unit)
                if options.logarithmic_y:
                    axis.set_yscale("log")
                if len(bundle.series) > 1:
                    axis.legend(loc="best")
                axis.grid(True, alpha=0.25)
                axis_labels.append((bundle.series[0].x_unit, unit))
            axes[-1].set_xlabel(bundle.series[0].x_unit)
            title = options.title or _default_title(kind, bundle)
            figure.suptitle(title)
            recipe = f"; recipe {bundle.recipe_id}" if bundle.recipe_id else ""
            source_range = bundle.series[0].source_range
            figure.text(
                0.01,
                0.005,
                f"session {bundle.source_session_id}; range "
                f"[{source_range.start_index}, {source_range.stop_index}){recipe}",
                fontsize=7,
            )
            metadata = {
                "Title": title,
                "Description": f"LabDAQ session {bundle.source_session_id}{recipe}",
            }
            if output_format is ExportFormat.PNG:
                FigureCanvasAgg(figure).print_png(  # type: ignore[no-untyped-call]
                    destination, metadata=metadata
                )
            else:
                FigureCanvasSVG(figure).print_svg(  # type: ignore[no-untyped-call]
                    destination, metadata=metadata
                )
            from labdaq.ports.plotting import FigureRenderReceipt

            return FigureRenderReceipt(
                kind,
                output_format,
                tuple(axis_labels),
                tuple(legends),
                len(bundle.series),
            )
        except (DependencyUnavailableError, ExportError):
            raise
        except Exception as cause:
            raise ExportError(
                "The static figure could not be rendered.",
                code="DAQ.EXPORT.RENDER_FAILED",
                context={"format": output_format.value, "kind": kind.value},
                remediation="Review the plot data and renderer dependency.",
                cause=cause,
            ) from cause


def _default_title(kind: FigureKind, bundle: PlotBundle) -> str:
    names = {
        FigureKind.TIME_HISTORY: "Time history",
        FigureKind.OVERLAY: "Synchronized overlay",
        FigureKind.FFT: "FFT amplitude",
        FigureKind.PSD: "Power spectral density",
    }
    return f"{names[kind]} — {bundle.representation.value}"
