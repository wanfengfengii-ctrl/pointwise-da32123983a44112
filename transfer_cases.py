"""Sample seeding, an ordinary overlapping control, and hand-off worked cases."""
from contextlib import closing
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from delivery.receive import claim, confirm, confirm_claim, read
from ingest.layout import SourceSpan, source_records
from ingest.recording import seal
from ingest.workspace import open_archive
from issue.orders import issue
from issue.transfer import transfer
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


def handoff_cases(directory):
    """Run the hand-worked examples from TRANSFER.md through the new entries."""
    with closing(open_archive(directory)) as db:
        capture = seed_shift(db)
        issue(db, PARENT, RECORDING, 1, 4)
        day_claim = claim(db, PARENT, 2, 2)
        child = transfer(db, PARENT, CHILD, [2, 3])
        night_claim = claim(db, CHILD)
        assert row_numbers(child.lines) == (2, 3)
        assert row_numbers(read(db, PARENT)) == (1, 4)
        assert row_numbers(night_claim.lines) == (2, 3)
        first = confirm_claim(db, PARENT, "白班补", day_claim.claim_id, [2])
        assert (first.rows[0].newly_settled, first.rows[0].settled_order_id) == (True, PARENT)
        late = confirm_claim(db, CHILD, "夜班迟", night_claim.claim_id, [2])
        assert (late.rows[0].newly_settled, late.rows[0].settled_order_id) == (False, PARENT)
        day = account(db, PARENT)
        night = account(db, CHILD)
        assert (day.total.microstrain, day.confirmed_total.microstrain, day.pending_total.microstrain) == (36, 7, 29)
        assert (night.total.microstrain, night.confirmed_total.microstrain, night.pending_total.microstrain) == (18, 7, 11)
        retired = retire(db, capture.page_ids[1])
        assert (retired.pending_orders, retired.pending_rows) == (1, 1)

    with closing(open_archive(directory)) as db:
        assert retire(db, capture.page_ids[1]) == retired
        replay = confirm_claim(db, PARENT, "白班补", day_claim.claim_id, [2])
        assert replay == first
        late_claim = claim(db, CHILD)
        assert row_numbers(late_claim.lines) == (3,)
        (row,) = late_claim.lines
        assert (row.raw, row.source) == (b"+011\n", SourceSpan(SOURCE, 7, 5))
        tail = confirm_claim(db, CHILD, "夜班补", late_claim.claim_id, [3])
        assert tail.rows[0].newly_settled
        assert retire(db, capture.page_ids[1]) == retired
        assert account(db, PARENT).confirmed_total.microstrain == 18
        assert account(db, CHILD).confirmed_total.microstrain == 18
    return {
        "child_manifest": {"first": child.first, "last": child.last, "rows": list(row_numbers(child.lines))},
        "day_settlement": [first.rows[0].settled_order_id, first.rows[0].settled_request_id],
        "late_night_row": [late.rows[0].row_number, late.rows[0].settled_order_id, late.rows[0].settled_request_id],
        "day_account": [day.total.microstrain, day.confirmed_total.microstrain, day.pending_total.microstrain],
        "night_account": [night.total.microstrain, night.confirmed_total.microstrain, night.pending_total.microstrain],
        "retirement_pending": [retired.pending_orders, retired.pending_rows],
        "reopened_night_row": [row.row_number, row.raw.decode("ascii"), row.source.offset, row.source.length],
    }


def main():
    with TemporaryDirectory(prefix="tingqiao-transfer-control-") as directory:
        with closing(open_archive(directory)) as db:
            control = issue_and_retire_control(db)
        with closing(open_archive(directory)) as db:
            result = read_control(db, *control)
    with TemporaryDirectory(prefix="tingqiao-transfer-handoff-") as directory:
        result["handoff"] = handoff_cases(directory)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
