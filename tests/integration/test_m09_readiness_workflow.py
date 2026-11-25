from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from labdaq.bootstrap import create_desktop_presenter
from labdaq.errors import ApplicationStateError

if TYPE_CHECKING:
    from pathlib import Path


class _ProgressSink:
    def __init__(self) -> None:
        self.values: list[object] = []

    def publish(self, progress: object) -> None:
        self.values.append(progress)


def test_simulated_qualification_readiness_freshness_and_metadata_exemption(
    tmp_path: Path,
) -> None:
    presenter = create_desktop_presenter(backend="simulated")
    try:
        draft = presenter.initial_draft(template_id="four-channel-vibration-synthetic-v2")
        draft = presenter.update_field("storage.parent_directory", str(tmp_path))
        sink = _ProgressSink()
        qualification = presenter.run_qualification(
            draft,
            sink,  # type: ignore[arg-type]
            approved_confirmation_ids=("charge-conditioning-compatible",),
        )
        destination = presenter.check_destination(draft)
        hardware = presenter.refresh_hardware(draft)
        ready = presenter.evaluate_readiness(draft, destination, hardware, qualification)

        assert ready.eligible
        assert ready.qualification_state == "warning"
        assert [value.phase for value in sink.values][0] == "starting"  # type: ignore[attr-defined]
        assert [value.phase for value in sink.values][-1] == "completed"  # type: ignore[attr-defined]
        assert any(value.indeterminate for value in sink.values)  # type: ignore[attr-defined]

        assert destination is not None
        intent, _, launch_decision = presenter.request_launch(
            draft,
            destination,
            qualification,
        )
        assert launch_decision.eligible
        assert intent.qualification_report_id == qualification.report.report_id  # type: ignore[union-attr]
        assert not intent.session_plan.session_path.exists()

        metadata_edit = presenter.update_field("experiment.notes", "operator-only note")
        metadata_destination = presenter.check_destination(metadata_edit)
        metadata_hardware = presenter.refresh_hardware(metadata_edit)
        still_ready = presenter.evaluate_readiness(
            metadata_edit,
            metadata_destination,
            metadata_hardware,
            qualification,
        )
        assert still_ready.eligible

        fingerprint_edit = presenter.update_field("acquisition.chunk_size", 2500)
        fingerprint_destination = presenter.check_destination(fingerprint_edit)
        fingerprint_hardware = presenter.refresh_hardware(fingerprint_edit)
        stale = presenter.evaluate_readiness(
            fingerprint_edit,
            fingerprint_destination,
            fingerprint_hardware,
            qualification,
        )
        assert not stale.eligible
        assert stale.qualification_state == "stale"
        assert fingerprint_edit.normalized is not None
        assert fingerprint_destination is not None
        assert qualification.report_path is not None
        with pytest.raises(ApplicationStateError, match="readiness is false"):
            presenter.services.readiness.request_launch(
                stale,
                profile=presenter.services.profile,
                experiment=fingerprint_edit.normalized,
                destination=fingerprint_destination,
                report_path=qualification.report_path,
            )

        restored = presenter.update_field("acquisition.chunk_size", 5000)
        restored_destination = presenter.check_destination(restored)
        restored_hardware = presenter.refresh_hardware(restored)
        recovered = presenter.evaluate_readiness(
            restored,
            restored_destination,
            restored_hardware,
            qualification,
        )
        assert recovered.eligible

        corrupt = tmp_path / "2026-08-13_120000_corrupt_12345678"
        corrupt.mkdir()
        (corrupt / "manifest.json").write_text("{not-json", encoding="utf-8")
        catalog = presenter.scan_sessions(tmp_path)
        valid_entries = [entry for entry in catalog.entries if entry.valid]
        invalid_entries = [entry for entry in catalog.entries if not entry.valid]
        assert len(valid_entries) == 1
        summary = valid_entries[0].summary
        assert summary is not None
        assert summary.session_type.value == "hardware-qualification"
        assert len(summary.channels) == 4
        assert summary.hardware is not None
        assert summary.recipe is not None and summary.recipe.recipe_id == "baseline"
        assert invalid_entries[0].session_path == corrupt
    finally:
        presenter.close()
