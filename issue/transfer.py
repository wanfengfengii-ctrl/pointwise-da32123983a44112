from collections.abc import Iterable
import sqlite3

from archive_common import Conflict, InvalidRequest, UnknownRecord, require_name, transaction
from delivery.claims import latest_sequence
from delivery.settlement import received_rows
from issue.manifest import OrderManifest
from issue.register import (
    find_order,
    find_transfer_by_parent,
    is_child_order,
    store_order,
    store_transfer,
)


def _normalize_rows(rows: Iterable[int]) -> tuple[int, ...]:
    try:
        supplied = tuple(rows)
    except TypeError as error:
        raise InvalidRequest('转派须提供原行位集合') from error
    if not supplied:
        raise InvalidRequest('转派须包含至少一条原行')
    if any(type(number) is not int or number <= 0 for number in supplied):
        raise InvalidRequest('转派行位须为有效的正整数')
    return tuple(sorted(set(supplied)))


def _child_manifest(parent: OrderManifest, child_id: str, rows: tuple[int, ...]) -> OrderManifest:
    selected = tuple(line for line in parent.lines if line.row_number in set(rows))
    return OrderManifest(child_id, parent.recording_id, selected[0].row_number,
                         selected[-1].row_number, selected)


def transfer(
    db: sqlite3.Connection, parent_id: str, child_id: str, rows: Iterable[int]
) -> OrderManifest:
    require_name(parent_id, '原单号')
    require_name(child_id, '接办单号')
    normalized = _normalize_rows(rows)
    with transaction(db, write=True):
        parent = find_order(db, parent_id)
        if parent is None:
            raise UnknownRecord(f'未知交接单 {parent_id}')

        existing = find_transfer_by_parent(db, parent_id)
        if existing is not None:
            if existing.child_id == child_id and existing.rows == normalized:
                # Replaying the same canonical hand-off returns the fixed
                # manifest even after the rows were later settled.
                child = find_order(db, child_id)
                if child is None:
                    raise UnknownRecord(f'未知交接单 {child_id}')
                return child
            raise Conflict(f'交接单 {parent_id} 已以不同接办单或原行集合转派')

        if is_child_order(db, parent_id):
            raise Conflict(f'接办单 {parent_id} 不能继续转派')
        if find_order(db, child_id) is not None:
            raise Conflict(f'单号 {child_id} 已被占用')

        members = {line.row_number for line in parent.lines}
        if not members.issuperset(normalized):
            raise InvalidRequest(f'转派行位超出交接单 {parent_id} 的冻结范围')
        settled = received_rows(db, parent)
        if any(number in settled for number in normalized):
            raise InvalidRequest(f'交接单 {parent_id} 的转派行已结清')

        child = _child_manifest(parent, child_id, normalized)
        floor = latest_sequence(db, parent_id)
        store_order(db, child)
        store_transfer(db, parent_id, child_id, floor)
        return child
