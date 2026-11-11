"""Resource templates: one statement or one billing year, attached with ``@``.

Claude Code lists templates in the ``@`` menu, asks for completion of their
arguments when one is picked there and reads the URI with ``resources/read``
(spike S0, plan 06). Prompt arguments get no completion: Claude Code does not
ask for it.

The arguments arrive as text. Each template checks them itself, so a wrong
value comes back as a German ``ResourceError`` instead of "Error reading
resource".
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import date
from http import HTTPStatus
from typing import Any, cast

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError
from mcp.types import Completion, CompletionArgument, CompletionContext, PromptReference, ResourceTemplateReference

from nkfix_mcp.client import NkfClient, NkfError
from nkfix_mcp.formatting import amount
from nkfix_mcp.resources.markdown import MARKDOWN, cell, day, md, outstanding_text, report_errors
from nkfix_mcp.tools.properties import fetch_properties
from nkfix_mcp.tools.statements import (
    StatementDetails,
    fetch_billing_year_status,
    fetch_statement,
    fetch_statements,
)

log = logging.getLogger(__name__)

STATEMENT_TEMPLATE = 'nkfix://statements/{statement_id}'
YEAR_TEMPLATE = 'nkfix://properties/{property_id}/years/{year}'
FIRST_YEAR, LAST_YEAR = 2000, 2100
# Longer numbers are no ID in NebenkostenFix (and int() refuses more than 4300 digits).
MAX_DIGITS = 9
STATEMENTS_HINT = 'Offene Abrechnungen zeigt nkfix://overview, alle das Werkzeug list_statements.'
PROPERTIES_HINT = 'Die Immobilien zeigt nkfix://overview.'
# The protocol allows at most 100 completion values.
MAX_COMPLETIONS = 100
# Offered for ``year``: this year and the two before.
YEARS_OFFERED = 3

CompletionHandler = Callable[
    [PromptReference | ResourceTemplateReference, CompletionArgument, CompletionContext | None],
    Awaitable[Completion],
]


def _digits(value: str, max_length: int) -> bool:
    return value.isascii() and value.isdigit() and len(value) <= max_length


def _shown(value: str) -> str:
    """The value as the message quotes it; a pasted novel stays out."""
    return value if len(value) <= MAX_DIGITS + 1 else value[:MAX_DIGITS] + '…'


def _number(value: str, what: str, hint: str) -> int:
    if not _digits(value, MAX_DIGITS):
        raise ResourceError(f'„{_shown(value)}“ ist keine gültige Nummer {what}. {hint}')
    return int(value)


def _year(value: str) -> int:
    if not _digits(value, len(str(LAST_YEAR))) or not FIRST_YEAR <= int(value) <= LAST_YEAR:
        raise ResourceError(f'„{_shown(value)}“ ist kein gültiges Abrechnungsjahr ({FIRST_YEAR} bis {LAST_YEAR}).')
    return int(value)


def _euro(value: str | None) -> str:
    return f'{value} €' if value is not None else '?'


def _date_or_text(value: str | None) -> str:
    """A date from NebenkostenFix as 31.12.2025; anything that is no ISO date stays as it is."""
    try:
        return day(value)
    except ValueError:
        return md(value)


def _table(head: list[str], rows: list[list[str]]) -> list[str]:
    return [
        '| ' + ' | '.join(head) + ' |',
        '|' + '---|' * len(head),
        *('| ' + ' | '.join(row) + ' |' for row in rows),
    ]


# --- Statement ---------------------------------------------------------------------


def _balance_line(balance: str | None) -> str:
    number = amount(balance)
    if number is None:
        return '- Saldo: ?'
    if number > 0:
        return f'- Nachzahlung: {_euro(balance)}'
    if number < 0:
        return f'- Guthaben: {_euro(f"{-number:.2f}")}'
    return f'- Saldo: {_euro(balance)}'


def _payment_line(s: StatementDetails) -> str:
    status = md(s.payment_status) or 'ohne Zahlungsstand'
    outstanding = outstanding_text(s.outstanding_amount, s.payment_status)
    return f'- Zahlungsstand: {status}, {outstanding}' if outstanding else f'- Zahlungsstand: {status}'


def _advance_payment_line(new: dict[str, str | None] | None) -> list[str]:
    if new is None:
        return []
    if 'reason' in new:
        return [f'- Keine neue Vorauszahlung: {md(new["reason"])}']
    return [
        f'- Neue Vorauszahlung: von {_euro(new.get("previous"))} auf {_euro(new.get("new"))} je Monat '
        f'ab {_date_or_text(new.get("effective_from"))}'
    ]


def render_statement(s: StatementDetails) -> str:
    """One statement as Markdown, from the same data as get_statement."""
    lines = [
        f'# Abrechnung {s.id}: {md(s.tenant)}' if s.tenant else f'# Abrechnung {s.id}',
        '',
        f'- Wohnung: {md(s.apartment) or "?"}, {md(s.property) or "?"}',
        f'- Zeitraum: {day(s.start_date)} bis {day(s.end_date)}',
    ]
    # Statements before NK-061 have no version; NebenkostenFix computes them live.
    if s.version is not None:
        version = f'- Version {s.version}'
        if s.outdated:
            version += (
                ', veraltet: nach einem älteren Regelstand gerechnet, eine Korrektur könnte heute anders ausfallen'
            )
        lines.append(version)
    lines += [
        '',
        '## Positionen',
        '',
    ]
    rows = [
        [
            cell(i.cost_category),
            cell(i.allocation),
            cell(i.period),
            _euro(i.invoice_amount),
            _euro(i.tenant_share),
            cell(i.explanation),
        ]
        for i in s.line_items
    ]
    head = ['Kostenart', 'Umlage', 'Zeitraum', 'Rechnungsbetrag', 'Anteil Mieter', 'Erläuterung']
    lines += _table(head, rows) if rows else ['Keine.']
    lines += [
        '',
        '## Ergebnis',
        '',
        f'- Kosten des Mieters: {_euro(s.total_costs)}',
        f'- Vorauszahlungen: {_euro(s.advance_payments)}',
        _balance_line(s.balance),
        _payment_line(s),
        f'- Zustellung: am {day(s.delivered_at)}' if s.delivered_at else '- Zustellung: noch nicht zugestellt',
        f'- Abrechnungsfrist: {day(s.statement_deadline)}',
    ]
    if s.objection_deadline:
        lines.append(f'- Einwendungsfrist: {day(s.objection_deadline)}')
    lines += _advance_payment_line(s.new_advance_payment)
    if s.warnings:
        lines += ['', '## Hinweise', '', *(f'- {md(w)}' for w in s.warnings)]
    return '\n'.join(lines) + '\n'


# --- Billing year ------------------------------------------------------------------


def _deadline_line(year: dict[str, Any]) -> str:
    line = f'- Abrechnungsfrist: {day(year.get("statement_deadline"))}'
    if year.get('deadline_exceeded'):
        return line + ', überschritten'
    days = year.get('deadline_days')
    if not isinstance(days, int):
        return line
    if days == 0:
        return line + ', läuft heute ab'
    return line + (', noch 1 Tag' if days == 1 else f', noch {days} Tage')


def _change(category: dict[str, Any]) -> str:
    change = category.get('change_percent')
    # Whole percent, as NebenkostenFix computes and shows it.
    text = f'{change:+.0f} %' if isinstance(change, int | float) else ''
    if category.get('jump'):
        text = f'{text}, Sprung' if text else 'Sprung'
    return text or '-'


def _cost_rows(year: dict[str, Any]) -> list[list[str]]:
    return [
        [
            cell(k.get('name')),
            str(k.get('invoice_count') if k.get('invoice_count') is not None else '?'),
            _euro(k.get('total')),
            _euro(k.get('total_previous_year')) if k.get('total_previous_year') is not None else 'keine',
            _change(k),
            cell(k.get('state')),
        ]
        for k in year.get('cost_categories') or []
    ]


def _cutoff(reading: dict[str, Any]) -> str:
    text = 'ok' if reading.get('cutoff_ok') else 'fehlt'
    missing = reading.get('missing_change_readings') or []
    if missing:
        text += ', Zwischenablesung fehlt: ' + ', '.join(cell(_date_or_text(str(m))) for m in missing)
    return text


def _reading_rows(year: dict[str, Any]) -> list[list[str]]:
    return [
        [
            cell(a.get('number')),
            cell(a.get('cost_category')),
            cell(a.get('apartment')) or 'Haus',
            day(a.get('last_reading_date')) if a.get('last_reading_date') else 'keine',
            _cutoff(a),
        ]
        for a in year.get('readings') or []
    ]


def _tenant_line(tenant: dict[str, Any]) -> str:
    head = (
        f'- {md(tenant.get("name"))} (Mieter {tenant.get("id")}), {md(tenant.get("apartment")) or "?"}, '
        f'{day(tenant.get("start_date"))} bis {day(tenant.get("end_date"))}'
    )
    statement_id = tenant.get('statement_id')
    if statement_id is None:
        return f'{head}: noch keine Abrechnung'
    delivered = tenant.get('delivered_at')
    delivery = f'zugestellt am {day(delivered)}' if delivered else 'nicht zugestellt'
    return f'{head}: Abrechnung {statement_id}, {delivery}'


def _issue_line(issue: dict[str, Any]) -> str:
    # NebenkostenFix writes both as sentences: "Eine Rechnung hat keinen Beleg." and "Laden Sie den Beleg hoch."
    action = md(issue.get('action'))
    return f'- {md(issue.get("message"))} {action}' if action else f'- {md(issue.get("message"))}'


def render_billing_year(year: dict[str, Any]) -> str:
    """One billing year of a property as Markdown, from the same data as get_billing_year_status."""
    lines = [
        f'# Jahresstand {year.get("year")}: {md(year.get("property"))} (Immobilie {year.get("property_id")})',
        '',
        f'- Zeitraum: {day(year.get("start_date"))} bis {day(year.get("end_date"))}',
        _deadline_line(year),
        '',
        '## Kostenarten',
        '',
    ]
    costs = _cost_rows(year)
    lines += (
        _table(['Kostenart', 'Rechnungen', 'Summe', 'Vorjahr', 'Änderung', 'Zustand'], costs) if costs else ['Keine.']
    )
    lines += ['', '## Ablesungen zum Stichtag', '']
    readings = _reading_rows(year)
    head = ['Zähler', 'Kostenart', 'Wohnung', 'Letzte Ablesung', 'Stichtag']
    lines += _table(head, readings) if readings else ['Keine.']
    tenants = year.get('tenants') or []
    lines += ['', '## Mieter', '', *(_tenant_line(m) for m in tenants)] if tenants else ['', '## Mieter', '', 'Keine.']
    issues = year.get('open_issues') or []
    lines += ['', '## Offene Punkte', '']
    lines += [_issue_line(p) for p in issues] if issues else ['Keine.']
    return '\n'.join(lines) + '\n'


# --- Completion --------------------------------------------------------------------


async def _candidates(nkf: NkfClient, argument: str) -> list[str]:
    if argument == 'statement_id':
        return [str(s.id) for s in await fetch_statements(nkf)]
    if argument == 'property_id':
        return [str(p.id) for p in await fetch_properties(nkf)]
    if argument == 'year':
        this_year = date.today().year
        return [str(this_year - k) for k in range(YEARS_OFFERED)]
    return []


def register(server: MCPServer, nkf: NkfClient) -> None:
    @server.resource(
        STATEMENT_TEMPLATE,
        name='statement',
        title='Abrechnung',
        description='Eine Nebenkostenabrechnung als Markdown: Positionen mit Anteil des Mieters, Ergebnis, '
        'Zahlungsstand, Zustellung, Fristen und neue Vorauszahlung. ' + STATEMENTS_HINT,
        mime_type=MARKDOWN,
    )
    @report_errors
    async def statement(statement_id: str) -> str:
        number = _number(statement_id, 'einer Abrechnung', STATEMENTS_HINT)
        try:
            details = await fetch_statement(nkf, number)
        except NkfError as error:
            if error.status == HTTPStatus.NOT_FOUND:
                raise ResourceError(f'Abrechnung {number} gibt es nicht. {STATEMENTS_HINT}') from error
            raise
        return render_statement(details)

    @server.resource(
        YEAR_TEMPLATE,
        name='billing_year',
        title='Jahresstand',
        description='Stand des Jahresabschlusses einer Immobilie als Markdown: Kostenarten mit Vorjahr, '
        'Ablesungen zum Stichtag, Mieter mit Abrechnung und Zustellung, offene Punkte und die Abrechnungsfrist.',
        mime_type=MARKDOWN,
    )
    @report_errors
    async def billing_year(property_id: str, year: str) -> str:
        number = _number(property_id, 'einer Immobilie', PROPERTIES_HINT)
        return render_billing_year(await fetch_billing_year_status(nkf, number, _year(year)))

    # The SDK leaves MCPServer.completion() untyped; this is the signature its docstring describes.
    completion = cast('Callable[[], Callable[[CompletionHandler], CompletionHandler]]', server.completion)

    @completion()
    async def complete(
        ref: PromptReference | ResourceTemplateReference,
        argument: CompletionArgument,
        context: CompletionContext | None,
    ) -> Completion:
        """Values for template arguments; prompt arguments get none (Claude Code does not ask)."""
        if not isinstance(ref, ResourceTemplateReference):
            return Completion(values=[])
        try:
            candidates = await _candidates(nkf, argument.name)
        except NkfError as error:
            # Completion is a convenience while typing: the input field gets no values, the log the reason.
            log.warning('Vervollständigung von %s: %s', argument.name, error)
            return Completion(values=[])
        matching = [c for c in candidates if c.startswith(argument.value)]
        return Completion(
            values=matching[:MAX_COMPLETIONS], total=len(matching), has_more=len(matching) > MAX_COMPLETIONS
        )
