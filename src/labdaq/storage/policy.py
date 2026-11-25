"""Format-independent session naming and capacity-estimation policy."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from labdaq.domain.naming import validate_slug
from labdaq.ports.session import RecordingPolicy, SessionPlan

if TYPE_CHECKING:
    from labdaq.domain.models import ExperimentConfig, SessionConfig


class SessionPathPolicy:
    """Build a stable preview without touching the filesystem."""

    def plan_session(
        self,
        session: SessionConfig,
        *,
        session_id: str,
        created_at: datetime,
        policy: RecordingPolicy,
    ) -> SessionPlan:
        validate_slug(session.experiment_slug)
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        parsed_id = UUID(session_id)
        timestamp = created_at.astimezone(UTC).strftime("%Y-%m-%d_%H%M%S")
        directory_name = f"{timestamp}_{session.experiment_slug}_{parsed_id.hex[:8]}"
        parent = session.parent_output_directory.absolute()
        session_path = parent / directory_name
        warnings: list[str] = []
        if len(str(session_path)) >= policy.path_warning_length:
            warnings.append(
                f"The planned session path is {len(str(session_path))} characters; "
                f"the conservative warning threshold is {policy.path_warning_length}."
            )
        return SessionPlan(
            session_id=str(parsed_id),
            parent_directory=parent,
            experiment_name=session.experiment_name,
            experiment_slug=session.experiment_slug,
            directory_name=directory_name,
            session_path=session_path,
            created_at=created_at.astimezone(UTC),
            warnings=tuple(warnings),
        )


def estimate_required_bytes(experiment: ExperimentConfig, policy: RecordingPolicy) -> int:
    """Return a conservative fixed-run estimate or continuous-run reserve."""
    duration = experiment.acquisition.requested_duration_seconds
    if duration is None:
        return policy.warning_free_bytes + policy.metadata_reserve_bytes
    raw_bytes = (
        len(experiment.acquisition.channel_ids)
        * experiment.acquisition.sample_rate_hz
        * duration
        * 8
    )
    estimated_data = math.ceil(raw_bytes * policy.hdf5_overhead_multiplier)
    return (
        estimated_data
        + policy.metadata_margin_bytes
        + policy.metadata_reserve_bytes
        + policy.hard_stop_free_bytes
    )
