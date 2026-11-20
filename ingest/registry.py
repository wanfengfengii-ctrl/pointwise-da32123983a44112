from collections.abc import Iterable
from dataclasses import dataclass
import sqlite3

from archive_common import Unavailable
from ingest.layout import SourceSpan
from ingest.samples import Observation


@dataclass(frozen=True, slots=True)
class Recording:
    recording_id: str
    row_count: int


def ensure_recording_tables(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS recordings (
            recording_id TEXT PRIMARY KEY,
            row_count INTEGER NOT NULL CHECK (row_count > 0)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS observations (
            recording_id TEXT NOT NULL REFERENCES recordings(recording_id),
            row_number INTEGER NOT NULL CHECK (row_number > 0),
            source TEXT NOT NULL,
            source_offset TEXT NOT NULL,
            source_length INTEGER NOT NULL CHECK (source_length > 0),
            value_hex TEXT,
            digest BLOB NOT NULL CHECK (length(digest) = 32),
            PRIMARY KEY (recording_id, row_number)
        )
    ''')


def store_recording(
    db: sqlite3.Connection, recording_id: str, observations: tuple[Observation, ...]
) -> Recording:
    recording = Recording(recording_id, len(observations))
    db.execute('INSERT INTO recordings VALUES (?, ?)', (recording_id, recording.row_count))
    db.executemany('''
        INSERT INTO observations
            (recording_id, row_number, source, source_offset, source_length, value_hex, digest)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (
        (
            item.recording_id, item.row_number, item.source.source,
            format(item.source.offset, 'x'), item.source.length,
            None if item.value is None else format(item.value, 'x'), item.digest,
        )
        for item in observations
    ))
    return recording


def find_recording(db: sqlite3.Connection, recording_id: str) -> Recording | None:
    row = db.execute(
        'SELECT row_count FROM recordings WHERE recording_id = ?', (recording_id,)
    ).fetchone()
    return None if row is None else Recording(recording_id, row[0])


def get_observations(
    db: sqlite3.Connection, recording_id: str, row_numbers: Iterable[int]
) -> tuple[Observation, ...]:
    observations = []
    for row_number in row_numbers:
        row = db.execute('''
            SELECT source, source_offset, source_length, value_hex, digest
            FROM observations WHERE recording_id = ? AND row_number = ?
        ''', (recording_id, row_number)).fetchone()
        if row is None:
            raise Unavailable(f'录制 {recording_id} 的 R{row_number} 观测依据缺失')
        source, offset, length, value_hex, digest = row
        try:
            span = SourceSpan(source, int(offset, 16), length)
            value = None if value_hex is None else int(value_hex, 16)
        except (TypeError, ValueError) as error:
            raise Unavailable(f'录制 {recording_id} 的 R{row_number} 观测依据无效') from error
        if span.offset < 0 or type(length) is not int or length < 1:
            raise Unavailable(f'录制 {recording_id} 的 R{row_number} 出处无效')
        if not isinstance(digest, bytes) or len(digest) != 32:
            raise Unavailable(f'录制 {recording_id} 的 R{row_number} 校验资料无效')
        observations.append(Observation(recording_id, row_number, span, value, digest))
    return tuple(observations)
