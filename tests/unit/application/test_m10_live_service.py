from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from labdaq.adapters.configuration_json import JsonConfigurationAdapter
from labdaq.application.decimation import StreamingMinMaxDecimator
from labdaq.application.live_service import (
    _resolve_experiment_recipe,
    _SignalRingBuffer,
    vectorized_min_max,
)
from labdaq.application.review_service import SessionReviewService
from labdaq.domain.enums import SignalStage
from labdaq.domain.plotting import (
    ExportFormat,
    ExportProduct,
    PlotRepresentation,
    SampleRange,
)
from labdaq.gui.presentation import DesktopPresenter
from labdaq.gui.signal_plot import axis_unit_groups
from labdaq.ports.acquisition import AcquisitionFaultSummary, AcquisitionState
from labdaq.ports.operator import LiveDisplayConfiguration, OperatorRunStatus
from labdaq.ports.processing import TransientDetectionSettings


def _matrix(start: int, stop: int, channels: int = 2) -> np.ndarray:
    values = np.vstack(
        [np.arange(start, stop, dtype=np.float64) + channel * 100 for channel in range(channels)]
    )
    return np.ascontiguousarray(values)


def test_live_display_configuration_enforces_bounds() -> None:
    assert LiveDisplayConfiguration().visible_window_seconds == 5.0
    with pytest.raises(ValueError, match="maximum history"):
        LiveDisplayConfiguration(visible_window_seconds=11.0)
    with pytest.raises(ValueError, match="at least four"):
        LiveDisplayConfiguration(maximum_points_per_channel=3)


def test_experiment_named_low_rate_recipe_is_resolved_deterministically() -> None:
    documents = JsonConfigurationAdapter()
    experiment = documents.decode_experiment_draft(
        documents.load_bundled_experiment_template("four-channel-vibration-synthetic-v2")
    )
    experiment = replace(experiment, draft_processing_recipe_id="baseline-low-rate")

    first = _resolve_experiment_recipe(documents, experiment)
    second = _resolve_experiment_recipe(documents, experiment)

    assert first.recipe_id == "baseline-low-rate"
    assert first == second


def test_ring_buffer_retains_exact_bounded_source_suffix() -> None:
    ring = _SignalRingBuffer(2, 5)
    ring.append(10, _matrix(10, 13))
    ring.append(13, _matrix(13, 17))

    assert ring.start_index == 12
    assert ring.stop_index == 17
    start, values = ring.snapshot(4) or pytest.fail("snapshot absent")
    assert start == 13
    np.testing.assert_array_equal(values, _matrix(13, 17))
    assert not values.flags.writeable

    with pytest.raises(ValueError, match="source-contiguous"):
        ring.append(20, _matrix(20, 21))


def test_vectorized_min_max_matches_streaming_reference() -> None:
    rng = np.random.default_rng(431)
    values = np.ascontiguousarray(rng.normal(size=(4, 10_003)), dtype=np.float64)
    expected = StreamingMinMaxDecimator(
        channel_count=4,
        start_index=123,
        stop_index=10_126,
        target_points=2000,
    )
    expected.append(123, values[:, :1111])
    expected.append(123 + 1111, values[:, 1111:])

    actual = vectorized_min_max(values, start_sample_index=123, maximum_points=2000)

    for actual_channel, expected_channel in zip(actual, expected.finalize(), strict=True):
        np.testing.assert_array_equal(actual_channel[0], expected_channel[0])
        np.testing.assert_array_equal(actual_channel[1], expected_channel[1])
        assert len(actual_channel[0]) <= 2000


def test_vectorized_min_max_rejects_nonfinite_source() -> None:
    values = _matrix(0, 10)
    values[0, 5] = np.nan
    with pytest.raises(ValueError, match="nonfinite"):
        vectorized_min_max(values, start_sample_index=0, maximum_points=8)


def test_axis_groups_share_only_compatible_units() -> None:
    channels = (("a", "g"), ("b", "g"), ("c", "pC"))
    assert axis_unit_groups(channels, independent=False) == (
        ("g", ("a", "b")),
        ("pC", ("c",)),
    )
    assert axis_unit_groups(channels, independent=True) == (
        ("a", ("a",)),
        ("b", ("b",)),
        ("c", ("c",)),
    )


def test_review_service_constructs_traceable_rejecting_export_request() -> None:
    service = SessionReviewService(None, None, None, None, None)  # type: ignore[arg-type]
    selected_range = SampleRange(10, 110, 50_000.0)

    request = service.build_export_request(
        Path("session"),
        ExportProduct.PSD_DATA,
        ExportFormat.CSV,
        PlotRepresentation.PSD,
        ("accel-x", "accel-y"),
        selected_range,
        recipe_id="baseline",
        spectral_input_stage=SignalStage.CONDITIONED,
    )

    assert request.sample_range == selected_range
    assert request.channels.channel_ids == ("accel-x", "accel-y")
    assert request.recipe_id == "baseline"
    assert request.collision_policy.value == "reject"


class _ReviewPlots:
    def __init__(self) -> None:
        self.requests: list[object] = []

    def resolve_range(self, path: Path, **values: object) -> SampleRange:
        self.requests.append((path, values))
        return SampleRange(0, 100, 50_000.0)

    def build_plot_data(self, request: object) -> object:
        self.requests.append(request)
        return request


@pytest.mark.parametrize(
    "representation",
    [
        PlotRepresentation.RAW_TIME,
        PlotRepresentation.FILTERED_TIME,
        PlotRepresentation.FFT_AMPLITUDE,
        PlotRepresentation.PSD,
    ],
)
def test_review_service_delegates_ranges_and_plot_requests(
    representation: PlotRepresentation,
) -> None:
    plots = _ReviewPlots()
    service = SessionReviewService(None, None, None, plots, None)  # type: ignore[arg-type]
    selected_range = service.resolve_range(Path("session"), start_seconds=0.0, stop_seconds=1.0)

    request = service.build_plot(
        Path("session"),
        representation,
        ("a",),
        selected_range,
        recipe_id="baseline-v1",
    )

    assert request.representation is representation  # type: ignore[attr-defined]
    assert request.recipe_id == (  # type: ignore[attr-defined]
        "baseline-v1" if representation is not PlotRepresentation.RAW_TIME else None
    )


def test_review_service_delegates_non_destructive_analysis() -> None:
    calls: list[tuple[str, Path, object]] = []

    class Analysis:
        def detect_transients(self, path: Path, **values: object) -> str:
            calls.append(("detect", path, values))
            return "events"

        def compute_extrema(self, path: Path, **values: object) -> str:
            calls.append(("extrema", path, values))
            return "extrema"

    service = SessionReviewService(
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        analysis=Analysis(),  # type: ignore[arg-type]
    )

    assert (
        service.detect_transients(
            Path("session"),
            ("a",),
            SignalStage.RAW,
            TransientDetectionSettings(),
        )
        == "events"
    )
    assert (
        service.compute_extrema(
            Path("session"),
            ("a",),
            SignalStage.RAW,
            SampleRange(10, 20, 1000.0),
        )
        == "extrema"
    )
    assert calls[0][0] == "detect"
    extrema_values = calls[1][2]
    assert isinstance(extrema_values, dict)
    assert extrema_values["sample_range"].start_sample_index == 10


def test_review_load_and_fault_presentation_are_adapter_neutral() -> None:
    summary = SimpleNamespace(
        qualification_report_available=True,
        raw_data_available=True,
        manifest_available=True,
        raw_container=SimpleNamespace(filename="raw.h5"),
    )
    inspection = SimpleNamespace(summary=lambda path, level: summary)
    events = SimpleNamespace(read_session_events=lambda path, limit: ("event",))
    history = SimpleNamespace(list_export_history=lambda path: ("artifact",))
    service = SessionReviewService(
        inspection,  # type: ignore[arg-type]
        events,  # type: ignore[arg-type]
        history,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        now=lambda: datetime(2026, 8, 13, tzinfo=UTC),
    )
    loaded = service.load(Path("session"))

    assert loaded.events == ("event",)
    assert loaded.export_history == ("artifact",)
    assert loaded.qualification_report_path is not None

    fault = AcquisitionFaultSummary("DAQ.ACQUISITION.DISCONNECTED", "disconnected", {})
    status = OperatorRunStatus(
        state=AcquisitionState.FAULTED,
        finalization_state=None,
        session_id="session",
        session_path=Path("session"),
        experiment_name="experiment",
        started_at=datetime(2026, 8, 13, tzinfo=UTC),
        elapsed_seconds=1.0,
        requested_duration_seconds=2.0,
        actual_sample_rate_hz=50_000.0,
        ordered_channel_ids=("a",),
        ordered_channel_units=("V",),
        acquired_samples_per_channel=10,
        persisted_samples_per_channel=10,
        recording_queue_occupancy=0,
        recording_queue_capacity=1,
        display_queue_occupancy=0,
        display_queue_capacity=1,
        display_frames_produced=1,
        display_frames_dropped=0,
        acquisition_gap_count=0,
        persistence_loss_samples=0,
        warning_count=0,
        fault=fault,
    )
    presented = service.present_fault(status, None)

    assert presented is not None
    assert presented.code == fault.code
    assert presented.session_path == Path("session")
    assert service.present_fault(replace(status, fault=None), None) is None


def test_m10_presenter_is_a_thin_live_and_review_command_surface() -> None:
    calls: list[tuple[str, object]] = []

    class Live:
        def start(self, value: object) -> str:
            calls.append(("start", value))
            return "started"

        def request_stop(self) -> str:
            return "stopped"

        def status(self) -> str:
            return "status"

        def build_plot_frame(self, stage: object, *, channel_ids: object) -> tuple[object, object]:
            return stage, channel_ids

        def metrics(self) -> str:
            return "metrics"

        def set_visible_window(self, seconds: float) -> None:
            calls.append(("window", seconds))

        def wait_for_terminal(self, timeout: float | None) -> tuple[str, float | None]:
            return "summary", timeout

        def close(self) -> None:
            calls.append(("close", True))

    class Review:
        def load(self, path: Path) -> tuple[str, Path]:
            return "review", path

        def resolve_range(self, path: Path, **values: object) -> tuple[Path, object]:
            return path, values

        def build_plot(self, *values: object) -> tuple[object, ...]:
            return values

        def build_export_request(self, *values: object) -> tuple[object, ...]:
            return values

        def run_export(self, requests: object, **values: object) -> tuple[object, object]:
            return requests, values

    class Open:
        def start_monitoring(self, value: object) -> tuple[str, object]:
            return "monitoring", value

        def start_recording(self) -> str:
            return "recording"

        def stop_recording(self) -> str:
            return "finalizing"

        def end_monitoring(self) -> str:
            return "stopping"

        def status(self) -> str:
            return "open-status"

        def build_plot_frame(self) -> str:
            return "open-frame"

        def wait_for_recording(self, timeout: float | None) -> tuple[str, float | None]:
            return "recorded", timeout

        def wait_for_terminal(self, timeout: float | None) -> tuple[str, float | None]:
            return "monitor-terminal", timeout

        def close(self) -> None:
            calls.append(("open-close", True))

    class RawExport:
        def export_tdms(self, path: Path, **values: object) -> tuple[Path, object]:
            calls.append(("tdms", path))
            return path, values

    preferences = SimpleNamespace(sync=lambda: calls.append(("sync", True)))
    services = SimpleNamespace(
        live=Live(),
        open_acquisition=Open(),
        review=Review(),
        raw_export=RawExport(),
        daq=None,
        preferences=preferences,
    )
    presenter = DesktopPresenter(services)  # type: ignore[arg-type]

    assert presenter.start_acquisition("intent") == "started"  # type: ignore[arg-type]
    assert presenter.stop_acquisition() == "stopped"
    assert presenter.acquisition_status() == "status"
    assert presenter.live_frame(SignalStage.RAW, channel_ids=("a",)) == (
        SignalStage.RAW,
        ("a",),
    )
    assert presenter.live_metrics() == "metrics"
    presenter.set_live_window(2.0)
    assert presenter.wait_for_acquisition(3.0) == ("summary", 3.0)
    assert presenter.start_monitoring("open-intent") == (  # type: ignore[arg-type]
        "monitoring",
        "open-intent",
    )
    assert presenter.start_open_recording() == "recording"
    assert presenter.stop_open_recording() == "finalizing"
    assert presenter.end_monitoring() == "stopping"
    assert presenter.open_acquisition_status() == "open-status"
    assert presenter.open_acquisition_frame() == "open-frame"
    assert presenter.wait_for_open_recording(4.0) == ("recorded", 4.0)
    assert presenter.wait_for_monitor_terminal(5.0) == ("monitor-terminal", 5.0)
    assert presenter.load_session_review(Path("session")) == ("review", Path("session"))
    assert presenter.resolve_review_range(Path("session"), start_seconds=1.0)[0] == Path("session")
    selected_range = SampleRange(0, 10, 10.0)
    assert presenter.build_review_plot(
        Path("session"),
        PlotRepresentation.RAW_TIME,
        ("a",),
        selected_range,
    )[0] == Path("session")
    request = presenter.build_export_request(
        Path("session"),
        ExportProduct.RAW_TIME_DATA,
        ExportFormat.CSV,
        PlotRepresentation.RAW_TIME,
        ("a",),
        selected_range,
    )
    assert request[0] == Path("session")
    assert presenter.run_export(("request",))[0] == ("request",)  # type: ignore[arg-type]
    assert presenter.run_tdms_export(Path("session"))[0] == Path("session")  # type: ignore[index]
    presenter.close()
    assert ("close", True) in calls and ("sync", True) in calls

    presenter.services = SimpleNamespace(
        live=None,
        open_acquisition=None,
        review=None,
        raw_export=None,
        daq=None,
        preferences=preferences,
    )
    assert presenter.acquisition_status() is None
    assert presenter.live_frame(SignalStage.RAW) is None
    assert presenter.live_metrics() is None
    presenter.set_live_window(1.0)
    with pytest.raises(RuntimeError, match="unavailable"):
        presenter.start_acquisition("intent")  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="unavailable"):
        presenter.stop_acquisition()
    with pytest.raises(RuntimeError, match="unavailable"):
        presenter.wait_for_acquisition()
    assert presenter.open_acquisition_status() is None
    assert presenter.open_acquisition_frame() is None
    with pytest.raises(RuntimeError, match="unavailable"):
        presenter.start_monitoring("intent")  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="unavailable"):
        presenter.start_open_recording()
    with pytest.raises(RuntimeError, match="unavailable"):
        presenter.stop_open_recording()
    with pytest.raises(RuntimeError, match="unavailable"):
        presenter.end_monitoring()
    with pytest.raises(RuntimeError, match="unavailable"):
        presenter.wait_for_open_recording()
    with pytest.raises(RuntimeError, match="unavailable"):
        presenter.wait_for_monitor_terminal()
    with pytest.raises(RuntimeError, match="unavailable"):
        presenter.load_session_review(Path("session"))
    with pytest.raises(RuntimeError, match="unavailable"):
        presenter.resolve_review_range(Path("session"))
    with pytest.raises(RuntimeError, match="unavailable"):
        presenter.build_review_plot(
            Path("session"),
            PlotRepresentation.RAW_TIME,
            ("a",),
            selected_range,
        )
    with pytest.raises(RuntimeError, match="unavailable"):
        presenter.build_export_request(
            Path("session"),
            ExportProduct.RAW_TIME_DATA,
            ExportFormat.CSV,
            PlotRepresentation.RAW_TIME,
            ("a",),
            selected_range,
        )
    with pytest.raises(RuntimeError, match="unavailable"):
        presenter.run_export(("request",))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="unavailable"):
        presenter.run_tdms_export(Path("session"))
