# 汀桥观测交班归档

汀桥材料检测中心的白班将探头录制封页，资料员向两个复核班组固定各自应收的范围。班组可以先点收一部分原件。卷管理员随后释放热区，夜班重开同一归档即可补发原件、查回原回执并核对各单的微应变合计。

## 运行

需要 Python 3.11，业务和测试均使用标准库。在项目根目录运行：

```sh
python3 -B check_project.py
python3 -B demo.py
```

`check_project.py` 由运行控制台在归档物化时提供。直接获取源码后可运行完整原套件：

```sh
python3 -B -m unittest discover -s tests -t . -v
```

演示使用 `samples/shift.obs`，在测试临时目录建档并重开。甲的范围为 R1–R3，乙为 R2–R4。甲先收 R2、乙先收 R3，共有页退役后，甲补 R1、R3，乙补 R2、R4。补齐后两单实测合计分别为 23 和 31 微应变。`samples/signed.obs` 提供负值、实测零和缺测的核账样例。

## API

各入口使用同一个归档连接，调用者负责关闭连接。各入口管理自身事务，在无外层事务时调用。可在同一归档目录重新打开独立连接。初始化复用目录内的 `archive.sqlite3` 和已提交数据。

| 入口 | 参数与结果 |
| --- | --- |
| `ingest.workspace.open_archive(directory)` | 创建或重开本地归档，返回 SQLite 连接。 |
| `ingest.recording.seal(db, recording_id, records, *, page_ends=None)` | 接纳按原序提供的 `(bytes, SourceSpan)` 记录，返回录制号、行数及原序页号。行号从 1 开始。`page_ends` 是严格递增的各页最后行位，最后一项等于录制行数，缺省按最多 128 行分卷。已有录制重投时复用首次封页布局。 |
| `issue.orders.issue(db, order_id, recording_id, first, last)` | 按闭区间出票，返回含原行号及出处的固定清单。同参重投返回原清单。 |
| `delivery.receive.read(db, order_id, first=None, last=None)` | 只领取本单范围内的欠行。可省略两端以领取全单欠行，也可指定有效的单内闭区间。返回含行位、原字节、值和出处的原序元组。任一所需原件不可完整读取时整次失败。 |
| `delivery.receive.confirm(db, order_id, request_id, row_numbers)` | 按具名请求点收原行集合，返回固定回执和当前实收行位。输入可乱序、重复或为空，重复行只参与集合一次。不同请求可重叠。同单同请求号同集合复用原回执，回执仍保留该请求的完整集合。 |
| `volume.retire.retire(db, page_id)` | 退役并返回固定回执，记录页号、保留字节数、仍欠交的单数及这些单合计涉及的不同原行数。重投返回已提交回执。 |
| `reconcile.report.account(db, order_id)` | 由持久观测目录、冻结清单和已提交回执生成原序明细、实收与欠交分组及其计量小计，附原回执。缺少必要依据时整次失败。 |

`SourceSpan(source, offset, length)` 记录来源标识、零基字节起点和字节长度。一个录制可以包含多个来源。单一字节流可用 `ingest.layout.source_records(source, data)` 生成带跨度的记录。原码为 ASCII 十进制整数（可带正负号）或 `NA`，可带一组 LF 或 CRLF 行尾。整数以微应变计，零与缺测分别记录。原字节、来源和原行序都保留，同名录制的原字节或出处发生变化时需要使用新录制号。账目合计使用 Python 整数，缺测只增加缺测计数。

`archive_common` 提供 `InvalidRequest`、`UnknownRecord`、`Conflict` 和 `Unavailable`，对应非法参数、未知业务编号、同号异参及依据或存储不可用。合法确认或退役成功后可按原业务编号查询固定回执。读取和核账不推进实收。替代写入、读回或提交失败时，退役回滚并保留原热区地址。

热区和替代区是同一归档内独立的 SQLite 载荷地址。稳定页号与采集出处保持不变。退役提交删除热区载荷、发布替代地址和回执，按原热区地址查询将返回空。正常读取凭有效地址取得整页，用固定跨度定位原件。退役保存整页，因此相关单全部点清后的合法新单也能领取。核账从冻结的观测目录汇总实收和欠交，同一行在每张包含它的单中分别计入一次。归档范围为本地交班，业务不涉及跨主机传输和长期副本回收。

```python
from contextlib import closing

from delivery.receive import confirm, read
from ingest.layout import source_records
from ingest.recording import seal
from ingest.workspace import open_archive
from issue.orders import issue
from reconcile.report import account
from volume.retire import retire

with closing(open_archive('shift-archive')) as db:
    rows = source_records('probe/export-17', b'+5\n007\nNA\n')
    capture = seal(db, 'capture-17', rows, page_ends=(1, 3))
    issue(db, 'team-a', 'capture-17', 1, 3)
    first_segment = read(db, 'team-a', 2, 2)
    confirm(db, 'team-a', 'morning-1', [line.row_number for line in first_segment])
    retire(db, capture.page_ids[1])

with closing(open_archive('shift-archive')) as db:
    originals = read(db, 'team-a')
    confirm(db, 'team-a', 'night-1', [line.row_number for line in originals])
    report = account(db, 'team-a')
    assert report.total.microstrain == 12 and report.total.missing_count == 1
```
