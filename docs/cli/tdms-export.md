# TDMS Interoperability Export

Install both storage formats, then export a fully verified source session:

```powershell
python -m pip install -e ".[storage,tdms]"
labdaq export tdms C:\LabData\session
labdaq export tdms C:\LabData\session --output D:\Exchange --name test-raw --json
```

The command exports every channel and the entire verified readable extent as
unconditioned float64 voltage. Channel selection, time ranges, calibration, and
filtering are intentionally unavailable. The default name is
`<experiment>-<session-id8>-raw.tdms` under the session `exports` directory.

The adjacent `.tdms.metadata.json` sidecar records source identity and format,
manifest and raw digests, completion state, exported extent, ordered channels
and units, output checksum, and whether the source was a recovered prefix.
Existing destinations are rejected. Cancellation or failure removes temporary
and partially installed artifacts and never changes the source session.
