from dataclasses import dataclass
import secrets
import sqlite3

from archive_common import Unavailable
from ingest.identity import get_archive_id


@dataclass(frozen=True, slots=True)
class Claim:
    sequence: int
    archive_id: str
    order_id: str
    claim_id: str
    rows: tuple[int, ...]


def ensure_claim_tables(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS claim_headers (
            sequence INTEGER PRIMARY KEY,
            archive_id TEXT NOT NULL,
            order_id TEXT NOT NULL REFERENCES orders(order_id),
            claim_id TEXT NOT NULL,
            UNIQUE (archive_id, order_id, claim_id)
        )
    ''')
    db.execute('CREATE INDEX IF NOT EXISTS claims_by_order ON claim_headers(archive_id, order_id, sequence)')
    db.execute('''
        CREATE TABLE IF NOT EXISTS claim_lines (
            sequence INTEGER NOT NULL REFERENCES claim_headers(sequence),
            row_number INTEGER NOT NULL CHECK (row_number > 0),
            PRIMARY KEY (sequence, row_number)
        )
    ''')


def new_claim_id() -> str:
    # A credential identifier is opaque and unguessable; it is stored, not derived.
    return secrets.token_hex(16)


def _load_claim(
    db: sqlite3.Connection, sequence: int, archive_id: str, order_id: str, claim_id: str
) -> Claim:
    rows = tuple(row_number for (row_number,) in db.execute('''
        SELECT row_number FROM claim_lines WHERE sequence = ? ORDER BY row_number
    ''', (sequence,)))
    if any(previous >= current for previous, current in zip(rows, rows[1:])):
        raise Unavailable(f'交接单 {order_id} 的凭据 {claim_id} 明细无效')
    return Claim(sequence, archive_id, order_id, claim_id, rows)


def find_claim(db: sqlite3.Connection, order_id: str, claim_id: str) -> Claim | None:
    archive_id = get_archive_id(db)
    row = db.execute('''
        SELECT sequence FROM claim_headers
        WHERE archive_id = ? AND order_id = ? AND claim_id = ?
    ''', (archive_id, order_id, claim_id)).fetchone()
    if row is None:
        return None
    return _load_claim(db, row[0], archive_id, order_id, claim_id)


def append_claim(
    db: sqlite3.Connection, order_id: str, claim_id: str, rows: tuple[int, ...]
) -> Claim:
    archive_id = get_archive_id(db)
    cursor = db.execute('''
        INSERT INTO claim_headers (archive_id, order_id, claim_id) VALUES (?, ?, ?)
    ''', (archive_id, order_id, claim_id))
    sequence = cursor.lastrowid
    db.executemany('INSERT INTO claim_lines VALUES (?, ?)', ((sequence, number) for number in rows))
    return Claim(sequence, archive_id, order_id, claim_id, rows)


def latest_sequence(db: sqlite3.Connection, order_id: str) -> int:
    row = db.execute('''
        SELECT max(sequence) FROM claim_headers WHERE archive_id = ? AND order_id = ?
    ''', (get_archive_id(db), order_id)).fetchone()
    return 0 if row is None or row[0] is None else row[0]


def list_claims(db: sqlite3.Connection, order_id: str) -> tuple[Claim, ...]:
    archive_id = get_archive_id(db)
    headers = db.execute('''
        SELECT sequence, claim_id FROM claim_headers
        WHERE archive_id = ? AND order_id = ? ORDER BY sequence
    ''', (archive_id, order_id)).fetchall()
    return tuple(_load_claim(db, sequence, archive_id, order_id, claim_id) for sequence, claim_id in headers)
