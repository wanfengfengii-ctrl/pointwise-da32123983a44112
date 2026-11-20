from collections.abc import Set

from archive_common import InvalidRequest
from issue.manifest import ManifestLine, OrderManifest


def pending_lines(
    order: OrderManifest,
    received: Set[int],
    first: int | None = None,
    last: int | None = None,
    *,
    eligible: Set[int] | None = None,
) -> tuple[ManifestLine, ...]:
    if first is None and last is None:
        first, last = order.first, order.last
    if type(first) is not int or type(last) is not int or not order.first <= first <= last <= order.last:
        raise InvalidRequest(f'领取闭区间须落在交接单 {order.order_id} 的冻结范围内')
    return tuple(
        line for line in order.lines
        if first <= line.row_number <= last
        and line.row_number not in received
        and (eligible is None or line.row_number in eligible)
    )
