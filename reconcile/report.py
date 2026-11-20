from dataclasses import dataclass
import sqlite3

from archive_common import require_name, transaction
from delivery.receipts import Receipt
from delivery.settlement import Handoff, handoffs
from issue.manifest import OrderManifest
from reconcile.lines import AccountLine, build_account_lines
from reconcile.totals import MeasurementTotals, summarize_measurements
from reconcile.verify import load_account_evidence


@dataclass(frozen=True, slots=True)
class AccountReport:
    order: OrderManifest
    lines: tuple[AccountLine, ...]
    confirmed: tuple[AccountLine, ...]
    pending: tuple[AccountLine, ...]
    total: MeasurementTotals
    confirmed_total: MeasurementTotals
    pending_total: MeasurementTotals
    receipts: tuple[Receipt, ...]
    handoffs: tuple[Handoff, ...]


def account(db: sqlite3.Connection, order_id: str) -> AccountReport:
    require_name(order_id, '交接单号')
    with transaction(db):
        evidence = load_account_evidence(db, order_id)
        lines = build_account_lines(evidence.order, evidence.observations, evidence.received)
        confirmed = tuple(line for line in lines if line.received)
        pending = tuple(line for line in lines if not line.received)
        return AccountReport(
            evidence.order, lines, confirmed, pending,
            summarize_measurements(lines), summarize_measurements(confirmed),
            summarize_measurements(pending), evidence.receipts,
            handoffs(db, order_id),
        )
