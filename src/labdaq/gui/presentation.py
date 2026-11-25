"""Qt-free presentation records and orchestration for the M10 desktop."""

from __future__ import annotations

from dataclasses import dataclass, replace
from threading import Event
from typing import TYPE_CHECKING, Protocol

from labdaq.domain.enums import SignalStage
from labdaq.domain.plotting import PlotRepresentation
from labdaq.domain.serialization import canonical_sha256

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from labdaq.application.configuration_workspace import (
        ConfigurationDraftSnapshot,
        ConfigurationWorkspaceService,
    )
    from labdaq.application.daq_service import DaqService
    from labdaq.application.destination_service import (
        SessionDestinationPreview,
        SessionDestinationService,
    )
    from labdaq.application.live_service import LiveAcquisitionCoordinator
    from labdaq.application.open_acquisition_service import OpenAcquisitionCoordinator
    from labdaq.application.operator_messages import OperatorMessage
    from labdaq.application.qualification_service import (
        QualificationRunOutcome,
        RunQualification,
    )
    from labdaq.application.raw_export_service import RawContainerExportService
    from labdaq.application.readiness_service import (
        AcquisitionLaunchIntent,
        DiagnosticReadinessDecision,
        FunctionalPreviewLaunchIntent,
        OpenAcquisitionIntent,
        RawDiagnosticConfirmation,
        RawDiagnosticLaunchIntent,
        ReadinessDecision,
        StartReadinessService,
    )
    from labdaq.application.review_service import SessionReviewService
    from labdaq.application.session_catalog_service import SessionCatalog, SessionCatalogService
    from labdaq.domain.models import HardwareProfile, ProcessingRecipeEnvelope
    from labdaq.domain.plotting import (
        ExportFormat,
        ExportManifest,
        ExportProduct,
        ExportProgress,
        ExportRequest,
        SampleRange,
    )
    from labdaq.domain.qualification import QualificationPlan, QualificationReport
    from labdaq.ports.daq import DaqDiscoveryReport, DaqValidationReport
    from labdaq.ports.operator import (
        LiveMetricSnapshot,
        LivePlotFrame,
        OpenAcquisitionStatus,
        OperatorRunStatus,
        OperatorRunSummary,
        SessionReviewSnapshot,
    )
    from labdaq.ports.plotting import PlotBundle
    from labdaq.ports.preferences import UserPreferencePort
    from labdaq.ports.processing import (
        ChannelExtremaResult,
        TransientDetectionResult,
        TransientDetectionSettings,
    )
    from labdaq.ports.qualification import QualificationProgressPort
    from labdaq.ports.session import RecordedSessionCompletion


class QualificationRunnerFactory(Protocol):
    def __call__(
        self,
        progress: QualificationProgressPort,
        approved_confirmation_ids: tuple[str, ...],
    ) -> RunQualification: ...


@dataclass(frozen=True, slots=True)
class HardwarePresentation:
    discovery: DaqDiscoveryReport | None = None
    validation: DaqValidationReport | None = None
    unavailable: OperatorMessage | None = None
    validation_key: str | None = None


@dataclass(frozen=True, slots=True)
class QualificationPresentation:
    report: QualificationReport | None = None
    report_path: Path | None = None
    outcome: QualificationRunOutcome | None = None


@dataclass(frozen=True, slots=True)
class DesktopServices:
    mode: str
    workspace: ConfigurationWorkspaceService
    destination: SessionDestinationService
    readiness: StartReadinessService
    catalog: SessionCatalogService | None
    daq: DaqService | None
    profile: HardwareProfile
    recipe: ProcessingRecipeEnvelope
    recipe_loader: Callable[[str], ProcessingRecipeEnvelope]
    qualification_plan: QualificationPlan
    available_qualification_plan_ids: tuple[str, ...]
    qualification_plan_loader: Callable[[str | Path], QualificationPlan]
    qualification_runner_factory: QualificationRunnerFactory | None
    preferences: UserPreferencePort
    startup_message: OperatorMessage | None = None
    live: LiveAcquisitionCoordinator | None = None
    open_acquisition: OpenAcquisitionCoordinator | None = None
    review: SessionReviewService | None = None
    raw_export: RawContainerExportService | None = None
    capability_messages: tuple[OperatorMessage, ...] = ()
    functional_preview: bool = False


class DesktopPresenter:
    """Coordinate M10 use cases while retaining no Qt or adapter types."""

    def __init__(self, services: DesktopServices) -> None:
        self.services = services
        self._runner: RunQualification | None = None
        self._qualification_cancel_requested = Event()

    def initial_draft(
        self,
        *,
        template_id: str,
        configuration_path: Path | None = None,
    ) -> ConfigurationDraftSnapshot:
        if configuration_path is not None:
            return self.services.workspace.load(self.services.profile, configuration_path)
        return self.services.workspace.create_from_template(self.services.profile, template_id)

    def update_field(self, field_path: str, value: object) -> ConfigurationDraftSnapshot:
        return self.services.workspace.update_field(field_path, value)

    def recipe_for_draft(
        self,
        draft: ConfigurationDraftSnapshot,
    ) -> ProcessingRecipeEnvelope:
        """Resolve the recipe selected by the current draft."""
        return self.services.recipe_loader(draft.active_recipe_id)

    def field_affects_hardware_validation(self, field_path: str) -> bool:
        from labdaq.application.configuration_workspace import field_affects_hardware_validation

        return field_affects_hardware_validation(field_path)

    def hardware_validation_key(self, draft: ConfigurationDraftSnapshot) -> str | None:
        experiment = draft.normalized
        if experiment is None:
            return None
        enabled = {
            channel_id: next(
                channel for channel in experiment.channels if channel.channel_id == channel_id
            )
            for channel_id in experiment.acquisition.channel_ids
        }
        return canonical_sha256(
            {
                "hardware_profile_id": self.services.profile.hardware_profile_id,
                "hardware_profile_revision": self.services.profile.profile_revision,
                "experiment_hardware_profile_id": experiment.hardware_profile_id,
                "compatible_profile_revision": experiment.compatible_profile_revision,
                "acquisition": experiment.acquisition,
                "channels": [
                    {
                        "channel_id": channel.channel_id,
                        "channel_order": channel.channel_order,
                        "physical_channel": channel.physical_channel,
                        "requested_input_min": channel.requested_input_min,
                        "requested_input_max": channel.requested_input_max,
                        "raw_quantity": channel.raw_quantity,
                        "raw_unit": channel.raw_unit,
                    }
                    for channel in (
                        enabled[channel_id] for channel_id in experiment.acquisition.channel_ids
                    )
                ],
            }
        )

    def check_destination(
        self, draft: ConfigurationDraftSnapshot
    ) -> SessionDestinationPreview | None:
        return (
            self.services.destination.preview(draft.normalized)
            if draft.normalized is not None
            else None
        )

    def refresh_destination_identity(self) -> None:
        self.services.destination.refresh_identity()

    def refresh_hardware(self, draft: ConfigurationDraftSnapshot) -> HardwarePresentation:
        if self.services.daq is None or draft.normalized is None:
            return HardwarePresentation(unavailable=self.services.startup_message)
        discovery = self.services.daq.discover()
        validation = self.services.daq.validate(self.services.profile, draft.normalized)
        return HardwarePresentation(
            discovery,
            validation,
            validation_key=self.hardware_validation_key(draft),
        )

    def evaluate_readiness(
        self,
        draft: ConfigurationDraftSnapshot,
        destination: SessionDestinationPreview | None,
        hardware: HardwarePresentation,
        qualification: QualificationPresentation,
    ) -> ReadinessDecision:
        from labdaq.application.qualification_service import BuildQualificationPlan

        plan = (
            BuildQualificationPlan().build(
                self.services.qualification_plan,
                self.services.profile,
                draft.normalized,
            )
            if draft.normalized is not None
            else self.services.qualification_plan
        )
        return self.services.readiness.evaluate(
            profile=self.services.profile,
            draft=draft,
            destination=destination,
            discovery=hardware.discovery,
            hardware_validation=hardware.validation,
            plan=plan,
            report=qualification.report,
        )

    def evaluate_raw_diagnostic_readiness(
        self,
        draft: ConfigurationDraftSnapshot,
        destination: SessionDestinationPreview | None,
        hardware: HardwarePresentation,
    ) -> DiagnosticReadinessDecision:
        return self.services.readiness.evaluate_raw_diagnostic(
            draft=draft,
            destination=destination,
            discovery=hardware.discovery,
            hardware_validation=hardware.validation,
            live_available=self.services.live is not None,
        )

    def evaluate_open_readiness(
        self, draft: ConfigurationDraftSnapshot, hardware: HardwarePresentation
    ) -> DiagnosticReadinessDecision:
        return self.services.readiness.evaluate_open_acquisition(
            draft=draft,
            discovery=hardware.discovery,
            hardware_validation=hardware.validation,
            live_available=self.services.open_acquisition is not None,
        )

    def run_qualification(
        self,
        draft: ConfigurationDraftSnapshot,
        progress: QualificationProgressPort,
        *,
        approved_confirmation_ids: tuple[str, ...] = (),
    ) -> QualificationPresentation:
        if draft.normalized is None or self.services.qualification_runner_factory is None:
            raise RuntimeError("Qualification requires a valid configuration and DAQ backend")
        from labdaq.application.qualification_service import BuildQualificationPlan

        plan = BuildQualificationPlan().build(
            self.services.qualification_plan,
            self.services.profile,
            draft.normalized,
        )
        runner = self.services.qualification_runner_factory(
            progress,
            approved_confirmation_ids,
        )
        self._runner = runner
        try:
            if self._qualification_cancel_requested.is_set():
                runner.cancel()
            outcome = runner.run(
                plan,
                self.services.profile,
                draft.normalized,
                self.recipe_for_draft(draft),
            )
            return QualificationPresentation(
                report=outcome.report,
                report_path=outcome.report_path,
                outcome=outcome,
            )
        finally:
            runner.close()
            self._runner = None
            self._qualification_cancel_requested.clear()

    def prepare_qualification(self) -> None:
        self._qualification_cancel_requested.clear()

    def select_qualification_plan(self, plan_or_path: str | Path) -> QualificationPlan:
        plan = self.services.qualification_plan_loader(plan_or_path)
        self.services = replace(self.services, qualification_plan=plan)
        return plan

    def cancel_qualification(self) -> None:
        self._qualification_cancel_requested.set()
        if self._runner is not None:
            self._runner.cancel()

    def scan_sessions(self, parent: Path) -> SessionCatalog:
        if self.services.catalog is None:
            raise RuntimeError("Session browsing requires the storage dependency")
        return self.services.catalog.scan(parent)

    def request_launch(
        self,
        draft: ConfigurationDraftSnapshot,
        destination: SessionDestinationPreview,
        qualification: QualificationPresentation,
    ) -> tuple[AcquisitionLaunchIntent, HardwarePresentation, ReadinessDecision]:
        """Refresh hardware and all readiness evidence immediately before launch."""
        hardware = self.refresh_hardware(draft)
        refreshed_destination = self.check_destination(draft)
        if refreshed_destination is None or draft.normalized is None:
            raise RuntimeError("Launch requires a normalized configuration and destination")
        decision = self.evaluate_readiness(
            draft,
            refreshed_destination,
            hardware,
            qualification,
        )
        if qualification.report_path is None:
            raise RuntimeError("Launch requires a retained qualification report")
        intent = self.services.readiness.request_launch(
            decision,
            profile=self.services.profile,
            experiment=draft.normalized,
            destination=refreshed_destination,
            report_path=qualification.report_path,
        )
        if qualification.report is None or decision.qualification_eligibility is None:
            raise RuntimeError("Launch requires current qualification evidence")
        from labdaq.application.qualification_service import AttachQualificationToSession

        attachment = AttachQualificationToSession().build_attachment(
            qualification.report_path,
            qualification.report,
            decision.qualification_eligibility,
        )
        intent = replace(intent, qualification_attachment=attachment)
        return intent, hardware, decision

    def request_raw_diagnostic_launch(
        self,
        draft: ConfigurationDraftSnapshot,
        destination: SessionDestinationPreview,
        confirmation: RawDiagnosticConfirmation,
    ) -> tuple[RawDiagnosticLaunchIntent, HardwarePresentation, DiagnosticReadinessDecision]:
        hardware = self.refresh_hardware(draft)
        refreshed_destination = self.check_destination(draft)
        if refreshed_destination is None or draft.normalized is None or hardware.validation is None:
            raise RuntimeError(
                "Raw diagnostic launch requires a normalized configuration, destination, and hardware validation"
            )
        decision = self.evaluate_raw_diagnostic_readiness(
            draft,
            refreshed_destination,
            hardware,
        )
        intent = self.services.readiness.request_raw_diagnostic_launch(
            decision,
            profile=self.services.profile,
            experiment=draft.normalized,
            destination=refreshed_destination,
            hardware_validation=hardware.validation,
            confirmation=confirmation,
        )
        return intent, hardware, decision

    def request_functional_preview_launch(
        self,
        draft: ConfigurationDraftSnapshot,
        destination: SessionDestinationPreview,
    ) -> tuple[
        FunctionalPreviewLaunchIntent,
        HardwarePresentation,
        DiagnosticReadinessDecision,
    ]:
        """Refresh the editable, storage, and hardware gates immediately before launch."""
        hardware = self.refresh_hardware(draft)
        refreshed_destination = self.check_destination(draft)
        if refreshed_destination is None or draft.normalized is None or hardware.validation is None:
            raise RuntimeError(
                "Functional launch requires a normalized configuration, destination, and hardware validation"
            )
        decision = self.evaluate_raw_diagnostic_readiness(
            draft,
            refreshed_destination,
            hardware,
        )
        intent = self.services.readiness.request_functional_preview_launch(
            decision,
            profile=self.services.profile,
            experiment=draft.normalized,
            destination=refreshed_destination,
            hardware_validation=hardware.validation,
        )
        return intent, hardware, decision

    def request_open_acquisition_launch(
        self, draft: ConfigurationDraftSnapshot
    ) -> tuple[OpenAcquisitionIntent, HardwarePresentation, DiagnosticReadinessDecision]:
        hardware = self.refresh_hardware(draft)
        if draft.normalized is None or hardware.validation is None:
            raise RuntimeError("Open monitoring requires valid acquisition settings and hardware")
        decision = self.evaluate_open_readiness(draft, hardware)
        intent = self.services.readiness.request_open_acquisition_launch(
            decision,
            profile=self.services.profile,
            experiment=draft.normalized,
            hardware_validation=hardware.validation,
            recorded_channel_ids=draft.normalized.acquisition.channel_ids,
        )
        return intent, hardware, decision

    def start_acquisition(
        self,
        intent: AcquisitionLaunchIntent | RawDiagnosticLaunchIntent | FunctionalPreviewLaunchIntent,
    ) -> OperatorRunStatus:
        if self.services.live is None:
            raise RuntimeError(
                "Live acquisition is unavailable because a required dependency is missing"
            )
        return self.services.live.start(intent)

    def stop_acquisition(self) -> OperatorRunStatus:
        if self.services.live is None:
            raise RuntimeError("Live acquisition is unavailable")
        return self.services.live.request_stop()

    def acquisition_status(self) -> OperatorRunStatus | None:
        return self.services.live.status() if self.services.live is not None else None

    def live_frame(
        self,
        signal_stage: SignalStage,
        *,
        channel_ids: tuple[str, ...] | None = None,
    ) -> LivePlotFrame | None:
        if self.services.live is None:
            return None
        return self.services.live.build_plot_frame(signal_stage, channel_ids=channel_ids)

    def live_metrics(self) -> LiveMetricSnapshot | None:
        return self.services.live.metrics() if self.services.live is not None else None

    def set_live_window(self, seconds: float) -> None:
        if self.services.live is not None:
            self.services.live.set_visible_window(seconds)

    def wait_for_acquisition(self, timeout: float | None = None) -> OperatorRunSummary:
        if self.services.live is None:
            raise RuntimeError("Live acquisition is unavailable")
        return self.services.live.wait_for_terminal(timeout)

    def start_monitoring(self, intent: OpenAcquisitionIntent) -> OpenAcquisitionStatus:
        if self.services.open_acquisition is None:
            raise RuntimeError("Open acquisition is unavailable")
        return self.services.open_acquisition.start_monitoring(intent)

    def start_open_recording(
        self, parent_output_directory: Path | None = None
    ) -> OpenAcquisitionStatus:
        if self.services.open_acquisition is None:
            raise RuntimeError("Open acquisition is unavailable")
        if parent_output_directory is None:
            return self.services.open_acquisition.start_recording()
        return self.services.open_acquisition.start_recording(
            parent_output_directory=parent_output_directory
        )

    def stop_open_recording(self) -> OpenAcquisitionStatus:
        if self.services.open_acquisition is None:
            raise RuntimeError("Open acquisition is unavailable")
        return self.services.open_acquisition.stop_recording()

    def end_monitoring(self) -> OpenAcquisitionStatus:
        if self.services.open_acquisition is None:
            raise RuntimeError("Open acquisition is unavailable")
        return self.services.open_acquisition.end_monitoring()

    def open_acquisition_status(self) -> OpenAcquisitionStatus | None:
        service = self.services.open_acquisition
        return service.status() if service is not None else None

    def open_acquisition_frame(self) -> LivePlotFrame | None:
        service = self.services.open_acquisition
        return service.build_plot_frame() if service is not None else None

    def wait_for_open_recording(
        self, timeout: float | None = None
    ) -> RecordedSessionCompletion | None:
        if self.services.open_acquisition is None:
            raise RuntimeError("Open acquisition is unavailable")
        return self.services.open_acquisition.wait_for_recording(timeout)

    def wait_for_monitor_terminal(self, timeout: float | None = None) -> OpenAcquisitionStatus:
        if self.services.open_acquisition is None:
            raise RuntimeError("Open acquisition is unavailable")
        return self.services.open_acquisition.wait_for_terminal(timeout)

    def load_session_review(self, path: Path) -> SessionReviewSnapshot:
        if self.services.review is None:
            raise RuntimeError(
                "Session review is unavailable because a required dependency is missing"
            )
        return self.services.review.load(path)

    def resolve_review_range(
        self,
        path: Path,
        *,
        start_seconds: float | None = None,
        stop_seconds: float | None = None,
    ) -> SampleRange:
        if self.services.review is None:
            raise RuntimeError("Session review is unavailable")
        return self.services.review.resolve_range(
            path,
            start_seconds=start_seconds,
            stop_seconds=stop_seconds,
        )

    def build_review_plot(
        self,
        path: Path,
        representation: PlotRepresentation,
        channel_ids: tuple[str, ...],
        sample_range: SampleRange,
        *,
        signal_stage: SignalStage = SignalStage.CONDITIONED,
    ) -> PlotBundle:
        if self.services.review is None:
            raise RuntimeError("Session review is unavailable")
        if representation in {
            PlotRepresentation.FFT_AMPLITUDE,
            PlotRepresentation.PSD,
        }:
            return self.services.review.build_plot(
                path,
                representation,
                channel_ids,
                sample_range,
                spectral_input_stage=signal_stage,
            )
        return self.services.review.build_plot(
            path,
            representation,
            channel_ids,
            sample_range,
        )

    def detect_review_transients(
        self,
        path: Path,
        channel_ids: tuple[str, ...],
        signal_stage: SignalStage,
        settings: TransientDetectionSettings,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> TransientDetectionResult:
        if self.services.review is None:
            raise RuntimeError("Session transient analysis is unavailable")
        return self.services.review.detect_transients(
            path,
            channel_ids,
            signal_stage,
            settings,
            cancelled=cancelled,
        )

    def compute_review_extrema(
        self,
        path: Path,
        channel_ids: tuple[str, ...],
        signal_stage: SignalStage,
        sample_range: SampleRange,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> tuple[ChannelExtremaResult, ...]:
        if self.services.review is None:
            raise RuntimeError("Session extrema analysis is unavailable")
        return self.services.review.compute_extrema(
            path,
            channel_ids,
            signal_stage,
            sample_range,
            cancelled=cancelled,
        )

    def build_export_request(
        self,
        path: Path,
        product: ExportProduct,
        output_format: ExportFormat,
        representation: PlotRepresentation,
        channel_ids: tuple[str, ...],
        sample_range: SampleRange,
        *,
        signal_stage: SignalStage = SignalStage.CONDITIONED,
    ) -> ExportRequest:
        if self.services.review is None:
            raise RuntimeError("Session export is unavailable")
        if representation in {
            PlotRepresentation.FFT_AMPLITUDE,
            PlotRepresentation.PSD,
        }:
            return self.services.review.build_export_request(
                path,
                product,
                output_format,
                representation,
                channel_ids,
                sample_range,
                spectral_input_stage=signal_stage,
            )
        return self.services.review.build_export_request(
            path,
            product,
            output_format,
            representation,
            channel_ids,
            sample_range,
        )

    def run_export(
        self,
        requests: tuple[ExportRequest, ...],
        *,
        cancelled: Callable[[], bool] | None = None,
        progress: Callable[[ExportProgress], None] | None = None,
    ) -> ExportManifest:
        if self.services.review is None:
            raise RuntimeError("Session export is unavailable")
        return self.services.review.run_export(
            requests,
            cancelled=cancelled,
            progress=progress,
        )

    def run_tdms_export(
        self,
        path: Path,
        *,
        cancelled: Callable[[], bool] | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> object:
        if self.services.raw_export is None:
            raise RuntimeError("TDMS interoperability export is unavailable")
        return self.services.raw_export.export_tdms(
            path,
            cancelled=cancelled,
            progress=progress,
        )

    def close(self) -> None:
        self.cancel_qualification()
        if self.services.live is not None:
            self.services.live.close()
        open_acquisition = getattr(self.services, "open_acquisition", None)
        if open_acquisition is not None:
            open_acquisition.close()
        if self.services.daq is not None:
            self.services.daq.close()
        self.services.preferences.sync()
