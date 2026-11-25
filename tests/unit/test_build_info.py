import pytest

from labdaq.build_info import BuildInfo, get_build_info


def test_packaged_build_info_is_versioned_lab_release() -> None:
    info = get_build_info()

    assert info.schema_version == 1
    assert info.release_channel == "lab-release"
    assert info.schema_versions["session_manifest"] == [1, 2]
    assert info.to_dict()["build_id"] == "source-tree"


def test_build_info_rejects_invalid_digest_and_schema() -> None:
    values = {
        "schema_version": 1,
        "release_channel": "feedback-preview",
        "build_id": "test",
        "build_utc": None,
        "source_revision": None,
        "source_tree_sha256": None,
        "source_state": "clean",
        "architecture": "AMD64",
        "python_version": "3.11",
        "runtime_dependencies": {},
        "schema_versions": {},
    }
    with pytest.raises(ValueError, match="schema version"):
        BuildInfo(**{**values, "schema_version": 2})
    with pytest.raises(ValueError, match="SHA-256"):
        BuildInfo(**{**values, "source_tree_sha256": "short"})
