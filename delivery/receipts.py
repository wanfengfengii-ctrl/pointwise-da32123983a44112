from dataclasses import dataclass
import sqlite3

from archive_common import Unavailable


@dataclass(frozen=True, slots=True)
class Receipt:
    sequence: int
    order_id: str
    request_id: str
    row_numbers: tuple[int, ...]


def ensure_receipt_tables(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS receipt_headers (
            sequence INTEGER PRIMARY KEY,
            order_id TEXT NOT NULL REFERENCES orders(order_id),
            request_id TEXT NOT NULL,
            row_count INTEGER NOT NULL CHECK (row_count >= 0),
            UNIQUE (order_id, request_id)
        )
    ''')
    db.execute('CREATE INDEX IF NOT EXISTS receipt_order_sequence ON receipt_headers(order_id, sequence)')
    db.execute('''
        CREATE TABLE IF NOT EXISTS receipt_lines (
            sequence INTEGER NOT NULL REFERENCES receipt_headers(sequence),
            row_number INTEGER NOT NULL CHECK (row_number > 0),
            PRIMARY KEY (sequence, row_number)
        )
    ''')


def _load_receipt(
    db: sqlite3.Connection, sequence: int, order_id: str, request_id: str, row_count: int
) -> Receipt:
    numbers = tuple(row_number for (row_number,) in db.execute('''
        SELECT row_number FROM receipt_lines WHERE sequence = ? ORDER BY row_number
    ''', (sequence,)))
    if len(numbers) != row_count:
        raise Unavailable(f'交接单 {order_id} 的回执 {request_id} 明细不完整')
    return Receipt(sequence, order_id, request_id, numbers)


def find_receipt(db: sqlite3.Connection, order_id: str, request_id: str) -> Receipt | None:
    header = db.execute('''
        SELECT sequence, row_count FROM receipt_headers WHERE order_id = ? AND request_id = ?
    ''', (order_id, request_id)).fetchone()
    if header is None:
        return None
    sequence, row_count = header
    return _load_receipt(db, sequence, order_id, request_id, row_count)


def append_receipt(
    db: sqlite3.Connection, order_id: str, request_id: str, row_numbers: tuple[int, ...]
) -> Receipt:
    cursor = db.execute('''
        INSERT INTO receipt_headers (order_id, request_id, row_count) VALUES (?, ?, ?)
    ''', (order_id, request_id, len(row_numbers)))
    sequence = cursor.lastrowid
    db.executemany('INSERT INTO receipt_lines VALUES (?, ?)', ((sequence, number) for number in row_numbers))
    return Receipt(sequence, order_id, request_id, row_numbers)


def list_receipts(db: sqlite3.Connection, order_id: str) -> tuple[Receipt, ...]:
    headers = db.execute('''
        SELECT sequence, request_id, row_count FROM receipt_headers
        WHERE order_id = ? ORDER BY sequence
    ''', (order_id,)).fetchall()
    return tuple(
        _load_receipt(db, sequence, order_id, request_id, row_count)
        for sequence, request_id, row_count in headers
    )
