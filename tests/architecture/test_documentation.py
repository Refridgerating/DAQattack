import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_required_governing_documents_exist() -> None:
    required = [
        "AGENTS.md",
        "ARCHITECTURE.md",
        "PROJECT_CHARTER.md",
        "DAQ-Software-Roadmap.md",
        "README.md",
        "LICENSE",
        "docs/development.md",
        "docs/glossary.md",
        "docs/adr/README.md",
        "docs/milestones/DAQ-M00-design-charter-repository-foundation.md",
        "docs/milestones/DAQ-M01-domain-records-validated-configuration.md",
        "docs/milestones/DAQ-M01-acceptance-evidence.md",
        "Milestones/DAQ-M02-simulated-daq-deterministic-signal-generation.md",
        "docs/milestones/DAQ-M02-acceptance-evidence.md",
        "docs/milestones/DAQ-M03-acceptance-evidence.md",
        "docs/milestones/DAQ-M04-acceptance-evidence.md",
        "Milestones/DAQ-M05-durable-recording-metadata-parsing-replay.md",
        "docs/milestones/DAQ-M05-acceptance-evidence.md",
        "Milestones/DAQ-M06-calibration-signal-conditioning-pipeline.md",
        "docs/milestones/DAQ-M06-acceptance-evidence.md",
        "Milestones/DAQ-M07-plotting-and-export-services.md",
        "docs/milestones/DAQ-M07-acceptance-evidence.md",
        "Milestones/DAQ-M10A-open-single-channel-acquisition.md",
        "docs/milestones/DAQ-M10A-acceptance-evidence.md",
        "docs/acquisition-engine.md",
        "docs/simulator-scenarios.md",
        "docs/ni-hardware.md",
        "docs/daq-events.md",
        "docs/storage/session-format.md",
        "docs/storage/manifest-schema.md",
        "docs/storage/tdms-layout.md",
        "docs/storage/recovery.md",
        "docs/cli/session-summary.md",
        "docs/processing.md",
        "docs/cli/processing.md",
        "docs/plotting-export.md",
        "docs/cli/plotting-export.md",
        "docs/adr/0016-half-open-ranges-and-bounded-min-max-decimation.md",
        "docs/adr/0017-headless-rendering-and-atomic-derived-artifacts.md",
        "docs/adr/0029-detachable-open-acquisition-recording.md",
        "docs/adr/0030-internal-feedback-preview-packaging.md",
    ]

    assert all((PROJECT_ROOT / relative_path).is_file() for relative_path in required)


def test_adrs_follow_numbering_and_status_convention() -> None:
    adr_paths = sorted((PROJECT_ROOT / "docs" / "adr").glob("[0-9][0-9][0-9][0-9]-*.md"))

    assert [path.name[:4] for path in adr_paths] == [
        "0001",
        "0002",
        "0003",
        "0004",
        "0005",
        "0006",
        "0007",
        "0008",
        "0009",
        "0010",
        "0011",
        "0012",
        "0013",
        "0014",
        "0015",
        "0016",
        "0017",
        "0018",
        "0019",
        "0020",
        "0021",
        "0022",
        "0023",
        "0024",
        "0025",
        "0026",
        "0027",
        "0028",
        "0029",
        "0030",
        "0031",
        "0032",
        "0033",
        "0034",
    ]
    for path in adr_paths:
        content = path.read_text(encoding="utf-8")
        assert re.search(
            r"^Status: (Proposed|Accepted|Superseded|Rejected)$", content, re.MULTILINE
        )


def test_roadmap_and_milestone_link_to_each_other() -> None:
    roadmap = (PROJECT_ROOT / "DAQ-Software-Roadmap.md").read_text(encoding="utf-8")
    milestone = (
        PROJECT_ROOT / "docs/milestones/DAQ-M00-design-charter-repository-foundation.md"
    ).read_text(encoding="utf-8")

    assert "docs/milestones/DAQ-M00-design-charter-repository-foundation.md" in roadmap
    assert "../../DAQ-Software-Roadmap.md" in milestone
    assert "Milestones/DAQ-M02-simulated-daq-deterministic-signal-generation.md" in roadmap


def test_readme_commands_match_configured_quality_tools() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    for command in [
        "ruff format --check .",
        "ruff check .",
        "mypy src",
        "lint-imports",
        "pytest -q",
    ]:
        assert command in readme
    for tool in ["ruff", "mypy", "pytest", "import-linter"]:
        assert tool in pyproject


def test_ci_requires_windows_and_retains_advisory_ubuntu() -> None:
    workflow = (PROJECT_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    for required_text in [
        "windows-latest",
        "ubuntu-latest",
        "support_level: required",
        "support_level: advisory",
        "advisory: false",
        "advisory: true",
        "continue-on-error: ${{ matrix.advisory }}",
        'python-version: "3.11"',
        "actions/checkout@v6",
        "actions/setup-python@v6",
        'python -m pip install -e ".[dev]"',
    ]:
        assert required_text in workflow


def test_completed_milestone_acceptance_is_consistent() -> None:
    roadmap = (PROJECT_ROOT / "DAQ-Software-Roadmap.md").read_text(encoding="utf-8")
    milestone = (
        PROJECT_ROOT / "docs/milestones/DAQ-M00-design-charter-repository-foundation.md"
    ).read_text(encoding="utf-8")
    evidence = (PROJECT_ROOT / "docs/milestones/DAQ-M00-acceptance-evidence.md").read_text(
        encoding="utf-8"
    )
    m01_specification = (
        PROJECT_ROOT / "docs/milestones/DAQ-M01-domain-records-validated-configuration.md"
    ).read_text(encoding="utf-8")
    m01_evidence = (PROJECT_ROOT / "docs/milestones/DAQ-M01-acceptance-evidence.md").read_text(
        encoding="utf-8"
    )
    m02_specification = (
        PROJECT_ROOT / "Milestones/DAQ-M02-simulated-daq-deterministic-signal-generation.md"
    ).read_text(encoding="utf-8")
    m02_evidence = (PROJECT_ROOT / "docs/milestones/DAQ-M02-acceptance-evidence.md").read_text(
        encoding="utf-8"
    )
    m05_specification = (
        PROJECT_ROOT / "Milestones/DAQ-M05-durable-recording-metadata-parsing-replay.md"
    ).read_text(encoding="utf-8")
    m05_evidence = (PROJECT_ROOT / "docs/milestones/DAQ-M05-acceptance-evidence.md").read_text(
        encoding="utf-8"
    )
    m06_specification = (
        PROJECT_ROOT / "Milestones/DAQ-M06-calibration-signal-conditioning-pipeline.md"
    ).read_text(encoding="utf-8")
    m06_evidence = (PROJECT_ROOT / "docs/milestones/DAQ-M06-acceptance-evidence.md").read_text(
        encoding="utf-8"
    )
    m07_specification = (
        PROJECT_ROOT / "Milestones/DAQ-M07-plotting-and-export-services.md"
    ).read_text(encoding="utf-8")
    m07_evidence = (PROJECT_ROOT / "docs/milestones/DAQ-M07-acceptance-evidence.md").read_text(
        encoding="utf-8"
    )

    assert "| DAQ-M00 | Complete |" in roadmap
    assert "| DAQ-M01 | Complete |" in roadmap
    assert "| DAQ-M02 | Complete |" in roadmap
    assert "Status: Accepted" in milestone
    assert "Milestone state: Accepted" in evidence
    assert "Hosted Windows CI is pending external verification" in evidence
    assert "Ubuntu CI is advisory and non-blocking" in evidence
    assert "Status: Complete" in m01_specification
    assert "Status: Accepted locally on Windows" in m01_evidence
    assert "Status: Complete" in m02_specification
    assert "Status: Accepted locally on Windows" in m02_evidence
    assert "not part of M02" in m02_evidence
    assert (
        "| DAQ-M05 | Implementation complete; physical NI recording verification pending |"
        in roadmap
    )
    assert (
        "Status: implementation complete; physical NI recording verification pending"
        in m05_specification
    )
    assert (
        "Status: Implementation complete on Windows; physical NI verification pending"
        in m05_evidence
    )
    assert "| DAQ-M06 | Complete; accepted locally on Windows |" in roadmap
    assert "Status: Complete; accepted locally on Windows" in m06_specification
    assert "Status: Accepted locally on Windows" in m06_evidence
    assert "| DAQ-M07 | Complete; accepted locally on Windows |" in roadmap
    assert "Status: Complete; accepted locally on Windows" in m07_specification
    assert "Status: Accepted locally on Windows" in m07_evidence
