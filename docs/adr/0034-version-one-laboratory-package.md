# ADR-0034: Version-one laboratory package

Status: Accepted

## Context

DAQ-M12 provides raw monitoring and optional recording through one acquisition
stream. The historical feedback-preview installer predates that workflow and uses
a separate installation identity. Laboratory users need a versioned Windows
package with an operator path for both NI hardware and simulation.

## Decision

Package LabDAQ 1.0.0 with a shared, administrator-installed Windows application
and separate Start menu shortcuts for NI hardware and simulation. Both use the
raw-first monitoring workflow; NI mode requires the external NI-DAQmx Windows
driver and does not silently fall back to simulation. Session data belongs in an
operator-selected writable folder outside the installation directory.

Use a separate application directory and registry identity from the historical
feedback preview. Installing version 1.0.0 does not remove the preview. Preserve
the existing owned-file inventory and nonrecursive cleanup policy. Package release
notes, installation instructions, dependency notices, build identity, a release
manifest and installer SHA-256. The Windows package is unsigned.

The version label is a distribution identity, not evidence of physical
qualification. Report simulator, packaged, clean-machine and physical NI checks
according to the evidence actually obtained. The existing proprietary LICENSE
continues to apply; this decision does not create a new licensing grant.

## Consequences

The laboratory receives an installable raw-first workflow and can exercise
simulation without NI software. Previous preview installations remain usable
until their owners remove them. Physical cDAQ-9171 / NI-9239 behavior and any
unperformed installation checks remain unverified; this package does not by
itself complete the historical M11 qualification campaign.
