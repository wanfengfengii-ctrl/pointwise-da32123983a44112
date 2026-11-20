from dataclasses import dataclass
import sqlite3

from archive_common import Unavailable, UnknownRecord
from delivery.coverage import confirmed_rows
from delivery.receipts import Receipt, list_receipts
from ingest.registry import get_observations
from ingest.samples import Observation
from issue.manifest import OrderManifest
from issue.register import find_order


@dataclass(frozen=True, slots=True)
class AccountEvidence:
    order: OrderManifest
    observations: tuple[Observation, ...]
    receipts: tuple[Receipt, ...]
    received: frozenset[int]


def load_account_evidence(db: sqlite3.Connection, order_id: str) -> AccountEvidence:
    order = find_order(db, order_id)
    if order is None:
        raise UnknownRecord(f'未知交接单 {order_id}')
    numbers = tuple(line.row_number for line in order.lines)
    observations = get_observations(db, order.recording_id, numbers)
    for line, item in zip(order.lines, observations, strict=True):
        if line.row_number != item.row_number or line.source != item.source:
            raise Unavailable(f'交接单 {order_id} 的 R{line.row_number} 冻结出处与观测依据不一致')
    receipts = list_receipts(db, order_id)
    received = confirmed_rows(receipts)
    if not received.issubset(numbers):
        raise Unavailable(f'交接单 {order_id} 的回执包含单外原行')
    return AccountEvidence(order, observations, receipts, received)
