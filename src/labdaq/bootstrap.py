"""Composition root for concrete application dependencies."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from labdaq.application.configuration_service import ConfigurationService
from labdaq.errors import DependencyUnavailableError

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication

    from labdaq.application.acquisition_engine import AcquisitionEngine
    from labdaq.application.daq_service import DaqService
    from labdaq.application.export_service import ExportCoordinator
    from labdaq.application.live_service import LiveAcquisitionCoordinator
    from labdaq.application.open_acquisition_service import OpenAcquisitionCoordinator
    from labdaq.application.plotting_service import PlotDataService
    from labdaq.application.processing_service import (
        RecordedSessionProcessingService,
        StreamingProcessingService,
    )
    from labdaq.application.qualification_service import (
        RecoverQualificationReport,
        RunQualification,
    )
    from labdaq.application.raw_export_service import RawContainerExportService
    from labdaq.application.review_service import SessionReviewService
    from labdaq.application.session_service import (
        RecordingSessionService,
        SessionInspectionService,
        SessionPlanningService,
        SessionRecoveryService,
        SessionReplayService,
    )
    from labdaq.domain.models import ExperimentConfig, HardwareProfile
    from labdaq.domain.qualification import ConfirmationDocument, QualificationPlan
    from labdaq.gui.main_window import LabDAQMainWindow
    from labdaq.gui.presentation import DesktopPresenter
    from labdaq.ports.daq import DaqPort
    from labdaq.ports.preferences import UserPreferencePort
    from labdaq.ports.processing import ScientificProcessingPort
    from labdaq.ports.qualification import (
        QualificationProgressPort,
        QualificationReportRepositoryPort,
    )
    from labdaq.ports.session import RecordingPolicy


def create_configuration_service() -> ConfigurationService:
    """Bind the M01 JSON adapter only when configuration behavior is requested."""
    from labdaq.adapters.configuration_json import JsonConfigurationAdapter

    return ConfigurationService(JsonConfigurationAdapter())


def load_bundled_processing_recipe(recipe_id: str) -> tuple[object, dict[str, object]]:
    """Load and encode a bundled recipe at the composition boundary."""
    from labdaq.adapters.configuration_json import JsonConfigurationAdapter

    documents = JsonConfigurationAdapter()
    recipe = documents.load_bundled_processing_recipe(recipe_id)
    return recipe, documents.encode(recipe)


def create_daq_service(
    *,
    backend: str,
    scenario_path: Path | None = None,
) -> DaqService:
    """Bind an explicitly selected DAQ backend and its scenario."""
    from labdaq.application.daq_service import DaqService

    return DaqService(_create_daq_adapter(backend=backend, scenario_path=scenario_path))


def create_acquisition_engine(
    *,
    backend: str,
    scenario_path: Path | None = None,
) -> AcquisitionEngine:
    """Bind the M04 engine to one explicitly selected DAQ adapter."""
    from labdaq.application.acquisition_engine import AcquisitionEngine

    return AcquisitionEngine(_create_daq_adapter(backend=backend, scenario_path=scenario_path))


def create_recording_session_service(
    *,
    backend: str,
    scenario_path: Path | None = None,
    policy: RecordingPolicy | None = None,
) -> RecordingSessionService:
    """Bind acquisition to the HDF5-primary durable-session adapter."""
    from labdaq.adapters.session import create_session_store
    from labdaq.application.session_service import RecordingSessionService

    store = create_session_store()
    return RecordingSessionService(
        _create_daq_adapter(backend=backend, scenario_path=scenario_path),
        store,
        store,
        store,
        policy=policy,
    )


def create_session_planning_service(
    *,
    policy: RecordingPolicy | None = None,
) -> SessionPlanningService:
    from labdaq.adapters.session import create_session_store
    from labdaq.application.session_service import SessionPlanningService

    return SessionPlanningService(create_session_store(), policy=policy)


def create_session_inspection_service() -> SessionInspectionService:
    from labdaq.adapters.session import create_session_store
    from labdaq.application.session_service import SessionInspectionService

    return SessionInspectionService(create_session_store())


def create_session_recovery_service() -> SessionRecoveryService:
    from labdaq.adapters.session import create_session_store
    from labdaq.application.session_service import SessionRecoveryService

    return SessionRecoveryService(create_session_store())


def create_session_replay_service() -> SessionReplayService:
    from labdaq.adapters.session import create_session_store
    from labdaq.application.session_service import SessionReplayService

    return SessionReplayService(create_session_store())


def create_streaming_processing_service() -> StreamingProcessingService:
    """Bind the application processing service to the optional SciPy implementation."""
    from labdaq.application.processing_service import StreamingProcessingService

    return StreamingProcessingService(_create_scientific_processing())


def create_recorded_session_processing_service() -> RecordedSessionProcessingService:
    """Bind verified M05 replay and recipe JSON to the M06 numerical pipeline."""
    from labdaq.adapters.configuration_json import JsonConfigurationAdapter
    from labdaq.adapters.session import create_session_store
    from labdaq.application.processing_service import RecordedSessionProcessingService

    return RecordedSessionProcessingService(
        create_session_store(),
        JsonConfigurationAdapter(),
        _create_scientific_processing(),
    )


def create_plot_data_service() -> PlotDataService:
    """Bind verified replay and M06 processing to the headless plot-data service."""
    from labdaq.adapters.configuration_json import JsonConfigurationAdapter
    from labdaq.adapters.session import create_session_store
    from labdaq.application.plotting_service import PlotDataService

    return PlotDataService(
        create_session_store(),
        JsonConfigurationAdapter(),
        _create_scientific_processing(),
    )


def create_export_coordinator() -> ExportCoordinator:
    """Bind M07 application services to filesystem, CSV, JSON, and figure adapters."""
    from labdaq.adapters.export import (
        CsvArtifactWriter,
        FileSystemArtifactStore,
        JsonArtifactWriter,
        MatplotlibStaticRenderer,
    )
    from labdaq.application.export_service import ExportCoordinator, ExportService

    service = ExportService(
        create_plot_data_service(),
        FileSystemArtifactStore(),
        CsvArtifactWriter(),
        JsonArtifactWriter(),
        MatplotlibStaticRenderer(),
    )
    return ExportCoordinator(service)


def create_raw_container_export_service() -> RawContainerExportService:
    """Bind verified replay to the optional npTDMS interoperability writer."""
    try:
        import nptdms  # type: ignore[import-untyped]  # noqa: F401

        from labdaq.adapters.tdms.export import TdmsArtifactWriter
    except ModuleNotFoundError as cause:
        if cause.name != "nptdms":
            raise
        raise DependencyUnavailableError(
            "TDMS interoperability export requires the tdms extra.",
            code="DAQ.DEPENDENCY.UNAVAILABLE",
            context={"dependency": "nptdms", "feature": "tdms_export"},
            remediation='Install LabDAQ with the "tdms" extra.',
            cause=cause,
        ) from cause
    from labdaq.adapters.export import FileSystemArtifactStore, PlainJsonArtifactWriter
    from labdaq.adapters.session import create_session_store
    from labdaq.application.raw_export_service import RawContainerExportService

    store = create_session_store()
    return RawContainerExportService(
        store,
        store,
        store,
        TdmsArtifactWriter(),
        FileSystemArtifactStore(),
        PlainJsonArtifactWriter(),
    )


def create_live_acquisition_coordinator(
    *,
    backend: str,
    scenario_path: Path | None = None,
    policy: RecordingPolicy | None = None,
) -> LiveAcquisitionCoordinator:
    """Bind the M10 operator coordinator to fresh single-run recording services."""
    from labdaq.adapters.configuration_json import JsonConfigurationAdapter
    from labdaq.adapters.session import create_session_store
    from labdaq.application.live_service import LiveAcquisitionCoordinator
    from labdaq.application.session_service import SessionInspectionService

    inspection_store = create_session_store()

    def recording_factory() -> RecordingSessionService:
        return create_recording_session_service(
            backend=backend,
            scenario_path=scenario_path,
            policy=policy,
        )

    return LiveAcquisitionCoordinator(
        recording_factory,
        SessionInspectionService(inspection_store),
        _create_scientific_processing(),
        JsonConfigurationAdapter(),
    )


def create_open_acquisition_coordinator(
    *,
    backend: str,
    scenario_path: Path | None = None,
    policy: RecordingPolicy | None = None,
    default_scenario_real_time: bool = False,
) -> OpenAcquisitionCoordinator:
    """Bind continuous diagnostic monitoring to one DAQ adapter and session store."""
    from labdaq.adapters.configuration_json import JsonConfigurationAdapter
    from labdaq.adapters.session import create_session_store
    from labdaq.application.open_acquisition_service import OpenAcquisitionCoordinator

    store = create_session_store()
    return OpenAcquisitionCoordinator(
        _create_daq_adapter(
            backend=backend,
            scenario_path=scenario_path,
            default_scenario_real_time=default_scenario_real_time,
        ),
        store,
        store,
        JsonConfigurationAdapter(),
        policy=policy,
    )


def create_session_review_service() -> SessionReviewService:
    """Bind full verification, bounded events, plotting, and export history for M10."""
    from labdaq.adapters.configuration_json import JsonConfigurationAdapter
    from labdaq.adapters.export import FileSystemExportHistoryAdapter
    from labdaq.adapters.session import create_session_store
    from labdaq.application.processing_service import RecordedSessionAnalysisService
    from labdaq.application.review_service import SessionReviewService
    from labdaq.application.session_service import SessionInspectionService

    store = create_session_store()
    return SessionReviewService(
        SessionInspectionService(store),
        store,
        FileSystemExportHistoryAdapter(),
        create_plot_data_service(),
        create_export_coordinator(),
        analysis=RecordedSessionAnalysisService(
            store,
            JsonConfigurationAdapter(),
            _create_scientific_processing(),
        ),
    )


def create_qualification_runner(
    *,
    backend: str,
    scenario_path: Path | None = None,
    confirmations: ConfirmationDocument | None = None,
    interactive: bool = False,
    operator: str = "operator",
    policy: RecordingPolicy | None = None,
    progress: QualificationProgressPort | None = None,
) -> RunQualification:
    """Bind M08 to one DAQ, one recording service, and one TDMS store."""
    from labdaq.adapters.qualification_json import AtomicQualificationReportRepository
    from labdaq.adapters.qualification_prompts import (
        ConfirmationFilePrompt,
        InteractiveOperatorPrompt,
    )
    from labdaq.adapters.session import create_session_store
    from labdaq.application.qualification_artifact_service import (
        QualificationArtifactService,
    )
    from labdaq.application.qualification_service import RunQualification
    from labdaq.application.session_service import RecordingSessionService
    from labdaq.processing.qualification import NumPyQualificationAnalysis

    daq = _create_daq_adapter(backend=backend, scenario_path=scenario_path)
    store = create_session_store()
    recording = RecordingSessionService(daq, store, store, store, policy=policy)
    prompt = (
        InteractiveOperatorPrompt(operator=operator)
        if interactive
        else ConfirmationFilePrompt(confirmations)
    )
    try:
        artifacts = QualificationArtifactService(create_export_coordinator())
    except DependencyUnavailableError:
        artifacts = None
    return RunQualification(
        daq,
        recording,
        store,
        store,
        NumPyQualificationAnalysis(),
        AtomicQualificationReportRepository(),
        prompt,
        artifacts,
        progress,
    )


def create_desktop_presenter(
    *,
    backend: str = "simulated",
    profile_path: Path | None = None,
    qualification_plan: str | Path = "preflight-v1",
    scenario_path: Path | None = None,
    preferences: UserPreferencePort | None = None,
    experiment_mode: str = "production",
    functional_preview: bool = False,
) -> DesktopPresenter:
    """Lazily compose the complete M10 presenter without importing Qt widgets."""
    from datetime import UTC, datetime

    from labdaq.adapters.configuration_json import JsonConfigurationAdapter
    from labdaq.adapters.filesystem import (
        FileSystemCapacityAdapter,
        FileSystemSessionCatalogAdapter,
    )
    from labdaq.application.configuration_workspace import ConfigurationWorkspaceService
    from labdaq.application.destination_service import SessionDestinationService
    from labdaq.application.operator_messages import operator_message_from_error
    from labdaq.application.readiness_service import StartReadinessService
    from labdaq.application.session_catalog_service import SessionCatalogService
    from labdaq.domain.qualification import ConfirmationDocument, OperatorConfirmation
    from labdaq.errors import LabDAQError
    from labdaq.gui.presentation import DesktopPresenter, DesktopServices
    from labdaq.storage.policy import SessionPathPolicy

    documents = JsonConfigurationAdapter()
    profile = (
        documents.load_hardware_profile(profile_path)
        if profile_path is not None
        else documents.load_bundled_hardware_profile(
            "cdaq-9171-ni-9239-four-channel",
            2,
        )
    )
    recipe = documents.load_bundled_processing_recipe("baseline-v1")
    plan = load_qualification_plan(qualification_plan)
    default_desktop_scenario = scenario_path is None
    if (
        backend == "simulated"
        and experiment_mode == "accelerometer_commissioning"
        and scenario_path is None
    ):
        scenario_path = (
            Path(__file__).parent / "resources/config/simulator-scenarios/clean-three-axis-v1.json"
        )
    workspace = ConfigurationWorkspaceService(
        documents,
        active_recipe_id=recipe.recipe_id,
        active_recipe_version=recipe.recipe_version,
    )
    filesystem = FileSystemCapacityAdapter()
    destination = SessionDestinationService(SessionPathPolicy(), filesystem, filesystem)

    startup_message = None
    try:
        daq = create_daq_service(backend=backend, scenario_path=scenario_path)
    except LabDAQError as error:
        if backend != "ni":
            raise
        daq = None
        startup_message = operator_message_from_error(error)

    try:
        from labdaq.adapters.session import create_session_store

        catalog = SessionCatalogService(
            FileSystemSessionCatalogAdapter(),
            create_session_store(),
        )
    except (ModuleNotFoundError, DependencyUnavailableError):
        catalog = None

    capability_messages = []
    try:
        live = create_live_acquisition_coordinator(
            backend=backend,
            scenario_path=scenario_path,
        )
    except (ModuleNotFoundError, DependencyUnavailableError) as error:
        live = None
        capability_messages.append(
            operator_message_from_error(
                error
                if isinstance(error, LabDAQError)
                else DependencyUnavailableError(
                    "Live acquisition dependencies are unavailable.",
                    code="DAQ.DEPENDENCY.UNAVAILABLE",
                    context={"dependency": error.name or "unknown", "feature": "live_acquisition"},
                    remediation='Install LabDAQ with the "full" extra.',
                    cause=error,
                )
            )
        )
    try:
        open_acquisition = create_open_acquisition_coordinator(
            backend=backend,
            scenario_path=scenario_path,
            default_scenario_real_time=default_desktop_scenario,
        )
    except (ModuleNotFoundError, LabDAQError) as error:
        open_acquisition = None
        capability_messages.append(
            operator_message_from_error(
                error
                if isinstance(error, LabDAQError)
                else DependencyUnavailableError(
                    "Open-acquisition dependencies are unavailable.",
                    code="DAQ.DEPENDENCY.UNAVAILABLE",
                    context={
                        "dependency": error.name or "unknown",
                        "feature": "open_acquisition",
                    },
                    remediation='Install LabDAQ with the "full" extra.',
                    cause=error,
                )
            )
        )
    try:
        review = create_session_review_service()
    except (ModuleNotFoundError, DependencyUnavailableError) as error:
        review = None
        capability_messages.append(
            operator_message_from_error(
                error
                if isinstance(error, LabDAQError)
                else DependencyUnavailableError(
                    "Session review dependencies are unavailable.",
                    code="DAQ.DEPENDENCY.UNAVAILABLE",
                    context={"dependency": error.name or "unknown", "feature": "session_review"},
                    remediation='Install LabDAQ with the "full" extra.',
                    cause=error,
                )
            )
        )
    try:
        raw_export = create_raw_container_export_service()
    except (ModuleNotFoundError, DependencyUnavailableError) as error:
        raw_export = None
        capability_messages.append(
            operator_message_from_error(
                error
                if isinstance(error, LabDAQError)
                else DependencyUnavailableError(
                    "TDMS interoperability export is unavailable.",
                    code="DAQ.DEPENDENCY.UNAVAILABLE",
                    context={"dependency": error.name or "unknown", "feature": "tdms_export"},
                    remediation='Install LabDAQ with the "tdms" extra.',
                    cause=error,
                )
            )
        )

    def qualification_runner_factory(
        progress: QualificationProgressPort,
        approved_confirmation_ids: tuple[str, ...],
    ) -> RunQualification:
        confirmations = ConfirmationDocument(
            1,
            tuple(
                OperatorConfirmation(
                    confirmation_id=confirmation_id,
                    confirmed=True,
                    confirmed_at=datetime.now(UTC),
                    operator="desktop-operator",
                    statement=f"The operator explicitly confirmed {confirmation_id} in M09.",
                    evidence_reference="M09 required-confirmation dialog",
                )
                for confirmation_id in approved_confirmation_ids
            ),
        )
        return create_qualification_runner(
            backend=backend,
            scenario_path=scenario_path,
            confirmations=confirmations,
            progress=progress,
        )

    if preferences is None:
        # QSettings is the production implementation and remains lazy so an
        # injected headless presenter does not require PySide6.
        from labdaq.adapters.qt_preferences import QSettingsPreferenceAdapter

        preferences = QSettingsPreferenceAdapter()

    services = DesktopServices(
        mode=backend,
        workspace=workspace,
        destination=destination,
        readiness=StartReadinessService(),
        catalog=catalog,
        daq=daq,
        profile=profile,
        recipe=recipe,
        recipe_loader=documents.load_bundled_processing_recipe,
        qualification_plan=plan,
        available_qualification_plan_ids=("preflight-v1", "throughput-v1", "full-v1"),
        qualification_plan_loader=load_qualification_plan,
        qualification_runner_factory=(qualification_runner_factory if daq is not None else None),
        preferences=preferences,
        startup_message=startup_message,
        live=live,
        open_acquisition=open_acquisition,
        review=review,
        raw_export=raw_export,
        capability_messages=tuple(capability_messages),
        functional_preview=functional_preview,
    )
    return DesktopPresenter(services)


def create_desktop_application(
    *,
    backend: str = "simulated",
    profile_path: Path | None = None,
    configuration_path: Path | None = None,
    qualification_plan: str | Path = "preflight-v1",
    scenario_path: Path | None = None,
    argv: list[str] | None = None,
    preferences: UserPreferencePort | None = None,
    experiment_mode: str = "production",
    functional_preview: bool = False,
) -> tuple[QApplication, LabDAQMainWindow]:
    """Lazily create QApplication and the M10 main window at the composition root."""
    from PySide6.QtWidgets import QApplication

    from labdaq.gui.main_window import LabDAQMainWindow

    application = cast("QApplication | None", QApplication.instance()) or QApplication(argv or [])
    presenter = create_desktop_presenter(
        backend=backend,
        profile_path=profile_path,
        qualification_plan=qualification_plan,
        scenario_path=scenario_path,
        preferences=preferences,
        experiment_mode=experiment_mode,
        functional_preview=functional_preview,
    )
    variable_rate = presenter.services.profile.profile_revision == 2
    if experiment_mode == "accelerometer_commissioning":
        template_id = (
            "accelerometer-commissioning-v3" if variable_rate else "accelerometer-commissioning-v2"
        )
    else:
        template_id = f"raw-voltage-v{2 if variable_rate else 1}"
    window = LabDAQMainWindow(
        presenter,
        template_id=template_id,
        configuration_path=configuration_path,
        profile_path=profile_path,
        qualification_plan=qualification_plan,
    )
    return application, window


def load_qualification_plan(plan_or_path: str | Path) -> QualificationPlan:
    from labdaq.adapters.qualification_json import QualificationJsonCodec

    return QualificationJsonCodec().load_plan(plan_or_path)


def load_qualification_confirmations(path: Path) -> ConfirmationDocument:
    from labdaq.adapters.qualification_json import QualificationJsonCodec

    return QualificationJsonCodec().load_confirmations(path)


def create_qualification_repository() -> QualificationReportRepositoryPort:
    from labdaq.adapters.qualification_json import AtomicQualificationReportRepository

    return AtomicQualificationReportRepository()


def create_qualification_recovery_service() -> RecoverQualificationReport:
    from labdaq.adapters.qualification_json import AtomicQualificationReportRepository
    from labdaq.adapters.session import create_session_store
    from labdaq.application.qualification_service import RecoverQualificationReport

    return RecoverQualificationReport(
        AtomicQualificationReportRepository(),
        create_session_store(),
    )


def current_qualification_fingerprint(
    *,
    backend: str,
    scenario_path: Path | None,
    plan: QualificationPlan,
    profile: HardwareProfile,
    experiment: ExperimentConfig,
) -> str:
    from labdaq.application.qualification_service import (
        build_qualification_fingerprint_input,
        build_qualification_hardware_identity,
    )

    daq = _create_daq_adapter(backend=backend, scenario_path=scenario_path)
    try:
        discovery = daq.discover()
        validation = daq.validate(profile, experiment)
        identity = build_qualification_hardware_identity(discovery, validation)
        return build_qualification_fingerprint_input(
            plan,
            profile,
            experiment,
            identity,
        ).fingerprint
    finally:
        daq.close()


def _create_scientific_processing() -> ScientificProcessingPort:
    try:
        from labdaq.processing import BaselineScientificProcessing
    except ModuleNotFoundError as cause:
        if cause.name not in {"numpy", "scipy"}:
            raise
        raise DependencyUnavailableError(
            "Scientific processing requires NumPy and SciPy.",
            code="DAQ.DEPENDENCY.UNAVAILABLE",
            context={"dependency": cause.name, "feature": "processing"},
            remediation='Install LabDAQ with the "analysis" extra.',
            cause=cause,
        ) from cause
    return BaselineScientificProcessing()


def _create_daq_adapter(
    *,
    backend: str,
    scenario_path: Path | None,
    default_scenario_real_time: bool = False,
) -> DaqPort:
    if backend not in {"simulated", "ni"}:
        raise DependencyUnavailableError(
            f"The requested DAQ backend is unavailable: {backend}.",
            code="DAQ.HARDWARE.BACKEND_UNAVAILABLE",
            context={"requested_backend": backend, "available_backends": ["simulated", "ni"]},
            remediation="Select --backend simulated or --backend ni.",
        )
    if backend == "ni":
        from labdaq.adapters.ni import create_ni_adapter

        if scenario_path is not None:
            raise DependencyUnavailableError(
                "The NI backend does not accept a simulator scenario.",
                code="DAQ.HARDWARE.BACKEND_ARGUMENT_INVALID",
                context={"requested_backend": backend, "argument": "scenario_path"},
                remediation="Remove --scenario when selecting --backend ni.",
            )
        return create_ni_adapter()
    try:
        from labdaq.adapters.simulated.adapter import SimulatedDaqAdapter
        from labdaq.adapters.simulated.scenario import JsonSimulatorScenarioCodec
    except ModuleNotFoundError as cause:
        if cause.name != "numpy":
            raise
        raise DependencyUnavailableError(
            "The simulated DAQ requires NumPy.",
            code="DAQ.DEPENDENCY.UNAVAILABLE",
            context={"dependency": "numpy", "feature": "simulated_daq"},
            remediation='Install LabDAQ with the "simulated" extra.',
            cause=cause,
        ) from cause
    codec = JsonSimulatorScenarioCodec()
    scenario = (
        codec.load(scenario_path)
        if scenario_path is not None
        else codec.load_bundled("clean-four-channel")
    )
    if default_scenario_real_time:
        from dataclasses import replace

        scenario = replace(scenario, pacing_mode="real_time")
    return SimulatedDaqAdapter(scenario)


def load_daq_configuration(
    *,
    experiment_path: Path,
    profile_path: Path | None = None,
) -> tuple[HardwareProfile, ExperimentConfig]:
    """Load DAQ configuration documents at the composition boundary."""
    from labdaq.adapters.configuration_json import JsonConfigurationAdapter

    documents = JsonConfigurationAdapter()
    experiment = documents.load_experiment_config(experiment_path)
    profile = (
        documents.load_hardware_profile(profile_path)
        if profile_path is not None
        else documents.load_bundled_hardware_profile(
            experiment.hardware_profile_id,
            experiment.compatible_profile_revision,
        )
    )
    return profile, experiment
