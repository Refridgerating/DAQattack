# Plotting and Export CLI

All commands operate on a verified recorded session path. Omitted channels mean
all channels in source order. Defaults are 2,000 display points and 8,192 replay
samples per chunk. Plot/export failures return exit code 9.

Inspect bounded plot data:

```powershell
labdaq plot-data path\to\session --view raw --json
labdaq plot-data path\to\session --view calibrated `
  --channels accel-x,accel-y --start-seconds 0.25 --stop-seconds 1.0
labdaq plot-data path\to\session --view filtered --recipe baseline-v1 `
  --start-sample 1000 --stop-sample 50000 --max-points 2000
labdaq plot-data path\to\session --view fft --spectral-input raw `
  --recipe baseline-v1 --json
```

Export exact CSV values:

```powershell
labdaq export csv path\to\session --view raw --name raw-selection `
  --channels accel-x,charge-signal --start-sample 1000 --stop-sample 10000
labdaq export csv path\to\session --view psd --spectral-input conditioned `
  --recipe baseline-v1 --name conditioned-psd --json
```

Export static figures:

```powershell
labdaq export figure path\to\session --kind overlay --view filtered `
  --recipe baseline-v1 --format png --name filtered-overlay
labdaq export figure path\to\session --kind psd --view psd `
  --spectral-input calibrated --recipe baseline-v1 --format svg --json
```

Use either `--start-sample/--stop-sample` or
`--start-seconds/--stop-seconds`, never both. Filtered and spectral requests
require `--recipe`; FFT/PSD also accept `--spectral-input raw|calibrated|conditioned`.
`--output` selects one existing destination, `--chunk-size` controls bounded
replay, and `--json` returns the plot bundle or export manifest.

FFT/PSD includes selected raw-only ai3 as volts, even with
`--spectral-input calibrated` or `conditioned` for the acceleration channels.
Each output channel records its actual spectral input stage. Raw-diagnostic
sessions, including open-acquisition recordings, require `--spectral-input raw`.
Use the recipe recorded with the session. Voltage spectra do not provide charge
calibration or qualification.
