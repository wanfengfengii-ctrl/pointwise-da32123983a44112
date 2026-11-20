from collections.abc import Iterable

from delivery.receipts import Receipt


def confirmed_rows(receipts: Iterable[Receipt]) -> frozenset[int]:
    """Project the receipts of one order to the exact set of rows received."""
    return frozenset(number for receipt in receipts for number in receipt.row_numbers)
