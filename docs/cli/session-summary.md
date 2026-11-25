# Session Summary CLI

Install the storage extra, plus the TDMS extra when inspecting legacy sessions:

```powershell
python -m pip install -e ".[storage,tdms]"
labdaq session summary C:\LabData\2026-08-12_163005_test_1234abcd
labdaq session summary C:\LabData\2026-08-12_163005_test_1234abcd --verify
labdaq session summary C:\LabData\2026-08-12_163005_test_1234abcd --verify --json
```

The default command performs fast bounded-metadata inspection. `--verify`
streams the complete logical dataset and checks digests, file checksums, events,
schemas, format-specific duplicated properties, journal/count consistency,
order, and channel lengths. `--json`
returns the stable `SessionSummary.to_dict()` shape.

Human output includes identity/name, completion state/reason, start/end times,
profile, requested/actual rate, ordered channels and raw units, per-channel and
common counts, acquired duration, event/warning/fault counts, integrity, and
recoverability, raw container format, and raw filename. A storage/session parse or integrity failure uses exit code 7.
Fast inspection is suitable for browsing; use full verification before relying
on or replaying a session.
