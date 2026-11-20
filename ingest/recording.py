from collections.abc import Iterable
from dataclasses import dataclass
import sqlite3

from archive_common import Conflict, transaction
from ingest.layout import SourceSpan, locate_records
from ingest.registry import find_recording, get_observations, store_recording
from ingest.samples import decode_sample
from volume.pages import list_recording_pages, read_page_rows
from volume.seal import normalize_page_ends, seal_pages


@dataclass(frozen=True, slots=True)
class SealedRecording:
    recording_id: str
    row_count: int
    page_ids: tuple[int, ...]


def seal(
    db: sqlite3.Connection,
    recording_id: str,
    records: Iterable[tuple[bytes, SourceSpan]],
    *,
    page_ends: Iterable[int] | None = None,
) -> SealedRecording:
    located = locate_records(recording_id, records)
    observations = tuple(decode_sample(record) for record in located)
    ends = normalize_page_ends(len(located), page_ends)
    with transaction(db, write=True):
        existing = find_recording(db, recording_id)
        if existing is None:
            store_recording(db, recording_id, observations)
            pages = seal_pages(db, recording_id, located, ends)
            return SealedRecording(recording_id, len(located), tuple(page.page_id for page in pages))

        if existing.row_count != len(located):
            raise Conflict(f'录制 {recording_id} 已以不同内容接纳')
        numbers = tuple(record.row_number for record in located)
        if get_observations(db, recording_id, numbers) != observations:
            raise Conflict(f'录制 {recording_id} 已以不同内容或出处接纳')
        archived = read_page_rows(db, recording_id, numbers)
        if any(archived[record.row_number] != record.raw for record in located):
            raise Conflict(f'录制 {recording_id} 已以不同原字节接纳')
        pages = list_recording_pages(db, recording_id)
        return SealedRecording(recording_id, existing.row_count, tuple(page.page_id for page in pages))
