from pathlib import Path
import sqlite3

from archive_common import transaction
from delivery.receipts import ensure_receipt_tables
from ingest.registry import ensure_recording_tables
from issue.register import ensure_order_tables
from volume.pages import ensure_page_tables
from volume.placement import ensure_placement_tables


def open_archive(directory: str | Path) -> sqlite3.Connection:
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / 'archive.sqlite3', isolation_level=None)
    try:
        db.execute('PRAGMA foreign_keys = ON')
        with transaction(db, write=True):
            ensure_recording_tables(db)
            ensure_page_tables(db)
            ensure_placement_tables(db)
            ensure_order_tables(db)
            ensure_receipt_tables(db)
    except BaseException:
        db.close()
        raise
    return db
