from archive_common import Conflict, InvalidRequest, UnknownRecord
from delivery.receive import claim, confirm, confirm_claim, read
from delivery.receipts import find_receipt, list_receipts
from ingest.layout import SourceSpan
from issue.orders import issue
from issue.register import find_order, find_transfer, find_transfer_by_parent
from issue.transfer import transfer
from tests.support import RECORDING, SOURCE, TransferCase, row_numbers


class TestTransfer(TransferCase):
    def test_manifest_and_identity(self):
        parent_claim, manifest, child_claim = self.hand_off(parent_claim_rows=(2, 2), rows=[3, 2, 3])
        self.assertEqual(manifest.order_id, '乙')
        self.assertEqual(row_numbers(manifest.lines), (2, 3))
        self.assertEqual((manifest.first, manifest.last), (2, 3))
        # The frozen lines keep original positions, identity and provenance.
        self.assertEqual(
            [(line.row_number, line.source) for line in manifest.lines],
            [(2, SourceSpan(SOURCE, 3, 4)), (3, SourceSpan(SOURCE, 7, 5))],
        )
        # The parent keeps its complete frozen manifest.
        self.assertEqual(row_numbers(find_order(self.db, '甲').lines), (1, 2, 3, 4))
        self.assertEqual(find_transfer_by_parent(self.db, '甲').child_id, '乙')
        self.assertEqual(find_transfer(self.db, '乙').parent_id, '甲')
        self.assertEqual(parent_claim.lines[0].source, SourceSpan(SOURCE, 3, 4))

    def test_sparse_manifest(self):
        _, manifest, _ = self.hand_off(parent_claim_rows=None, rows=[2, 4])
        self.assertEqual(row_numbers(manifest.lines), (2, 4))
        self.assertEqual((manifest.first, manifest.last), (2, 4))

    def test_reissue_replays(self):
        _, manifest, _ = self.hand_off()
        self.assertEqual(transfer(self.db, '甲', '乙', [3, 2]), manifest)
        self.reopen()
        self.assertEqual(transfer(self.db, '甲', '乙', [2, 3]), manifest)

    def test_replay_even_after_settlement(self):
        parent_claim, manifest, child_claim = self.hand_off()
        confirm_claim(self.db, '乙', '清', child_claim.claim_id, [2, 3])
        confirm_claim(self.db, '甲', '清', parent_claim.claim_id, [2])
        self.reopen()
        self.assertEqual(transfer(self.db, '甲', '乙', [2, 3]), manifest)

    def test_conflicts(self):
        self.hand_off()
        with self.assertRaises(Conflict):
            transfer(self.db, '甲', '丙', [2, 3])
        with self.assertRaises(Conflict):
            transfer(self.db, '甲', '乙', [2])
        with self.assertRaises(Conflict):
            transfer(self.db, '乙', '丁', [2])
        with self.assertRaises(Conflict):
            issue(self.db, '乙', RECORDING, 1, 2)

    def test_occupied_child_number(self):
        issue(self.db, '丙', RECORDING, 1, 1)
        with self.assertRaises(Conflict):
            transfer(self.db, '甲', '丙', [2, 3])

    def test_invalid_requests_leave_nothing(self):
        with self.assertRaises(UnknownRecord):
            transfer(self.db, '无此单', '乙', [1])
        with self.assertRaises(InvalidRequest):
            transfer(self.db, '甲', '乙', [])
        with self.assertRaises(InvalidRequest):
            transfer(self.db, '甲', '乙', [0])
        with self.assertRaises(InvalidRequest):
            transfer(self.db, '甲', '乙', [5])
        self.assertIsNone(find_transfer(self.db, '乙'))
        self.assertIsNone(find_order(self.db, '乙'))
        self.assertEqual(row_numbers(read(self.db, '甲')), (1, 2, 3, 4))

    def test_cannot_transfer_settled_row(self):
        confirm(self.db, '甲', '先点', [2])
        with self.assertRaises(InvalidRequest):
            transfer(self.db, '甲', '乙', [2, 3])
        self.assertIsNone(find_transfer(self.db, '乙'))

    def test_original_receipts_keep_reissue(self):
        issue(self.db, '丁', RECORDING, 1, 2)
        receipt = confirm(self.db, '丁', '点', [1]).receipt
        self.hand_off()
        self.assertEqual(confirm(self.db, '丁', '点', [1]).receipt, receipt)
        self.assertEqual(find_receipt(self.db, '甲', '点'), None)
        self.assertEqual(list_receipts(self.db, '丁'), (receipt,))

    def test_failed_write_leaves_no_partial_manifest(self):
        from archive_common import Unavailable

        self.db.execute('''
            CREATE TEMP TRIGGER reject_transfer BEFORE INSERT ON main.order_transfers
            BEGIN SELECT RAISE(ABORT, 'transfer blocked'); END
        ''')
        with self.assertRaisesRegex(Unavailable, 'transfer blocked'):
            transfer(self.db, '甲', '乙', [2, 3])
        self.assertIsNone(find_order(self.db, '乙'))
        self.assertIsNone(find_transfer(self.db, '乙'))
        self.assertEqual(row_numbers(read(self.db, '甲')), (1, 2, 3, 4))
        self.db.execute('DROP TRIGGER reject_transfer')
        manifest = transfer(self.db, '甲', '乙', [2, 3])
        self.assertEqual(row_numbers(manifest.lines), (2, 3))


class TestTransferReading(TransferCase):
    def test_parent_and_child_reads(self):
        parent_claim, _, child_claim = self.hand_off()
        self.assertEqual(row_numbers(read(self.db, '甲')), (1, 4))
        self.assertEqual(row_numbers(child_claim.lines), (2, 3))
        self.assertEqual(row_numbers(read(self.db, '乙')), (2, 3))
        self.assertEqual([line.raw for line in child_claim.lines], [b'007\n', b'+011\n'])

    def test_child_interval_and_gap(self):
        self.hand_off(parent_claim_rows=None, rows=[2, 4])
        self.assertEqual(row_numbers(read(self.db, '乙', 2, 4)), (2, 4))
        self.assertEqual(read(self.db, '乙', 3, 3), ())
        interval = claim(self.db, '乙', 3, 3)
        self.assertEqual(interval.lines, ())
        self.assertTrue(interval.claim_id)
        with self.assertRaises(InvalidRequest):
            read(self.db, '乙', 2, 5)

    def test_parent_cannot_hand_out_transferred(self):
        self.hand_off(parent_claim_rows=None)
        self.assertEqual(row_numbers(claim(self.db, '甲').lines), (1, 4))
