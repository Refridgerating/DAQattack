# ADR-0002: Optional Dependency Isolation

Status: Accepted

## Context

NI-DAQmx, Qt, scientific processing, TDMS, and packaging dependencies are large,
platform-specific, or unavailable on many development computers.

## Decision

The core distribution has no runtime dependencies. NI, GUI, analysis, storage,
and release capabilities use bounded optional extras. Concrete adapter modules
perform local optional imports when later implemented. Missing dependencies
become typed `DependencyUnavailableError` instances only when a feature is
requested.

## Consequences

Core development, CI, and diagnostics run without NI software or a display.
Each feature composition path must explicitly validate its dependencies, and
architecture tests must detect accidental eager imports.
