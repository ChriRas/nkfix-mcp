from decimal import Decimal

import pytest

from nkfix_mcp.formatting import format_money, in_year, truncate


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        (460.0, '460.00'),
        (460, '460.00'),
        ('108.32', '108.32'),
        ('-12.5', '-12.50'),
        (0.1 + 0.2, '0.30'),
        (123.455, '123.46'),
        (Decimal('7'), '7.00'),
        (None, None),
        ('', None),
    ],
)
def test_format_money_is_a_string_with_two_decimal_places(raw, expected):
    assert format_money(raw) == expected


def test_unusable_amount_is_not_swallowed():
    with pytest.raises(ValueError, match='Kein Geldbetrag'):
        format_money('zwölf')


@pytest.mark.parametrize(
    ('start_date', 'end_date', 'year', 'expected'),
    [
        ('2025-01-01', '2025-12-31', 2025, True),
        ('2024-12-01', '2025-01-31', 2025, True),
        ('2024-12-01', '2025-01-31', 2024, True),
        ('2024-01-01', '2024-12-31', 2025, False),
        ('2026-01-01', '2026-03-31', 2025, False),
        ('2025-06-01', None, 2025, True),
        (None, None, 2025, False),
    ],
)
def test_period_counts_for_every_year_it_touches(start_date, end_date, year, expected):
    assert in_year(start_date, end_date, year) is expected


def test_truncate_cuts_off_and_says_how_many_are_missing():
    items = truncate(list(range(130)), 100)
    assert items.count == 130
    assert len(items.items) == 100
    assert items.omitted == 30
    assert 'Filter' in items.note


def test_short_list_stays_whole_and_without_note():
    items = truncate([1, 2], 100)
    assert (items.count, items.omitted, items.note) == (2, 0, None)
