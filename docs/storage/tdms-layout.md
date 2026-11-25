# Legacy TDMS Layout Version 1

This layout remains authoritative for existing schema-v1 sessions. New
recordings use schema-v2 HDF5 layout v1. On-demand TDMS exports use a normalized
interoperability layout and are derived artifacts, not replacement sources.

`raw.tdms` contains one group named `/raw`. Channels are named by stable logical
IDs and appear in the manifest's authoritative order, for example:

```text
/raw/accel-x
/raw/accel-y
/raw/accel-z
/raw/charge
```

Each acquisition chunk is appended as one segment containing all channels with
equal positive length. The writer accepts only float64 arrays shaped
`[channels, samples]` and rejects a changed session ID, sequence, starting index,
channel set/order, sample rate, shape, or dtype before returning a receipt. It
does not add a `.tdms_index` sidecar.

Root properties include session/schema/layout/application identity,
requested/actual rate, channel count/order, UTC creation time, timezone,
canonical dtype, and `unconditioned_voltage` source classification. Per-channel
properties include logical/display/physical identities, order, raw unit, sensor
and calibration provenance, terminal configuration, requested/effective input
ranges, and conditioning-chain identity when applicable.

The same critical fields are duplicated in `manifest.json`. Full verification
opens TDMS in bounded mode, checks `/raw`, order, properties, equal lengths,
manifest counts, the logical digest, and file checksum. Replay reads slices no
larger than the caller's chunk size and emits read-only M04 `AcquisitionChunk`
arrays without calibration or conversion.
