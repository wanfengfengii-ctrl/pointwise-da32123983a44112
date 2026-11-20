from collections.abc import Iterable
from itertools import pairwise
import sqlite3

from archive_common import InvalidRequest
from ingest.layout import LocatedRecord
from volume.pages import Page, PageSpan, store_page
from volume.placement import put_hot_payload


_DEFAULT_PAGE_ROWS = 128


def normalize_page_ends(row_count: int, page_ends: Iterable[int] | None) -> tuple[int, ...]:
    if page_ends is None:
        return (*range(_DEFAULT_PAGE_ROWS, row_count, _DEFAULT_PAGE_ROWS), row_count)
    ends = tuple(page_ends)
    if not ends or any(type(end) is not int for end in ends) or ends[-1] != row_count:
        raise InvalidRequest('封页末行须为行位，且最后一页须到录制末行')
    if any(left >= right for left, right in pairwise((0, *ends))):
        raise InvalidRequest('各页末行须严格递增且从正行位开始')
    return ends


def seal_pages(
    db: sqlite3.Connection,
    recording_id: str,
    records: tuple[LocatedRecord, ...],
    page_ends: tuple[int, ...],
) -> tuple[Page, ...]:
    pages = []
    for ordinal, (start, stop) in enumerate(pairwise((0, *page_ends)), start=1):
        payload = bytearray()
        spans = []
        for record in records[start:stop]:
            spans.append(PageSpan(record.row_number, len(payload), len(record.raw)))
            payload.extend(record.raw)
        data = bytes(payload)
        page = store_page(db, recording_id, ordinal, tuple(spans), data)
        put_hot_payload(db, page.page_id, data)
        pages.append(page)
    return tuple(pages)
