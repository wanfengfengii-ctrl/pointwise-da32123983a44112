from collections.abc import Iterable
from dataclasses import dataclass
import sqlite3

from archive_common import Conflict, InvalidRequest, UnknownRecord, require_name, transaction
from delivery.coverage import confirmed_rows
from delivery.payload import DeliveredLine, load_original_lines
from delivery.receipts import Receipt, append_receipt, find_receipt, list_receipts
from delivery.resume import pending_lines
from issue.manifest import OrderManifest
from issue.register import find_order


@dataclass(frozen=True, slots=True)
class ConfirmationResult:
    receipt: Receipt
    confirmed: tuple[int, ...]


def _require_order(db: sqlite3.Connection, order_id: str) -> OrderManifest:
    order = find_order(db, order_id)
    if order is None:
        raise UnknownRecord(f'未知交接单 {order_id}')
    return order


def read(
    db: sqlite3.Connection, order_id: str, first: int | None = None, last: int | None = None
) -> tuple[DeliveredLine, ...]:
    require_name(order_id, '交接单号')
    with transaction(db):
        order = _require_order(db, order_id)
        received = confirmed_rows(list_receipts(db, order_id))
        lines = pending_lines(order, received, first, last)
        return load_original_lines(db, order.recording_id, lines)


def confirm(
    db: sqlite3.Connection, order_id: str, request_id: str, row_numbers: Iterable[int]
) -> ConfirmationResult:
    require_name(order_id, '交接单号')
    require_name(request_id, '确认请求号')
    supplied = tuple(row_numbers)
    if any(type(number) is not int for number in supplied):
        raise InvalidRequest('确认须提供整数原行位')
    numbers = tuple(sorted(set(supplied)))
    with transaction(db, write=True):
        order = _require_order(db, order_id)
        members = {line.row_number for line in order.lines}
        if not members.issuperset(numbers):
            raise InvalidRequest(f'确认包含交接单 {order_id} 范围以外的原行')
        receipt = find_receipt(db, order_id, request_id)
        if receipt is None:
            receipt = append_receipt(db, order_id, request_id, numbers)
        elif receipt.row_numbers != numbers:
            raise Conflict(f'交接单 {order_id} 的确认请求 {request_id} 已以不同原行集合提交')
        received = confirmed_rows(list_receipts(db, order_id))
        return ConfirmationResult(receipt, tuple(sorted(received)))
