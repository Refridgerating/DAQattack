import json
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZipFile

import pytest

from labdaq.errors import LabDAQError
from labdaq.feedback import FeedbackForm, create_feedback_bundle, sanitized_diagnostics


def test_feedback_bundle_contains_structured_sanitized_local_evidence(tmp_path: Path) -> None:
    bundle = create_feedback_bundle(
        tmp_path,
        form=FeedbackForm(
            category="simulator",
            summary="Plot did not update",
            steps_to_reproduce="Start the simulator",
            expected_result="Plot advances",
            actual_result="Plot remained still",
            impact="Blocked review",
        ),
        now=datetime(2026, 8, 24, 12, 0, tzinfo=UTC),
    )

    assert bundle.name.startswith("labdaq-feedback-20260824T120000Z-")
    with ZipFile(bundle) as archive:
        assert set(archive.namelist()) == {
            "README.txt",
            "build-info.json",
            "diagnostics.json",
            "feedback.json",
            "feedback.md",
        }
        feedback = json.loads(archive.read("feedback.json"))
        diagnostics = json.loads(archive.read("diagnostics.json"))
        rendered = archive.read("feedback.md").decode("utf-8")
    assert feedback["feedback"]["category"] == "simulator"
    assert "Plot did not update" in rendered
    assert "working_directory" not in diagnostics
    assert "executable" not in diagnostics["python"]
    assert diagnostics["privacy"] == {
        "automatic_raw_samples_included": False,
        "automatic_session_data_included": False,
        "automatic_user_paths_included": False,
        "telemetry_transmitted": False,
    }


def test_sanitized_diagnostics_exclude_path_bearing_fields() -> None:
    report = sanitized_diagnostics()

    assert "working_directory" not in report
    assert set(report["python"]) == {"implementation", "version"}


def test_feedback_bundle_requires_existing_directory(tmp_path: Path) -> None:
    with pytest.raises(LabDAQError) as captured:
        create_feedback_bundle(tmp_path / "missing")

    assert captured.value.code == "DAQ.FEEDBACK.OUTPUT_INVALID"


def test_feedback_form_rejects_unknown_category() -> None:
    with pytest.raises(ValueError, match="unsupported feedback category"):
        FeedbackForm(category="unknown")
