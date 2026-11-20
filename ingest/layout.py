from collections.abc import Iterable
from dataclasses import dataclass
from io import BytesIO

from archive_common import InvalidRequest, require_name


@dataclass(frozen=True, slots=True)
class SourceSpan:
    source: str
    offset: int
    length: int


@dataclass(frozen=True, slots=True)
class LocatedRecord:
    recording_id: str
    row_number: int
    source: SourceSpan
    raw: bytes


def source_records(source: str, data: bytes) -> tuple[tuple[bytes, SourceSpan], ...]:
    """Frame a single source stream at LF boundaries and retain all its bytes."""
    require_name(source, '来源')
    if not isinstance(data, bytes):
        raise InvalidRequest('来源内容须为字节串')
    records = []
    offset = 0
    for raw in BytesIO(data):
        records.append((raw, SourceSpan(source, offset, len(raw))))
        offset += len(raw)
    return tuple(records)


def locate_records(
    recording_id: str, records: Iterable[tuple[bytes, SourceSpan]]
) -> tuple[LocatedRecord, ...]:
    require_name(recording_id, '录制号')
    located = []
    for row_number, entry in enumerate(records, start=1):
        if not isinstance(entry, (tuple, list)) or len(entry) != 2:
            raise InvalidRequest(f'R{row_number} 须提供原字节及出处')
        raw, span = entry
        if not isinstance(raw, bytes) or not raw or not isinstance(span, SourceSpan):
            raise InvalidRequest(f'R{row_number} 的字节或出处无效')
        require_name(span.source, '来源')
        if type(span.offset) is not int or span.offset < 0:
            raise InvalidRequest(f'R{row_number} 的来源字节起点无效')
        if type(span.length) is not int or span.length != len(raw):
            raise InvalidRequest(f'R{row_number} 的来源跨度与原字节长度不一致')
        located.append(LocatedRecord(recording_id, row_number, span, raw))
    if not located:
        raise InvalidRequest('录制至少须有一条原行')
    return tuple(located)
