from archive_common import Unavailable
from delivery.receive import claim, confirm, confirm_claim
from delivery.receipts import find_receipt, list_receipts
from delivery.settlement import find_claim_receipt
from ingest.layout import SourceSpan
from issue.transfer import transfer
from reconcile.report import account
from tests.support import SOURCE, TransferCase, row_numbers
from volume.obligations import PageObligation, find_page_obligations
from volume.pages import get_page
from volume.placement import get_hot_payload, get_replacement_payload
from volume.retire import retire


class TestHandoffRetirement(TransferCase):
    def test_two_sided_accounting_after_reopen(self):
        parent_claim, _, child_claim = self.hand_off()
        confirm_claim(self.db, '甲', '白班补', parent_claim.claim_id, [2])
        retirement = retire(self.db, self.sealed.page_ids[1])
        # First retirement counts the still-owed hand-off rows against the
        # current responsible order (the night shift's hand-off order only).
        self.assertEqual((retirement.pending_orders, retirement.pending_rows), (1, 1))
        self.reopen()
        self.assertIsNone(get_hot_payload(self.db, self.sealed.page_ids[1]))
        self.assertEqual(get_replacement_payload(self.db, self.sealed.page_ids[1]), b'007\n+011\n')
        self.assertEqual(retire(self.db, self.sealed.page_ids[1]), retirement)

        late = claim(self.db, '乙')
        self.assertEqual(row_numbers(late.lines), (3,))
        line = late.lines[0]
        self.assertEqual((line.raw, line.source), (b'+011\n', SourceSpan(SOURCE, 7, 5)))
        settled = confirm_claim(self.db, '乙', '夜班补', late.claim_id, [3])
        self.assertTrue(settled.rows[0].newly_settled)
        self.assertEqual(account(self.db, '甲').confirmed_total.microstrain, 18)
        self.assertEqual(account(self.db, '乙').confirmed_total.microstrain, 18)
        # The signed retirement receipt does not change after later receipts.
        self.assertEqual(retire(self.db, self.sealed.page_ids[1]), retirement)
        # The original request replays its fixed receipt after reopen.
        self.assertEqual(
            confirm_claim(self.db, '乙', '夜班补', late.claim_id, [3]), settled)

    def test_claimed_before_transfer_survives_retirement(self):
        parent_claim = claim(self.db, '甲', 1, 4)
        transfer(self.db, '甲', '乙', [2, 3])
        retirement = retire(self.db, self.sealed.page_ids[1])
        self.reopen()
        # The pre-transfer credential still settles the handed-off row.
        late = confirm_claim(self.db, '甲', '白班迟到', parent_claim.claim_id, [2])
        self.assertTrue(late.rows[0].newly_settled)
        self.assertEqual(late.rows[0].settled_order_id, '甲')
        self.assertEqual(retire(self.db, self.sealed.page_ids[1]), retirement)

    def test_retirement_obligations_belong_to_receiver(self):
        self.hand_off(parent_claim_rows=None, take_child_claim=False)
        obligations = find_page_obligations(self.db, get_page(self.db, self.sealed.page_ids[1]))
        self.assertEqual({item.order_id: item for item in obligations},
                         {'乙': PageObligation('乙', (2, 3))})
        # The day shift's own page positions R1/R4 are unaffected.
        self.assertEqual(find_page_obligations(self.db, get_page(self.db, self.sealed.page_ids[0])),
                         (PageObligation('甲', (1,)),))

    def test_settled_handoff_removes_retirement_obligation(self):
        _, _, child_claim = self.hand_off()
        confirm_claim(self.db, '乙', '清', child_claim.claim_id, [2, 3])
        receipt = retire(self.db, self.sealed.page_ids[1])
        self.assertEqual((receipt.pending_orders, receipt.pending_rows), (0, 0))

    def test_write_failure_leaves_no_claim_or_receipt(self):
        parent_claim, _, _ = self.hand_off()
        self.db.execute('''
            CREATE TEMP TRIGGER reject_settlements BEFORE INSERT ON main.settlements
            BEGIN SELECT RAISE(ABORT, 'settlement blocked'); END
        ''')
        with self.assertRaisesRegex(Unavailable, 'settlement blocked'):
            confirm_claim(self.db, '甲', '待重试', parent_claim.claim_id, [2])
        self.assertIsNone(find_claim_receipt(self.db, '甲', '待重试'))
        self.db.execute('DROP TRIGGER reject_settlements')
        retried = confirm_claim(self.db, '甲', '待重试', parent_claim.claim_id, [2])
        self.assertTrue(retried.rows[0].newly_settled)
        self.assertEqual(find_claim_receipt(self.db, '甲', '待重试'), retried)

    def test_claim_write_failure_leaves_no_credential(self):
        self.hand_off(parent_claim_rows=None, take_child_claim=False)
        self.db.execute('''
            CREATE TEMP TRIGGER reject_claims BEFORE INSERT ON main.claim_headers
            BEGIN SELECT RAISE(ABORT, 'claim blocked'); END
        ''')
        with self.assertRaisesRegex(Unavailable, 'claim blocked'):
            claim(self.db, '乙')
        self.db.execute('DROP TRIGGER reject_claims')
        retried = claim(self.db, '乙')
        self.assertEqual(row_numbers(retried.lines), (2, 3))
        self.assertTrue(retried.claim_id)

    def test_legacy_receipts_survive_transfer(self):
        receipt = confirm(self.db, '甲', '先点R1', [1]).receipt
        self.hand_off(parent_claim_rows=None)
        self.reopen()
        self.assertEqual(find_receipt(self.db, '甲', '先点R1'), receipt)
        self.assertEqual(list_receipts(self.db, '甲'), (receipt,))
        self.assertEqual(row_numbers(account(self.db, '甲').confirmed), (1,))
