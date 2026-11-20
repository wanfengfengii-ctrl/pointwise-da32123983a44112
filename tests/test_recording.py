from archive_common import Conflict, InvalidRequest, UnknownRecord
from delivery.receive import confirm, read
from delivery.receipts import find_receipt
from ingest.layout import SourceSpan, source_records
from ingest.recording import seal
from ingest.registry import find_recording
from issue.orders import issue
from tests.support import ArchiveCase, RECORDING, row_numbers
from volume.pages import list_recording_pages
from volume.placement import get_hot_payload


class TestRecording(ArchiveCase):
    def test_sealed_zero_and_missing(self):
        records = source_records('probe/zero', b'+0\r\nNA\n')
        sealed = seal(self.db, 'zero-missing', records, page_ends=(1, 2))
        self.assertEqual(sealed.row_count, 2)
        self.assertEqual(len(sealed.page_ids), 2)
        self.reopen()
        issue(self.db, '甲', 'zero-missing', 1, 2)
        zero, missing = read(self.db, '甲')
        self.assertEqual((zero.recording_id, zero.row_number, zero.value, zero.missing, zero.raw),
                         ('zero-missing', 1, 0, False, b'+0\r\n'))
        self.assertEqual((missing.recording_id, missing.row_number, missing.value, missing.missing, missing.raw),
                         ('zero-missing', 2, None, True, b'NA\n'))
        self.assertEqual(zero.source, SourceSpan('probe/zero', 0, 4))
        self.assertEqual(missing.source, SourceSpan('probe/zero', 4, 3))
        self.assertEqual(get_hot_payload(self.db, sealed.page_ids[1]), b'NA\n')

    def test_byte_identity(self):
        first = SourceSpan('probe/left', 90, 3)
        second = SourceSpan('probe/right', 112, 3)
        sealed = seal(self.db, 'duplicate', [(b'07\n', first), (b'07\n', second)])
        self.assertEqual(get_hot_payload(self.db, sealed.page_ids[0]), b'07\n07\n')
        self.reopen()
        manifest = issue(self.db, '甲', 'duplicate', 1, 2)
        self.assertEqual(tuple(line.source for line in manifest.lines), (first, second))
        rows = read(self.db, '甲')
        self.assertEqual([(row.recording_id, row.row_number, row.raw, row.value, row.source) for row in rows],
                         [('duplicate', 1, b'07\n', 7, first), ('duplicate', 2, b'07\n', 7, second)])

    def test_reinitialize(self):
        upstream = self.root.parent / 'upstream.obs'
        upstream.write_bytes(b'+5\n007\n')
        original = source_records('upstream', upstream.read_bytes())
        sealed = seal(self.db, 'repeatable', original, page_ends=(1, 2))
        order = issue(self.db, '甲', 'repeatable', 1, 2)
        receipt = confirm(self.db, '甲', '白班', [1]).receipt
        self.reopen()
        self.assertEqual(seal(self.db, 'repeatable', original), sealed)
        self.assertEqual(issue(self.db, '甲', 'repeatable', 1, 2), order)
        self.assertEqual(find_receipt(self.db, '甲', '白班'), receipt)
        upstream.write_bytes(b'+9\n007\n')
        with self.assertRaises(Conflict):
            seal(self.db, 'repeatable', source_records('upstream', upstream.read_bytes()))
        with self.assertRaises(Conflict):
            seal(self.db, 'repeatable', source_records('other-source', b'+5\n007\n'))
        self.assertEqual(row_numbers(read(self.db, '甲')), (2,))
        self.assertEqual(get_hot_payload(self.db, sealed.page_ids[0]), b'+5\n')
        self.assertEqual(read(self.db, '甲')[0].source, SourceSpan('upstream', 3, 4))
        self.assertEqual(confirm(self.db, '甲', '白班', [1]).receipt, receipt)

    def test_invalid_capture(self):
        existing = self.seed_shift()
        with self.assertRaisesRegex(InvalidRequest, 'R1'):
            seal(self.db, 'bad', source_records('broken', b'+\n'))
        self.assertIsNone(find_recording(self.db, 'bad'))
        self.assertEqual(get_hot_payload(self.db, existing.page_ids[0]), b'+5\n')
        self.assertEqual(list_recording_pages(self.db, 'bad'), ())
        with self.assertRaises(UnknownRecord):
            issue(self.db, '坏单', 'bad', 1, 1)

    def test_invalid_tail(self):
        old = self.seed_shift()
        with self.assertRaisesRegex(InvalidRequest, 'R3'):
            seal(self.db, 'bad-tail', source_records('bad-tail/raw', b'-1\n0\n3.0\n'), page_ends=(2, 3))
        self.reopen()
        self.assertIsNone(find_recording(self.db, 'bad-tail'))
        self.assertEqual(list_recording_pages(self.db, 'bad-tail'), ())
        self.assertEqual(self.db.execute(
            'SELECT count(*) FROM observations WHERE recording_id = ?', ('bad-tail',)
        ).fetchone()[0], 0)
        self.assertEqual(get_hot_payload(self.db, old.page_ids[1]), b'007\n+011\n')
        issue(self.db, '旧单', RECORDING, 1, 4)
        self.assertEqual([line.value for line in read(self.db, '旧单')], [5, 7, 11, 13])

    def test_bad_span_and_layout_are_rejected(self):
        with self.assertRaisesRegex(InvalidRequest, '跨度'):
            seal(self.db, 'bad-span', [(b'0\n', SourceSpan('probe', 0, 1))])
        with self.assertRaisesRegex(InvalidRequest, '严格递增'):
            seal(self.db, 'bad-layout', source_records('probe', b'0\n1\n'), page_ends=(1, 1, 2))
        self.assertIsNone(find_recording(self.db, 'bad-span'))
        self.assertIsNone(find_recording(self.db, 'bad-layout'))
