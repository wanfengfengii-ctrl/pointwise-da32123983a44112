from archive_common import Conflict, InvalidRequest, UnknownRecord
from delivery.claims import find_claim, list_claims
from delivery.receive import claim, confirm, confirm_claim, read
from delivery.settlement import find_claim_receipt, find_settlement
from ingest.layout import SourceSpan
from issue.orders import issue
from issue.ownership import Obligation
from issue.transfer import transfer
from reconcile.report import account
from reconcile.totals import MeasurementTotals
from tests.support import RECORDING, SOURCE, TransferCase, row_numbers


def settled_view(receipt):
    return [(row.row_number, row.newly_settled, row.settled_order_id, row.settled_request_id)
            for row in receipt.rows]


class TestClaimBasics(TransferCase):
    def test_claim_returns_origc_and_credential(self):
        self.hand_off(parent_claim_rows=None)
        pulled = claim(self.db, '乙')
        self.assertIsInstance(pulled.claim_id, str)
        self.assertTrue(pulled.claim_id)
        self.assertEqual(
            [(line.row_number, line.raw, line.value, line.source) for line in pulled.lines],
            [(2, b'007\n', 7, SourceSpan(SOURCE, 3, 4)), (3, b'+011\n', 11, SourceSpan(SOURCE, 7, 5))],
        )
        # The credential is persisted and reopens with the same identifier.
        self.reopen()
        self.assertEqual(find_claim(self.db, '乙', pulled.claim_id).rows, (2, 3))

    def test_each_claim_is_distinct(self):
        self.hand_off(parent_claim_rows=None, take_child_claim=False)
        one = claim(self.db, '乙')
        two = claim(self.db, '乙')
        self.assertNotEqual(one.claim_id, two.claim_id)
        self.assertEqual(len(list_claims(self.db, '乙')), 2)

    def test_empty_segment_credential(self):
        self.hand_off(parent_claim_rows=None, rows=[2, 4], take_child_claim=False)
        empty = claim(self.db, '乙', 3, 3)
        self.assertEqual(empty.lines, ())
        receipt = confirm_claim(self.db, '乙', '空', empty.claim_id, [])
        self.assertEqual(receipt.rows, ())
        self.assertEqual(confirm_claim(self.db, '乙', '空', empty.claim_id, []), receipt)

    def test_read_carries_no_credential(self):
        self.hand_off(parent_claim_rows=None, take_child_claim=False)
        self.assertTrue(read(self.db, '乙'))
        self.assertEqual(list_claims(self.db, '乙'), ())


class TestWorkedExample(TransferCase):
    def test_day_settles_first(self):
        parent_claim, _, child_claim = self.hand_off()
        first = confirm_claim(self.db, '甲', '白班补', parent_claim.claim_id, [2])
        self.assertEqual(settled_view(first), [(2, True, '甲', '白班补')])
        self.assertEqual(row_numbers(read(self.db, '乙')), (3,))
        late = confirm_claim(self.db, '乙', '夜班迟', child_claim.claim_id, [2])
        self.assertEqual(settled_view(late), [(2, False, '甲', '白班补')])

        a, b = account(self.db, '甲'), account(self.db, '乙')
        self.assertEqual(a.total, MeasurementTotals(4, 4, 0, 36))
        self.assertEqual(a.confirmed_total.microstrain, 7)
        self.assertEqual(a.pending_total.microstrain, 29)
        self.assertEqual(b.total, MeasurementTotals(2, 2, 0, 18))
        self.assertEqual(b.confirmed_total.microstrain, 7)
        self.assertEqual(b.pending_total.microstrain, 11)
        self.assertEqual([(h.row_number, h.pending_order_id, h.settled_order_id, h.settled_request_id)
                          for h in b.handoffs],
                         [(2, None, '甲', '白班补'), (3, '乙', None, None)])
        self.assertEqual(a.handoffs, b.handoffs)

    def test_night_settles_first_swaps_owner(self):
        parent_claim, _, child_claim = self.hand_off()
        first = confirm_claim(self.db, '乙', '夜班先', child_claim.claim_id, [2])
        self.assertEqual(settled_view(first), [(2, True, '乙', '夜班先')])
        late = confirm_claim(self.db, '甲', '白班迟', parent_claim.claim_id, [2])
        self.assertEqual(settled_view(late), [(2, False, '乙', '夜班先')])
        a, b = account(self.db, '甲'), account(self.db, '乙')
        self.assertEqual((a.confirmed_total.microstrain, a.pending_total.microstrain), (7, 29))
        self.assertEqual((b.confirmed_total.microstrain, b.pending_total.microstrain), (7, 11))
        self.assertEqual([(h.row_number, h.pending_order_id, h.settled_order_id, h.settled_request_id)
                          for h in b.handoffs],
                         [(2, None, '乙', '夜班先'), (3, '乙', None, None)])

    def test_clean_preconfirm_then_spill(self):
        confirm(self.db, '甲', '先点R1', [1])
        parent_claim = claim(self.db, '甲', 1, 3)
        self.assertEqual(row_numbers(parent_claim.lines), (2, 3))
        transfer(self.db, '甲', '乙', [2, 3])
        child_claim = claim(self.db, '乙')
        confirm_claim(self.db, '乙', '夜班R2', child_claim.claim_id, [2])
        late = confirm_claim(self.db, '甲', '白班迟到', parent_claim.claim_id, [2, 3])
        self.assertEqual(settled_view(late), [(2, False, '乙', '夜班R2'), (3, True, '甲', '白班迟到')])

    def test_clean_preconfirm_rejects_spill_of_pre_settled(self):
        confirm(self.db, '甲', '先点R1', [1])
        parent_claim = claim(self.db, '甲', 1, 3)
        transfer(self.db, '甲', '乙', [2, 3])
        with self.assertRaises(InvalidRequest):
            confirm_claim(self.db, '甲', '夹带R1', parent_claim.claim_id, [1, 2])
        self.assertIsNone(find_claim_receipt(self.db, '甲', '夹带R1'))


class TestEligibility(TransferCase):
    def test_credential_must_cover_rows(self):
        parent_claim, _, _ = self.hand_off()
        with self.assertRaises(InvalidRequest):
            # The pre-transfer claim covered only R2, not R3.
            confirm_claim(self.db, '甲', '多报', parent_claim.claim_id, [2, 3])

    def test_post_transfer_parent_credential_cannot_settle_handoff(self):
        self.hand_off(parent_claim_rows=None)
        after = claim(self.db, '甲')
        self.assertEqual(row_numbers(after.lines), (1, 4))
        with self.assertRaises(InvalidRequest):
            confirm_claim(self.db, '甲', '越权', after.claim_id, [2])
        self.assertIsNone(find_claim_receipt(self.db, '甲', '越权'))

    def test_night_only_settles_its_subset(self):
        parent_claim, _, child_claim = self.hand_off()
        with self.assertRaises(InvalidRequest):
            confirm_claim(self.db, '乙', '越界', child_claim.claim_id, [1])
        with self.assertRaises(InvalidRequest):
            confirm_claim(self.db, '乙', '甲凭据', parent_claim.claim_id, [2])

    def test_wrong_order_and_archive_credentials(self):
        parent_claim, _, _ = self.hand_off()
        issue(self.db, '丁', RECORDING, 1, 1)
        with self.assertRaises(InvalidRequest):
            confirm_claim(self.db, '丁', '错单', parent_claim.claim_id, [2])
        with self.assertRaises(InvalidRequest):
            confirm_claim(self.db, '甲', '错凭据', 'no-such-claim', [2])

    def test_unknown_order(self):
        with self.assertRaises(UnknownRecord):
            claim(self.db, '无此单')
        with self.assertRaises(UnknownRecord):
            confirm_claim(self.db, '无此单', 'x', 'y', [1])

    def test_read_output_is_not_a_credential(self):
        self.hand_off(parent_claim_rows=None, take_child_claim=False)
        read(self.db, '乙')
        # No credential was minted, so any invented identifier must be rejected.
        with self.assertRaises(InvalidRequest):
            confirm_claim(self.db, '乙', '伪造', 'not-a-real-claim', [2])

    def test_plain_order_has_no_handoffs(self):
        issue(self.db, '丙', RECORDING, 1, 2)
        self.assertEqual(account(self.db, '丙').handoffs, ())

    def test_claim_does_not_change_settlement(self):
        parent_claim, _, child_claim = self.hand_off()
        self.assertIsNone(find_settlement(self.db, Obligation('甲', 2)))
        self.assertEqual(account(self.db, '乙').confirmed_total.microstrain, 0)


class TestReplayAndConflicts(TransferCase):
    def test_credential_replay_is_fixed_receipt(self):
        _, _, child_claim = self.hand_off()
        first = confirm_claim(self.db, '乙', '清', child_claim.claim_id, [3, 2, 2])
        self.assertEqual(settled_view(first), [(2, True, '乙', '清'), (3, True, '乙', '清')])
        self.reopen()
        self.assertEqual(confirm_claim(self.db, '乙', '清', child_claim.claim_id, [2, 3]), first)

    def test_replay_keeps_newly_settled_false_when_late(self):
        parent_claim, _, child_claim = self.hand_off()
        confirm_claim(self.db, '甲', '先', parent_claim.claim_id, [2])
        night = confirm_claim(self.db, '乙', '迟', child_claim.claim_id, [2])
        self.assertFalse(night.rows[0].newly_settled)
        self.assertEqual(confirm_claim(self.db, '乙', '迟', child_claim.claim_id, [2]), night)

    def test_change_credential_or_set_conflicts(self):
        _, _, child_claim = self.hand_off()
        other = claim(self.db, '乙')
        confirm_claim(self.db, '乙', '清', child_claim.claim_id, [2])
        with self.assertRaises(Conflict):
            confirm_claim(self.db, '乙', '清', child_claim.claim_id, [3])
        with self.assertRaises(Conflict):
            confirm_claim(self.db, '乙', '清', other.claim_id, [2])

    def test_cross_entry_request_id_conflicts(self):
        parent_claim, _, child_claim = self.hand_off()
        confirm(self.db, '甲', '共用', [1])
        with self.assertRaises(Conflict):
            confirm_claim(self.db, '甲', '共用', parent_claim.claim_id, [2])
        confirm_claim(self.db, '乙', '夜号', child_claim.claim_id, [2])
        with self.assertRaises(Conflict):
            confirm(self.db, '乙', '夜号', [])

    def test_different_request_same_rows_keeps_first(self):
        _, _, child_claim = self.hand_off()
        first = confirm_claim(self.db, '乙', '一', child_claim.claim_id, [2])
        second = confirm_claim(self.db, '乙', '二', child_claim.claim_id, [2])
        self.assertEqual(settled_view(first), [(2, True, '乙', '一')])
        self.assertEqual(settled_view(second), [(2, False, '乙', '一')])
