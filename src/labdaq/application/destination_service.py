"""Non-mutating session destination preview and capacity checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from labdaq.application.acquisition_engine import SystemAcquisitionClock
from labdaq.application.configuration_workspace import FieldIssue
from labdaq.errors import ErrorSeverity
from labdaq.ports.session import RecordingPolicy, SessionPlan

if TYPE_CHECKING:
    from datetime import datetime

    from labdaq.domain.models import ExperimentConfig
    from labdaq.ports.session import (
        SessionLocationPort,
        SessionPathPlannerPort,
        StorageCapacityPort,
    )


@dataclass(frozen=True, slots=True)
class SessionDestinationPreview:
    plan: SessionPlan
    sanitized_name: str
    parent_exists: bool
    parent_is_directory: bool
    writable: bool
    collision: bool
    free_bytes: int | None
    required_bytes: int
    issues: tuple[FieldIssue, ...]

    @property
    def valid(self) -> bool:
        return not any(
            issue.severity in {ErrorSeverity.ERROR, ErrorSeverity.CRITICAL} for issue in self.issues
        )


class SessionDestinationService:
    """Keep a stable preview identity until the operator explicitly refreshes it."""

    def __init__(
        self,
        planner: SessionPathPlannerPort,
        capacity: StorageCapacityPort,
        locations: SessionLocationPort,
        *,
        policy: RecordingPolicy | None = None,
        clock: SystemAcquisitionClock | None = None,
    ) -> None:
        self._planner = planner
        self._capacity = capacity
        self._locations = locations
        self._policy = policy or RecordingPolicy()
        self._clock = clock or SystemAcquisitionClock()
        self._session_id = str(uuid4())
        self._created_at = self._clock.wall_time()

    def refresh_identity(self) -> None:
        self._session_id = str(uuid4())
        self._created_at = self._clock.wall_time()

    def preview(self, experiment: ExperimentConfig) -> SessionDestinationPreview:
        plan = self._planner.plan_session(
            experiment.session,
            session_id=self._session_id,
            created_at=self._created_at,
            policy=self._policy,
        )
        parent_exists = self._locations.exists(plan.parent_directory)
        parent_is_directory = self._locations.is_directory(plan.parent_directory)
        writable = parent_is_directory and self._capacity.is_writable(plan.parent_directory)
        collision = self._locations.exists(plan.session_path)
        required = self._capacity.estimate_required_bytes(experiment, self._policy)
        free = self._capacity.free_bytes(plan.parent_directory) if writable else None
        issues: list[FieldIssue] = []
        if not parent_exists:
            issues.append(
                _issue(
                    "DAQ.STORAGE.INVALID_SESSION_PARENT",
                    "The selected session parent does not exist.",
                    "Choose an existing directory.",
                )
            )
        elif not parent_is_directory:
            issues.append(
                _issue(
                    "DAQ.STORAGE.INVALID_SESSION_PARENT",
                    "The selected session parent is not a directory.",
                    "Choose an existing directory.",
                )
            )
        elif not writable:
            issues.append(
                _issue(
                    "DAQ.STORAGE.INVALID_SESSION_PARENT",
                    "The selected session parent is not writable.",
                    "Choose a writable directory or correct its permissions.",
                )
            )
        if collision:
            issues.append(
                _issue(
                    "DAQ.STORAGE.SESSION_PATH_COLLISION",
                    "The previewed session directory already exists.",
                    "Generate a new destination preview; existing data is never overwritten.",
                )
            )
        if free is not None and free < required:
            issues.append(
                _issue(
                    "DAQ.STORAGE.INSUFFICIENT_SPACE",
                    "The selected destination has insufficient free space.",
                    "Free disk space or choose a different destination.",
                )
            )
        issues.extend(
            FieldIssue(
                "DAQ.STORAGE.PATH_LENGTH_WARNING",
                "storage.parent_directory",
                ErrorSeverity.WARNING,
                warning,
                "Use a shorter parent path if Windows tools cannot access the destination.",
            )
            for warning in plan.warnings
        )
        return SessionDestinationPreview(
            plan=plan,
            sanitized_name=plan.experiment_slug,
            parent_exists=parent_exists,
            parent_is_directory=parent_is_directory,
            writable=writable,
            collision=collision,
            free_bytes=free,
            required_bytes=required,
            issues=tuple(issues),
        )

    @property
    def created_at(self) -> datetime:
        return self._created_at


def _issue(code: str, message: str, remediation: str) -> FieldIssue:
    return FieldIssue(
        code,
        "storage.parent_directory",
        ErrorSeverity.ERROR,
        message,
        remediation,
    )
