"""Application identity and installed-version lookup."""

from importlib.metadata import PackageNotFoundError, version

from labdaq.build_info import get_build_info

APP_NAME = "LabDAQ"
DISTRIBUTION_NAME = "labdaq"
FALLBACK_VERSION = "0.0.0+unknown"


def get_version() -> str:
    """Return installed distribution metadata or a deterministic fallback."""
    try:
        return version(DISTRIBUTION_NAME)
    except PackageNotFoundError:
        bundled_version = get_build_info().runtime_dependencies.get(DISTRIBUTION_NAME)
        return bundled_version or FALLBACK_VERSION


def get_version_label() -> str:
    """Return version plus release channel and immutable build identifier."""
    build = get_build_info()
    return f"{APP_NAME} {get_version()} [{build.release_channel}; {build.build_id}]"


__version__ = get_version()
