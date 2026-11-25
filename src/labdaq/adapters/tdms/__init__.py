"""Lazy TDMS durable-session adapter composition."""

from __future__ import annotations

from typing import TYPE_CHECKING

from labdaq.errors import DependencyUnavailableError

if TYPE_CHECKING:
    from labdaq.adapters.tdms.session import TdmsSessionStore


def create_tdms_session_store() -> TdmsSessionStore:
    try:
        import nptdms  # type: ignore[import-untyped]  # noqa: F401

        from labdaq.adapters.tdms.session import TdmsSessionStore
    except ModuleNotFoundError as cause:
        if cause.name not in {"nptdms", "numpy"}:
            raise
        raise DependencyUnavailableError(
            "Legacy TDMS sessions require the tdms extra.",
            code="DAQ.DEPENDENCY.UNAVAILABLE",
            context={"dependency": cause.name, "feature": "durable_sessions"},
            remediation='Install LabDAQ with the "tdms" extra.',
            cause=cause,
        ) from cause
    return TdmsSessionStore()


__all__ = ["create_tdms_session_store"]
