from dataclasses import dataclass

from ingest.layout import SourceSpan
from ingest.samples import Observation


@dataclass(frozen=True, slots=True)
class ManifestLine:
    row_number: int
    source: SourceSpan


@dataclass(frozen=True, slots=True)
class OrderManifest:
    order_id: str
    recording_id: str
    first: int
    last: int
    lines: tuple[ManifestLine, ...]


def freeze_manifest(
    order_id: str, recording_id: str, first: int, last: int, observations: tuple[Observation, ...]
) -> OrderManifest:
    return OrderManifest(
        order_id, recording_id, first, last,
        tuple(ManifestLine(item.row_number, item.source) for item in observations),
    )
