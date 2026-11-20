import sqlite3

from archive_common import InvalidRequest, transaction
from volume.obligations import find_page_obligations
from volume.pages import get_page
from volume.placement import RetirementReceipt, find_retirement, publish_retirement
from volume.replacement import prepare_replacement


def retire(db: sqlite3.Connection, page_id: int) -> RetirementReceipt:
    if type(page_id) is not int or page_id <= 0 or page_id > (1 << 63) - 1:
        raise InvalidRequest('原页号须为有效的正整数')
    with transaction(db, write=True):
        existing = find_retirement(db, page_id)
        if existing is not None:
            return existing
        page = get_page(db, page_id)
        obligations = find_page_obligations(db, page)
        prepared = prepare_replacement(db, page, obligations)
        pending_rows = {row for item in obligations for row in item.row_numbers}
        return publish_retirement(db, prepared, len(obligations), len(pending_rows))
