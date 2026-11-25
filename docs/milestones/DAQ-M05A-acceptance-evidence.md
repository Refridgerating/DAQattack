# DAQ-M05A Acceptance Evidence

Status: Automated implementation and target-disk soak accepted locally on Windows; physical NI evidence pending

## Automated evidence

- Deterministic four-channel HDF5 record, full verification, exact replay, and
  processing invariance across different chunk sizes
- Layout, Fletcher32, SWMR, journal, manifest-v2, logical digest, and file-checksum checks
- Fault injection before write, after data flush, after commit, during OS sync,
  and during finalization; abrupt-process recovery uses only the committed prefix
- Schema-v1 TDMS full verification, summary, replay, processing, plotting,
  CSV/SVG export, and normalized TDMS export
- TDMS export exact sample equality, sidecar checksums, collision rejection,
  cancellation, cleanup, and source immutability
- Architecture checks for lazy `h5py`/`npTDMS` imports and typed missing dependencies
- A real-time 600-second run at four channels and 50 kS/s/channel wrote
  30,000,000 samples/channel (960,076,708-byte `raw.h5`), full-verified and
  replayed the complete extent with zero gaps or persistence loss, and used a
  69,644,288-byte peak working set. Recording/finalization took 604.25 seconds
  and full replay completed within 611.96 seconds total on the workspace `F:`
  fixed exFAT disk. Windows reported that
  volume's health status as `Warning`; the data path still completed and
  verified, but the volume warning requires independent operator follow-up.

Exact commands and results are recorded in the implementation completion report.

## Pending external evidence

- Short physical NI HDF5 record, reopen, verification, replay, and task-release check

No physical wiring, conditioning, disk-throughput, or NI-hardware claim is made
from simulator results.
