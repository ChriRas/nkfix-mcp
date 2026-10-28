"""Turn the UI-shaped responses of NebenkostenFix into stable output.

The API delivers money sometimes as a JSON float, sometimes as a string with
two decimal places. Here both become a string like ``"1234.56"``, and
calculations use ``Decimal``: sums of floats otherwise carry rounding
residue into the response.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar('T')
CENT = Decimal('0.01')


def amount(value: Any) -> Decimal | None:
    if value is None or value == '':
        return None
    try:
        # str() before Decimal: Decimal(0.1) would carry the binary inaccuracy along.
        return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation as error:
        raise ValueError(f'Kein Geldbetrag: {value!r}') from error


def format_money(value: Any) -> str | None:
    number = amount(value)
    return None if number is None else f'{number:.2f}'


def total(values: Iterable[Any]) -> str:
    return f'{sum((amount(w) or Decimal(0) for w in values), Decimal(0)):.2f}'


def in_year(start_date: str | None, end_date: str | None, year: int) -> bool:
    """Does the period ``start_date``–``end_date`` touch the calendar year? An open end runs to today and beyond."""
    if not start_date:
        return False
    return start_date <= f'{year}-12-31' and (end_date or '9999-12-31') >= f'{year}-01-01'


class ItemList(BaseModel, Generic[T]):
    count: int
    items: list[T]
    omitted: int = 0
    note: str | None = None


def truncate(items: Sequence[T], limit: int) -> ItemList[T]:
    """Shorten long lists: Claude Code warns above 10,000 tokens per tool response."""
    omitted = max(0, len(items) - limit)
    note = None
    if omitted:
        note = (f'{omitted} weitere Einträge weggelassen. Grenze die Abfrage mit den Filtern ein '
                '(z. B. Immobilie, Jahr, Mieter).')
    return ItemList[T](count=len(items), items=list(items[:limit]), omitted=omitted,
                       note=note)
