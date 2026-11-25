"""Local, privacy-bounded feedback bundles for the internal preview."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from labdaq.build_info import get_build_info
from labdaq.diagnostics import collect_diagnostics
from labdaq.errors import LabDAQError

if TYPE_CHECKING:
    from pathlib import Path

FEEDBACK_CATEGORIES = (
    "installation",
    "simulator",
    "ni-hardware",
    "acquisition",
    "review-export",
    "other",
)


@dataclass(frozen=True, slots=True)
class FeedbackForm:
    """Structured comments supplied explicitly by a preview tester."""

    category: str = "other"
    summary: str = ""
    steps_to_reproduce: str = ""
    expected_result: str = ""
    actual_result: str = ""
    impact: str = ""

    def __post_init__(self) -> None:
        if self.category not in FEEDBACK_CATEGORIES:
            raise ValueError(f"unsupported feedback category: {self.category}")

    def to_dict(self) -> dict[str, str]:
        return cast("dict[str, str]", asdict(self))


def create_feedback_bundle(
    output_parent: Path,
    *,
    form: FeedbackForm | None = None,
    now: datetime | None = None,
) -> Path:
    """Create a collision-resistant ZIP without collecting sessions or user paths."""
    parent = output_parent.resolve()
    if not parent.is_dir():
        raise LabDAQError(
            "The feedback output directory does not exist.",
            code="DAQ.FEEDBACK.OUTPUT_INVALID",
            context={"output_parent": str(output_parent)},
            remediation="Select an existing writable directory.",
        )
    created_at = now or datetime.now(UTC)
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("feedback bundle time must be timezone-aware")
    submission = form or FeedbackForm()
    timestamp = created_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = parent / f"labdaq-feedback-{timestamp}-{uuid4().hex[:8]}.zip"
    try:
        with destination.open("xb"):
            pass
        with ZipFile(destination, mode="w", compression=ZIP_DEFLATED) as archive:
            _write_json(
                archive,
                "feedback.json",
                {
                    "schema_version": 1,
                    "created_at": created_at.astimezone(UTC).isoformat(),
                    "feedback": submission.to_dict(),
                },
            )
            archive.writestr("feedback.md", _render_feedback(submission))
            _write_json(archive, "diagnostics.json", sanitized_diagnostics())
            _write_json(archive, "build-info.json", get_build_info().to_dict())
            archive.writestr("README.txt", _bundle_readme())
    except (OSError, ValueError) as cause:
        destination.unlink(missing_ok=True)
        raise LabDAQError(
            "The feedback bundle could not be created.",
            code="DAQ.FEEDBACK.WRITE_FAILED",
            context={"output_parent": str(output_parent)},
            remediation="Choose another writable directory and try again.",
            cause=cause,
        ) from cause
    return destination


def sanitized_diagnostics() -> dict[str, object]:
    """Remove path-bearing fields from the existing environment diagnostic."""
    report = collect_diagnostics()
    python = _dict(report["python"])
    platform = _dict(report["platform"])
    return {
        "schema_version": 1,
        "application": report["application"],
        "build": get_build_info().to_dict(),
        "python": {
            "version": python["version"],
            "implementation": python["implementation"],
        },
        "platform": {
            "system": platform["system"],
            "release": platform["release"],
            "machine": platform["machine"],
        },
        "core_import": report["core_import"],
        "optional_dependencies": report["optional_dependencies"],
        "temporary_file": report["temporary_file"],
        "overall": report["overall"],
        "privacy": {
            "automatic_session_data_included": False,
            "automatic_raw_samples_included": False,
            "automatic_user_paths_included": False,
            "telemetry_transmitted": False,
        },
    }


def _dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("diagnostic field must be an object")
    return cast("dict[str, object]", value)


def _write_json(archive: ZipFile, name: str, payload: object) -> None:
    archive.writestr(
        name,
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )


def _render_feedback(form: FeedbackForm) -> str:
    return (
        "# LabDAQ feedback preview report\n\n"
        f"- Category: {form.category}\n"
        f"- Summary: {form.summary or '[not supplied]'}\n\n"
        "## Steps to reproduce\n\n"
        f"{form.steps_to_reproduce or '[not supplied]'}\n\n"
        "## Expected result\n\n"
        f"{form.expected_result or '[not supplied]'}\n\n"
        "## Actual result\n\n"
        f"{form.actual_result or '[not supplied]'}\n\n"
        "## Impact\n\n"
        f"{form.impact or '[not supplied]'}\n"
    )


def _bundle_readme() -> str:
    return (
        "This archive was created locally and was not transmitted automatically.\r\n"
        "Return it through the lab's approved internal communication channel.\r\n"
        "No sessions, raw samples, calibration values, usernames, or absolute user paths\r\n"
        "are collected automatically. Add screenshots or session data only after reviewing\r\n"
        "them and only when the lab's data-handling policy permits it.\r\n"
    )
