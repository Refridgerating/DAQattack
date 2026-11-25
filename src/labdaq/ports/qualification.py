"""Backend-neutral ports and numeric records for hardware qualification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    import numpy as np

    from labdaq.domain.qualification import (
        ConfirmationDocument,
        EvidenceLink,
        OperatorConfirmation,
        QualificationPlan,
        QualificationReport,
    )


@dataclass(frozen=True, slots=True)
class ChannelDiagnosticSummary:
    sample_count: int
    mean_v: float
    median_v: float
    minimum_v: float
    maximum_v: float
    peak_to_peak_v: float
    standard_deviation_v: float
    rms_v: float
    robust_noise_v: float
    range_utilization: float
    near_clipping_fraction: float
    clipping_fraction: float
    nonfinite_count: int
    longest_repeated_value_run: int


@dataclass(frozen=True, slots=True)
class AlignmentDiagnosticSummary:
    lag_samples: int
    normalized_correlation: float
    phase_degrees: float | None


@dataclass(frozen=True, slots=True)
class ResponseDiagnosticSummary:
    baseline_energy: float
    stimulus_energy: float
    response_ratio: float


@dataclass(frozen=True, slots=True)
class CrosstalkDiagnosticSummary:
    stimulated_rms: float
    coupled_rms: float
    coupling_db: float


@dataclass(frozen=True, slots=True)
class QualificationArtifactBundle:
    evidence: tuple[EvidenceLink, ...]
    warning_reason_codes: tuple[str, ...] = ()


class QualificationAnalysisPort(Protocol):
    def channel_diagnostics(
        self,
        samples: np.ndarray,
        *,
        input_range_v: tuple[float, float],
    ) -> ChannelDiagnosticSummary: ...

    def alignment(
        self,
        reference: np.ndarray,
        compared: np.ndarray,
        *,
        sample_rate_hz: float,
        stimulus_frequency_hz: float | None = None,
    ) -> AlignmentDiagnosticSummary: ...

    def response(
        self,
        baseline: np.ndarray,
        stimulus: np.ndarray,
    ) -> ResponseDiagnosticSummary: ...

    def crosstalk(
        self,
        stimulated: np.ndarray,
        unstimulated: np.ndarray,
    ) -> CrosstalkDiagnosticSummary: ...


class OperatorPromptPort(Protocol):
    def obtain(
        self,
        confirmation_id: str,
        statement: str,
        *,
        requested_at: datetime,
    ) -> OperatorConfirmation | None: ...


class QualificationReportRepositoryPort(Protocol):
    def initialize(self, session_path: Path, plan: QualificationPlan) -> None: ...

    def checkpoint(self, session_path: Path, report: QualificationReport) -> Path: ...

    def load(self, report_path: Path) -> QualificationReport: ...

    def load_plan(self, plan_path: Path) -> QualificationPlan: ...


@dataclass(frozen=True, slots=True)
class QualificationProgress:
    phase: str
    message: str
    current_test_id: str | None
    completed_test_ids: tuple[str, ...]
    total_test_count: int
    report_path: Path | None = None
    indeterminate: bool = True


class QualificationProgressPort(Protocol):
    def publish(self, progress: QualificationProgress) -> None: ...


class ConfirmationRepositoryPort(Protocol):
    def load(self, path: Path) -> ConfirmationDocument: ...


@dataclass(frozen=True, slots=True)
class EnvironmentInformation:
    operating_system: str
    application_version: str
    python_version: str


class EnvironmentInformationPort(Protocol):
    def capture(self) -> EnvironmentInformation: ...


class QualificationAttachmentPort(Protocol):
    def copy_report(self, report_path: Path, destination_session: Path) -> Path: ...


class QualificationArtifactPort(Protocol):
    def generate(
        self,
        session_path: Path,
        *,
        ordered_channel_ids: tuple[str, ...],
        sample_rate_hz: float,
        sample_count: int,
    ) -> QualificationArtifactBundle: ...
