from dataclasses import dataclass
import sqlite3

from archive_common import Conflict, InvalidRequest, Unavailable, UnknownRecord, require_name, transaction
from delivery.claims import Claim, append_claim, find_claim, new_claim_id
from delivery.payload import DeliveredLine, load_original_lines
from delivery.receipts import find_receipt, list_receipts
from delivery.resume import pending_lines
from issue.manifest import OrderManifest
from issue.ownership import Obligation, obligation_for
from issue.register import find_order, find_transfer, find_transfer_by_parent


@dataclass(frozen=True, slots=True)
class ClaimResult:
    claim_id: str
    lines: tuple[DeliveredLine, ...]


@dataclass(frozen=True, slots=True)
class SettledRow:
    row_number: int
    newly_settled: bool
    settled_order_id: str
    settled_request_id: str


@dataclass(frozen=True, slots=True)
class ClaimReceipt:
    order_id: str
    request_id: str
    claim_id: str
    rows: tuple[SettledRow, ...]


@dataclass(frozen=True, slots=True)
class Settlement:
    settled_order_id: str
    settled_request_id: str


@dataclass(frozen=True, slots=True)
class Handoff:
    row_number: int
    parent_id: str
    child_id: str
    pending_order_id: str | None
    settled_order_id: str | None
    settled_request_id: str | None


def ensure_settlement_tables(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS settlements (
            obligation_order_id TEXT NOT NULL,
            row_number INTEGER NOT NULL CHECK (row_number > 0),
            settled_order_id TEXT NOT NULL,
            settled_request_id TEXT NOT NULL,
            PRIMARY KEY (obligation_order_id, row_number)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS claim_receipts (
            sequence INTEGER PRIMARY KEY,
            order_id TEXT NOT NULL REFERENCES orders(order_id),
            request_id TEXT NOT NULL,
            claim_id TEXT NOT NULL,
            row_count INTEGER NOT NULL CHECK (row_count >= 0),
            UNIQUE (order_id, request_id)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS claim_receipt_rows (
            sequence INTEGER NOT NULL REFERENCES claim_receipts(sequence),
            row_number INTEGER NOT NULL CHECK (row_number > 0),
            newly_settled INTEGER NOT NULL CHECK (newly_settled IN (0, 1)),
            settled_order_id TEXT NOT NULL,
            settled_request_id TEXT NOT NULL,
            PRIMARY KEY (sequence, row_number)
        )
    ''')


def require_order(db: sqlite3.Connection, order_id: str) -> OrderManifest:
    order = find_order(db, order_id)
    if order is None:
        raise UnknownRecord(f'未知交接单 {order_id}')
    return order


def _ledger_settlement(db: sqlite3.Connection, obligation: Obligation) -> Settlement | None:
    row = db.execute('''
        SELECT settled_order_id, settled_request_id FROM settlements
        WHERE obligation_order_id = ? AND row_number = ?
    ''', (obligation.order_id, obligation.row_number)).fetchone()
    return None if row is None else Settlement(*row)


def _legacy_settlement(db: sqlite3.Connection, obligation: Obligation) -> Settlement | None:
    row = db.execute('''
        SELECT h.order_id, h.request_id FROM receipt_headers AS h
        JOIN receipt_lines AS l ON l.sequence = h.sequence
        WHERE h.order_id = ? AND l.row_number = ?
        ORDER BY h.sequence LIMIT 1
    ''', (obligation.order_id, obligation.row_number)).fetchone()
    return None if row is None else Settlement(*row)


def find_settlement(db: sqlite3.Connection, obligation: Obligation) -> Settlement | None:
    found = _ledger_settlement(db, obligation)
    if found is not None:
        return found
    return _legacy_settlement(db, obligation)


def _record_settlement(
    db: sqlite3.Connection, obligation: Obligation, order_id: str, request_id: str
) -> None:
    db.execute('''
        INSERT INTO settlements (obligation_order_id, row_number, settled_order_id, settled_request_id)
        VALUES (?, ?, ?, ?)
    ''', (obligation.order_id, obligation.row_number, order_id, request_id))


def settle_rows(
    db: sqlite3.Connection, order_id: str, request_id: str, row_numbers: tuple[int, ...]
) -> tuple[SettledRow, ...]:
    settled = []
    for row_number in row_numbers:
        obligation = obligation_for(db, order_id, row_number)
        existing = find_settlement(db, obligation)
        if existing is not None:
            settled.append(SettledRow(row_number, False, existing.settled_order_id, existing.settled_request_id))
        else:
            _record_settlement(db, obligation, order_id, request_id)
            settled.append(SettledRow(row_number, True, order_id, request_id))
    return tuple(settled)


def received_rows(db: sqlite3.Connection, order: OrderManifest) -> frozenset[int]:
    """Rows on this order already covered by an actual first receipt.

    A legacy ordinary receipt counts directly. A settlement recorded from
    either side of a hand-off counts for both sides.
    """
    members = frozenset(line.row_number for line in order.lines)
    received = set()
    for receipt in list_receipts(db, order.order_id):
        received.update(number for number in receipt.row_numbers if number in members)
    for line in order.lines:
        if find_settlement(db, obligation_for(db, order.order_id, line.row_number)) is not None:
            received.add(line.row_number)
    return frozenset(received)


def eligible_rows(db: sqlite3.Connection, order: OrderManifest) -> frozenset[int]:
    """Rows this order may still hand out or settle itself."""
    received = received_rows(db, order)
    outgoing = find_transfer_by_parent(db, order.order_id)
    handed_off = frozenset() if outgoing is None else frozenset(outgoing.rows)
    return frozenset(
        line.row_number for line in order.lines
        if line.row_number not in received and line.row_number not in handed_off
    )


def pending_deliveries(
    db: sqlite3.Connection, order: OrderManifest, first: int | None = None, last: int | None = None
) -> tuple[DeliveredLine, ...]:
    lines = pending_lines(order, frozenset(), first, last, eligible=eligible_rows(db, order))
    return load_original_lines(db, order.recording_id, lines)


def claim(
    db: sqlite3.Connection, order_id: str, first: int | None = None, last: int | None = None
) -> ClaimResult:
    require_name(order_id, '交接单号')
    with transaction(db, write=True):
        order = require_order(db, order_id)
        delivered = pending_deliveries(db, order, first, last)
        claim_record = append_claim(db, order_id, new_claim_id(), tuple(line.row_number for line in delivered))
        return ClaimResult(claim_record.claim_id, delivered)


def _claim_rows_eligible(
    db: sqlite3.Connection, order: OrderManifest, claim_record: Claim, row_numbers: tuple[int, ...]
) -> bool:
    members = {line.row_number for line in order.lines}
    incoming = find_transfer(db, order.order_id)
    outgoing = find_transfer_by_parent(db, order.order_id)
    for row_number in row_numbers:
        if row_number not in members:
            return False
        if incoming is not None:
            # The night shift settles only its hand-off subset through the
            # hand-off order's own credentials.
            if row_number not in incoming.rows:
                return False
            continue
        if outgoing is not None and row_number in outgoing.rows:
            # The day shift may settle a row it handed off only with a
            # credential signed out before the hand-off was committed.
            if claim_record.sequence > outgoing.claim_floor:
                return False
    return True


def _load_claim_receipt(
    db: sqlite3.Connection, sequence: int, order_id: str, request_id: str, claim_id: str, row_count: int
) -> ClaimReceipt:
    rows = tuple(
        SettledRow(row_number, bool(newly_settled), settled_order_id, settled_request_id)
        for row_number, newly_settled, settled_order_id, settled_request_id in db.execute('''
            SELECT row_number, newly_settled, settled_order_id, settled_request_id
            FROM claim_receipt_rows WHERE sequence = ? ORDER BY row_number
        ''', (sequence,))
    )
    if len(rows) != row_count:
        raise Unavailable(f'交接单 {order_id} 的凭据点收回执 {request_id} 明细不完整')
    return ClaimReceipt(order_id, request_id, claim_id, rows)


def find_claim_receipt(db: sqlite3.Connection, order_id: str, request_id: str) -> ClaimReceipt | None:
    header = db.execute('''
        SELECT sequence, claim_id, row_count FROM claim_receipts
        WHERE order_id = ? AND request_id = ?
    ''', (order_id, request_id)).fetchone()
    if header is None:
        return None
    sequence, claim_id, row_count = header
    return _load_claim_receipt(db, sequence, order_id, request_id, claim_id, row_count)


def _append_claim_receipt(
    db: sqlite3.Connection, order_id: str, request_id: str, claim_id: str, rows: tuple[SettledRow, ...]
) -> ClaimReceipt:
    cursor = db.execute('''
        INSERT INTO claim_receipts (order_id, request_id, claim_id, row_count) VALUES (?, ?, ?, ?)
    ''', (order_id, request_id, claim_id, len(rows)))
    sequence = cursor.lastrowid
    db.executemany('''
        INSERT INTO claim_receipt_rows
            (sequence, row_number, newly_settled, settled_order_id, settled_request_id)
        VALUES (?, ?, ?, ?, ?)
    ''', (
        (sequence, row.row_number, 1 if row.newly_settled else 0,
         row.settled_order_id, row.settled_request_id)
        for row in rows
    ))
    return ClaimReceipt(order_id, request_id, claim_id, rows)


def confirm_claim(
    db: sqlite3.Connection, order_id: str, request_id: str, claim_id: str, row_numbers
) -> ClaimReceipt:
    require_name(order_id, '交接单号')
    require_name(request_id, '确认请求号')
    require_name(claim_id, '凭据号')
    supplied = tuple(row_numbers)
    if any(type(number) is not int for number in supplied):
        raise InvalidRequest('点收须提供整数原行位')
    numbers = tuple(sorted(set(supplied)))
    with transaction(db, write=True):
        order = require_order(db, order_id)
        claim_record = find_claim(db, order_id, claim_id)
        if claim_record is None:
            raise InvalidRequest(f'交接单 {order_id} 没有可兑现的凭据 {claim_id}')
        if not set(claim_record.rows).issuperset(numbers):
            raise InvalidRequest(f'点收包含凭据 {claim_id} 未实际领取的原行')

        recorded = find_claim_receipt(db, order_id, request_id)
        if recorded is not None:
            if recorded.claim_id != claim_id or tuple(row.row_number for row in recorded.rows) != numbers:
                raise Conflict(f'交接单 {order_id} 的点收请求 {request_id} 已以不同凭据或原行集合提交')
            return recorded
        if find_receipt(db, order_id, request_id) is not None:
            raise Conflict(f'交接单 {order_id} 的点收请求 {request_id} 已由原点收入口使用')
        if not _claim_rows_eligible(db, order, claim_record, numbers):
            raise InvalidRequest(f'点收包含交接单 {order_id} 不合资格的原行')

        settled = settle_rows(db, order_id, request_id, numbers)
        return _append_claim_receipt(db, order_id, request_id, claim_id, settled)


def handoffs(db: sqlite3.Connection, order_id: str) -> tuple[Handoff, ...]:
    outgoing = find_transfer_by_parent(db, order_id)
    incoming = find_transfer(db, order_id)
    if outgoing is None and incoming is None:
        return ()
    parent_id = outgoing.parent_id if outgoing is not None else incoming.parent_id
    child_id = outgoing.child_id if outgoing is not None else incoming.child_id
    rows = outgoing.rows if outgoing is not None else incoming.rows
    result = []
    for row_number in sorted(rows):
        settlement = find_settlement(db, Obligation(parent_id, row_number))
        if settlement is None:
            result.append(Handoff(row_number, parent_id, child_id, child_id, None, None))
        else:
            result.append(Handoff(
                row_number, parent_id, child_id, None,
                settlement.settled_order_id, settlement.settled_request_id,
            ))
    return tuple(result)
