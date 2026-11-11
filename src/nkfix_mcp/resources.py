"""Resources: data the human attaches with ``@``, e.g. ``@nkfix:nkfix://overview``.

Each read builds the Markdown fresh, from the same mapping the tools use.
Errors from NebenkostenFix become a ``ResourceError``: only that exception
reaches the client with its text, any other one arrives as "Error reading
resource".
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable, Sequence
from datetime import date, timedelta
from typing import ParamSpec, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError

from nkfix_mcp.client import NkfClient, NkfError
from nkfix_mcp.formatting import amount as as_amount
from nkfix_mcp.formatting import format_money
from nkfix_mcp.tools import MAX_ITEMS
from nkfix_mcp.tools.properties import Property, fetch_properties
from nkfix_mcp.tools.statements import Deadline, Statement, fetch_deadlines, fetch_statements
from nkfix_mcp.tools.tenants import Tenant, fetch_tenants

OVERVIEW_URI = 'nkfix://overview'
DEADLINES_URI = 'nkfix://deadlines'
# The overview is for "what is coming up"; the deadlines resource has all of them.
OVERVIEW_DAYS = 90
SETTLED = ('beglichen', 'ausgeglichen')
MARKDOWN = 'text/markdown'

P = ParamSpec('P')
T = TypeVar('T')


def _report_errors(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return await func(*args, **kwargs)
        except NkfError as error:
            raise ResourceError(str(error)) from error

    return wrapper


def _day(text: str | None) -> str:
    return date.fromisoformat(text).strftime('%d.%m.%Y') if text else '?'


# Markdown would read these as formatting; a name like "Haus *A*" stays a name.
MARKDOWN_SPECIAL = str.maketrans({c: f'\\{c}' for c in '\\`*_#[]'})


def _md(text: str | None) -> str:
    """A value typed into NebenkostenFix, safe inside a Markdown line: one line, no formatting."""
    return ' '.join((text or '').split()).translate(MARKDOWN_SPECIAL)


def _shortened(items: Sequence[T], what: str) -> tuple[Sequence[T], str | None]:
    omitted = len(items) - MAX_ITEMS
    if omitted <= 0:
        return items, None
    return items[:MAX_ITEMS], f'_{omitted} weitere {what} weggelassen; die Werkzeuge zeigen alle._'


def _tenant_line(t: Tenant) -> str:
    parts = [f'{_md(t.name)} (Mieter {t.id})', _md(t.apartment) or 'ohne Wohnung', f'eingezogen am {_day(t.move_in)}']
    if t.move_out:
        parts.append(f'ausgezogen am {_day(t.move_out)}')
    parts.append(f'abgerechnet bis {_day(t.billed_until)}' if t.billed_until else 'noch nicht abgerechnet')
    return '- ' + ', '.join(parts)


def _statement_line(s: Statement) -> str:
    parts = [
        f'Abrechnung {s.id}: {_md(s.tenant)}',
        f'{_day(s.start_date)} bis {_day(s.end_date)}',
        s.payment_status or 'ohne Zahlungsstand',
    ]
    outstanding = as_amount(s.outstanding_amount)
    if outstanding is not None and outstanding < 0:
        # Negative: a credit the landlord still pays out (zahlungsstand.py upstream).
        parts.append(f'{format_money(-outstanding)} € an den Mieter auszuzahlen')
    elif outstanding is not None and (outstanding or s.payment_status not in SETTLED):
        # Zero only repeats a settled status; with any other status it is worth seeing.
        parts.append(f'{format_money(outstanding)} € offen')
    parts.append(f'zugestellt am {_day(s.delivered_at)}' if s.delivered_at else 'nicht zugestellt')
    return '- ' + ', '.join(parts)


def _property_section(p: Property, tenants: list[Tenant], statements: list[Statement]) -> list[str]:
    lines = [f'## {_md(p.name)} (Immobilie {p.id})', '']
    if p.billing_year_start:
        lines += [f'Abrechnungsjahr beginnt am {p.billing_year_start}', '']
    own = [t for t in tenants if t.property_id == p.id]
    shown, note = _shortened(own, 'Mieter')
    lines += ['Mieter:', *(_tenant_line(t) for t in shown)] if shown else ['Keine Mieter.']
    lines += [note] if note else []
    own_statements = [s for s in statements if s.property_id == p.id]
    open_ones = [s for s in own_statements if not _done(s)]
    done = len(own_statements) - len(open_ones)
    if open_ones:
        shown_statements, statements_note = _shortened(open_ones, 'Abrechnungen')
        lines += ['', 'Abrechnungen mit offenem Punkt:', *(_statement_line(s) for s in shown_statements)]
        lines += [statements_note] if statements_note else []
    if done:
        lines += ['', _done_line(done, further=bool(open_ones))]
    return [*lines, '']


def _done(s: Statement) -> bool:
    # Settled is not enough: the deadline is only kept once the statement reaches the tenant,
    # and a credit still to pay out is money that is open.
    return s.payment_status in SETTLED and s.delivered_at is not None and not as_amount(s.outstanding_amount)


def _done_line(count: int, further: bool) -> str:
    noun = 'weitere Abrechnung' if further else 'Abrechnung'
    if count == 1:
        return f'1 {noun} ist beglichen und zugestellt.'
    return f'{count} {noun}en sind beglichen und zugestellt.'


def _statement_note(d: Deadline) -> str:
    """Which statement a statement deadline belongs to, with its period when known."""
    if d.statement_id is None:
        return ''
    period = f', {_day(d.period_start)} bis {_day(d.period_end)}' if d.period_start and d.period_end else ''
    return f' (Abrechnung {d.statement_id}{period})'


def _deadline_line(d: Deadline, with_kind: bool) -> str:
    return f'- {_day(d.date)}: {_md(d.title)}{_statement_note(d)}' + (f' ({d.kind})' if with_kind else '')


def render_overview(
    properties: list[Property],
    tenants: list[Tenant],
    statements: list[Statement],
    deadlines: list[Deadline],
    today: date,
) -> str:
    lines = ['# Überblick NebenkostenFix', '']
    shown, note = _shortened(properties, 'Immobilien')
    for p in shown:
        lines += _property_section(p, tenants, statements)
    lines += [note, ''] if note else []
    homeless = [t for t in tenants if t.property_id is None]
    if homeless:
        shown_homeless, homeless_note = _shortened(homeless, 'Mieter')
        lines += ['## Mieter ohne Immobilie', '', *(_tenant_line(t) for t in shown_homeless)]
        lines += [homeless_note, ''] if homeless_note else ['']
    until = (today + timedelta(days=OVERVIEW_DAYS)).isoformat()
    near = [d for d in deadlines if d.date <= until]
    lines += [f'## Fristen der nächsten {OVERVIEW_DAYS} Tage', '']
    shown_near, near_note = _shortened(near, 'Fristen')
    lines += [_deadline_line(d, with_kind=False) for d in shown_near] if near else ['Keine.']
    lines += [near_note] if near_note else []
    return '\n'.join(lines) + '\n'


def render_deadlines(deadlines: list[Deadline]) -> str:
    shown, note = _shortened(deadlines, 'Fristen')
    lines = (
        ['# Fristen', '', *(_deadline_line(d, with_kind=True) for d in shown)] if shown else ['# Fristen', '', 'Keine.']
    )
    return '\n'.join([*lines, *([note] if note else [])]) + '\n'


def register(server: MCPServer, nkf: NkfClient) -> None:
    @server.resource(
        OVERVIEW_URI,
        name='overview',
        title='Überblick',
        description='Immobilien mit ihren Mietern (samt IDs), Abrechnungen, die noch offen oder nicht zugestellt '
        f'sind, und die Fristen der nächsten {OVERVIEW_DAYS} Tage, als Markdown.',
        mime_type=MARKDOWN,
    )
    @_report_errors
    async def overview() -> str:
        properties = await fetch_properties(nkf)
        tenants = await fetch_tenants(nkf)
        statements = await fetch_statements(nkf)
        return render_overview(properties, tenants, statements, await fetch_deadlines(nkf, statements), date.today())

    @server.resource(
        DEADLINES_URI,
        name='deadlines',
        title='Fristen',
        description='Alle anstehenden Fristen und Termine: Abrechnungs-, Einwendungs- und Eichfristen, Ablesungen, '
        'neue Vorauszahlungen und die früheste Mieterhöhung.',
        mime_type=MARKDOWN,
    )
    @_report_errors
    async def deadlines() -> str:
        return render_deadlines(await fetch_deadlines(nkf))
