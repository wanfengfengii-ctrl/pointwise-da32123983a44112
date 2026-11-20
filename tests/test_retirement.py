from archive_common import Unavailable
from delivery.receive import confirm, read
from delivery.receipts import find_receipt, list_receipts
from ingest.layout import SourceSpan
from issue.orders import issue
from reconcile.report import account
from tests.support import ArchiveCase, RECORDING, SOURCE, row_numbers
from volume.obligations import PageObligation, find_page_obligations
from volume.pages import get_page
from volume.placement import find_retirement, get_hot_payload, get_replacement_payload
from volume.retire import retire


class TestRetirement(ArchiveCase):
    def assert_crossed_originals(self):
        a = [(line.recording_id, line.row_number, line.raw, line.value, line.source) for line in read(self.db, '甲')]
        b = [(line.recording_id, line.row_number, line.raw, line.value, line.source) for line in read(self.db, '乙')]
        self.assertEqual(a, [(RECORDING, 1, b'+5\n', 5, SourceSpan(SOURCE, 0, 3)),
                             (RECORDING, 3, b'+011\n', 11, SourceSpan(SOURCE, 7, 5))])
        self.assertEqual(b, [(RECORDING, 2, b'007\n', 7, SourceSpan(SOURCE, 3, 4)),
                             (RECORDING, 4, b'13\n', 13, SourceSpan(SOURCE, 12, 3))])

    def test_two_orders_after_retirement(self):
        page_id, receipt_a, receipt_b = self.cross_confirm()
        obligations = find_page_obligations(self.db, get_page(self.db, page_id))
        self.assertEqual({item.order_id: item for item in obligations},
                         {'甲': PageObligation('甲', (3,)), '乙': PageObligation('乙', (2,))})
        retirement = retire(self.db, page_id)
        self.assertEqual((retirement.preserved_bytes, retirement.pending_orders, retirement.pending_rows), (9, 2, 2))
        self.assertIsNone(get_hot_payload(self.db, page_id))
        self.reopen()
        self.assertIsNone(get_hot_payload(self.db, page_id))
        self.assertEqual(get_replacement_payload(self.db, page_id), b'007\n+011\n')
        self.assertEqual(retire(self.db, page_id), retirement)
        self.assert_crossed_originals()
        self.assertEqual(find_receipt(self.db, '甲', '白班'), receipt_a)
        self.assertEqual(find_receipt(self.db, '乙', '白班'), receipt_b)
        confirm(self.db, '甲', '夜班', row_numbers(read(self.db, '甲')))
        confirm(self.db, '乙', '夜班', row_numbers(read(self.db, '乙')))
        for order_id, total in (('甲', 23), ('乙', 31)):
            self.assertEqual(read(self.db, order_id), ())
            report = account(self.db, order_id)
            self.assertEqual((report.confirmed_total.row_count, report.confirmed_total.microstrain), (3, total))
            self.assertEqual(report.pending, ())

    def test_no_pending(self):
        page_id, _, _ = self.cross_confirm()
        a = confirm(self.db, '甲', '补齐', [1, 3]).receipt
        b = confirm(self.db, '乙', '补齐', [2, 4]).receipt
        receipt = retire(self.db, page_id)
        self.assertEqual((receipt.pending_orders, receipt.pending_rows), (0, 0))
        self.reopen()
        self.assertIsNone(get_hot_payload(self.db, page_id))
        self.assertEqual(read(self.db, '甲'), ())
        self.assertEqual(read(self.db, '乙'), ())
        self.assertEqual(find_receipt(self.db, '甲', '补齐'), a)
        self.assertEqual(find_receipt(self.db, '乙', '补齐'), b)
        issue(self.db, '丙', RECORDING, 2, 3)
        self.assertEqual([line.raw for line in read(self.db, '丙')], [b'007\n', b'+011\n'])

    def test_replacement_write_failure(self):
        page_id, a, b = self.cross_confirm()
        self.db.execute('''
            CREATE TEMP TRIGGER reject_replacement BEFORE INSERT ON main.replacement_payloads
            BEGIN SELECT RAISE(ABORT, 'replacement write blocked'); END
        ''')
        with self.assertRaisesRegex(Unavailable, 'replacement write blocked'):
            retire(self.db, page_id)
        self.reopen()
        self.assertEqual(get_hot_payload(self.db, page_id), b'007\n+011\n')
        self.assertIsNone(get_replacement_payload(self.db, page_id))
        self.assertIsNone(find_retirement(self.db, page_id))
        self.assert_crossed_originals()
        self.assertEqual(list_receipts(self.db, '甲'), (a,))
        self.assertEqual(list_receipts(self.db, '乙'), (b,))

    def test_retry_after_paused_attempt(self):
        page_id, _, _ = self.cross_confirm()
        peer = self.open_peer()
        visible = []

        def observe():
            visible.append((
                get_hot_payload(peer, page_id), get_replacement_payload(peer, page_id), find_retirement(peer, page_id),
                row_numbers(read(peer, '甲')), row_numbers(read(peer, '乙')),
            ))
            return 0

        self.db.create_function('observe_retirement', 0, observe)
        self.db.execute('''
            CREATE TEMP TRIGGER pause_then_fail AFTER INSERT ON main.replacement_payloads
            BEGIN
                SELECT observe_retirement();
                SELECT RAISE(ABORT, 'controlled retirement interruption');
            END
        ''')
        with self.assertRaisesRegex(Unavailable, 'controlled retirement interruption'):
            retire(self.db, page_id)
        self.assertEqual(visible, [(b'007\n+011\n', None, None, (1, 3), (2, 4))])
        self.assertIsNone(find_retirement(peer, page_id))
        self.assertIsNone(get_replacement_payload(peer, page_id))
        self.assert_crossed_originals()
        self.db.execute('DROP TRIGGER pause_then_fail')
        receipt = retire(self.db, page_id)
        self.assertIsNone(get_hot_payload(peer, page_id))
        self.reopen()
        self.assertIsNone(get_hot_payload(self.db, page_id))
        self.assertEqual(find_retirement(self.db, page_id), receipt)
        self.assert_crossed_originals()

    def test_replacement_readback_failure(self):
        page_id, _, _ = self.cross_confirm()
        self.db.execute('''
            CREATE TEMP TRIGGER alter_copy AFTER INSERT ON main.replacement_payloads
            BEGIN
                UPDATE replacement_payloads SET payload = zeroblob(length(NEW.payload))
                WHERE page_id = NEW.page_id;
            END
        ''')
        with self.assertRaisesRegex(Unavailable, '替代载荷与热区原字节不一致'):
            retire(self.db, page_id)
        self.assertEqual(get_hot_payload(self.db, page_id), b'007\n+011\n')
        self.assertIsNone(get_replacement_payload(self.db, page_id))
        self.assertIsNone(find_retirement(self.db, page_id))
        self.assert_crossed_originals()

    def test_unreadable_payload(self):
        page_id, receipt_a, receipt_b = self.cross_confirm()
        retirement = retire(self.db, page_id)
        self.db.execute('DELETE FROM replacement_payloads WHERE page_id = ?', (page_id,))
        self.reopen()
        with self.assertRaisesRegex(Unavailable, '替代载荷不可读'):
            read(self.db, '甲', 3, 3)
        with self.assertRaisesRegex(Unavailable, '替代载荷不可读'):
            read(self.db, '乙', 2, 2)
        self.assertEqual(confirm(self.db, '甲', '白班', [2]).receipt, receipt_a)
        self.assertEqual(confirm(self.db, '乙', '白班', [3]).receipt, receipt_b)
        self.assertEqual(find_retirement(self.db, page_id), retirement)

    def test_no_partial(self):
        page_id, receipt_a, _ = self.cross_confirm()
        retire(self.db, page_id)
        self.db.execute('UPDATE replacement_payloads SET payload = ? WHERE page_id = ?', (b'007\n', page_id))
        self.assertEqual([line.raw for line in read(self.db, '甲', 1, 1)], [b'+5\n'])
        with self.assertRaisesRegex(Unavailable, '载荷长度不完整'):
            read(self.db, '甲')
        self.assertEqual(list_receipts(self.db, '甲'), (receipt_a,))
        self.assertEqual(row_numbers(account(self.db, '甲').pending), (1, 3))
