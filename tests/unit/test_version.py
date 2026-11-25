from importlib.metadata import PackageNotFoundError

import pytest

from labdaq import version
from labdaq.version import APP_NAME, __version__, get_version, get_version_label


def test_version_is_non_empty() -> None:
    assert APP_NAME == "LabDAQ"
    assert isinstance(__version__, str)
    assert __version__
    assert get_version() == __version__
    assert get_version_label().startswith(f"LabDAQ {__version__} [lab-release;")


def test_version_has_deterministic_source_tree_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_distribution(distribution_name: str) -> str:
        raise PackageNotFoundError(distribution_name)

    monkeypatch.setattr(version, "version", missing_distribution)

    assert version.get_version() == "0.0.0+unknown"
