import sqlite3

from archive_common import Unavailable
from volume.obligations import PageObligation
from volume.pages import Page, slice_page_rows
from volume.placement import (
    PreparedReplacement,
    get_hot_payload,
    get_replacement_payload,
    put_replacement_payload,
)


def prepare_replacement(
    db: sqlite3.Connection, page: Page, obligations: tuple[PageObligation, ...]
) -> PreparedReplacement:
    if not db.in_transaction:
        raise RuntimeError('替代准备须在写事务中执行')
    original = get_hot_payload(db, page.page_id)
    if original is None:
        raise Unavailable(f'原页 {page.page_id} 无可供退役的热区载荷')
    put_replacement_payload(db, page.page_id, original)
    copied = get_replacement_payload(db, page.page_id)
    if copied is None or copied != original:
        raise Unavailable(f'原页 {page.page_id} 的替代载荷与热区原字节不一致')
    needed = sorted({row for item in obligations for row in item.row_numbers})
    slice_page_rows(page, copied, needed)
    return PreparedReplacement(page.page_id, len(copied))
