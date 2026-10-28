"""Read tools around the annual statement: deadlines, data quality, suggestions, statements."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from nkfix_mcp.client import NkfClient
from nkfix_mcp.formatting import ItemList, format_money, truncate
from nkfix_mcp.tools import (
    MAX_ITEMS,
    READ_ONLY,
    IsoDate,
    PropertyId,
    report_errors,
    tool,
)

BillingYear = Annotated[
    int,
    Field(
        ge=2000,
        le=2100,
        description=(
            'Abrechnungsjahr der Immobilie, das in diesem Kalenderjahr beginnt (siehe billing_year_start), z. B. 2025'
        ),
    ),
]


class Deadline(BaseModel):
    date: str
    kind: str
    title: str


class DataQuality(BaseModel):
    meters: list[dict[str, Any]]
    warnings: list[dict[str, Any]]


class SuggestedCostCategory(BaseModel):
    cost_category_id: int
    cost_category: str | None
    start_date: str | None
    end_date: str | None
    invoice_count: int | None


class BillingSuggestion(BaseModel):
    tenant_id: int
    tenant: str | None
    apartment: str | None
    start_date: str | None
    end_date: str | None
    cost_categories: list[SuggestedCostCategory]


class Statement(BaseModel):
    id: int
    tenant_id: int | None
    tenant: str | None
    apartment: str | None
    property_id: int | None
    property: str | None
    start_date: str | None
    end_date: str | None
    created_at: str | None
    outdated: bool | None = Field(description='Daten haben sich seit der Abrechnung geändert')
    delivered_at: str | None
    outstanding_amount: str | None
    payment_status: str | None


class LineItem(BaseModel):
    cost_category: str | None
    allocation: str | None
    period: str | None
    invoice_id: int | None
    invoice_amount: str | None
    tenant_share: str | None
    explanation: str | None


class StatementDetails(BaseModel):
    id: int
    tenant_id: int | None
    tenant: str | None
    apartment: str | None
    property: str | None
    start_date: str | None
    end_date: str | None
    version: int | None
    outdated: bool | None
    total_costs: str | None
    advance_payments: str | None
    balance: str | None = Field(description='positiv: Nachzahlung des Mieters, negativ: Guthaben')
    line_items: list[LineItem]
    warnings: list[str]
    new_advance_payment: dict[str, str | None] | None = Field(
        description=(
            'vorgeschlagene neue monatliche Vorauszahlung, oder nur ein reason, warum es keinen Vorschlag gibt'
        )
    )
    statement_deadline: str | None
    objection_deadline: str | None
    delivered_at: str | None
    outstanding_amount: str | None
    payment_status: str | None


def _statement(raw: dict[str, Any]) -> Statement:
    return Statement(
        id=raw['id'],
        tenant_id=raw.get('tenant_id'),
        tenant=raw.get('tenant_name'),
        apartment=raw.get('apartment_name'),
        property_id=raw.get('property_id'),
        property=raw.get('property_name'),
        start_date=raw.get('start_date'),
        end_date=raw.get('end_date'),
        created_at=raw.get('created_at'),
        outdated=raw.get('veraltet'),
        delivered_at=raw.get('zugestellt_am'),
        outstanding_amount=format_money(raw.get('offen_betrag')),
        payment_status=raw.get('zahlungsstand'),
    )


def _statement_details(statement_id: int, raw: dict[str, Any]) -> StatementDetails:
    # Statements without a snapshot (before NK-061) return a flat result.
    result = raw.get('ergebnis') or raw
    advance_payment = raw.get('vorauszahlung')
    return StatementDetails(
        id=statement_id,
        tenant_id=raw.get('tenant_id'),
        tenant=result.get('tenant_name'),
        apartment=result.get('apartment'),
        property=result.get('property'),
        start_date=raw.get('start_date'),
        end_date=raw.get('end_date'),
        version=raw.get('version'),
        outdated=raw.get('veraltet'),
        total_costs=format_money(result.get('total_amount')),
        advance_payments=format_money(result.get('prepaid_amount')),
        balance=format_money(result.get('balance')),
        line_items=[
            LineItem(
                cost_category=p.get('category'),
                allocation=p.get('billing_type'),
                period=p.get('period'),
                invoice_id=p.get('invoice_id'),
                invoice_amount=format_money(p.get('total_amount')),
                tenant_share=format_money(p.get('tenant_cost')),
                explanation=p.get('description'),
            )
            for p in result.get('line_items') or []
        ],
        warnings=[str(w) for w in result.get('warnings') or []],
        new_advance_payment=_new_advance_payment(advance_payment),
        statement_deadline=raw.get('frist_ende'),
        objection_deadline=raw.get('einwendungsfrist_ende'),
        delivered_at=raw.get('zugestellt_am'),
        outstanding_amount=format_money(raw.get('offen_betrag')),
        payment_status=raw.get('zahlungsstand'),
    )


def _new_advance_payment(raw: dict[str, Any] | None) -> dict[str, str | None] | None:
    if not raw:
        return None
    if raw.get('neu') is None:
        # No adjustment possible, e.g. because the tenant has moved out: only the reason.
        return {'reason': raw.get('grund')}
    return {
        'previous': format_money(raw.get('bisher')),
        'new': format_money(raw.get('neu')),
        'effective_from': raw.get('ab'),
    }


def register(server: MCPServer, nkf: NkfClient) -> None:
    @tool(server, 'Fristen', READ_ONLY)
    @report_errors
    async def list_deadlines(
        until: Annotated[IsoDate | None, Field(description='nur Termine bis zu diesem Datum (YYYY-MM-DD)')] = None,
    ) -> ItemList[Deadline]:
        """Anstehende Termine und Fristen: Abrechnungsfristen, Einwendungsfristen, Eichfristen."""
        events = (await nkf.get('/api/fristen')).get('termine') or []
        return truncate(
            [
                Deadline(date=t['datum'], kind=t['art'], title=t['titel'])
                for t in events
                if until is None or t['datum'] <= until
            ],
            MAX_ITEMS,
        )

    @tool(server, 'Datenqualität', READ_ONLY)
    @report_errors
    async def get_data_quality(property_id: PropertyId) -> DataQuality:
        """Prüft den Datenbestand einer Immobilie: veraltete Zählerstände und fehlende Zähler."""
        raw = await nkf.get(f'/api/analytics/data-quality/{property_id}')
        return DataQuality(
            meters=[
                {
                    'meter_id': z.get('meter_id'),
                    'number': z.get('meter_number'),
                    'cost_category': z.get('category_name'),
                    'apartment': z.get('apartment_name'),
                    'last_reading_date': z.get('last_reading_date'),
                    'days_since_reading': z.get('days_since_reading'),
                    'reading_count': z.get('reading_count'),
                    'status': z.get('status'),
                }
                for z in raw.get('meters') or []
            ],
            warnings=[
                {
                    'type': w.get('type'),
                    'severity': w.get('severity'),
                    'message': w.get('message'),
                    'apartment': w.get('apartment_name'),
                    'cost_category': w.get('category_name'),
                    'tenant': w.get('tenant_name'),
                }
                for w in raw.get('warnings') or []
            ],
        )

    @tool(server, 'Jahresabrechnung (Stand)', READ_ONLY)
    @report_errors
    async def get_billing_year_status(property_id: PropertyId, year: BillingYear) -> dict[str, Any]:
        """Stand des Jahresabschlusses einer Immobilie: Kosten je Kostenart im Abrechnungsjahr (zeitanteilig) mit
        Vorjahr, fehlende Ablesungen, Mieter mit ihren Abrechnungen, offene Punkte (open_issues) und die
        Abrechnungsfrist."""
        raw = await nkf.get('/api/jahresabrechnung', property_id=property_id, jahr=year)
        return {
            'property_id': raw.get('property_id'),
            'property': raw.get('immobilie'),
            'year': raw.get('jahr'),
            'start_date': raw.get('von'),
            'end_date': raw.get('bis'),
            'statement_deadline': raw.get('frist_ende'),
            'deadline_days': raw.get('frist_tage'),
            'deadline_exceeded': raw.get('frist_ueberschritten'),
            'cost_categories': [
                {
                    'id': k.get('id'),
                    'name': k.get('name'),
                    'invoice_count': k.get('anzahl'),
                    'total': format_money(k.get('summe')),
                    'total_previous_year': format_money(k.get('summe_vorjahr')),
                    'change_percent': k.get('aenderung'),
                    'jump': k.get('sprung'),
                    'state': k.get('zustand'),
                }
                for k in raw.get('kostenarten') or []
            ],
            'readings': [
                {
                    'meter_id': a.get('id'),
                    'number': a.get('nummer'),
                    'cost_category': a.get('kostenart'),
                    'apartment': a.get('wohnung'),
                    'last_reading_date': a.get('letzte'),
                    'cutoff_ok': a.get('stichtag_ok'),
                    'missing_change_readings': a.get('wechsel_fehlen'),
                }
                for a in raw.get('ablesungen') or []
            ],
            'tenants': [
                {
                    'id': m.get('id'),
                    'name': m.get('name'),
                    'apartment': m.get('wohnung'),
                    'start_date': m.get('von'),
                    'end_date': m.get('bis'),
                    'statement_id': m.get('abrechnung_id'),
                    'delivered_at': m.get('zugestellt_am'),
                }
                for m in raw.get('mieter') or []
            ],
            'open_issues': [
                {'type': p.get('art'), 'message': p.get('text'), 'action': p.get('tun')}
                for p in raw.get('pruefstand') or []
            ],
        }

    @tool(server, 'Abrechnungsvorschläge', READ_ONLY)
    @report_errors
    async def list_billing_suggestions() -> ItemList[BillingSuggestion]:
        """Mieter, für die eine Nebenkostenabrechnung fällig ist, mit vorgeschlagenem Zeitraum."""
        return truncate(
            [
                BillingSuggestion(
                    tenant_id=v['tenant_id'],
                    tenant=v.get('tenant_name'),
                    apartment=v.get('apartment_name'),
                    start_date=v.get('suggested_start'),
                    end_date=v.get('suggested_end'),
                    cost_categories=[
                        SuggestedCostCategory(
                            cost_category_id=k['category_id'],
                            cost_category=k.get('category_name'),
                            start_date=k.get('suggested_start'),
                            end_date=k.get('suggested_end'),
                            invoice_count=k.get('invoice_count'),
                        )
                        for k in v.get('categories') or []
                    ],
                )
                for v in await nkf.get('/api/billing/suggestions')
            ],
            MAX_ITEMS,
        )

    @tool(server, 'Abrechnungen', READ_ONLY)
    @report_errors
    async def list_statements(
        tenant_id: Annotated[int | None, Field(description='nur Abrechnungen dieses Mieters')] = None,
        property_id: Annotated[int | None, Field(description='nur Abrechnungen dieser Immobilie')] = None,
    ) -> ItemList[Statement]:
        """Erstellte Nebenkostenabrechnungen mit Zeitraum, Zustellung und offenem Betrag."""
        return truncate(
            [
                a
                for a in (_statement(raw) for raw in await nkf.get('/api/billing/reports'))
                if (tenant_id is None or a.tenant_id == tenant_id)
                and (property_id is None or a.property_id == property_id)
            ],
            MAX_ITEMS,
        )

    @tool(server, 'Abrechnung im Detail', READ_ONLY)
    @report_errors
    async def get_statement(
        statement_id: Annotated[int, Field(description='ID der Abrechnung, siehe Werkzeug list_statements')],
    ) -> StatementDetails:
        """Ergebnis einer Abrechnung: Kosten je Position mit Anteil des Mieters, Vorauszahlungen, Saldo,
        Anpassung der Vorauszahlung und Fristen."""
        return _statement_details(statement_id, await nkf.get(f'/api/billing/reports/{statement_id}/details'))
