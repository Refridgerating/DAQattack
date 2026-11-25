"""npTDMS writer for verified raw-container interoperability exports."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from labdaq.errors import ExportError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

    from labdaq.ports.acquisition import AcquisitionChunk
    from labdaq.ports.processing import ProcessingSourceMetadata, SessionChannelMetadata
    from labdaq.ports.session import SessionSummary


class TdmsArtifactWriter:
    def write(
        self,
        destination: Path,
        *,
        chunks: Iterable[AcquisitionChunk],
        metadata: ProcessingSourceMetadata,
        summary: SessionSummary,
        cancelled: Callable[[], bool],
        progressed: Callable[[int], None],
    ) -> None:
        from nptdms import (  # type: ignore[import-untyped]
            ChannelObject,
            GroupObject,
            RootObject,
            TdmsWriter,
        )

        channel_by_id = {channel.channel_id: channel for channel in metadata.channels}
        try:
            with destination.open("xb") as stream:
                with TdmsWriter(stream, index_file=False) as writer:
                    writer.write_segment(
                        [
                            RootObject(
                                {
                                    "source_session_id": metadata.session_id,
                                    "source_manifest_sha256": metadata.manifest_sha256,
                                    "source_raw_digest": metadata.raw_data_digest,
                                    "source_completion_state": metadata.completion_state,
                                    "actual_sample_rate_hz": metadata.sample_rate_hz,
                                    "ordered_channel_ids_json": __import__("json").dumps(
                                        metadata.ordered_channel_ids
                                    ),
                                    "exported_sample_count": metadata.readable_sample_count,
                                }
                            ),
                            GroupObject("raw", {"channel_order_is_authoritative": True}),
                            *[
                                ChannelObject(
                                    "raw",
                                    channel_id,
                                    np.empty(0, dtype=np.float64),
                                    properties=_channel_properties(channel_by_id[channel_id]),
                                )
                                for channel_id in metadata.ordered_channel_ids
                            ],
                        ]
                    )
                    for chunk in chunks:
                        if cancelled():
                            _raise_cancelled()
                        writer.write_segment(
                            [
                                ChannelObject("raw", channel_id, chunk.samples[index])
                                for index, channel_id in enumerate(chunk.ordered_channel_ids)
                            ]
                        )
                        progressed(chunk.sample_count)
                stream.flush()
                __import__("os").fsync(stream.fileno())
        except ExportError:
            raise
        except Exception as cause:
            raise ExportError(
                "The TDMS artifact could not be written.",
                code="DAQ.EXPORT.TDMS_WRITE_FAILED",
                context={"path": str(destination)},
                remediation="Check the destination capacity and TDMS dependency.",
                cause=cause,
            ) from cause


def _raise_cancelled() -> None:
    raise ExportError(
        "The TDMS export was cancelled.",
        code="DAQ.EXPORT.CANCELLED",
    )


def _channel_properties(channel: SessionChannelMetadata) -> dict[str, object]:
    calibration = channel.calibration
    properties: dict[str, object] = {
        "logical_channel_id": channel.channel_id,
        "display_name": channel.display_name,
        "raw_unit": channel.raw_unit.value,
        "physical_channel": channel.physical_channel,
        "channel_role": channel.channel_role.value,
        "processing_disposition": channel.processing_disposition.value,
    }
    for key in ("sensor_id", "sensor_model", "sensor_serial_number"):
        value = getattr(channel, key)
        if value is not None:
            properties[key] = value
    if calibration is not None:
        properties["calibration_id"] = calibration.calibration_id
        properties["calibration_source"] = calibration.source_reference
    return properties
