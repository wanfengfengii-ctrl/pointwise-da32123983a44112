from dataclasses import dataclass
import sqlite3

from delivery.settlement import received_rows
from issue.register import find_orders_covering, find_transfer_by_parent
from volume.pages import Page


@dataclass(frozen=True, slots=True)
class PageObligation:
    order_id: str
    row_numbers: tuple[int, ...]


def find_page_obligations(db: sqlite3.Connection, page: Page) -> tuple[PageObligation, ...]:
    page_rows = frozenset(span.row_number for span in page.spans)
    obligations = []
    for order in find_orders_covering(db, page.recording_id, page_rows):
        received = received_rows(db, order)
        outgoing = find_transfer_by_parent(db, order.order_id)
        handed_off = frozenset() if outgoing is None else frozenset(outgoing.rows)
        pending = tuple(
            line.row_number for line in order.lines
            if line.row_number in page_rows
            and line.row_number not in received
            and line.row_number not in handed_off
        )
        if pending:
            obligations.append(PageObligation(order.order_id, pending))
    return tuple(obligations)
