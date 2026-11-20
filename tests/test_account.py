from archive_common import Unavailable, UnknownRecord
from delivery.receive import confirm, read
from delivery.receipts import find_receipt
from ingest.layout import SourceSpan, source_records
from ingest.recording import seal
from issue.orders import issue
from reconcile.report import account
from reconcile.totals import MeasurementTotals
from tests.support import ArchiveCase, RECORDING, SAMPLES, SOURCE, row_numbers
from volume.placement import get_hot_payload
from volume.retire import retire


class TestAccount(ArchiveCase):
    def test_reopen_totals(self):
        upstream = self.root.parent / 'captured.obs'
        upstream.write_bytes((SAMPLES / 'shift.obs').read_bytes())
        captured = seal(self.db, RECORDING, source_records(SOURCE, upstream.read_bytes()), page_ends=(1, 3, 4))
        manifest_a = issue(self.db, '甲', RECORDING, 1, 3)
        manifest_b = issue(self.db, '乙', RECORDING, 2, 4)
        first = confirm(self.db, '甲', '先到', [2]).receipt
        second = confirm(self.db, '乙', '先到', [3]).receipt
        upstream.unlink()
        retire(self.db, captured.page_ids[1])
        self.reopen()
        self.assertFalse(upstream.exists())
        self.assertIsNone(get_hot_payload(self.db, captured.page_ids[1]))
        last_a = confirm(self.db, '甲', '补到', row_numbers(read(self.db, '甲'))).receipt
        last_b = confirm(self.db, '乙', '补到', row_numbers(read(self.db, '乙'))).receipt
        self.reopen()
        a, b = account(self.db, '甲'), account(self.db, '乙')
        self.assertEqual(a.order, manifest_a)
        self.assertEqual(b.order, manifest_b)
        self.assertEqual(a.total, MeasurementTotals(3, 3, 0, 23))
        self.assertEqual(b.total, MeasurementTotals(3, 3, 0, 31))
        self.assertEqual(a.confirmed, a.lines)
        self.assertEqual(b.confirmed, b.lines)
        self.assertEqual(a.pending, ())
        self.assertEqual(b.pending, ())
        self.assertEqual(a.receipts, (first, last_a))
        self.assertEqual(b.receipts, (second, last_b))
        self.assertEqual([(line.row_number, line.source, line.value) for line in a.lines],
                         [(1, SourceSpan(SOURCE, 0, 3), 5), (2, SourceSpan(SOURCE, 3, 4), 7),
                          (3, SourceSpan(SOURCE, 7, 5), 11)])
        self.assertEqual([(line.row_number, line.source, line.value) for line in b.lines],
                         [(2, SourceSpan(SOURCE, 3, 4), 7), (3, SourceSpan(SOURCE, 7, 5), 11),
                          (4, SourceSpan(SOURCE, 12, 3), 13)])

    def test_overlap_total(self):
        self.seed_shift()
        issue(self.db, '甲', RECORDING, 1, 3)
        issue(self.db, '乙', RECORDING, 2, 4)
        a1 = confirm(self.db, '甲', '1', [1, 2]).receipt
        a2 = confirm(self.db, '甲', '2', [2, 3]).receipt
        b1 = confirm(self.db, '乙', '1', [2, 3, 4]).receipt
        self.assertEqual(confirm(self.db, '甲', '1', [2, 1]).receipt, a1)
        self.reopen()
        a, b = account(self.db, '甲'), account(self.db, '乙')
        self.assertEqual(a.receipts, (a1, a2))
        self.assertEqual(b.receipts, (b1,))
        self.assertEqual(row_numbers(a.confirmed), (1, 2, 3))
        self.assertEqual(row_numbers(b.confirmed), (2, 3, 4))
        self.assertEqual(a.total, MeasurementTotals(3, 3, 0, 23))
        self.assertEqual(b.total, MeasurementTotals(3, 3, 0, 31))
        self.assertEqual(a.confirmed_total, a.total)
        self.assertEqual(a.pending_total, MeasurementTotals(0, 0, 0, 0))
        self.assertEqual(b.confirmed_total, b.total)

    def test_signed_missing(self):
        raw = (SAMPLES / 'signed.obs').read_bytes()
        seal(self.db, 'signed', source_records('probe/signed', raw))
        issue(self.db, '甲', 'signed', 1, 4)
        confirm(self.db, '甲', '先点', [2, 3, 4])
        self.reopen()
        report = account(self.db, '甲')
        self.assertEqual([(line.row_number, line.value, line.missing, line.received) for line in report.lines],
                         [(1, -8, False, False), (2, 0, False, True), (3, None, True, True), (4, 3, False, True)])
        self.assertEqual(report.total, MeasurementTotals(4, 3, 1, -5))
        self.assertEqual(row_numbers(report.confirmed), (2, 3, 4))
        self.assertEqual(row_numbers(report.pending), (1,))
        self.assertEqual(report.confirmed_total, MeasurementTotals(3, 2, 1, 3))
        self.assertEqual(report.pending_total, MeasurementTotals(1, 1, 0, -8))
        issue(self.db, '仅缺测', 'signed', 3, 3)
        self.assertEqual(account(self.db, '仅缺测').total, MeasurementTotals(1, 0, 1, 0))

    def test_exact_large_integer(self):
        digit_count = 4401
        raw = b'9' * digit_count + b'\n-7\n'
        captured = seal(self.db, 'large', source_records('large-source', raw))
        issue(self.db, '甲', 'large', 1, 2)
        retire(self.db, captured.page_ids[0])
        self.reopen()
        first = read(self.db, '甲', 1, 1)[0]
        self.assertEqual(first.raw, raw[:-3])
        self.assertTrue(first.value == 10 ** digit_count - 1)
        self.assertTrue(account(self.db, '甲').total.microstrain == 10 ** digit_count - 8)

    def test_incomplete_basis(self):
        self.seed_shift()
        issue(self.db, '甲', RECORDING, 1, 3)
        receipt = confirm(self.db, '甲', '先点', [2]).receipt
        # Simulate a missing directory entry while retaining the frozen order and receipts.
        self.db.execute('PRAGMA foreign_keys = OFF')
        self.db.execute('DELETE FROM observations WHERE recording_id = ? AND row_number = 3', (RECORDING,))
        self.reopen()
        with self.assertRaisesRegex(Unavailable, 'R3 观测依据缺失'):
            account(self.db, '甲')
        self.assertEqual(find_receipt(self.db, '甲', '先点'), receipt)

    def test_incomplete_receipt_lines(self):
        self.seed_shift()
        issue(self.db, '甲', RECORDING, 1, 3)
        preserved = confirm(self.db, '甲', '前次', [1]).receipt
        damaged = confirm(self.db, '甲', '后次', [2, 3]).receipt
        self.db.execute('DELETE FROM receipt_lines WHERE sequence = ? AND row_number = 3', (damaged.sequence,))
        self.reopen()
        with self.assertRaisesRegex(Unavailable, '回执 后次 明细不完整'):
            account(self.db, '甲')
        self.assertEqual(find_receipt(self.db, '甲', '前次'), preserved)

    def test_unknown_order(self):
        with self.assertRaises(UnknownRecord):
            account(self.db, '不存在')
