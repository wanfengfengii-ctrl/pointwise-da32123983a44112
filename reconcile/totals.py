from collections.abc import Iterable
from dataclasses import dataclass

from reconcile.lines import AccountLine


@dataclass(frozen=True, slots=True)
class MeasurementTotals:
    row_count: int
    measured_count: int
    missing_count: int
    microstrain: int


def summarize_measurements(lines: Iterable[AccountLine]) -> MeasurementTotals:
    measured = 0
    missing = 0
    total = 0
    for line in lines:
        if line.value is None:
            missing += 1
        else:
            measured += 1
            total += line.value
    return MeasurementTotals(measured + missing, measured, missing, total)
