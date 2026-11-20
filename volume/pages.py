from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
import sqlite3

from archive_common import Unavailable, UnknownRecord
from volume.placement import get_active_payload


@dataclass(frozen=True, slots=True)
class PageSpan:
    row_number: int
    offset: int
    length: int


@dataclass(frozen=True, slots=True)
class Page:
    page_id: int
    recording_id: str
    ordinal: int
    byte_count: int
    digest: bytes
    spans: tuple[PageSpan, ...]


def ensure_page_tables(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS pages (
            page_id INTEGER PRIMARY KEY,
            recording_id TEXT NOT NULL REFERENCES recordings(recording_id),
            ordinal INTEGER NOT NULL CHECK (ordinal > 0),
            byte_count INTEGER NOT NULL CHECK (byte_count > 0),
            digest BLOB NOT NULL CHECK (length(digest) = 32),
            UNIQUE (recording_id, ordinal),
            UNIQUE (page_id, recording_id)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS page_rows (
            recording_id TEXT NOT NULL,
            row_number INTEGER NOT NULL,
            page_id INTEGER NOT NULL,
            byte_offset INTEGER NOT NULL CHECK (byte_offset >= 0),
            byte_length INTEGER NOT NULL CHECK (byte_length > 0),
            PRIMARY KEY (recording_id, row_number),
            FOREIGN KEY (page_id, recording_id) REFERENCES pages(page_id, recording_id),
            FOREIGN KEY (recording_id, row_number) REFERENCES observations(recording_id, row_number)
        )
    ''')
    db.execute('CREATE INDEX IF NOT EXISTS page_rows_by_page ON page_rows(page_id, row_number)')


def store_page(
    db: sqlite3.Connection, recording_id: str, ordinal: int, spans: tuple[PageSpan, ...], payload: bytes
) -> Page:
    digest = sha256(payload).digest()
    cursor = db.execute('''
        INSERT INTO pages (recording_id, ordinal, byte_count, digest) VALUES (?, ?, ?, ?)
    ''', (recording_id, ordinal, len(payload), digest))
    page_id = cursor.lastrowid
    db.executemany('''
        INSERT INTO page_rows (recording_id, row_number, page_id, byte_offset, byte_length)
        VALUES (?, ?, ?, ?, ?)
    ''', ((recording_id, span.row_number, page_id, span.offset, span.length) for span in spans))
    return Page(page_id, recording_id, ordinal, len(payload), digest, spans)


def get_page(db: sqlite3.Connection, page_id: int) -> Page:
    row = db.execute('''
        SELECT recording_id, ordinal, byte_count, digest FROM pages WHERE page_id = ?
    ''', (page_id,)).fetchone()
    if row is None:
        raise UnknownRecord(f'未知原页 {page_id}')
    spans = tuple(PageSpan(*span) for span in db.execute('''
        SELECT row_number, byte_offset, byte_length FROM page_rows
        WHERE page_id = ? ORDER BY row_number
    ''', (page_id,)))
    page = Page(page_id, *row, spans)
    if not spans:
        raise Unavailable(f'原页 {page_id} 缺少行位目录')
    offset = 0
    for expected_row, span in enumerate(spans, start=spans[0].row_number):
        if span.row_number != expected_row or span.offset != offset or span.length <= 0:
            raise Unavailable(f'原页 {page_id} 的行位或跨度不连续')
        offset += span.length
    if offset != page.byte_count:
        raise Unavailable(f'原页 {page_id} 的字节跨度不完整')
    return page


def list_recording_pages(db: sqlite3.Connection, recording_id: str) -> tuple[Page, ...]:
    page_ids = db.execute('''
        SELECT page_id FROM pages WHERE recording_id = ? ORDER BY ordinal
    ''', (recording_id,)).fetchall()
    return tuple(get_page(db, page_id) for (page_id,) in page_ids)


def slice_page_rows(page: Page, payload: bytes, row_numbers: Iterable[int]) -> dict[int, bytes]:
    if not isinstance(payload, bytes) or len(payload) != page.byte_count:
        raise Unavailable(f'原页 {page.page_id} 的载荷长度不完整')
    if sha256(payload).digest() != page.digest:
        raise Unavailable(f'原页 {page.page_id} 的载荷与封页校验不一致')
    spans = {span.row_number: span for span in page.spans}
    chunks = {}
    for row_number in row_numbers:
        span = spans.get(row_number)
        if span is None or span.offset < 0 or span.length <= 0:
            raise Unavailable(f'原页 {page.page_id} 缺少 R{row_number} 的有效跨度')
        chunk = payload[span.offset:span.offset + span.length]
        if len(chunk) != span.length:
            raise Unavailable(f'原页 {page.page_id} 的 R{row_number} 原字节不完整')
        chunks[row_number] = chunk
    return chunks


def read_page_rows(
    db: sqlite3.Connection, recording_id: str, row_numbers: Iterable[int]
) -> dict[int, bytes]:
    grouped = {}
    for row_number in row_numbers:
        location = db.execute('''
            SELECT page_id FROM page_rows WHERE recording_id = ? AND row_number = ?
        ''', (recording_id, row_number)).fetchone()
        if location is None:
            raise Unavailable(f'录制 {recording_id} 的 R{row_number} 无封页位置')
        grouped.setdefault(location[0], []).append(row_number)

    result = {}
    for page_id, rows in grouped.items():
        page = get_page(db, page_id)
        payload = get_active_payload(db, page_id)
        result.update(slice_page_rows(page, payload, rows))
    return result
