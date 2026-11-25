"""Optional M07 evidence generation for a completed qualification session."""

from __future__ import annotations

from typing import TYPE_CHECKING

from labdaq.domain.enums import SignalStage
from labdaq.domain.plotting import (
    ChannelSelection,
    ExportFormat,
    ExportJobState,
    ExportProduct,
    ExportRequest,
    PlotRepresentation,
    SampleRange,
)
from labdaq.domain.qualification import EvidenceLink
from labdaq.ports.qualification import QualificationArtifactBundle

if TYPE_CHECKING:
    from pathlib import Path

    from labdaq.application.export_service import ExportCoordinator


class QualificationArtifactService:
    """Render bounded powered-idle time-history and conditioned PSD evidence."""

    def __init__(self, exports: ExportCoordinator) -> None:
        self._exports = exports

    def generate(
        self,
        session_path: Path,
        *,
        ordered_channel_ids: tuple[str, ...],
        sample_rate_hz: float,
        sample_count: int,
    ) -> QualificationArtifactBundle:
        selected = ChannelSelection(ordered_channel_ids)
        sample_range = SampleRange(0, sample_count, sample_rate_hz)
        destination = session_path / "plots"
        requests = (
            ExportRequest(
                source_session=session_path,
                product=ExportProduct.OVERLAY_FIGURE,
                output_format=ExportFormat.PNG,
                channels=selected,
                sample_range=sample_range,
                representation=PlotRepresentation.RAW_TIME,
                destination_directory=destination,
                base_name="qualification-powered-idle",
                max_points=2_000,
            ),
            ExportRequest(
                source_session=session_path,
                product=ExportProduct.PSD_FIGURE,
                output_format=ExportFormat.PNG,
                channels=selected,
                sample_range=sample_range,
                representation=PlotRepresentation.PSD,
                recipe_id="baseline-v1",
                spectral_input_stage=SignalStage.CONDITIONED,
                destination_directory=destination,
                base_name="qualification-powered-idle-psd",
                max_points=2_000,
            ),
        )
        manifest = self._exports.run_export_job(requests)
        evidence = tuple(
            EvidenceLink(
                artifact_type=artifact.product.value,
                relative_path=f"plots/{artifact.relative_path}",
                sha256=artifact.sha256,
                disposition="retained",
            )
            for artifact in manifest.artifacts
        )
        if manifest.manifest_path is not None:
            evidence = (
                *evidence,
                EvidenceLink(
                    artifact_type="export-manifest",
                    relative_path=f"plots/{manifest.manifest_path.name}",
                    sha256=None,
                    disposition="retained",
                ),
            )
        warnings = (
            ()
            if manifest.final_state is ExportJobState.COMPLETED
            else ("DAQ.QUALIFICATION.RENDERING_UNAVAILABLE",)
        )
        return QualificationArtifactBundle(evidence, warnings)
