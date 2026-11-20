from collections.abc import Collection
import sqlite3

from archive_common import Unavailable
from ingest.layout import SourceSpan
from issue.manifest import ManifestLine, OrderManifest


def ensure_order_tables(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            recording_id TEXT NOT NULL REFERENCES recordings(recording_id),
            first_row INTEGER NOT NULL CHECK (first_row > 0),
            last_row INTEGER NOT NULL CHECK (last_row >= first_row)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS order_lines (
            order_id TEXT NOT NULL REFERENCES orders(order_id),
            row_number INTEGER NOT NULL CHECK (row_number > 0),
            source TEXT NOT NULL,
            source_offset TEXT NOT NULL,
            source_length INTEGER NOT NULL CHECK (source_length > 0),
            PRIMARY KEY (order_id, row_number)
        )
    ''')
    db.execute('CREATE INDEX IF NOT EXISTS orders_by_recording ON orders(recording_id, first_row, last_row)')


def store_order(db: sqlite3.Connection, order: OrderManifest) -> None:
    db.execute('''
        INSERT INTO orders (order_id, recording_id, first_row, last_row) VALUES (?, ?, ?, ?)
    ''', (order.order_id, order.recording_id, order.first, order.last))
    db.executemany('''
        INSERT INTO order_lines (order_id, row_number, source, source_offset, source_length)
        VALUES (?, ?, ?, ?, ?)
    ''', ((
        order.order_id, line.row_number, line.source.source,
        format(line.source.offset, 'x'), line.source.length,
    ) for line in order.lines))


def _load_order(
    db: sqlite3.Connection, order_id: str, recording_id: str, first: int, last: int
) -> OrderManifest:
    rows = db.execute('''
        SELECT row_number, source, source_offset, source_length FROM order_lines
        WHERE order_id = ? ORDER BY row_number
    ''', (order_id,)).fetchall()
    if len(rows) != last - first + 1 or any(row[0] != first + index for index, row in enumerate(rows)):
        raise Unavailable(f'交接单 {order_id} 的冻结清单不完整')
    try:
        lines = tuple(
            ManifestLine(number, SourceSpan(source, int(offset, 16), length))
            for number, source, offset, length in rows
        )
    except (TypeError, ValueError) as error:
        raise Unavailable(f'交接单 {order_id} 的冻结出处无效') from error
    return OrderManifest(order_id, recording_id, first, last, lines)


def find_order(db: sqlite3.Connection, order_id: str) -> OrderManifest | None:
    row = db.execute('''
        SELECT recording_id, first_row, last_row FROM orders WHERE order_id = ?
    ''', (order_id,)).fetchone()
    return None if row is None else _load_order(db, order_id, *row)


def find_orders_covering(
    db: sqlite3.Connection, recording_id: str, row_numbers: Collection[int]
) -> tuple[OrderManifest, ...]:
    if not row_numbers:
        return ()
    candidates = db.execute('''
        SELECT order_id, first_row, last_row FROM orders
        WHERE recording_id = ? AND first_row <= ? AND last_row >= ? ORDER BY order_id
    ''', (recording_id, max(row_numbers), min(row_numbers))).fetchall()
    return tuple(
        _load_order(db, order_id, recording_id, first, last)
        for order_id, first, last in candidates
        if any(first <= number <= last for number in row_numbers)
    )
