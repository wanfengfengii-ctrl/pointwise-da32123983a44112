import sqlite3

from archive_common import Conflict, InvalidRequest, require_name, transaction
from issue.manifest import OrderManifest, freeze_manifest
from issue.register import find_order, store_order
from issue.selection import select_closed_range


def issue(
    db: sqlite3.Connection, order_id: str, recording_id: str, first: int, last: int
) -> OrderManifest:
    require_name(order_id, '交接单号')
    require_name(recording_id, '录制号')
    if type(first) is not int or type(last) is not int or not 1 <= first <= last:
        raise InvalidRequest('出票闭区间须提供依序排列的正整数行位')
    with transaction(db, write=True):
        existing = find_order(db, order_id)
        if existing is not None:
            if (existing.recording_id, existing.first, existing.last) != (recording_id, first, last):
                raise Conflict(f'交接单 {order_id} 已以不同选行出票')
            return existing
        observations = select_closed_range(db, recording_id, first, last)
        manifest = freeze_manifest(order_id, recording_id, first, last, observations)
        store_order(db, manifest)
        return manifest
