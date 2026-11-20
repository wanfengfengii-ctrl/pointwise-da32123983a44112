from dataclasses import dataclass

from ingest.layout import SourceSpan
from ingest.samples import Observation
from issue.manifest import OrderManifest


@dataclass(frozen=True, slots=True)
class AccountLine:
    recording_id: str
    row_number: int
    source: SourceSpan
    value: int | None
    received: bool

    @property
    def missing(self) -> bool:
        return self.value is None


def build_account_lines(
    order: OrderManifest, observations: tuple[Observation, ...], received: frozenset[int]
) -> tuple[AccountLine, ...]:
    return tuple(
        AccountLine(order.recording_id, line.row_number, line.source, observation.value, line.row_number in received)
        for line, observation in zip(order.lines, observations, strict=True)
    )
