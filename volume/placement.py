from dataclasses import dataclass
import sqlite3

from archive_common import Unavailable


@dataclass(frozen=True, slots=True)
class RetirementReceipt:
    page_id: int
    preserved_bytes: int
    pending_orders: int
    pending_rows: int


@dataclass(frozen=True, slots=True)
class PreparedReplacement:
    page_id: int
    byte_count: int


def ensure_placement_tables(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS hot_payloads (
            page_id INTEGER PRIMARY KEY REFERENCES pages(page_id),
            payload BLOB NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS replacement_payloads (
            page_id INTEGER PRIMARY KEY REFERENCES pages(page_id),
            payload BLOB NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS retirements (
            page_id INTEGER PRIMARY KEY REFERENCES pages(page_id),
            preserved_bytes INTEGER NOT NULL CHECK (preserved_bytes > 0),
            pending_orders INTEGER NOT NULL CHECK (pending_orders >= 0),
            pending_rows INTEGER NOT NULL CHECK (pending_rows >= 0)
        )
    ''')


def put_hot_payload(db: sqlite3.Connection, page_id: int, data: bytes) -> None:
    db.execute('INSERT INTO hot_payloads VALUES (?, ?)', (page_id, data))


def get_hot_payload(db: sqlite3.Connection, page_id: int) -> bytes | None:
    row = db.execute('SELECT payload FROM hot_payloads WHERE page_id = ?', (page_id,)).fetchone()
    return None if row is None else row[0]


def put_replacement_payload(db: sqlite3.Connection, page_id: int, data: bytes) -> None:
    db.execute('INSERT INTO replacement_payloads VALUES (?, ?)', (page_id, data))


def get_replacement_payload(db: sqlite3.Connection, page_id: int) -> bytes | None:
    row = db.execute('SELECT payload FROM replacement_payloads WHERE page_id = ?', (page_id,)).fetchone()
    return None if row is None else row[0]


def find_retirement(db: sqlite3.Connection, page_id: int) -> RetirementReceipt | None:
    row = db.execute('''
        SELECT preserved_bytes, pending_orders, pending_rows FROM retirements WHERE page_id = ?
    ''', (page_id,)).fetchone()
    return None if row is None else RetirementReceipt(page_id, *row)


def get_active_payload(db: sqlite3.Connection, page_id: int) -> bytes:
    # The committed retirement record also binds the page to its replacement address.
    if find_retirement(db, page_id) is not None:
        data = get_replacement_payload(db, page_id)
        address = '替代'
    else:
        data = get_hot_payload(db, page_id)
        address = '热区'
    if data is None:
        raise Unavailable(f'原页 {page_id} 的{address}载荷不可读')
    return data


def publish_retirement(
    db: sqlite3.Connection, prepared: PreparedReplacement, pending_orders: int, pending_rows: int
) -> RetirementReceipt:
    if not db.in_transaction:
        raise RuntimeError('退役发布须在写事务中执行')
    # Preparation and publication share the caller's write transaction.
    removed = db.execute('DELETE FROM hot_payloads WHERE page_id = ?', (prepared.page_id,))
    if removed.rowcount != 1:
        raise Unavailable(f'原页 {prepared.page_id} 的热区地址无法释放')
    db.execute('''
        INSERT INTO retirements (page_id, preserved_bytes, pending_orders, pending_rows)
        VALUES (?, ?, ?, ?)
    ''', (prepared.page_id, prepared.byte_count, pending_orders, pending_rows))
    return RetirementReceipt(prepared.page_id, prepared.byte_count, pending_orders, pending_rows)
