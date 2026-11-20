from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from delivery.receive import confirm
from ingest.layout import source_records
from ingest.recording import seal
from ingest.workspace import open_archive
from issue.orders import issue


SAMPLES = Path(__file__).resolve().parent.parent / 'samples'
RECORDING = 'probe-01'
SOURCE = 'probe/A.raw'


def row_numbers(lines):
    return tuple(line.row_number for line in lines)


class ArchiveCase(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix='tingqiao-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'archive'
        self.db = open_archive(self.root)
        self.addCleanup(lambda: self.db.close())

    def reopen(self):
        self.db.close()
        self.db = open_archive(self.root)

    def open_peer(self):
        peer = open_archive(self.root)
        self.addCleanup(peer.close)
        return peer

    def seed_shift(self):
        records = source_records(SOURCE, (SAMPLES / 'shift.obs').read_bytes())
        return seal(self.db, RECORDING, records, page_ends=(1, 3, 4))

    def cross_confirm(self):
        sealed = self.seed_shift()
        issue(self.db, '甲', RECORDING, 1, 3)
        issue(self.db, '乙', RECORDING, 2, 4)
        receipt_a = confirm(self.db, '甲', '白班', [2]).receipt
        receipt_b = confirm(self.db, '乙', '白班', [3]).receipt
        return sealed.page_ids[1], receipt_a, receipt_b
