from archive_common import Conflict, InvalidRequest, UnknownRecord
from delivery.receive import confirm, read
from delivery.receipts import find_receipt, list_receipts
from ingest.layout import SourceSpan
from issue.orders import issue
from issue.register import find_order
from reconcile.report import account
from tests.support import ArchiveCase, RECORDING, SOURCE, row_numbers


class TestDispatch(ArchiveCase):
    def test_ordered_delivery(self):
        self.seed_shift()
        issue(self.db, '甲', RECORDING, 1, 3)
        first = read(self.db, '甲', 1, 1)
        self.assertEqual(row_numbers(first), (1,))
        self.assertEqual(confirm(self.db, '甲', '第一段', row_numbers(first)).confirmed, (1,))
        rest = read(self.db, '甲')
        self.assertEqual(row_numbers(rest), (2, 3))
        self.assertEqual(confirm(self.db, '甲', '第二段', row_numbers(rest)).confirmed, (1, 2, 3))
        self.assertEqual(read(self.db, '甲'), ())
        report = account(self.db, '甲')
        self.assertEqual((row_numbers(report.confirmed), report.confirmed_total.microstrain), ((1, 2, 3), 23))
        self.assertEqual([receipt.row_numbers for receipt in report.receipts], [(1,), (2, 3)])

    def test_closed_range(self):
        self.seed_shift()
        manifest = issue(self.db, '甲', RECORDING, 1, 3)
        self.assertEqual(row_numbers(manifest.lines), (1, 2, 3))
        self.assertEqual(manifest.lines[0].source, SourceSpan(SOURCE, 0, 3))
        self.assertEqual(manifest.lines[-1].source, SourceSpan(SOURCE, 7, 5))
        self.assertEqual(issue(self.db, '甲', RECORDING, 1, 3), manifest)
        with self.assertRaises(Conflict):
            issue(self.db, '甲', RECORDING, 2, 4)
        with self.assertRaises(UnknownRecord):
            issue(self.db, '未知录制', 'none', 1, 1)
        with self.assertRaises(InvalidRequest):
            issue(self.db, '越界', RECORDING, 1, 5)
        with self.assertRaises(InvalidRequest):
            issue(self.db, '倒序', RECORDING, 3, 2)
        self.reopen()
        self.assertEqual(find_order(self.db, '甲'), manifest)
        self.assertIsNone(find_order(self.db, '越界'))
        self.assertIsNone(find_order(self.db, '未知录制'))
        self.assertEqual(row_numbers(read(self.db, '甲')), (1, 2, 3))

    def test_segment(self):
        self.seed_shift()
        issue(self.db, '甲', RECORDING, 1, 4)
        segment = read(self.db, '甲', 2, 3)
        self.assertEqual(row_numbers(segment), (2, 3))
        self.assertEqual([line.raw for line in segment], [b'007\n', b'+011\n'])
        self.assertEqual(read(self.db, '甲', 2, 3), segment)
        self.assertEqual(list_receipts(self.db, '甲'), ())
        self.assertEqual(row_numbers(account(self.db, '甲').pending), (1, 2, 3, 4))
        confirm(self.db, '甲', '中段', [3])
        self.assertEqual(read(self.db, '甲', 2, 3), (segment[0],))
        with self.assertRaises(InvalidRequest):
            read(self.db, '甲', 2)
        with self.assertRaises(InvalidRequest):
            read(self.db, '甲', 2, 5)

    def test_overlapping_confirmations(self):
        self.seed_shift()
        issue(self.db, '甲', RECORDING, 1, 4)
        one = confirm(self.db, '甲', '一', [2, 3])
        two = confirm(self.db, '甲', '二', [4, 3])
        replay = confirm(self.db, '甲', '一', [3, 2, 2])
        self.assertEqual(one.confirmed, (2, 3))
        self.assertEqual(two.receipt.row_numbers, (3, 4))
        self.assertEqual(replay.receipt, one.receipt)
        self.assertEqual(replay.confirmed, (2, 3, 4))
        self.assertEqual(list_receipts(self.db, '甲'), (one.receipt, two.receipt))
        self.assertEqual(row_numbers(read(self.db, '甲')), (1,))

    def test_sparse_reopen(self):
        self.seed_shift()
        issue(self.db, '甲', RECORDING, 1, 4)
        first = confirm(self.db, '甲', '先到', [2]).receipt
        second = confirm(self.db, '甲', '后到', [2, 4]).receipt
        self.assertEqual(confirm(self.db, '甲', '先到', [2]).receipt, first)
        self.reopen()
        self.assertEqual(row_numbers(read(self.db, '甲')), (1, 3))
        self.assertEqual([line.raw for line in read(self.db, '甲')], [b'+5\n', b'+011\n'])
        self.assertEqual(row_numbers(account(self.db, '甲').confirmed), (2, 4))
        self.assertEqual(list_receipts(self.db, '甲'), (first, second))

    def test_conflicting_replay(self):
        self.seed_shift()
        issue(self.db, '甲', RECORDING, 1, 3)
        old = confirm(self.db, '甲', '重投号', [2])
        with self.assertRaises(Conflict):
            confirm(self.db, '甲', '重投号', [3])
        self.reopen()
        self.assertEqual(confirm(self.db, '甲', '重投号', [2]), old)
        self.assertEqual(list_receipts(self.db, '甲'), (old.receipt,))
        self.assertEqual(row_numbers(read(self.db, '甲')), (1, 3))

    def test_line_outside_order(self):
        self.seed_shift()
        issue(self.db, '甲', RECORDING, 1, 3)
        old = confirm(self.db, '甲', '已点', [1]).receipt
        with self.assertRaises(InvalidRequest):
            confirm(self.db, '甲', '混合', [2, 4])
        self.assertIsNone(find_receipt(self.db, '甲', '混合'))
        self.assertEqual(list_receipts(self.db, '甲'), (old,))
        self.assertEqual(row_numbers(read(self.db, '甲')), (2, 3))

    def test_empty_actual_set(self):
        self.seed_shift()
        issue(self.db, '甲', RECORDING, 1, 1)
        receipt = confirm(self.db, '甲', '无实收', [])
        self.assertEqual(receipt.receipt.row_numbers, ())
        self.assertEqual(receipt.confirmed, ())
        self.assertEqual(confirm(self.db, '甲', '无实收', []), receipt)
        self.assertEqual(row_numbers(read(self.db, '甲')), (1,))
