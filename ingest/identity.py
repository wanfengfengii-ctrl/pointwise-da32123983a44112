from uuid import uuid4
import sqlite3

from archive_common import Unavailable


def ensure_archive_identity(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS archive_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')
    row = db.execute("SELECT value FROM archive_meta WHERE key = 'archive_id'").fetchone()
    if row is None:
        db.execute(
            "INSERT INTO archive_meta (key, value) VALUES ('archive_id', ?)",
            (uuid4().hex,),
        )


def get_archive_id(db: sqlite3.Connection) -> str:
    row = db.execute("SELECT value FROM archive_meta WHERE key = 'archive_id'").fetchone()
    if row is None or not isinstance(row[0], str) or not row[0]:
        raise Unavailable('归档身份不可用')
    return row[0]
