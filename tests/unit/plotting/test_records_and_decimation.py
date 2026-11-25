from __future__ import annotations

import builtins
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from labdaq.adapters.export.matplotlib_renderer import MatplotlibStaticRenderer
from labdaq.adapters.export.writers import CsvArtifactWriter
from labdaq.adapters.plotting_json import JsonPlotExportCodec
from labdaq.application.decimation import StreamingMinMaxDecimator, decimate_xy
from labdaq.domain.plotting import (
    ChannelSelection,
    ExportFormat,
    ExportProduct,
    ExportRequest,
    FigureKind,
    FigureOptionsV1,
    PlotRepresentation,
    PlotRequest,
    SampleRange,
    resolve_sample_range,
)
from labdaq.errors import DependencyUnavailableError
from labdaq.ports.plotting import PlotSeries, TabularChunk, TimeDataChunk


def _range() -> SampleRange:
    return SampleRange(0, 10, 5.0)


def test_sample_range_resolves_indices_and_decimal_seconds_without_off_by_one() -> None:
    direct = resolve_sample_range(
        sample_rate_hz=10.0,
        readable_sample_count=100,
        start_index=3,
        stop_index=8,
    )
    seconds = resolve_sample_range(
        sample_rate_hz=10.0,
        readable_sample_count=100,
        start_seconds=0.21,
        stop_seconds=0.71,
    )
    open_stop = resolve_sample_range(
        sample_rate_hz=3.0,
        readable_sample_count=10,
        start_seconds=1.0,
    )

    assert (direct.start_index, direct.stop_index, direct.sample_count) == (3, 8, 5)
    assert (seconds.start_index, seconds.stop_index) == (3, 8)
    assert (open_stop.start_index, open_stop.stop_index) == (3, 10)
    assert seconds.requested_start_seconds == 0.21
    assert [index / seconds.sample_rate_hz for index in range(3, 8)] == [0.3, 0.4, 0.5, 0.6, 0.7]


@pytest.mark.parametrize(
    "changes",
    [
        {"start_index": 1, "start_seconds": 0.1},
        {"start_index": 10, "stop_index": 10},
        {"start_seconds": -1.0},
        {"stop_index": 101},
    ],
)
def test_invalid_sample_ranges_are_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        resolve_sample_range(sample_rate_hz=10.0, readable_sample_count=100, **changes)  # type: ignore[arg-type]


def test_request_records_enforce_channels_recipe_stage_and_product_format() -> None:
    channels = ChannelSelection(("a", "b"), ("A", "B"))
    raw = PlotRequest(Path("session"), PlotRepresentation.RAW_TIME, channels, _range())
    assert raw.to_dict()["representation"] == "raw"

    with pytest.raises(ValueError, match="duplicates"):
        ChannelSelection(("a", "a"))
    with pytest.raises(ValueError, match="recipe"):
        PlotRequest(Path("session"), PlotRepresentation.FILTERED_TIME, channels, _range())
    with pytest.raises(ValueError, match="input signal stage"):
        PlotRequest(
            Path("session"),
            PlotRepresentation.FFT_AMPLITUDE,
            channels,
            _range(),
            recipe_id="baseline-v1",
        )
    with pytest.raises(ValueError, match="CSV"):
        ExportRequest(
            Path("session"),
            ExportProduct.RAW_TIME_DATA,
            ExportFormat.PNG,
            channels,
            _range(),
            PlotRepresentation.RAW_TIME,
        )
    with pytest.raises(ValueError, match="lowercase"):
        ExportRequest(
            Path("session"),
            ExportProduct.RAW_TIME_DATA,
            ExportFormat.CSV,
            channels,
            _range(),
            PlotRepresentation.RAW_TIME,
            base_name="Unsafe Name",
        )
    with pytest.raises(ValueError, match="schema"):
        FigureOptionsV1(schema_version=2)


def test_request_json_codecs_round_trip_version_one_documents() -> None:
    codec = JsonPlotExportCodec()
    channels = ChannelSelection(("a", "b"), ("A", "B"))
    plot = PlotRequest(Path("session"), PlotRepresentation.RAW_TIME, channels, _range())
    export = ExportRequest(
        Path("session"),
        ExportProduct.RAW_TIME_DATA,
        ExportFormat.CSV,
        channels,
        _range(),
        PlotRepresentation.RAW_TIME,
        base_name="raw-data",
    )

    assert codec.loads_plot_request(codec.dumps_plot_request(plot)) == plot
    assert codec.loads_export_request(codec.dumps_export_request(export)) == export

    with pytest.raises(Exception, match="schema validation"):
        codec.loads_plot_request('{"schema_version": 2}')


def test_matplotlib_dependency_failure_is_lazy_and_structured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    original_import = builtins.__import__

    def blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("matplotlib"):
            cause = ModuleNotFoundError("No module named 'matplotlib'")
            cause.name = "matplotlib"
            raise cause
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    with pytest.raises(DependencyUnavailableError) as captured:
        MatplotlibStaticRenderer().render(
            None,  # type: ignore[arg-type]
            kind=FigureKind.OVERLAY,
            options=FigureOptionsV1(),
            output_format=ExportFormat.PNG,
            destination=tmp_path / "unused.png",
        )
    assert captured.value.code == "DAQ.DEPENDENCY.UNAVAILABLE"


def test_csv_writer_uses_exact_float_format_and_reserved_nan(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "values.csv"
    chunks = (
        TabularChunk(
            (
                np.asarray([0, 1], dtype=np.int64),
                np.asarray([1.0 / 3.0, np.nan], dtype=np.float64),
            )
        ),
    )
    progress = []

    CsvArtifactWriter().write(
        destination,
        headers=("sample_index", "value"),
        chunks=chunks,
        cancelled=lambda: False,
        progressed=progress.append,
    )

    assert destination.read_text(encoding="utf-8").splitlines() == [
        "sample_index,value",
        f"0,{format(1.0 / 3.0, '.17g')}",
        "1,nan",
    ]
    assert progress == [2]


def test_streaming_min_max_preserves_endpoints_extrema_impulse_and_bound() -> None:
    values = np.zeros((2, 101), dtype=np.float64)
    values[0, 37] = 10.0
    values[0, 38] = -7.0
    values[1] = np.linspace(5.0, -5.0, 101)
    collector = StreamingMinMaxDecimator(
        channel_count=2,
        start_index=100,
        stop_index=201,
        target_points=12,
    )
    for start, stop in ((0, 13), (13, 49), (49, 101)):
        block = np.ascontiguousarray(values[:, start:stop])
        block.flags.writeable = False
        collector.append(100 + start, block)
    result = collector.finalize()

    assert all(len(indices) <= 12 for indices, _ in result)
    assert result[0][0][0] == 100 and result[0][0][-1] == 200
    assert 137 in result[0][0] and 138 in result[0][0]
    assert np.all(np.diff(result[0][0]) > 0)
    assert set(result[0][1]).issubset(set(values[0]))


def test_decimation_passthrough_and_invalid_inputs_are_explicit() -> None:
    values = np.arange(5, dtype=np.float64).reshape(1, 5)
    values.flags.writeable = False
    collector = StreamingMinMaxDecimator(
        channel_count=1,
        start_index=0,
        stop_index=5,
        target_points=5,
    )
    collector.append(0, values)
    indices, output = collector.finalize()[0]
    np.testing.assert_array_equal(indices, np.arange(5))
    np.testing.assert_array_equal(output, values[0])

    with pytest.raises(ValueError, match="four"):
        StreamingMinMaxDecimator(
            channel_count=1,
            start_index=0,
            stop_index=5,
            target_points=3,
        )
    bad = values.copy()
    bad[0, 2] = np.nan
    bad.flags.writeable = False
    collector = StreamingMinMaxDecimator(
        channel_count=1,
        start_index=0,
        stop_index=5,
        target_points=5,
    )
    with pytest.raises(ValueError, match="nonfinite"):
        collector.append(0, bad)


def test_spectral_decimation_uses_source_points_and_is_deterministic() -> None:
    x = np.arange(100, dtype=np.float64)
    y = np.sin(x)
    first = decimate_xy(x, y, target_points=10)
    second = decimate_xy(x, y, target_points=10)

    np.testing.assert_array_equal(first[0], second[0])
    np.testing.assert_array_equal(first[1], second[1])
    assert first[2] is True
    assert len(first[0]) <= 10
    assert set(first[1]).issubset(set(y))


def test_numpy_result_carriers_reject_mutability_and_misalignment() -> None:
    x = np.arange(3, dtype=np.float64)
    y = np.arange(3, dtype=np.float64)
    indices = np.arange(3, dtype=np.int64)
    for array in (x, y, indices):
        array.flags.writeable = False
    series = PlotSeries(
        channel_id="a",
        physical_channel="Dev1/ai0",
        sensor_id="sensor-a",
        sensor_model="model-a",
        sensor_serial_number="serial-a",
        label="A",
        x_values=x,
        y_values=y,
        x_unit="s",
        y_unit="V",
        raw_unit="V",
        representation=PlotRepresentation.RAW_TIME,
        source_range=SampleRange(0, 3, 1.0),
        output_point_count=3,
        decimated=False,
        decimation_algorithm=None,
        source_sample_indices=indices,
    )
    assert series.to_dict()["y_values"] == [0.0, 1.0, 2.0]
    assert TabularChunk((x, y)).columns[0] is x
    matrix = np.arange(6, dtype=np.float64).reshape(2, 3)
    matrix.flags.writeable = False
    assert TimeDataChunk(0, matrix).values.shape == (2, 3)

    with pytest.raises(ValueError, match="read-only"):
        PlotSeries(
            channel_id="a",
            physical_channel="ai0",
            sensor_id=None,
            sensor_model="model-a",
            sensor_serial_number="serial-a",
            label="A",
            x_values=np.arange(3, dtype=np.float64),
            y_values=y,
            x_unit="s",
            y_unit="V",
            raw_unit="V",
            representation=PlotRepresentation.RAW_TIME,
            source_range=SampleRange(0, 3, 1.0),
            output_point_count=3,
            decimated=False,
            decimation_algorithm=None,
        )


def test_export_progress_timestamp_is_timezone_aware() -> None:
    from labdaq.domain.plotting import ExportJobState, ExportProgress

    progress = ExportProgress(
        "job", ExportJobState.RUNNING, 1, 2, 0, 1, "writing", datetime.now(UTC)
    )
    assert progress.timestamp.utcoffset() is not None
