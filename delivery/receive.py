from collections.abc import Iterable
from dataclasses import dataclass
import sqlite3

from archive_common import Conflict, InvalidRequest, require_name, transaction
from delivery.payload import DeliveredLine
from delivery.receipts import Receipt, append_receipt, find_receipt
from delivery.settlement import (
    ClaimReceipt,
    ClaimResult,
    claim as _claim,
    confirm_claim as _confirm_claim,
    find_claim_receipt,
    pending_deliveries,
    received_rows,
    require_order,
    settle_rows,
)
from issue.register import find_transfer_by_parent, is_child_order


@dataclass(frozen=True, slots=True)
class ConfirmationResult:
    receipt: Receipt
    confirmed: tuple[int, ...]


def read(
    db: sqlite3.Connection, order_id: str, first: int | None = None, last: int | None = None
) -> tuple[DeliveredLine, ...]:
    require_name(order_id, '交接单号')
    with transaction(db):
        order = require_order(db, order_id)
        return pending_deliveries(db, order, first, last)


def claim(
    db: sqlite3.Connection, order_id: str, first: int | None = None, last: int | None = None
) -> ClaimResult:
    return _claim(db, order_id, first, last)


def confirm_claim(
    db: sqlite3.Connection, order_id: str, request_id: str, claim_id: str, row_numbers: Iterable[int]
) -> ClaimReceipt:
    return _confirm_claim(db, order_id, request_id, claim_id, row_numbers)


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
        order = require_order(db, order_id)
        members = {line.row_number for line in order.lines}
        if not members.issuperset(numbers):
            raise InvalidRequest(f'确认包含交接单 {order_id} 范围以外的原行')

        recorded = find_receipt(db, order_id, request_id)
        if recorded is not None:
            if recorded.row_numbers != numbers:
                raise Conflict(f'交接单 {order_id} 的确认请求 {request_id} 已以不同原行集合提交')
            return ConfirmationResult(recorded, tuple(sorted(received_rows(db, order))))

        if find_claim_receipt(db, order_id, request_id) is not None:
            raise Conflict(f'交接单 {order_id} 的确认请求 {request_id} 已由凭据点收入口使用')

        # The legacy entry keeps its replay semantics for its own request ids.
        # A fresh request may only settle rows an ordinary order still owns:
        # every row on a hand-off order belongs to the credential flow, and a
        # transferred row belongs to the receiving shift.
        if is_child_order(db, order_id):
            raise InvalidRequest(f'交接单 {order_id} 为接办单，须凭凭据点收')
        outgoing = find_transfer_by_parent(db, order_id)
        handed_off = frozenset() if outgoing is None else frozenset(outgoing.rows)
        if any(number in handed_off for number in numbers):
            raise InvalidRequest(f'确认包含交接单 {order_id} 已转派的行位')
        settle_rows(db, order_id, request_id, numbers)
        receipt = append_receipt(db, order_id, request_id, numbers)
        return ConfirmationResult(receipt, tuple(sorted(received_rows(db, order))))
