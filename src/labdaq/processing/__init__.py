"""GUI-independent scientific processing implementations."""

from labdaq.processing.pipeline import (
    BaselineScientificProcessing,
    BaselineStreamingProcessor,
    BoundedWelchAccumulator,
    design_butterworth_sos,
)

__all__ = [
    "BaselineScientificProcessing",
    "BaselineStreamingProcessor",
    "BoundedWelchAccumulator",
    "design_butterworth_sos",
]
