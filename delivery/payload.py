from dataclasses import dataclass
from hashlib import sha256
import sqlite3

from archive_common import Unavailable
from ingest.layout import SourceSpan
from ingest.registry import get_observations
from issue.manifest import ManifestLine
from volume.pages import read_page_rows


@dataclass(frozen=True, slots=True)
class DeliveredLine:
    recording_id: str
    row_number: int
    source: SourceSpan
    raw: bytes
    value: int | None

    @property
    def missing(self) -> bool:
        return self.value is None


def load_original_lines(
    db: sqlite3.Connection, recording_id: str, lines: tuple[ManifestLine, ...]
) -> tuple[DeliveredLine, ...]:
    numbers = tuple(line.row_number for line in lines)
    observations = get_observations(db, recording_id, numbers)
    originals = read_page_rows(db, recording_id, numbers)
    delivered = []
    for line, observation in zip(lines, observations, strict=True):
        if observation.row_number != line.row_number or observation.source != line.source:
            raise Unavailable(f'录制 {recording_id} 的 R{line.row_number} 出处与冻结清单不一致')
        raw = originals.get(line.row_number)
        if raw is None or len(raw) != line.source.length or sha256(raw).digest() != observation.digest:
            raise Unavailable(f'录制 {recording_id} 的 R{line.row_number} 原字节无法核对')
        delivered.append(DeliveredLine(recording_id, line.row_number, line.source, raw, observation.value))
    return tuple(delivered)
