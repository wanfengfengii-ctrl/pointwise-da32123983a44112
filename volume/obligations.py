from dataclasses import dataclass
import sqlite3

from delivery.coverage import confirmed_rows
from delivery.receipts import list_receipts
from issue.register import find_orders_covering
from volume.pages import Page


@dataclass(frozen=True, slots=True)
class PageObligation:
    order_id: str
    row_numbers: tuple[int, ...]


def find_page_obligations(db: sqlite3.Connection, page: Page) -> tuple[PageObligation, ...]:
    page_rows = frozenset(span.row_number for span in page.spans)
    obligations = []
    for order in find_orders_covering(db, page.recording_id, page_rows):
        confirmed = confirmed_rows(list_receipts(db, order.order_id))
        pending = tuple(
            line.row_number for line in order.lines
            if line.row_number in page_rows and line.row_number not in confirmed
        )
        if pending:
            obligations.append(PageObligation(order.order_id, pending))
    return tuple(obligations)
