from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from labdaq.adapters.configuration_json import JsonConfigurationAdapter
from labdaq.adapters.processing_json import JsonProcessingRecordAdapter
from labdaq.application.processing_service import CalibrationResolutionService
from labdaq.domain import (
    ProcessingMode,
    ProcessingProvenance,
    ProcessingRange,
    canonical_sha256_excluding,
)
from labdaq.ports.acquisition import AcquisitionChunk
from labdaq.processing import BaselineScientificProcessing

if TYPE_CHECKING:
    from labdaq.ports.processing import (
        ProcessingRecordSerializerPort,
        RecipeRepositoryPort,
        ScientificProcessingPort,
    )

FIXTURE = Path("tests/fixtures/configuration/valid-four-channel-synthetic-v1.json")


def _records():
    repository = JsonConfigurationAdapter()
    experiment = repository.load_experiment_config(FIXTURE)
    profile = repository.load_bundled_hardware_profile(
        experiment.hardware_profile_id,
        experiment.compatible_profile_revision,
    )
    recipe = repository.load_bundled_processing_recipe("baseline-v1")
    calibrations, _ = CalibrationResolutionService().resolve_experiment(
        profile,
        experiment,
        experiment.acquisition.channel_ids,
    )
    return repository, experiment, recipe, calibrations


def test_recipe_repository_and_numerical_processor_contracts_preserve_alignment() -> None:
    repository, experiment, recipe, calibrations = _records()
    recipe_port: RecipeRepositoryPort = repository
    numerical_port: ScientificProcessingPort = BaselineScientificProcessing()
    assert recipe_port.load_bundled_processing_recipe("baseline-v1") == recipe

    processor = numerical_port.create_stream(
        recipe,
        calibrations,
        sample_rate_hz=50000.0,
        mode=ProcessingMode.LIVE,
    )
    source = np.arange(48, dtype=np.float64).reshape(4, 12) / 1000.0
    source.flags.writeable = False
    chunk = AcquisitionChunk(
        session_id="processing-contract",
        sequence_number=0,
        start_sample_index=17,
        sample_count=12,
        sample_rate_hz=50000.0,
        ordered_channel_ids=experiment.acquisition.channel_ids,
        ordered_channel_units=("V", "V", "V", "V"),
        samples=source,
        read_completed_monotonic=0.1,
    )
    result = processor.process(chunk)

    assert result.start_sample_index == chunk.start_sample_index
    assert result.sample_count == chunk.sample_count
    assert result.ordered_channel_ids == chunk.ordered_channel_ids
    assert result.calibration_hashes == tuple(item.calibration_hash for item in calibrations)
    assert result.calibrated_samples.shape == source.shape
    assert not result.calibrated_samples.flags.writeable
    assert np.array_equal(chunk.samples, source)


def test_processing_record_serializer_contract_round_trips_records() -> None:
    _, _, recipe, calibrations = _records()
    serializer: ProcessingRecordSerializerPort = JsonProcessingRecordAdapter()
    calibration = calibrations[0]
    assert serializer.loads_calibration(serializer.dumps_calibration(calibration)) == calibration

    provenance = ProcessingProvenance(
        schema_version=1,
        provenance_hash="a" * 64,
        source_session_id="processing-contract",
        source_completion_state="completed",
        source_raw_digest="b" * 64,
        source_range=ProcessingRange(0, 12),
        ordered_channel_ids=tuple(item.logical_channel_id for item in calibrations),
        sample_rate_hz=50000.0,
        calibration_ids=tuple(item.calibration_id for item in calibrations),
        calibration_hashes=tuple(item.calibration_hash for item in calibrations),
        calibration_qualities=tuple(item.quality for item in calibrations),
        recipe_id=recipe.recipe_id,
        recipe_version=recipe.recipe_version,
        recipe_hash="c" * 64,
        processing_mode=ProcessingMode.REPLAY,
        application_version="contract",
        numpy_version=np.__version__,
        scipy_version="contract",
        output_units=tuple(item.output_unit for item in calibrations),
        notices=(),
        algorithm_details={"contract": "processing-record-v1"},
    )
    provenance = replace(
        provenance,
        provenance_hash=canonical_sha256_excluding(provenance, "provenance_hash"),
    )
    assert serializer.loads_provenance(serializer.dumps_provenance(provenance)) == provenance
