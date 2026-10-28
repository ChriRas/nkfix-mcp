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


def _shortened(items: Sequence[T], what: str) -> tuple[Sequence[T], str | None]:
    omitted = len(items) - MAX_ITEMS
    if omitted <= 0:
        return items, None
    return items[:MAX_ITEMS], f'_{omitted} weitere {what} weggelassen; die Werkzeuge zeigen alle._'


def _tenant_line(t: Tenant) -> str:
    parts = [f'{t.name} (Mieter {t.id})', t.apartment or 'ohne Wohnung', f'eingezogen am {_day(t.move_in)}']
    if t.move_out:
        parts.append(f'ausgezogen am {_day(t.move_out)}')
    parts.append(f'abgerechnet bis {_day(t.billed_until)}' if t.billed_until else 'noch nicht abgerechnet')
    return '- ' + ', '.join(parts)


def _statement_line(s: Statement) -> str:
    parts = [
        f'Abrechnung {s.id}: {s.tenant}',
        f'{_day(s.start_date)} bis {_day(s.end_date)}',
        s.payment_status or 'ohne Zahlungsstand',
    ]
    if s.outstanding_amount is not None:
        parts.append(f'{s.outstanding_amount} € offen')
    parts.append(f'zugestellt am {s.delivered_at}' if s.delivered_at else 'nicht zugestellt')
    return '- ' + ', '.join(parts)


def _property_section(p: Property, tenants: list[Tenant], statements: list[Statement]) -> list[str]:
    lines = [f'## {p.name} (Immobilie {p.id})', '']
    if p.billing_year_start:
        lines += [f'Abrechnungsjahr beginnt am {p.billing_year_start}', '']
    own = [t for t in tenants if t.property_id == p.id]
    shown, note = _shortened(own, 'Mieter')
    lines += ['Mieter:', *(_tenant_line(t) for t in shown)] if shown else ['Keine Mieter.']
    lines += [note] if note else []
    open_ones = [s for s in statements if s.property_id == p.id and s.payment_status not in SETTLED]
    settled = sum(1 for s in statements if s.property_id == p.id and s.payment_status in SETTLED)
    if open_ones:
        lines += ['', 'Offene Abrechnungen:', *(_statement_line(s) for s in open_ones)]
    if settled:
        lines += [
            '',
            f'{settled} weitere Abrechnung ist beglichen.'
            if settled == 1
            else f'{settled} weitere Abrechnungen sind beglichen.',
        ]
    return [*lines, '']


def _deadline_line(d: Deadline, with_kind: bool) -> str:
    return f'- {_day(d.date)}: {d.title}' + (f' ({d.kind})' if with_kind else '')


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
        lines += ['## Mieter ohne Immobilie', '', *(_tenant_line(t) for t in homeless), '']
    until = (today + timedelta(days=OVERVIEW_DAYS)).isoformat()
    near = [d for d in deadlines if d.date <= until]
    lines += [f'## Fristen der nächsten {OVERVIEW_DAYS} Tage', '']
    lines += [_deadline_line(d, with_kind=False) for d in near] if near else ['Keine.']
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
        description='Immobilien mit ihren Mietern (samt IDs), offene Abrechnungen und die Fristen der nächsten '
        f'{OVERVIEW_DAYS} Tage, als Markdown.',
        mime_type=MARKDOWN,
    )
    @_report_errors
    async def overview() -> str:
        return render_overview(
            await fetch_properties(nkf),
            await fetch_tenants(nkf),
            await fetch_statements(nkf),
            await fetch_deadlines(nkf),
            date.today(),
        )

    @server.resource(
        DEADLINES_URI,
        name='deadlines',
        title='Fristen',
        description='Alle anstehenden Fristen und Termine: Abrechnungs-, Einwendungs- und Eichfristen, Ablesungen.',
        mime_type=MARKDOWN,
    )
    @_report_errors
    async def deadlines() -> str:
        return render_deadlines(await fetch_deadlines(nkf))
