"""Sample seeding and an ordinary overlapping-order control for the archive."""
from contextlib import closing
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from delivery.receive import confirm, read
from ingest.layout import SourceSpan, source_records
from ingest.recording import seal
from ingest.workspace import open_archive
from issue.orders import issue
from reconcile.report import account
from volume.retire import retire


RECORDING = "probe-01"
SOURCE = "probe/A.raw"
PARENT = "甲"
CHILD = "乙"
SAMPLE = Path("samples/shift.obs")


def seed_shift(db):
    return seal(db, RECORDING, source_records(SOURCE, SAMPLE.read_bytes()), page_ends=(1, 3, 4))


def row_numbers(lines):
    return tuple(line.row_number for line in lines)


def issue_and_retire_control(db):
    capture = seed_shift(db)
    issue(db, PARENT, RECORDING, 1, 4)
    issue(db, CHILD, RECORDING, 2, 3)
    assert row_numbers(read(db, PARENT, 2, 2)) == (2,)
    first = confirm(db, PARENT, "点收", [2]).receipt
    child_pending = row_numbers(read(db, CHILD))
    assert child_pending == (2, 3)
    second = confirm(db, CHILD, "点收", [2]).receipt
    assert first != second
    retired = retire(db, capture.page_ids[1])
    assert (retired.pending_orders, retired.pending_rows) == (2, 1)
    return first, second, retired, child_pending


def read_control(db, first, second, retired, child_pending):
    totals = []
    for order_id, receipt in ((PARENT, first), (CHILD, second)):
        assert confirm(db, order_id, "点收", [2]).receipt == receipt
        report = account(db, order_id)
        assert row_numbers(report.confirmed) == (2,)
        totals.append(report.confirmed_total.microstrain)
    assert totals == [7, 7]
    (row,) = read(db, CHILD)
    assert (row.row_number, row.raw, row.source) == (3, b"+011\n", SourceSpan(SOURCE, 7, 5))
    assert retire(db, retired.page_id) == retired
    return {
        "child_pending_after_parent": child_pending,
        "receipt_orders": [first.order_id, second.order_id],
        "confirmed_microstrain": totals,
        "retirement_pending": [retired.pending_orders, retired.pending_rows],
        "child_row_after_retirement": [row.row_number, row.raw.decode("ascii"), row.source.offset, row.source.length],
    }


def main():
    with TemporaryDirectory(prefix="tingqiao-transfer-control-") as directory:
        with closing(open_archive(directory)) as db:
            control = issue_and_retire_control(db)
        with closing(open_archive(directory)) as db:
            result = read_control(db, *control)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
