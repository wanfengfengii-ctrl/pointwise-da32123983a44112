import sqlite3

from archive_common import InvalidRequest, UnknownRecord
from ingest.registry import find_recording, get_observations
from ingest.samples import Observation


def select_closed_range(
    db: sqlite3.Connection, recording_id: str, first: int, last: int
) -> tuple[Observation, ...]:
    recording = find_recording(db, recording_id)
    if recording is None:
        raise UnknownRecord(f'未知录制 {recording_id}')
    if type(first) is not int or type(last) is not int or not 1 <= first <= last <= recording.row_count:
        raise InvalidRequest(f'出票闭区间须落在录制 {recording_id} 的原行范围内')
    return get_observations(db, recording_id, range(first, last + 1))


def select_rows(
    db: sqlite3.Connection, recording_id: str, row_numbers: tuple[int, ...]
) -> tuple[Observation, ...]:
    recording = find_recording(db, recording_id)
    if recording is None:
        raise UnknownRecord(f'未知录制 {recording_id}')
    if any(
        type(number) is not int or not 1 <= number <= recording.row_count
        for number in row_numbers
    ):
        raise InvalidRequest(f'原行位须落在录制 {recording_id} 的原行范围内')
    return get_observations(db, recording_id, row_numbers)
