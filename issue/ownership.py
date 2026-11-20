from dataclasses import dataclass
import sqlite3

from issue.register import find_transfer, find_transfer_by_parent


@dataclass(frozen=True, slots=True)
class Obligation:
    """The business obligation behind one original row position.

    Both sides of a hand-off share the same obligation, so a row can only ever
    be settled once no matter which shift reports it first.
    """

    order_id: str
    row_number: int


def obligation_for(db: sqlite3.Connection, order_id: str, row_number: int) -> Obligation:
    outgoing = find_transfer_by_parent(db, order_id)
    if outgoing is not None and row_number in outgoing.rows:
        return Obligation(order_id, row_number)
    incoming = find_transfer(db, order_id)
    if incoming is not None and row_number in incoming.rows:
        return Obligation(incoming.parent_id, row_number)
    return Obligation(order_id, row_number)
