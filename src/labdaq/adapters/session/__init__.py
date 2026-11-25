"""Lazy composition for HDF5-primary and legacy-TDMS durable sessions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from labdaq.errors import DependencyUnavailableError

if TYPE_CHECKING:
    from labdaq.adapters.tdms.session import TdmsSessionStore as SessionStore


def create_session_store() -> SessionStore:
    try:
        import h5py  # type: ignore[import-untyped]  # noqa: F401
        import numpy  # noqa: F401

        from labdaq.adapters.tdms.session import TdmsSessionStore
    except ModuleNotFoundError as cause:
        if cause.name not in {"h5py", "numpy"}:
            raise
        raise DependencyUnavailableError(
            "Durable HDF5 sessions require the storage extra.",
            code="DAQ.DEPENDENCY.UNAVAILABLE",
            context={"dependency": cause.name, "feature": "durable_sessions"},
            remediation='Install LabDAQ with the "storage" extra.',
            cause=cause,
        ) from cause
    return TdmsSessionStore()


__all__ = ["create_session_store"]
