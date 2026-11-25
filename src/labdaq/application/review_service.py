"""Disk-backed session review, plotting, and export orchestration for M10."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from labdaq.domain.enums import SignalStage
from labdaq.domain.plotting import (
    ChannelSelection,
    CollisionPolicy,
    ExportFormat,
    ExportProduct,
    ExportRequest,
    PlotRepresentation,
    PlotRequest,
    PlotViewport,
)
from labdaq.ports.operator import OperatorFaultPresentation, SessionReviewSnapshot
from labdaq.ports.session import VerificationLevel

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from labdaq.application.export_service import ExportCoordinator
    from labdaq.application.plotting_service import PlotDataService
    from labdaq.application.processing_service import RecordedSessionAnalysisService
    from labdaq.application.session_service import SessionInspectionService
    from labdaq.domain.plotting import ExportManifest, ExportProgress, SampleRange
    from labdaq.ports.operator import (
        ExportHistorySourcePort,
        OperatorRunStatus,
        OperatorRunSummary,
        SessionEventSourcePort,
    )
    from labdaq.ports.plotting import PlotBundle
    from labdaq.ports.processing import (
        ChannelExtremaResult,
        TransientDetectionResult,
        TransientDetectionSettings,
    )


class SessionReviewService:
    """Offer one review surface for completed, faulted, and browsed sessions."""

    def __init__(
        self,
        inspection: SessionInspectionService,
        events: SessionEventSourcePort,
        history: ExportHistorySourcePort,
        plots: PlotDataService,
        exports: ExportCoordinator,
        *,
        analysis: RecordedSessionAnalysisService | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._inspection = inspection
        self._events = events
        self._history = history
        self._plots = plots
        self._exports = exports
        self._analysis = analysis
        self._now = now or (lambda: datetime.now(UTC))

    def load(self, path: Path, *, event_limit: int = 500) -> SessionReviewSnapshot:
        session_path = path.absolute()
        summary = self._inspection.summary(session_path, level=VerificationLevel.FULL)
        qualification_path = session_path / "qualification-report.json"
        return SessionReviewSnapshot(
            summary=summary,
            events=self._events.read_session_events(session_path, limit=event_limit),
            export_history=self._history.list_export_history(session_path),
            manifest_path=session_path / "manifest.json",
            event_log_path=session_path / "events.jsonl",
            raw_data_path=session_path / summary.raw_container.filename,
            qualification_report_path=(
                qualification_path if summary.qualification_report_available else None
            ),
            loaded_at=self._now(),
        )

    def resolve_range(
        self,
        path: Path,
        *,
        start_index: int | None = None,
        stop_index: int | None = None,
        start_seconds: float | None = None,
        stop_seconds: float | None = None,
    ) -> SampleRange:
        return self._plots.resolve_range(
            path,
            start_index=start_index,
            stop_index=stop_index,
            start_seconds=start_seconds,
            stop_seconds=stop_seconds,
        )

    def build_plot(
        self,
        path: Path,
        representation: PlotRepresentation,
        channel_ids: tuple[str, ...],
        sample_range: SampleRange,
        *,
        maximum_points: int = 2000,
        recipe_id: str | None = None,
        spectral_input_stage: SignalStage = SignalStage.CONDITIONED,
    ) -> PlotBundle:
        spectral = representation in {
            PlotRepresentation.FFT_AMPLITUDE,
            PlotRepresentation.PSD,
        }
        resolved_recipe_id = (
            self._session_recipe_id(path)
            if recipe_id is None
            and (representation is PlotRepresentation.FILTERED_TIME or spectral)
            else recipe_id
        )
        request = PlotRequest(
            source_session=path,
            representation=representation,
            channels=ChannelSelection(channel_ids),
            sample_range=sample_range,
            viewport=PlotViewport(maximum_points),
            recipe_id=(
                resolved_recipe_id
                if representation is PlotRepresentation.FILTERED_TIME or spectral
                else None
            ),
            spectral_input_stage=(spectral_input_stage if spectral else None),
        )
        return self._plots.build_plot_data(request)

    def build_export_request(
        self,
        path: Path,
        product: ExportProduct,
        output_format: ExportFormat,
        representation: PlotRepresentation,
        channel_ids: tuple[str, ...],
        sample_range: SampleRange,
        *,
        destination_directory: Path | None = None,
        base_name: str | None = None,
        recipe_id: str | None = None,
        spectral_input_stage: SignalStage = SignalStage.CONDITIONED,
    ) -> ExportRequest:
        spectral = representation in {
            PlotRepresentation.FFT_AMPLITUDE,
            PlotRepresentation.PSD,
        }
        resolved_recipe_id = (
            self._session_recipe_id(path)
            if recipe_id is None
            and (representation is PlotRepresentation.FILTERED_TIME or spectral)
            else recipe_id
        )
        return ExportRequest(
            source_session=path,
            product=product,
            output_format=output_format,
            channels=ChannelSelection(channel_ids),
            sample_range=sample_range,
            representation=representation,
            recipe_id=(
                resolved_recipe_id
                if representation is PlotRepresentation.FILTERED_TIME or spectral
                else None
            ),
            spectral_input_stage=(spectral_input_stage if spectral else None),
            destination_directory=destination_directory,
            base_name=base_name,
            collision_policy=CollisionPolicy.REJECT,
        )

    def _session_recipe_id(self, path: Path) -> str:
        summary = self._inspection.summary(path.absolute(), level=VerificationLevel.FULL)
        if summary.recipe is None:
            raise ValueError("The recorded session does not contain processing recipe provenance")
        return summary.recipe.recipe_id

    def run_export(
        self,
        requests: Sequence[ExportRequest],
        *,
        cancelled: Callable[[], bool] | None = None,
        progress: Callable[[ExportProgress], None] | None = None,
    ) -> ExportManifest:
        return self._exports.run_export_job(
            requests,
            cancelled=cancelled,
            progress=progress,
        )

    def detect_transients(
        self,
        path: Path,
        channel_ids: tuple[str, ...],
        signal_stage: SignalStage,
        settings: TransientDetectionSettings,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> TransientDetectionResult:
        if self._analysis is None:
            raise RuntimeError("Session transient analysis is unavailable")
        if signal_stage is SignalStage.CONDITIONED:
            return self._analysis.detect_transients(
                path,
                ordered_channel_ids=channel_ids,
                signal_stage=signal_stage,
                settings=settings,
                recipe_id=self._session_recipe_id(path),
                cancelled=cancelled,
            )
        return self._analysis.detect_transients(
            path,
            ordered_channel_ids=channel_ids,
            signal_stage=signal_stage,
            settings=settings,
            cancelled=cancelled,
        )

    def compute_extrema(
        self,
        path: Path,
        channel_ids: tuple[str, ...],
        signal_stage: SignalStage,
        sample_range: SampleRange,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> tuple[ChannelExtremaResult, ...]:
        if self._analysis is None:
            raise RuntimeError("Session extrema analysis is unavailable")
        from labdaq.domain.models import ProcessingRange

        if signal_stage is SignalStage.CONDITIONED:
            return self._analysis.compute_extrema(
                path,
                ordered_channel_ids=channel_ids,
                signal_stage=signal_stage,
                sample_range=ProcessingRange(sample_range.start_index, sample_range.stop_index),
                recipe_id=self._session_recipe_id(path),
                cancelled=cancelled,
            )
        return self._analysis.compute_extrema(
            path,
            ordered_channel_ids=channel_ids,
            signal_stage=signal_stage,
            sample_range=ProcessingRange(sample_range.start_index, sample_range.stop_index),
            cancelled=cancelled,
        )

    def present_fault(
        self,
        status: OperatorRunStatus,
        summary: OperatorRunSummary | None,
    ) -> OperatorFaultPresentation | None:
        session_path = status.session_path
        raw_available = False
        manifest_available = False
        code = status.storage_fault_code
        message = "The session storage path faulted during finalization."
        technical = code or "No technical fault was retained."
        if status.fault is not None:
            code = status.fault.code
            message = status.fault.message
            technical = str(status.fault.to_dict())
        if summary is not None:
            raw_available = summary.session.raw_data_available
            manifest_available = summary.session.manifest_available
            if summary.recorded.fault is not None:
                code = summary.recorded.fault.code
                message = summary.recorded.fault.message
                technical = str(summary.recorded.fault.to_dict())
        if code is None:
            return None
        return OperatorFaultPresentation(
            code=code,
            severity="error",
            summary=message,
            technical_detail=technical,
            recommended_action=(
                "Review the finalized session and diagnostics; do not retry until the cause is corrected."
            ),
            acquisition_state=status.state.value,
            session_path=session_path,
            recoverable=manifest_available or raw_available,
            raw_data_available=raw_available,
            manifest_available=manifest_available,
        )
