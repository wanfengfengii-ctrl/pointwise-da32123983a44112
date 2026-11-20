from pathlib import Path
import sqlite3

from archive_common import transaction
from delivery.claims import ensure_claim_tables
from delivery.receipts import ensure_receipt_tables
from delivery.settlement import ensure_settlement_tables
from ingest.identity import ensure_archive_identity
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
            ensure_archive_identity(db)
            ensure_recording_tables(db)
            ensure_page_tables(db)
            ensure_placement_tables(db)
            ensure_order_tables(db)
            ensure_receipt_tables(db)
            ensure_claim_tables(db)
            ensure_settlement_tables(db)
    except BaseException:
        db.close()
        raise
    return db
