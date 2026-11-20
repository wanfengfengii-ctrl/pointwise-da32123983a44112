from dataclasses import dataclass
from hashlib import sha256
import re

from archive_common import InvalidRequest
from ingest.layout import LocatedRecord, SourceSpan


_DECIMAL = re.compile(rb'([+-]?)([0-9]+)')


@dataclass(frozen=True, slots=True)
class Observation:
    recording_id: str
    row_number: int
    source: SourceSpan
    value: int | None
    digest: bytes

    @property
    def missing(self) -> bool:
        return self.value is None


def _decimal_integer(sign: bytes, digits: bytes) -> int:
    # Parse in bounded chunks so Python's decimal digit limit cannot round or reject a reading.
    value = 0
    for offset in range(0, len(digits), 9):
        chunk = digits[offset:offset + 9]
        value = value * (10 ** len(chunk)) + int(chunk)
    return -value if sign == b'-' else value


def decode_sample(record: LocatedRecord) -> Observation:
    raw = record.raw
    code = raw.removesuffix(b'\r\n') if raw.endswith(b'\r\n') else raw.removesuffix(b'\n')
    if code == b'NA':
        value = None
    else:
        match = _DECIMAL.fullmatch(code)
        if match is None:
            raise InvalidRequest(f'R{record.row_number} 的微应变原码无效')
        value = _decimal_integer(*match.groups())
    return Observation(record.recording_id, record.row_number, record.source, value, sha256(raw).digest())
