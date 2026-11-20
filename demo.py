from contextlib import closing
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from delivery.receive import confirm, read
from ingest.layout import source_records
from ingest.recording import seal
from ingest.workspace import open_archive
from issue.orders import issue
from reconcile.report import account
from volume.placement import get_hot_payload
from volume.retire import retire


def _originals(lines):
    return [
        [line.row_number, line.raw.decode('ascii'), line.value, line.source.source,
         line.source.offset, line.source.length]
        for line in lines
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
        'code_paths': ['ingest/recording.py', 'issue/orders.py', 'delivery/receive.py',
                       'volume/retire.py', 'volume/placement.py', 'reconcile/report.py'],
        'oracle': '闭区间分别减去两单首收集合，按源字节手数偏移及长度。甲 5+7+11，乙 7+11+13。',
    }
    print('POINTWISE_SCENARIO ' + json.dumps(scenario, ensure_ascii=False, separators=(',', ':')))


if __name__ == '__main__':
    main()
