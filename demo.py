from contextlib import closing
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from delivery.receive import claim, confirm, confirm_claim, read
from ingest.layout import source_records
from ingest.recording import seal
from ingest.workspace import open_archive
from issue.orders import issue
from issue.transfer import transfer
from reconcile.report import account
from volume.placement import get_hot_payload
from volume.retire import retire


def _originals(lines):
    return [
        [line.row_number, line.raw.decode('ascii'), line.value, line.source.source,
         line.source.offset, line.source.length]
        for line in lines
    ]


def _handoff_view(report):
    return [
        [item.row_number, item.parent_id, item.child_id, item.pending_order_id,
         item.settled_order_id, item.settled_request_id]
        for item in report.handoffs
    ]


def main():
    sample = (Path(__file__).resolve().parent / 'samples' / 'shift.obs').read_bytes()
    source = 'probe/A.raw'
    records = source_records(source, sample)
    bounds = {'甲': [1, 3], '乙': [2, 4]}
    first_received = {'甲': [2], '乙': [3]}
    expected = {
        'hot_readable': False,
        'pending_at_retirement': [2, 2],
        'owed': {
            '甲': [[1, '+5\n', 5, source, 0, 3], [3, '+011\n', 11, source, 7, 5]],
            '乙': [[2, '007\n', 7, source, 3, 4], [4, '13\n', 13, source, 12, 3]],
        },
    }
    downstream_expected = {
        '甲': {'confirmed': [1, 2, 3], 'pending': [], 'row_count': 3, 'microstrain': 23},
        '乙': {'confirmed': [2, 3, 4], 'pending': [], 'row_count': 3, 'microstrain': 31},
    }
    with TemporaryDirectory(prefix='tingqiao-demo-') as directory:
        with closing(open_archive(directory)) as db:
            sealed = seal(db, 'probe-01', records, page_ends=(1, 3, 4))
            for order_id, (first, last) in bounds.items():
                issue(db, order_id, sealed.recording_id, first, last)
                (row_number,) = first_received[order_id]
                segment = read(db, order_id, row_number, row_number)
                confirm(db, order_id, '白班', [line.row_number for line in segment])
            retirement = retire(db, sealed.page_ids[1])

        with closing(open_archive(directory)) as db:
            originals = {order_id: read(db, order_id) for order_id in bounds}
            actual = {
                'hot_readable': get_hot_payload(db, retirement.page_id) is not None,
                'pending_at_retirement': [retirement.pending_orders, retirement.pending_rows],
                'owed': {order_id: _originals(lines) for order_id, lines in originals.items()},
            }
            downstream_actual = {}
            for order_id, lines in originals.items():
                confirm(db, order_id, '夜班', [line.row_number for line in lines])
                report = account(db, order_id)
                downstream_actual[order_id] = {
                    'confirmed': [line.row_number for line in report.confirmed],
                    'pending': [line.row_number for line in report.pending],
                    'row_count': report.total.row_count,
                    'microstrain': report.total.microstrain,
                }

    handoff = _handoff_demo()

    if actual != expected or downstream_actual != downstream_expected:
        raise AssertionError(f'交班对照不一致：{actual!r} / {downstream_actual!r}')
    scenario = {
        'case_id': 'v6',
        'input': {'raw': sample.decode('ascii'), 'page_ends': [1, 3, 4], 'orders': bounds,
                  'first_received': first_received, 'retired_rows': [2, 3]},
        'expected': expected,
        'actual': actual,
        'downstream_expected': downstream_expected,
        'downstream_actual': downstream_actual,
        'handoff': handoff,
        'code_paths': ['ingest/recording.py', 'issue/orders.py', 'issue/transfer.py',
                       'delivery/receive.py', 'delivery/settlement.py', 'volume/retire.py',
                       'volume/obligations.py', 'volume/placement.py', 'reconcile/report.py'],
        'oracle': '闭区间分别减去两单首收集合，按源字节手数偏移及长度。甲 5+7+11，乙 7+11+13。'
                  '转派后按原单仍全单、接办单仅子集核账，同一原行两侧共用一次首收。',
    }
    print('POINTWISE_SCENARIO ' + json.dumps(scenario, ensure_ascii=False, separators=(',', ':')))


def _handoff_demo():
    """One hand-off, retirement reopen re-claim, and fixed-receipt replay."""
    sample = (Path(__file__).resolve().parent / 'samples' / 'shift.obs').read_bytes()
    source = 'probe/A.raw'
    result = {}
    with TemporaryDirectory(prefix='tingqiao-demo-handoff-') as directory:
        with closing(open_archive(directory)) as db:
            sealed = seal(db, 'probe-01', source_records(source, sample), page_ends=(1, 3, 4))
            issue(db, '甲', 'probe-01', 1, 4)
            day_claim = claim(db, '甲', 2, 2)
            child = transfer(db, '甲', '乙', [2, 3])
            night_claim = claim(db, '乙')
            # Day shift settles the handed-off R2 with its pre-transfer credential.
            first = confirm_claim(db, '甲', '白班补', day_claim.claim_id, [2])
            # Night shift late report sees the existing settlement, not a new one.
            late = confirm_claim(db, '乙', '夜班迟', night_claim.claim_id, [2])
            day = account(db, '甲')
            night = account(db, '乙')
            retirement = retire(db, sealed.page_ids[1])
            result['before_retirement'] = {
                'child_manifest': {'first': child.first, 'last': child.last,
                                   'rows': [line.row_number for line in child.lines]},
                'day_account': [day.total.microstrain, day.confirmed_total.microstrain,
                                day.pending_total.microstrain],
                'night_account': [night.total.microstrain, night.confirmed_total.microstrain,
                                  night.pending_total.microstrain],
                'day_settlement': [first.rows[0].row_number, first.rows[0].newly_settled,
                                   first.rows[0].settled_order_id, first.rows[0].settled_request_id],
                'late_night': [late.rows[0].row_number, late.rows[0].newly_settled,
                               late.rows[0].settled_order_id, late.rows[0].settled_request_id],
                'retirement_pending': [retirement.pending_orders, retirement.pending_rows],
                'night_handoffs': _handoff_view(night),
            }

        with closing(open_archive(directory)) as db:
            # Reopen: the night shift re-claims the still-owed R3 from the saved page.
            reopened_claim = claim(db, '乙')
            (row,) = reopened_claim.lines
            tail = confirm_claim(db, '乙', '夜班补', reopened_claim.claim_id, [3])
            day = account(db, '甲')
            night = account(db, '乙')
            result['after_reopen'] = {
                'reclaimed_row': [row.row_number, row.raw.decode('ascii'), row.source.source,
                                  row.source.offset, row.source.length],
                'tail_settlement': [tail.rows[0].row_number, tail.rows[0].newly_settled,
                                    tail.rows[0].settled_order_id, tail.rows[0].settled_request_id],
                'day_confirmed': day.confirmed_total.microstrain,
                'night_confirmed': night.confirmed_total.microstrain,
                'retirement_unchanged': [retirement.pending_orders, retirement.pending_rows],
                # The pre-transfer day credential replays its fixed receipt.
                'replay_same': confirm_claim(db, '甲', '白班补', day_claim.claim_id, [2]) == first,
            }
    return result


if __name__ == '__main__':
    main()
