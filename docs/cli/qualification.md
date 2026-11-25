# Qualification CLI

Install the simulator, analysis, and storage extras for software qualification,
or replace `simulated` with the NI extra for physical work.

```powershell
python -m pip install -e ".[simulated,analysis,storage]"

labdaq qualify hardware --backend simulated `
  --profile src\labdaq\resources\config\hardware-profiles\cdaq-9171-ni-9239-four-channel-v1.json `
  --config tests\fixtures\configuration\valid-four-channel-synthetic-v1.json `
  --plan preflight-v1 --json
```

The full command surface is:

```text
labdaq qualify hardware --backend ni|simulated --profile PROFILE --config CONFIG
  --plan PLAN [--scenario SCENARIO] [--confirmations FILE] [--duration SECONDS]
  [--json]
labdaq qualification show REPORT
labdaq qualification check REPORT --profile PROFILE --config CONFIG
  [--backend ni|simulated] [--scenario SCENARIO]
labdaq qualification recover SESSION
```

Without `--json`, assisted tests prompt interactively. JSON mode is
noninteractive and reads the version-1 confirmations file supplied with
`--confirmations`; absent confirmations make affected tests `not-tested`.

`show` returns success for any valid report, including failed, cancelled, or
recovered reports. `qualify` and `check` return exit code 10 when start
eligibility is false. Other established validation, DAQ, storage, processing,
and export exit codes remain unchanged.

Checking without `--backend` validates the saved report and current
configuration but cannot establish current physical identity, so the decision
is ineligible. Use `--backend ni` only while the intended chassis and module are
connected and available.
