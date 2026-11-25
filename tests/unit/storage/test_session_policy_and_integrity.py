from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import numpy as np
import pytest

from labdaq.adapters.configuration_json import JsonConfigurationAdapter
from labdaq.ports.session import (
    IntegrityIssue,
    IntegrityStatus,
    PersistenceReceipt,
    RecordingPolicy,
    Recoverability,
    SessionCompletionState,
    SessionIntegrityReport,
    SessionPlan,
    VerificationLevel,
)
from labdaq.storage.integrity import LogicalDataDigest, file_sha256, logical_sha256
from labdaq.storage.policy import SessionPathPolicy, estimate_required_bytes

FIXTURE = Path("tests/fixtures/configuration/valid-four-channel-synthetic-v1.json")
SESSION_ID = "12345678-1234-5678-9abc-def012345678"
CREATED = datetime(2026, 8, 12, 16, 30, 5, tzinfo=UTC)


def _experiment() -> object:
    return JsonConfigurationAdapter().load_experiment_config(FIXTURE)


def test_session_path_preview_is_stable_uuid_backed_and_warns_for_long_paths(
    tmp_path: Path,
) -> None:
    experiment = _experiment()
    session = replace(experiment.session, parent_output_directory=tmp_path)  # type: ignore[attr-defined]
    policy = RecordingPolicy(
        warning_free_bytes=2048,
        hard_stop_free_bytes=1024,
        path_warning_length=10,
    )

    first = SessionPathPolicy().plan_session(
        session,
        session_id=SESSION_ID,
        created_at=CREATED,
        policy=policy,
    )
    second = SessionPathPolicy().plan_session(
        session,
        session_id=SESSION_ID,
        created_at=CREATED,
        policy=policy,
    )

    assert first == second
    assert first.directory_name == ("2026-08-12_163005_synthetic-four-channel-vibration_12345678")
    assert first.session_path.parent == tmp_path.absolute()
    assert first.warnings
    assert first.to_dict()["session_id"] == str(UUID(SESSION_ID))


def test_session_path_policy_rejects_invalid_identity_and_time(tmp_path: Path) -> None:
    experiment = _experiment()
    session = replace(experiment.session, parent_output_directory=tmp_path)  # type: ignore[attr-defined]
    policy = RecordingPolicy(warning_free_bytes=2048, hard_stop_free_bytes=1024)

    with pytest.raises(ValueError):
        SessionPathPolicy().plan_session(
            session,
            session_id="not-a-uuid",
            created_at=CREATED,
            policy=policy,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        SessionPathPolicy().plan_session(
            session,
            session_id=SESSION_ID,
            created_at=datetime(2026, 8, 12),
            policy=policy,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tdms_overhead_multiplier", 0.5),
        ("metadata_margin_bytes", 0),
        ("metadata_reserve_bytes", -1),
        ("warning_free_bytes", 100),
        ("capacity_check_interval_seconds", 0.0),
        ("checkpoint_interval_seconds", float("nan")),
    ],
)
def test_recording_policy_rejects_unsafe_values(field: str, value: object) -> None:
    changes = {field: value}
    if field == "warning_free_bytes":
        changes["hard_stop_free_bytes"] = 100
    with pytest.raises(ValueError):
        replace(RecordingPolicy(), **changes)


def test_recording_policy_defaults_match_version_one_capacity_contract() -> None:
    policy = RecordingPolicy()

    assert policy.tdms_overhead_multiplier == 1.10
    assert policy.metadata_margin_bytes == 16 * 1024 * 1024
    assert policy.metadata_reserve_bytes == 1024 * 1024
    assert policy.warning_free_bytes == 1024 * 1024 * 1024
    assert policy.hard_stop_free_bytes == 512 * 1024 * 1024
    assert policy.capacity_check_interval_seconds == 1.0
    assert policy.checkpoint_interval_seconds == 1.0
    assert policy.path_warning_length == 240
    assert policy.file_checksums is True


def test_storage_estimate_covers_data_overhead_and_reserves() -> None:
    experiment = _experiment()
    policy = RecordingPolicy(
        tdms_overhead_multiplier=1.1,
        metadata_margin_bytes=100,
        metadata_reserve_bytes=10,
        warning_free_bytes=2000,
        hard_stop_free_bytes=1000,
    )
    expected_raw = 4 * 50_000 * 10 * 8
    assert estimate_required_bytes(experiment, policy) == int(expected_raw * 1.1) + 1110  # type: ignore[arg-type]
    continuous = replace(
        experiment,  # type: ignore[arg-type]
        acquisition=replace(
            experiment.acquisition,  # type: ignore[attr-defined]
            sampling_mode=type(experiment.acquisition.sampling_mode).CONTINUOUS,  # type: ignore[attr-defined]
            requested_duration_seconds=None,
        ),
    )
    assert estimate_required_bytes(continuous, policy) == 2010


def test_active_baseline_and_legacy_draft_resolve_independently() -> None:
    documents = JsonConfigurationAdapter()
    canonical = documents.load_bundled_processing_recipe("baseline")
    named_snapshot = documents.load_bundled_processing_recipe("baseline-v1-draft")

    assert canonical.recipe_version == "v1"
    assert canonical.status.value == "active"
    assert len(canonical.stages) == 3
    assert named_snapshot.recipe_version == "v1-draft"
    assert named_snapshot.status.value == "draft"
    assert named_snapshot.stages == ()
    assert named_snapshot.recipe_id == "baseline"
    assert named_snapshot.recipe_version == "v1-draft"
    assert named_snapshot.stages == ()


def test_logical_digest_is_exact_and_independent_of_chunk_boundaries(tmp_path: Path) -> None:
    values = np.arange(60, dtype=np.float64).reshape(4, 15)
    all_at_once = logical_sha256([values])
    rechunked = logical_sha256([values[:, :3], values[:, 3:11], values[:, 11:]])

    assert all_at_once == rechunked
    digest = LogicalDataDigest()
    digest.update(values)
    assert digest.hexdigest() == all_at_once
    path = tmp_path / "bytes.bin"
    path.write_bytes(b"abc")
    assert file_sha256(path) == ("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
    with pytest.raises(ValueError):
        digest.update(np.zeros(3))
    with pytest.raises(ValueError):
        digest.update(np.zeros((2, 3), dtype=np.float32))
    with pytest.raises(ValueError):
        file_sha256(path, block_size=0)


def test_session_records_validate_and_serialize(tmp_path: Path) -> None:
    plan = SessionPlan(
        session_id=SESSION_ID,
        parent_directory=tmp_path,
        experiment_name="Experiment",
        experiment_slug="experiment",
        directory_name="run",
        session_path=tmp_path / "run",
        created_at=CREATED,
    )
    receipt = PersistenceReceipt(SESSION_ID, 0, 9, 10, 10, 2, CREATED)
    issue = IntegrityIssue(
        "DAQ.STORAGE.TEST_WARNING",
        IntegrityStatus.WARNING,
        "warning",
        {"value": 1},
    )
    report = SessionIntegrityReport(
        tmp_path / "run",
        VerificationLevel.FAST,
        IntegrityStatus.WARNING,
        SessionCompletionState.RECORDING,
        SessionCompletionState.INTERRUPTED,
        Recoverability.RECOVERY_CANDIDATE,
        SESSION_ID,
        10,
        8,
        {"a": 10},
        {"raw.tdms": None},
        1,
        (issue,),
        CREATED,
    )

    assert plan.to_dict()["directory_name"] == "run"
    assert receipt.to_dict()["accepted_sample_count"] == 10
    assert report.to_dict()["issues"][0]["code"] == issue.code

    with pytest.raises(ValueError):
        replace(plan, session_path=tmp_path / "other")
    with pytest.raises(ValueError):
        replace(receipt, last_sample_index=10)
    with pytest.raises(ValueError):
        replace(report, readable_common_sample_count=-1)
