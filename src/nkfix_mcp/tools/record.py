"""Record tools: create a payment, a meter reading and an invoice.

They only create, never change or delete; an API key cannot do that
anyway. A mistake therefore stays until the human removes it in
NebenkostenFix. That is why everything is checked before creating
(duplicate, implausible reading), and if there are concerns, the human decides:

- If the client can ask (MCP elicitation, e.g. Claude Code), the server asks
  the human directly. Their 'no' is final, even if the model sends an approval
  code along.
- Otherwise "not saved" comes back with the concerns and an approval code.
  The model must ask the human and, only with their consent, repeat the same
  call with ``approval_code=<code>``. The code matches exactly these arguments
  (for a receipt also its content) and these concerns (review S4-2): it cannot
  be set in advance, and if a value changes or a concern is added before the
  second call, it no longer applies. Even the code does not prove that the
  human was actually asked.

The confirmation prompt sits in front of the tool as a resolver (``Resolve``):
that way it works over both protocol generations of MCP, the old one with a
request from the server and the new one (from 2026-07-28) with ``InputRequiredResult``.

No ``from __future__ import annotations``: ``Resolve(…)`` refers to
local functions in ``register``, and the SDK evaluates the annotations
only later, where it no longer sees these names.
"""

import hashlib
import hmac
import json
import re
import secrets
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Context, Elicit, ElicitationResult, MCPServer, Resolve
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, BeforeValidator, Field

from nkfix_mcp.client import NkfClient, NkfConfirmationRequired
from nkfix_mcp.formatting import CENT, format_money
from nkfix_mcp.formatting import amount as as_amount
from nkfix_mcp.receipts import Receipt, read_receipt
from nkfix_mcp.tools import IsoDate, report_errors, tool
from nkfix_mcp.tools.check import MeterId
from nkfix_mcp.tools.read import PaymentType, PropertyId, TenantId

CREATE_ONLY = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
)
# According to the Claude Code docs (2026-10-27): asks on every call, even
# if the human has approved the tool in general.
REQUIRE_USER_INTERACTION = {'anthropic/requiresUserInteraction': True}

HeatingCostType = Literal[
    'brennstoff',
    'betriebsstrom',
    'bedienung',
    'pruefung',
    'reinigung',
    'messung',
    'anmietung_erfassung',
    'verwendung_erfassung',
]


Section35aCategory = Literal['haushaltsnah', 'handwerker']
MAX_DECIMALS = 2
AMBIGUOUS_AMOUNT = (
    'Der Betrag {value!r} hat mehr als zwei Nachkommastellen und ist mehrdeutig (ist „1.500“ 1500 € '
    'oder 1,50 €?). Gib ihn eindeutig an, z. B. "1500.00" oder "1500,00".'
)


def _parse_amount(value: Any) -> Decimal:
    """``"1234,5"``, ``"1.234,56"``, ``"98.7"`` and ``98.7`` become ``Decimal('1234.50')`` etc.

    More than two decimal places are not rounded but rejected:
    "1.500" copied from a receipt almost always means 1500 €; read as a
    decimal number it would be 1.50 €, and nobody would notice.
    """
    if isinstance(value, bool) or not isinstance(value, str | int | float | Decimal):
        # ValueError, not TypeError: pydantic turns only ValueError into a validation message.
        raise ValueError(f'Kein Betrag: {value!r}')  # noqa: TRY004
    text = str(value).strip().replace(' ', '').replace('€', '')
    if ',' in text:
        text = text.replace('.', '').replace(',', '.')
    try:
        number = Decimal(text)
    except InvalidOperation as error:
        raise ValueError(f'Kein Betrag: {value!r}') from error
    exponent = number.as_tuple().exponent
    # 'n', 'N' or 'F' instead of a number: NaN or infinity.
    if not isinstance(exponent, int) or exponent < -MAX_DECIMALS:
        raise ValueError(AMBIGUOUS_AMOUNT.format(value=value))
    return number.quantize(CENT)


Amount = Annotated[
    Decimal,
    BeforeValidator(_parse_amount),
    Field(description='Betrag in Euro mit höchstens zwei Nachkommastellen, z. B. "1500.00" oder "1.500,00"'),
]
OptionalAmount = Annotated[
    Decimal | None,
    BeforeValidator(lambda w: None if w in (None, '') else _parse_amount(w)),
    Field(description='Betrag in Euro'),
]
ApprovalCode = Annotated[
    str | None,
    Field(
        max_length=20,
        description=(
            'Freigabecode aus einer vorigen Antwort mit saved=false. Nur mitschicken, nachdem du dem Menschen die '
            'Bedenken gezeigt hast und er dem Speichern ausdrücklich zugestimmt hat. Nie vorab und nie von dir aus.'
        ),
    ),
]

# New on every start: a code is only valid in this process and cannot be
# computed in advance from the source.
_SALT = secrets.token_bytes(16)


def _approval_code(values: tuple[Any, ...], concerns: list[str]) -> str:
    raw = json.dumps([values, concerns], default=str, ensure_ascii=False)
    return hmac.new(_SALT, raw.encode(), 'sha256').hexdigest()[:8]


class ConfirmationPrompt(BaseModel):
    save: bool = Field(title='Trotzdem speichern?', description='Ja: der Eintrag wird trotzdem angelegt.')


class PrecheckResult(BaseModel):
    """Result of the precheck when nobody was asked."""

    concerns: list[str]
    can_elicit: bool
    approval_code: str | None = None


class RecordResult(BaseModel):
    saved: bool
    id: int | None = Field(description='ID des neuen Eintrags')
    concerns: list[str] = Field(description='was gegen das Speichern sprach (auch wenn trotzdem gespeichert wurde)')
    note: str
    approval_code: str | None = Field(
        default=None, description=('Code für den zweiten Aufruf, falls der Mensch trotz der Bedenken speichern will')
    )


class Decision(BaseModel):
    """Save: the human agreed, or there was nothing to agree to.

    Not saving is no Decision but the ``RecordResult`` that goes back to the model.
    """

    despite_concerns: bool = False
    concerns: list[str] = []


def _can_elicit(ctx: Context) -> bool:
    capabilities = ctx.client_capabilities
    elicitation = capabilities.elicitation if capabilities is not None else None
    return elicitation is not None and (elicitation.form is not None or elicitation.url is None)


def _ask(
    ctx: Context, summary: str, concerns: list[str], values: tuple[Any, ...]
) -> Elicit[ConfirmationPrompt] | PrecheckResult:
    """``values``: the tool and all arguments the approval code is bound to."""
    if not concerns:
        return PrecheckResult(concerns=[], can_elicit=True)
    if not _can_elicit(ctx):
        return PrecheckResult(concerns=concerns, can_elicit=False, approval_code=_approval_code(values, concerns))
    bullets = '\n'.join(f'• {b}' for b in concerns)
    return Elicit(f'{summary}\n\nBedenken:\n{bullets}\n\nTrotzdem speichern?', ConfirmationPrompt)


def _decide(decision: ElicitationResult[ConfirmationPrompt], approval_code: str | None) -> Decision | RecordResult:
    if decision.action != 'accept':
        return RecordResult(
            saved=False,
            id=None,
            concerns=[],
            note='Nicht gespeichert: Der Mensch hat abgelehnt oder abgebrochen. Er kann den Eintrag selbst in '
            'NebenkostenFix anlegen.',
        )
    data = decision.data
    if isinstance(data, ConfirmationPrompt):
        if data.save:
            return Decision(despite_concerns=True)
        return RecordResult(saved=False, id=None, concerns=[], note='Nicht gespeichert: Der Mensch hat abgelehnt.')
    if not data.concerns:
        return Decision()
    # As bytes: compare_digest raises on str with umlauts ("bestätigt").
    if approval_code is not None and hmac.compare_digest(
        approval_code.strip().lower().encode(), data.approval_code.encode()
    ):
        return Decision(despite_concerns=True, concerns=data.concerns)
    note = (
        f'Zeig dem Menschen die Bedenken und frag, ob trotzdem gespeichert werden soll. Nur wenn er '
        f"zustimmt: dasselbe Werkzeug mit denselben Werten und approval_code='{data.approval_code}' erneut "
        f'aufrufen.'
    )
    if approval_code is not None:
        note = (
            'Nicht gespeichert: Der Freigabecode passt nicht (mehr). Er gilt nur für die Werte und Bedenken '
            'der Antwort, aus der er stammt; die haben sich geändert, der Server wurde neu gestartet oder '
            f'der Code ist geraten. {note}'
        )
    else:
        note = f'Nicht gespeichert. {note}'
    return RecordResult(saved=False, id=None, concerns=data.concerns, note=note, approval_code=data.approval_code)


def _saved(response: Any, decision: Decision) -> RecordResult:
    new_id = response.get('id') if isinstance(response, dict) else None
    if decision.concerns:
        # Via the approval code: the concerns belong in the response so that the
        # human sees them, even if the model did not pass them on.
        note = 'Gespeichert trotz Bedenken (Freigabecode). Nenne dem Menschen die Bedenken.'
    elif decision.despite_concerns:
        note = 'Gespeichert, nachdem der Mensch im Dialog zugestimmt hat.'
    else:
        note = 'Gespeichert.'
    return RecordResult(saved=True, id=new_id, concerns=decision.concerns, note=note)


def _format_date(text: str) -> str:
    return date.fromisoformat(text).strftime('%d.%m.%Y')


def _format_reading(value: float) -> str:
    """Like ``_stand_text`` in NebenkostenFix: three decimals, no trailing zeros, with a comma."""
    return f'{value:.3f}'.rstrip('0').rstrip('.').replace('.', ',')


def _name_core(filename: str) -> str:
    """The filename as NebenkostenFix stores it, only without separators and extension.

    ``ablage.sanitize_name`` replaces umlauts and turns everything except
    a-z, A-Z, 0-9 into an underscore; "Stadtwerke-Gäste 2025.pdf" becomes
    "Stadtwerke_Gaeste_2025.pdf". The comparison ignores underscores and
    case.
    """
    stem = Path(filename).stem
    for umlaut, replacement in (
        ('ä', 'ae'),
        ('ö', 'oe'),
        ('ü', 'ue'),
        ('Ä', 'Ae'),
        ('Ö', 'Oe'),
        ('Ü', 'Ue'),
        ('ß', 'ss'),
    ):
        stem = stem.replace(umlaut, replacement)
    return re.sub(r'[^a-z0-9]', '', stem.lower())


def _without_none(fields: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in fields.items() if v is not None}


ReceiptPath = Annotated[
    str,
    Field(
        description=(
            'Pfad der Belegdatei auf diesem Rechner (PDF, JPEG, PNG, WebP oder HEIC), z. B. ~/Belege/wasser-2025.pdf'
        )
    ),
]

PAYMENT_TYPES_HELP = (
    'Miete: Kaltmiete. Nebenkostenvorauszahlung: der monatliche NK-Abschlag; eine Warmmiete '
    'teilst du in beide auf (Soll siehe get_advance_payment_status). Nebenkostenzahlung: '
    'Nachzahlung auf eine Abrechnung oder Guthabenauszahlung negativ, jeweils mit statement_id. '
    'Kaution: Mietsicherheit.'
)
SETTLEMENT_PAYMENT = 'Nebenkostenzahlung'
StatementId = Annotated[
    int | None,
    Field(
        description=(
            'Abrechnung, auf die diese Nachzahlung (oder ein ausgezahltes Guthaben, negativ) geht, siehe '
            'list_statements. Nur bei Nebenkostenzahlung; ohne sie zählt die Zahlung in der Abrechnung nicht '
            'als bezahlt.'
        )
    ),
]


def _outstanding_text(outstanding: Decimal) -> str:
    """Positive: the tenant still owes; negative: a credit the landlord still pays out (zahlungsstand.py)."""
    if outstanding < 0:
        return f'{format_money(-outstanding)} € Guthaben auszuzahlen'
    return f'{format_money(outstanding)} € offen'


def _statement_text(statement: dict[str, Any]) -> str:
    period = f'{_format_date(statement["start_date"])} bis {_format_date(statement["end_date"])}'
    outstanding = as_amount(statement.get('offen_betrag'))
    if outstanding is None:
        return f'Abrechnung {statement["id"]} ({period})'
    return f'Abrechnung {statement["id"]} ({period}, {_outstanding_text(outstanding)})'


def _unlinked_concerns(tenant_statements: list[dict[str, Any]]) -> list[str]:
    """A settlement payment without a statement does not count as paid anywhere."""
    if not tenant_statements:
        return []
    return [
        'Die Zahlung wird keiner Abrechnung zugeordnet und zählt dort nicht als bezahlt. Abrechnungen dieses '
        f'Mieters: {"; ".join(_statement_text(s) for s in tenant_statements)}. Gib die passende als '
        'statement_id mit.'
    ]


def _outstanding_concerns(statement: dict[str, Any], amount: Decimal) -> list[str]:
    """The payment is not the amount still outstanding on its statement."""
    # Old statements without a version have no payment status.
    outstanding = as_amount(statement.get('offen_betrag'))
    if outstanding is None or amount == outstanding:
        return []
    prefix = f'In Abrechnung {statement["id"]} sind {_outstanding_text(outstanding)}'
    if outstanding == 0:
        return [
            f'Abrechnung {statement["id"]} ist schon beglichen; die Zahlung über {format_money(amount)} € wäre zu viel.'
        ]
    if outstanding < 0 < amount:
        return [
            f'{prefix}, die Zahlung ist aber positiv ({format_money(amount)} €). Eine Auszahlung wird negativ '
            f'erfasst ({format_money(outstanding)}).'
        ]
    if amount < 0 < outstanding:
        return [
            f'{prefix}, die Zahlung ist aber negativ ({format_money(amount)} €). Negativ wird nur ein '
            'ausgezahltes Guthaben erfasst.'
        ]
    # Same sign: compare what is paid (or paid out) with what is due.
    paid, due = abs(amount), abs(outstanding)
    difference = f'{format_money(abs(paid - due))} € {"mehr" if paid > due else "weniger"}'
    payment = 'die Auszahlung' if outstanding < 0 else 'die Zahlung'
    return [f'{prefix}, {payment} ist {format_money(paid)} € ({difference}).']


def register(server: MCPServer, nkf: NkfClient, require_interaction: bool = True) -> None:
    meta = REQUIRE_USER_INTERACTION if require_interaction else None
    receipt_dir = nkf.config.receipt_dir

    def _read_receipt(path: str) -> Receipt:
        return read_receipt(path, receipt_dir)

    def _fingerprint(receipt: Receipt | None) -> str | None:
        # For the approval code: different content under the same path is a different receipt.
        return hashlib.sha256(receipt.content).hexdigest() if receipt is not None else None

    # --- Payment ------------------------------------------------------------

    @report_errors
    async def precheck_payment(
        tenant_id: int, amount: Decimal, payment_date: str, payment_type: str, statement_id: int | None, ctx: Context
    ) -> Elicit[ConfirmationPrompt] | PrecheckResult:
        # The API would silently drop billing_report_id for any other type.
        if statement_id is not None and payment_type != SETTLEMENT_PAYMENT:
            raise ToolError(f'statement_id gibt es nur bei {SETTLEMENT_PAYMENT}, nicht bei {payment_type}.')
        concerns = [
            f'Eine Zahlung über {format_money(amount)} € ({payment_type}) vom {_format_date(payment_date)} gibt es '
            f'schon für diesen Mieter (Zahlung {z["id"]}).'
            for z in await nkf.get('/api/payments')
            if z['tenant_id'] == tenant_id
            and z['payment_date'] == payment_date
            and z['type'] == payment_type
            and as_amount(z['amount']) == amount
        ]
        if payment_type == SETTLEMENT_PAYMENT:
            own = [s for s in await nkf.get('/api/billing/reports') if s['tenant_id'] == tenant_id]
            if statement_id is None:
                concerns += _unlinked_concerns(own)
            else:
                statement = next((s for s in own if s['id'] == statement_id), None)
                if statement is None:
                    # Also for a locked tenant: the API leaves their statements out of the list.
                    raise ToolError(
                        f'Abrechnung {statement_id} gibt es für Mieter {tenant_id} nicht (siehe list_statements).'
                    )
                concerns += _outstanding_concerns(statement, amount)
        summary = (
            f'Neue Zahlung: {format_money(amount)} € ({payment_type}) vom {_format_date(payment_date)}, '
            f'Mieter {tenant_id}'
        )
        if statement_id is not None:
            summary += f', Abrechnung {statement_id}'
        return _ask(
            ctx, summary + '.', concerns, ('payment', tenant_id, amount, payment_date, payment_type, statement_id)
        )

    @tool(server, 'Zahlung erfassen', CREATE_ONLY, meta=meta)
    @report_errors
    async def record_payment(
        tenant_id: TenantId,
        amount: Amount,
        payment_date: Annotated[IsoDate, Field(description='Zahlungseingang, YYYY-MM-DD')],
        payment_type: Annotated[PaymentType, Field(description=PAYMENT_TYPES_HELP)],
        decision: Annotated[ElicitationResult[ConfirmationPrompt], Resolve(precheck_payment)],
        statement_id: StatementId = None,
        approval_code: ApprovalCode = None,
    ) -> RecordResult:
        """Legt in NebenkostenFix eine eingegangene Zahlung eines Mieters an (schreibt, ändert nichts
        Bestehendes). Gibt es dieselbe Zahlung schon, wird vorher nachgefragt. Eine Nachzahlung gehört an ihre
        Abrechnung (statement_id); fehlt sie oder weicht der Betrag vom offenen Betrag ab, wird nachgefragt."""
        result = _decide(decision, approval_code)
        if isinstance(result, RecordResult):
            return result
        return _saved(
            await nkf.post(
                '/api/payments',
                _without_none(
                    {
                        'tenant_id': tenant_id,
                        'amount': format_money(amount),
                        'type': payment_type,
                        'payment_date': payment_date,
                        'billing_report_id': statement_id,
                    }
                ),
            ),
            result,
        )

    # --- Meter reading -------------------------------------------------------

    @report_errors
    async def precheck_reading(
        meter_id: int,
        reading_date: str,
        value: float,
        value_off_peak: float | None,
        interim_reading: bool,
        ctx: Context,
    ) -> Elicit[ConfirmationPrompt] | PrecheckResult:
        concerns: list[str] = []
        # The same rules NebenkostenFix uses to answer 409 "nachfrage"
        # when saving (app.py, _ablesung_pruefen): here beforehand, so that
        # the human decides and not the model.
        readings = [s for s in await nkf.get('/api/readings') if s['meter_id'] == meter_id]
        same_day = [s for s in readings if s['reading_date'] == reading_date]
        if same_day:
            # With the IDs: after saving, the text reads differently, so a
            # repeated consent no longer matches (Review S4-6).
            ids = ', '.join(str(s['id']) for s in same_day)
            concerns.append(
                f'Für diesen Zähler gibt es am {_format_date(reading_date)} schon einen Stand (Stand {ids}).'
            )
        previous = max(
            (s for s in readings if s['reading_date'] < reading_date), key=lambda s: s['reading_date'], default=None
        )
        following = min(
            (s for s in readings if s['reading_date'] > reading_date), key=lambda s: s['reading_date'], default=None
        )
        for new, field, name in ((value, 'value', 'Stand'), (value_off_peak, 'value_nt', 'NT-Stand')):
            earlier = previous.get(field) if previous else None
            later = following.get(field) if following else None
            if new is not None and previous is not None and earlier is not None and new < earlier:
                concerns.append(
                    f'Der {name} {_format_reading(new)} ist kleiner als der vorige vom '
                    f'{_format_date(previous["reading_date"])} ({_format_reading(earlier)}). Ein Zähler '
                    'läuft nicht rückwärts.'
                )
            if new is not None and following is not None and later is not None and new > later:
                concerns.append(
                    f'Der {name} {_format_reading(new)} ist größer als der folgende vom '
                    f'{_format_date(following["reading_date"])} ({_format_reading(later)}).'
                )
        plausibility = await nkf.post(
            '/api/readings/check_plausibility', {'meter_id': meter_id, 'reading_date': reading_date, 'value': value}
        )
        if plausibility.get('status') == 'warning' and plausibility.get('message'):
            concerns.append(plausibility['message'])
        new_values = _format_reading(value) + (
            f' / NT {_format_reading(value_off_peak)}' if value_off_peak is not None else ''
        )
        return _ask(
            ctx,
            f'Neuer Zählerstand {new_values} am {_format_date(reading_date)}, Zähler {meter_id}.',
            concerns,
            ('meter_reading', meter_id, reading_date, value, value_off_peak, interim_reading),
        )

    @tool(server, 'Zählerstand erfassen', CREATE_ONLY, meta=meta)
    @report_errors
    async def record_meter_reading(
        meter_id: MeterId,
        reading_date: IsoDate,
        value: Annotated[float, Field(ge=0, description='abgelesener Stand (beim Zweitarifzähler: Haupttarif)')],
        decision: Annotated[ElicitationResult[ConfirmationPrompt], Resolve(precheck_reading)],
        value_off_peak: Annotated[float | None, Field(ge=0, description='Nebentarif, nur bei Zweitarifzählern')] = None,
        interim_reading: Annotated[
            bool, Field(description='Stand zum Datum eines Mieterwechsels (§ 9b HeizkostenV)')
        ] = False,
        approval_code: ApprovalCode = None,
    ) -> RecordResult:
        """Legt in NebenkostenFix einen abgelesenen Zählerstand an (schreibt, ändert nichts Bestehendes). Vorher
        wird geprüft, ob er zu den bisherigen Ständen passt; bei Bedenken wird nachgefragt. Zähler selbst legt
        nur der Mensch an: gibt es keinen passenden Zähler, sag das, statt etwas anderes zu erfassen."""
        result = _decide(decision, approval_code)
        if isinstance(result, RecordResult):
            return result
        payload = _without_none(
            {
                'meter_id': meter_id,
                'reading_date': reading_date,
                'value': value,
                'value_nt': value_off_peak,
                'ablesungsart': 'zwischenablesung' if interim_reading else 'ablesung',
                'trotzdem': True if result.despite_concerns else None,
            }
        )
        try:
            return _saved(await nkf.post('/api/readings', payload), result)
        except NkfConfirmationRequired as confirmation:
            # A confirmation request the precheck does not know. A second call
            # would not find it either; say so honestly instead of looping.
            return RecordResult(
                saved=False,
                id=None,
                concerns=[str(confirmation)],
                note=(
                    'Nicht gespeichert. NebenkostenFix fragt nach, und diese Rückfrage kann der MCP-Server nicht '
                    'weitergeben. Zeig sie dem Menschen; er kann den Stand in NebenkostenFix selbst eintragen und dort '
                    'bestätigen.'
                ),
            )

    # --- Invoice ------------------------------------------------------------

    @report_errors
    async def precheck_invoice(
        property_id: int,
        cost_category_id: int,
        amount: Decimal,
        start_date: str,
        end_date: str,
        invoice_number: str | None,
        invoice_date: str | None,
        description: str | None,
        provider_id: int | None,
        apartment_id: int | None,
        invoice_document_id: int | None,
        heating_system_id: int | None,
        heating_cost_type: str | None,
        base_price: Decimal | None,
        section_35a_category: str | None,
        labor_cost: Decimal | None,
        co2_cost: Decimal | None,
        co2_emission_kg: float | None,
        receipt_path: str | None,
        ctx: Context,
    ) -> Elicit[ConfirmationPrompt] | PrecheckResult:
        # First what can never be right, then the question to the human:
        # otherwise they say "yes, save" and get an error afterwards.
        if end_date < start_date:
            raise ToolError(f'Das Ende des Zeitraums ({end_date}) liegt vor dem Beginn ({start_date}).')
        if receipt_path and invoice_document_id is not None:
            raise ToolError(
                'Entweder receipt_path (eigener Beleg) oder invoice_document_id (vorhandener '
                'Sammelbeleg), nicht beides.'
            )
        if (section_35a_category is None) != (labor_cost is None):
            raise ToolError(
                'section_35a_category und labor_cost gehören zusammen (§ 35a EStG): beide angeben oder keins.'
            )
        receipt = _read_receipt(receipt_path) if receipt_path else None
        if invoice_document_id is not None and not any(
            b['id'] == invoice_document_id and b.get('property_id') == property_id
            for b in await nkf.get('/api/invoice_documents')
        ):
            raise ToolError(
                f'Sammelbeleg {invoice_document_id} gehört nicht zur Immobilie {property_id} '
                '(siehe list_invoice_documents).'
            )
        if apartment_id is not None and not any(
            w['id'] == apartment_id for w in await nkf.get(f'/api/properties/{property_id}/apartments')
        ):
            raise ToolError(f'Wohnung {apartment_id} gehört nicht zur Immobilie {property_id} (siehe list_apartments).')

        new_number = (invoice_number or '').strip()
        concerns = []
        for r in await nkf.get('/api/invoices'):
            if r['property_id'] != property_id:
                continue
            number = (r.get('invoice_number') or '').strip()
            same_number = bool(new_number) and number == new_number
            # With the apartment: the same maintenance for ten apartments is not a duplicate.
            same_item = (
                r['category_id'] == cost_category_id
                and as_amount(r['amount']) == amount
                and r.get('start_date') == start_date
                and r.get('end_date') == end_date
                and r.get('apartment_id') == apartment_id
            )
            if same_number or same_item:
                cost_category = r.get('category_name') or f'Kostenart {r["category_id"]}'
                parts = [
                    cost_category,
                    f'{format_money(r["amount"])} €',
                    f'{r.get("start_date")} bis {r.get("end_date")}',
                ]
                if number:
                    parts.append(f'Nr. {number}')
                concerns.append(f'Rechnung {r["id"]} ({", ".join(parts)}) gibt es schon.')
        new = f'Neue Rechnung: Kostenart {cost_category_id}, {format_money(amount)} €, {start_date} bis {end_date}'
        if new_number:
            new += f', Nr. {new_number}'
        return _ask(
            ctx,
            new + '.',
            concerns,
            (
                'invoice',
                property_id,
                cost_category_id,
                amount,
                start_date,
                end_date,
                invoice_number,
                invoice_date,
                description,
                provider_id,
                apartment_id,
                invoice_document_id,
                heating_system_id,
                heating_cost_type,
                base_price,
                section_35a_category,
                labor_cost,
                co2_cost,
                co2_emission_kg,
                receipt_path,
                _fingerprint(receipt),
            ),
        )

    @tool(server, 'Rechnung erfassen', CREATE_ONLY, meta=meta)
    @report_errors
    async def record_invoice(
        property_id: PropertyId,
        cost_category_id: Annotated[int, Field(description='ID der Kostenart, siehe Werkzeug list_cost_categories')],
        amount: Amount,
        start_date: Annotated[IsoDate, Field(description='Beginn des Leistungszeitraums, YYYY-MM-DD')],
        end_date: Annotated[IsoDate, Field(description='Ende des Leistungszeitraums, YYYY-MM-DD')],
        decision: Annotated[ElicitationResult[ConfirmationPrompt], Resolve(precheck_invoice)],
        invoice_number: Annotated[str | None, Field(max_length=100)] = None,
        invoice_date: Annotated[IsoDate | None, Field(description='Datum auf der Rechnung, YYYY-MM-DD')] = None,
        description: Annotated[str | None, Field(max_length=500)] = None,
        provider_id: Annotated[int | None, Field(description='siehe Werkzeug list_providers')] = None,
        apartment_id: Annotated[
            int | None, Field(description='nur wenn die Rechnung allein eine Wohnung betrifft')
        ] = None,
        invoice_document_id: Annotated[
            int | None, Field(description='Beleg, auf dem diese Rechnung steht, siehe list_invoice_documents')
        ] = None,
        heating_system_id: Annotated[
            int | None, Field(description='bei Heizkosten: die Anlage, zusammen mit heating_cost_type')
        ] = None,
        heating_cost_type: Annotated[
            HeatingCostType | None, Field(description='bei Heizkosten: Posten nach § 7 Abs. 2 HeizkostenV')
        ] = None,
        base_price: OptionalAmount = None,
        section_35a_category: Annotated[
            Section35aCategory | None,
            Field(description='§ 35a EStG: haushaltsnahe Dienstleistung oder Handwerkerleistung, nur mit labor_cost'),
        ] = None,
        labor_cost: Annotated[
            OptionalAmount, Field(description='Arbeitskosten nach § 35a EStG, nur mit section_35a_category')
        ] = None,
        co2_cost: Annotated[OptionalAmount, Field(description='CO2-Kosten auf der Brennstoffrechnung')] = None,
        co2_emission_kg: Annotated[float | None, Field(ge=0)] = None,
        receipt_path: Annotated[
            ReceiptPath | None,
            Field(
                description='Original der Rechnung als Datei; wird hochgeladen und mit der Rechnung verknüpft. '
                'Steht die Rechnung auf einem Beleg mit mehreren Rechnungen: upload_invoice_document '
                'und invoice_document_id nutzen.'
            ),
        ] = None,
        approval_code: ApprovalCode = None,
    ) -> RecordResult:
        """Legt in NebenkostenFix eine Rechnung (Kosten einer Immobilie) an, auf Wunsch mit dem Beleg als Datei
        (schreibt, ändert nichts Bestehendes). Gibt es schon eine Rechnung mit derselben Nummer oder mit gleicher
        Kostenart, gleichem Betrag, Zeitraum und Wohnung, wird vorher nachgefragt. Den Inhalt eines Belegs liest du
        selbst; NebenkostenFix hat keine Texterkennung."""
        result = _decide(decision, approval_code)
        if isinstance(result, RecordResult):
            return result
        receipt = _read_receipt(receipt_path) if receipt_path else None
        payload = _without_none(
            {
                'property_id': property_id,
                'category_id': cost_category_id,
                'amount': format_money(amount),
                'start_date': start_date,
                'end_date': end_date,
                'invoice_number': (invoice_number or '').strip() or None,
                'rechnungsdatum': invoice_date,
                'description': description,
                'provider_id': provider_id,
                'apartment_id': apartment_id,
                'invoice_document_id': invoice_document_id,
                'heizungsanlage_id': heating_system_id,
                'heizkostenart': heating_cost_type,
                'grundpreis': format_money(base_price),
                'art_35a': section_35a_category,
                'lohnanteil': format_money(labor_cost),
                'co2_kosten': format_money(co2_cost),
                'co2_emission_kg': co2_emission_kg,
            }
        )
        # Never rechnungsdatum_fuer_beleg: it would write into invoices that
        # already exist, and is locked for API keys.
        if receipt is not None:
            response = await nkf.post('/api/invoices', form=payload, files=receipt.as_upload())
        else:
            response = await nkf.post('/api/invoices', payload)
        return _saved(response, result)

    # --- Invoice document -----------------------------------------------------

    @report_errors
    async def precheck_invoice_document(
        property_id: int, receipt_path: str, description: str | None, ctx: Context
    ) -> Elicit[ConfirmationPrompt] | PrecheckResult:
        receipt = _read_receipt(receipt_path)
        stem = _name_core(receipt.filename)
        concerns = [
            f'Sammelbeleg {b["id"]} „{b.get("filename")}“ gibt es für diese Immobilie schon.'
            for b in await nkf.get('/api/invoice_documents')
            if b.get('property_id') == property_id and _name_core(b.get('filename') or '') == stem
        ]
        return _ask(
            ctx,
            f'Neuer Sammelbeleg „{receipt.filename}“ für Immobilie {property_id}.',
            concerns,
            ('invoice_document', property_id, receipt_path, description, _fingerprint(receipt)),
        )

    @tool(server, 'Sammelbeleg hochladen', CREATE_ONLY, meta=meta)
    @report_errors
    async def upload_invoice_document(
        property_id: PropertyId,
        receipt_path: ReceiptPath,
        decision: Annotated[ElicitationResult[ConfirmationPrompt], Resolve(precheck_invoice_document)],
        description: Annotated[str | None, Field(max_length=1000)] = None,
        approval_code: ApprovalCode = None,
    ) -> RecordResult:
        """Lädt einen Beleg mit mehreren Rechnungen (z. B. Jahresabrechnung der Stadtwerke) zu einer Immobilie
        hoch (schreibt, ändert nichts Bestehendes). Danach legst du jede Rechnung darauf mit record_invoice und
        invoice_document_id an."""
        result = _decide(decision, approval_code)
        if isinstance(result, RecordResult):
            return result
        receipt = _read_receipt(receipt_path)
        response = await nkf.post(
            '/api/invoice_documents',
            form=_without_none({'property_id': property_id, 'description': description}),
            files=receipt.as_upload(),
        )
        recorded = _saved(response, result)
        if recorded.id is not None:
            recorded.note = (
                f'{recorded.note} Lege die Rechnungen dieses Belegs mit record_invoice und '
                f'invoice_document_id={recorded.id} an.'
            )
        return recorded
