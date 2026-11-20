from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from delivery.receive import claim, confirm
from ingest.layout import source_records
from ingest.recording import seal
from ingest.workspace import open_archive
from issue.orders import issue
from issue.transfer import transfer

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


class TransferCase(ArchiveCase):
    """Shared hand-off fixture mirroring the TRANSFER.md worked example."""

    def setUp(self):
        super().setUp()
        self.sealed = self.seed_shift()
        issue(self.db, '甲', RECORDING, 1, 4)

    def hand_off(self, parent_claim_rows=(2, 2), rows=(2, 3), *, take_child_claim=True):
        parent_claim = claim(self.db, '甲', *parent_claim_rows) if parent_claim_rows else None
        manifest = transfer(self.db, '甲', '乙', list(rows))
        child_claim = claim(self.db, '乙') if take_child_claim else None
        return parent_claim, manifest, child_claim
