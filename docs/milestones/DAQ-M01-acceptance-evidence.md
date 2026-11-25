# DAQ-M01 Acceptance Evidence

Status: Accepted locally on Windows  
Date: 2026-07-28  
Platform: Windows, CPython 3.11.15

## Implemented behavior

- Frozen, slotted, value-equality records cover devices, modules, physical
  channels, coordinate frames, sensors, conditioning chains, calibrations,
  logical channels, triggers, acquisition/chunk/session intent, hardware
  profiles, experiment configurations, and draft recipe envelopes.
- Version-1 Draft 2020-12 schemas reject unknown properties and unsupported
  versions before partial construction.
- Pure validation enforces references, ordering, units, calibration provenance,
  exact requested sample counts, safe Windows slugs, profile compatibility,
  and the complete conditioned-charge chain.
- The installed CLI validates profiles, experiments, and draft recipes without
  NI or GUI packages.

## Canonical resources

- Initial profile: `cdaq-9171-ni-9239-four-channel-v1.json`
- Operator template: `four-channel-vibration-template-v1.json`
- Draft recipe: `baseline-v1-draft.json`
- Valid synthetic fixture: `valid-four-channel-synthetic-v1.json`

The operator template is expected to fail until its charge-chain sensitivity
and provenance fields are replaced with authoritative values. The valid
fixture uses `test_fixture` sources and is not physical evidence.

## Automated verification

The final local acceptance run produced:

```text
ruff format --check .       59 files already formatted
ruff check .                all checks passed
mypy src                    success, 29 source files
lint-imports                3 contracts kept, 0 broken
focused pytest              62 passed
pytest -q                   112 passed, 89.72% coverage
python -m build             sdist and wheel built successfully
```

It also exercises all three validation commands through both the console script
and `python -m labdaq`, validates package resources from the built wheel, and
confirms that NI, Qt, PyQtGraph, TDMS, NumPy, and SciPy are not imported by
domain or configuration validation.

The built wheel was installed into an isolated target directory and loaded the
packaged profile as four channels at 50,000 samples/s/channel. CLI smoke checks
returned 0 for the profile, synthetic experiment, and draft recipe, and
returned the documented configuration exit code 5 for the unresolved operator
template.

## Acceptance mapping

- Valid profile/configuration/recipe round trips: automated
- Duplicate channels, references, units, rates, names, versions, duration,
  trigger, order, and calibration rejection: automated
- Unsupported versions fail before construction: automated
- CLI and application service behavior: automated
- Optional dependency and NI-type isolation: automated
- Real charge-chain calibration: intentionally absent and blocked
- Physical NI hardware behavior: not part of M01 and not tested
