# Processing CLI

Show the checked-in active recipe:

```powershell
labdaq recipe show baseline-v1
labdaq recipe show baseline-v1 --json
labdaq recipe validate path\to\processing-recipe.json
```

Process the complete verified extent of a session:

```powershell
labdaq process-session path\to\session --summary
```

Process a half-open sample range and control replay chunk size:

```powershell
labdaq process-session path\to\session `
  --recipe baseline-v1 --summary `
  --start-sample 50000 --stop-sample 150000 `
  --chunk-size 8192 --json
```

The summary contains complete per-channel metrics and compact FFT/PSD peak and
integrated-power fields. It does not write to `processed/`, alter the manifest,
or implement CSV/figure export. Processing faults return exit code 8. The source
must pass full M05 verification and be a replayable terminal session or an
explicitly recovered verified prefix.

By default, processing resolves the recipe ID retained by the session. Use
`--recipe` only for an explicit compatible override; the resulting provenance
records that recipe and its canonical hash.
