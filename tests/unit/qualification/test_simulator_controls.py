from dataclasses import replace
from typing import TYPE_CHECKING, cast

import numpy as np

from labdaq.adapters.simulated.adapter import SimulatedDaqAdapter
from labdaq.adapters.simulated.scenario import (
    ChannelDelayFault,
    CrosstalkFault,
    FrozenFault,
    JsonSimulatorScenarioCodec,
    NonfiniteFault,
    SimulatorTopology,
    SourceIndexFault,
    StaleReadFault,
)

if TYPE_CHECKING:
    from labdaq.domain.models import ExperimentConfig, HardwareProfile


def _adapter(
    fault: object,
    baseline_profile: object,
    synthetic_experiment: object,
) -> SimulatedDaqAdapter:
    codec = JsonSimulatorScenarioCodec()
    scenario = codec.load_bundled("clean-four-channel")
    changed = replace(scenario, scenario_id="qualification-fault", fault_schedule=(fault,))
    decoded = codec.loads(codec.dumps(changed))
    adapter = SimulatedDaqAdapter(decoded)
    profile = cast("HardwareProfile", baseline_profile)
    experiment = cast("ExperimentConfig", synthetic_experiment)
    assert adapter.validate(profile, experiment).accepted
    adapter.configure(experiment)
    adapter.start()
    return adapter


def test_nonfinite_and_frozen_faults_are_exact(
    baseline_profile: object,
    synthetic_experiment: object,
) -> None:
    nonfinite = _adapter(
        NonfiniteFault("bad", ("accel-x",), 2, "nan"),
        baseline_profile,
        synthetic_experiment,
    )
    assert np.isnan(nonfinite.read(8, 1.0).samples[0, 2])
    nonfinite.close()

    frozen = _adapter(
        FrozenFault("frozen", ("accel-y",), 2, 6, 0.75),
        baseline_profile,
        synthetic_experiment,
    )
    values = frozen.read(8, 1.0).samples[1]
    np.testing.assert_array_equal(values[2:6], np.full(4, 0.75))
    frozen.close()


def test_source_gap_duplicate_reorder_and_stale_reads_are_observable(
    baseline_profile: object,
    synthetic_experiment: object,
) -> None:
    for mode, offset, expected in (("gap", 3, 11), ("duplicate", -8, 0), ("reorder", -4, 4)):
        adapter = _adapter(
            SourceIndexFault("index", 1, offset, mode),  # type: ignore[arg-type]
            baseline_profile,
            synthetic_experiment,
        )
        adapter.read(8, 1.0)
        assert adapter.read(8, 1.0).source_start_sample_index == expected
        adapter.close()

    stale = _adapter(StaleReadFault("stale", 1), baseline_profile, synthetic_experiment)
    first = stale.read(8, 1.0)
    second = stale.read(8, 1.0)
    assert second.source_start_sample_index == first.source_start_sample_index
    np.testing.assert_array_equal(second.samples, first.samples)
    stale.close()


def test_channel_delay_crosstalk_and_self_test_controls(
    baseline_profile: object,
    synthetic_experiment: object,
) -> None:
    delayed = _adapter(
        ChannelDelayFault("delay", ("accel-x",), 2, 2, None),
        baseline_profile,
        synthetic_experiment,
    )
    delayed_values = delayed.read(16, 1.0).samples[0]
    assert delayed_values[2] == delayed_values[0]
    delayed.close()

    coupled = _adapter(
        CrosstalkFault("coupled", "accel-x", ("charge-signal",), 0.5, 0, None),
        baseline_profile,
        synthetic_experiment,
    )
    output = coupled.read(16, 1.0).samples
    assert np.max(np.abs(output[3])) > 0.5
    coupled.close()

    scenario = JsonSimulatorScenarioCodec().load_bundled("clean-four-channel")
    unsupported = SimulatedDaqAdapter(replace(scenario, self_test_supported=False))
    assert unsupported.self_test().passed is None
    unsupported.close()
    failed = SimulatedDaqAdapter(replace(scenario, self_test_passed=False))
    assert failed.self_test().passed is False
    failed.close()


def test_topology_identity_and_capability_controls_are_discoverable_and_blocking(
    baseline_profile: object,
    synthetic_experiment: object,
) -> None:
    profile = cast("HardwareProfile", baseline_profile)
    experiment = cast("ExperimentConfig", synthetic_experiment)
    clean = JsonSimulatorScenarioCodec().load_bundled("clean-four-channel")

    absent = SimulatedDaqAdapter(
        replace(
            clean,
            topology=SimulatorTopology(device_present=False, module_present=False),
        )
    )
    assert absent.discover().devices == ()
    assert not absent.validate(profile, experiment).accepted
    absent.close()

    wrong = SimulatedDaqAdapter(
        replace(
            clean,
            topology=replace(
                clean.topology,
                module_model="NI-9999",
                maximum_sample_rate_hz=25_000.0,
                input_min_v=-5.0,
                input_max_v=5.0,
            ),
        )
    )
    discovery = wrong.discover()
    assert discovery.devices[0].modules[0].model == "NI-9999"
    validation = wrong.validate(profile, experiment)
    assert not validation.accepted
    assert validation.supported_input_range_v == (-5.0, 5.0)
    wrong.close()
