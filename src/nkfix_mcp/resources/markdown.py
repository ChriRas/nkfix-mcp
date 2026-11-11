"""Markdown helpers shared by the resources and the resource templates."""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable, Sequence
from datetime import date
from typing import ParamSpec, TypeVar

from mcp.server.mcpserver.exceptions import ResourceError

from nkfix_mcp.client import NkfError
from nkfix_mcp.formatting import amount, format_money
from nkfix_mcp.tools import MAX_ITEMS

MARKDOWN = 'text/markdown'
SETTLED = ('beglichen', 'ausgeglichen')

P = ParamSpec('P')
T = TypeVar('T')


def report_errors(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    """Errors from NebenkostenFix as ResourceError: only that one reaches the client with its text."""

    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return await func(*args, **kwargs)
        except NkfError as error:
            raise ResourceError(str(error)) from error

    return wrapper


def day(text: str | None) -> str:
    """An ISO date as 31.12.2025; '?' if there is none."""
    return date.fromisoformat(text).strftime('%d.%m.%Y') if text else '?'


# Markdown would read these as formatting; a name like "Haus *A*" stays a name.
MARKDOWN_SPECIAL = str.maketrans({c: f'\\{c}' for c in '\\`*_#[]'})


def md(text: str | None) -> str:
    """A value typed into NebenkostenFix, safe inside a Markdown line: one line, no formatting."""
    return ' '.join((text or '').split()).translate(MARKDOWN_SPECIAL)


def cell(text: str | None) -> str:
    """Like md, and safe inside a table cell."""
    return md(text).replace('|', '\\|')


def shortened(items: Sequence[T], what: str) -> tuple[Sequence[T], str | None]:
    """At most MAX_ITEMS items, and a note how many were left out."""
    omitted = len(items) - MAX_ITEMS
    if omitted <= 0:
        return items, None
    return items[:MAX_ITEMS], f'_{omitted} weitere {what} weggelassen; die Werkzeuge zeigen alle._'


def outstanding_text(outstanding_amount: str | None, payment_status: str | None) -> str | None:
    """What is still open on a statement, as the overview and the statement template name it."""
    outstanding = amount(outstanding_amount)
    if outstanding is None:
        return None
    if outstanding < 0:
        # Negative: a credit the landlord still pays out (zahlungsstand.py upstream).
        return f'{format_money(-outstanding)} € an den Mieter auszuzahlen'
    if outstanding or payment_status not in SETTLED:
        # Zero only repeats a settled status; with any other status it is worth seeing.
        return f'{format_money(outstanding)} € offen'
    return None
