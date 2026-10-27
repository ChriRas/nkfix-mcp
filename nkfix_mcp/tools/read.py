"""Read tools: the data of a NebenkostenFix instance, without changing anything.

Every tool translates the UI-shaped fields of the API (a mix of English
and German) into fields of its own, in English. If upstream renames a
field, the contract tests fail at exactly that spot.

The API barely filters (``/api/invoices`` returns all invoices); the
filtering happens here.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from nkfix_mcp.client import NkfClient
from nkfix_mcp.formatting import ItemList, format_money, in_year, total, truncate
from nkfix_mcp.tools import READ_ONLY, IsoDate, report_errors, tool

MAX_ITEMS = 100
# Measured (review S2-6): 100 invoices are about 20,000 tokens, Claude Code warns from 10,000.
MAX_INVOICES = 40
READINGS_PER_METER = 10

PaymentType = Literal['Miete', 'Nebenkostenvorauszahlung', 'Kaution', 'Nebenkostenzahlung']

PropertyId = Annotated[int, Field(description='ID der Immobilie, siehe Werkzeug list_properties')]
TenantId = Annotated[int, Field(description='ID des Mieters, siehe Werkzeug list_tenants')]
BillingYear = Annotated[int, Field(ge=2000, le=2100, description=(
    'Abrechnungsjahr der Immobilie, das in diesem Kalenderjahr beginnt (siehe billing_year_start), '
    'z. B. 2025'))]


class Property(BaseModel):
    id: int
    name: str
    billing_year_start: str | None = Field(description='Tag und Monat, an dem das Abrechnungsjahr beginnt')
    standalone: bool = Field(description='Einfamilienhaus bzw. einzeln vermietetes Objekt')
    is_example: bool


class Apartment(BaseModel):
    id: int
    name: str
    area_sqm: float | None
    active: bool | None
    usage_type: str | None
    owner_occupied: bool | None
    self_supplied: bool | None


class Tenant(BaseModel):
    id: int
    name: str
    property_id: int | None
    property: str | None
    apartment_id: int | None
    apartment: str | None
    move_in: str | None
    move_out: str | None
    billed_until: str | None
    is_example: bool


class HeatingSystem(BaseModel):
    id: int
    name: str
    supplies: str | None
    consumption_share_percent: float | None
    hot_water_method: str | None


class CostCategory(BaseModel):
    id: int
    name: str
    allocation: str | None = Field(description='Umlageschlüssel, z. B. qm, personen, verbrauch')
    requires_meter: bool
    betrkv_number: int | None
    betrkv_label: str | None
    invoice_count: int | None
    meter_count: int | None


class Provider(BaseModel):
    id: int
    name: str


class Invoice(BaseModel):
    id: int
    property_id: int
    property: str | None
    cost_category_id: int
    cost_category: str | None
    amount: str | None
    start_date: str | None
    end_date: str | None
    invoice_date: str | None
    invoice_number: str | None
    description: str | None
    provider_id: int | None
    provider: str | None
    apartment_id: int | None = Field(description='nur bei Rechnungen, die eine einzelne Wohnung betreffen')
    invoice_document_id: int | None
    has_receipt: bool
    heating_system_id: int | None
    heating_cost_type: str | None
    base_price: str | None
    labor_cost: str | None
    co2_cost: str | None


class Invoices(ItemList[Invoice]):
    total: str = Field(description='Summe der vollen Rechnungsbeträge aller passenden Rechnungen, auch der '
                                   'weggelassenen; nicht zeitanteilig. Die Kosten eines Jahres je Kostenart '
                                   'liefert get_billing_year_status.')


class InvoiceDocument(BaseModel):
    id: int
    property_id: int | None
    filename: str | None
    description: str | None
    uploaded_at: str | None


class Reading(BaseModel):
    id: int
    reading_date: str
    value: float | None
    value_off_peak: float | None = Field(description='Nebentarif bei Zweitarifzählern')
    reading_type: str | None = Field(description='ablesung oder zwischenablesung (Mieterwechsel)')


class Meter(BaseModel):
    id: int
    number: str | None
    cost_category_id: int | None
    cost_category: str | None
    unit: str | None
    property_id: int | None
    apartment_id: int | None
    apartment: str | None = Field(description='leer bei Hauptzählern des Hauses')
    main_meter: bool
    dual_tariff: bool
    calibrated_until: str | None
    location: str | None
    replaced_by: int | None
    reading_count: int
    readings: list[Reading] = Field(description=f'neuester Stand zuerst; ohne meter_id nur der neueste, mit '
                                                f'meter_id die neuesten {READINGS_PER_METER}')


class Payment(BaseModel):
    id: int
    tenant_id: int
    tenant: str | None
    amount: str | None
    payment_date: str
    payment_type: str


class Payments(ItemList[Payment]):
    total: str


class Month(BaseModel):
    month: str
    expected: str | None
    actual: str | None
    difference: str | None
    arrears: bool | None
    is_current: bool | None = Field(description='laufender Monat: noch nicht fällig, zählt nicht als Rückstand')


class AdvancePayments(BaseModel):
    months: list[Month]
    outstanding: str | None = Field(description='Summe der Rückstände, über alle Monate')
    overpaid: str | None
    balance: str | None = Field(description='offen minus überzahlt; positiv: der Mieter ist im Rückstand')


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
    new_advance_payment: dict[str, str | None] | None = Field(description=(
        'vorgeschlagene neue monatliche Vorauszahlung, oder nur ein reason, warum es keinen Vorschlag gibt'))
    statement_deadline: str | None
    objection_deadline: str | None
    delivered_at: str | None
    outstanding_amount: str | None
    payment_status: str | None


def _tenant(raw: dict[str, Any], apartment_to_property: dict[int, int]) -> Tenant:
    return Tenant(
        id=raw['id'], name=raw['name'], property_id=apartment_to_property.get(raw.get('apartment_id')),
        property=raw.get('property_name'), apartment_id=raw.get('apartment_id'), apartment=raw.get('apartment_name'),
        move_in=raw.get('move_in_date'), move_out=raw.get('move_out_date'),
        billed_until=raw.get('last_billed_until'), is_example=bool(raw.get('ist_beispiel')))


def _invoice(raw: dict[str, Any]) -> Invoice:
    return Invoice(
        id=raw['id'], property_id=raw['property_id'], property=raw.get('property_name'),
        cost_category_id=raw['category_id'], cost_category=raw.get('category_name'), amount=format_money(raw['amount']),
        start_date=raw.get('start_date'), end_date=raw.get('end_date'), invoice_date=raw.get('rechnungsdatum'),
        invoice_number=raw.get('invoice_number'), description=raw.get('description'),
        provider_id=raw.get('provider_id'), provider=raw.get('provider_name'),
        apartment_id=raw.get('apartment_id'), invoice_document_id=raw.get('invoice_document_id'),
        has_receipt=bool(raw.get('document_path') or raw.get('invoice_document_id')),
        heating_system_id=raw.get('heizungsanlage_id'), heating_cost_type=raw.get('heizkostenart'),
        base_price=format_money(raw.get('grundpreis')), labor_cost=format_money(raw.get('lohnanteil')),
        co2_cost=format_money(raw.get('co2_kosten')))


def _reading(raw: dict[str, Any]) -> Reading:
    return Reading(id=raw['id'], reading_date=raw['reading_date'], value=raw.get('value'),
                   value_off_peak=raw.get('value_nt'), reading_type=raw.get('ablesungsart'))


def _meter(raw: dict[str, Any], readings: list[dict[str, Any]], count: int) -> Meter:
    latest = sorted(readings, key=lambda s: (s['reading_date'], s['id']), reverse=True)
    return Meter(
        id=raw['id'], number=raw.get('meter_number'), cost_category_id=raw.get('category_id'),
        cost_category=raw.get('category_name'), unit=raw.get('einheit'), property_id=raw.get('property_id'),
        apartment_id=raw.get('apartment_id'), apartment=raw.get('apartment_name'),
        main_meter=bool(raw.get('is_main_meter')), dual_tariff=bool(raw.get('has_dual_tariff')),
        calibrated_until=raw.get('geeicht_bis'), location=raw.get('ort'), replaced_by=raw.get('ersetzt_durch'),
        reading_count=len(readings), readings=[_reading(s) for s in latest[:count]])


def _payment(raw: dict[str, Any]) -> Payment:
    return Payment(id=raw['id'], tenant_id=raw['tenant_id'], tenant=raw.get('tenant_name'),
                   amount=format_money(raw['amount']), payment_date=raw['payment_date'], payment_type=raw['type'])


def _statement(raw: dict[str, Any]) -> Statement:
    return Statement(
        id=raw['id'], tenant_id=raw.get('tenant_id'), tenant=raw.get('tenant_name'),
        apartment=raw.get('apartment_name'), property_id=raw.get('property_id'), property=raw.get('property_name'),
        start_date=raw.get('start_date'), end_date=raw.get('end_date'), created_at=raw.get('created_at'),
        outdated=raw.get('veraltet'), delivered_at=raw.get('zugestellt_am'),
        outstanding_amount=format_money(raw.get('offen_betrag')), payment_status=raw.get('zahlungsstand'))


def _statement_details(statement_id: int, raw: dict[str, Any]) -> StatementDetails:
    # Statements without a snapshot (before NK-061) return the result flat.
    result = raw.get('ergebnis') or raw
    advance_payment = raw.get('vorauszahlung')
    return StatementDetails(
        id=statement_id, tenant_id=raw.get('tenant_id'), tenant=result.get('tenant_name'),
        apartment=result.get('apartment'), property=result.get('property'),
        start_date=raw.get('start_date'), end_date=raw.get('end_date'), version=raw.get('version'),
        outdated=raw.get('veraltet'), total_costs=format_money(result.get('total_amount')),
        advance_payments=format_money(result.get('prepaid_amount')), balance=format_money(result.get('balance')),
        line_items=[LineItem(
            cost_category=p.get('category'), allocation=p.get('billing_type'), period=p.get('period'),
            invoice_id=p.get('invoice_id'), invoice_amount=format_money(p.get('total_amount')),
            tenant_share=format_money(p.get('tenant_cost')), explanation=p.get('description'),
        ) for p in result.get('line_items') or []],
        warnings=[str(w) for w in result.get('warnings') or []],
        new_advance_payment=_new_advance_payment(advance_payment),
        statement_deadline=raw.get('frist_ende'), objection_deadline=raw.get('einwendungsfrist_ende'),
        delivered_at=raw.get('zugestellt_am'), outstanding_amount=format_money(raw.get('offen_betrag')),
        payment_status=raw.get('zahlungsstand'))


def _new_advance_payment(raw: dict[str, Any] | None) -> dict[str, str | None] | None:
    if not raw:
        return None
    if raw.get('neu') is None:
        # No adjustment possible, e.g. because the tenant has moved out: only the reason.
        return {'reason': raw.get('grund')}
    return {'previous': format_money(raw.get('bisher')), 'new': format_money(raw.get('neu')),
            'effective_from': raw.get('ab')}


def register(server: MCPServer, nkf: NkfClient) -> None:
    async def apartment_to_property() -> dict[int, int]:
        return {w['id']: w['property_id'] for w in await nkf.get('/api/apartments')}

    @tool(server, 'Immobilien', READ_ONLY)
    @report_errors
    async def list_properties() -> ItemList[Property]:
        """Alle Immobilien (Häuser bzw. Objekte) mit ID und Name. Ausgangspunkt für fast alle anderen Werkzeuge."""
        return truncate([Property(
            id=i['id'], name=i['name'], billing_year_start=i.get('abrechnungsjahr_beginn'),
            standalone=bool(i.get('is_standalone')), is_example=bool(i.get('ist_beispiel')),
        ) for i in await nkf.get('/api/properties')], MAX_ITEMS)

    @tool(server, 'Wohnungen', READ_ONLY)
    @report_errors
    async def list_apartments(property_id: PropertyId) -> ItemList[Apartment]:
        """Die Wohnungen bzw. Einheiten einer Immobilie mit Fläche und Nutzungsart."""
        return truncate([Apartment(
            id=w['id'], name=w['name'], area_sqm=w.get('sqm'), active=w.get('is_active'),
            usage_type=w.get('nutzungsart'), owner_occupied=w.get('eigennutzung'),
            self_supplied=w.get('selbstversorger'),
        ) for w in await nkf.get(f'/api/properties/{property_id}/apartments')], MAX_ITEMS)

    @tool(server, 'Heizungsanlagen', READ_ONLY)
    @report_errors
    async def list_heating_systems(property_id: PropertyId) -> ItemList[HeatingSystem]:
        """Die Heizungsanlagen einer Immobilie (für Heizkostenrechnungen nach HeizkostenV)."""
        return truncate([HeatingSystem(
            id=a['id'], name=a['name'], supplies=a.get('versorgt'),
            consumption_share_percent=a.get('verbrauchsanteil_prozent'), hot_water_method=a.get('warmwasser_weg'),
        ) for a in await nkf.get(f'/api/properties/{property_id}/heizungsanlagen')], MAX_ITEMS)

    @tool(server, 'Mieter', READ_ONLY)
    @report_errors
    async def list_tenants(
        property_id: Annotated[int | None, Field(description='nur Mieter dieser Immobilie')] = None,
        current_only: Annotated[bool, Field(description='nur Mieter, die heute dort wohnen (eingezogen, nicht '
                                                        'ausgezogen)')] = False,
    ) -> ItemList[Tenant]:
        """Mieter mit Wohnung, Ein- und Auszug und dem Datum, bis zu dem zuletzt abgerechnet wurde."""
        mapping = await apartment_to_property()
        today = date.today().isoformat()
        tenants = [_tenant(m, mapping) for m in await nkf.get('/api/tenants')]
        return truncate([m for m in tenants
                         if (property_id is None or m.property_id == property_id)
                         and (not current_only
                              or ((m.move_in or '') <= today and (not m.move_out or m.move_out >= today)))],
                        MAX_ITEMS)

    @tool(server, 'Vorauszahlungen (Soll/Ist)', READ_ONLY)
    @report_errors
    async def get_advance_payment_status(
        tenant_id: TenantId,
        year: Annotated[int | None, Field(description='nur die Monate dieses Kalenderjahrs; Summen gelten immer '
                                                      'für die ganze Mietzeit')] = None,
    ) -> AdvancePayments:
        """Soll und Ist der Nebenkostenvorauszahlungen eines Mieters je Monat, mit offenem Betrag und Saldo."""
        raw = await nkf.get(f'/api/tenants/{tenant_id}/vorauszahlungen')
        return AdvancePayments(
            months=[Month(month=m['monat'], expected=format_money(m.get('soll')), actual=format_money(m.get('ist')),
                          difference=format_money(m.get('differenz')), arrears=m.get('rueckstand'),
                          is_current=m.get('laufend'))
                    for m in raw.get('monate') or [] if year is None or m['monat'].startswith(f'{year}-')],
            outstanding=format_money(raw.get('offen')), overpaid=format_money(raw.get('ueberzahlt')),
            balance=format_money(raw.get('saldo')))

    @tool(server, 'Kostenarten', READ_ONLY)
    @report_errors
    async def list_cost_categories() -> ItemList[CostCategory]:
        """Kostenarten (Betriebskostenarten nach BetrKV) mit Umlageschlüssel. Die ID braucht man zum Erfassen
        von Rechnungen."""
        return truncate([CostCategory(
            id=k['id'], name=k['name'], allocation=k.get('allocation_method'),
            requires_meter=bool(k.get('requires_meter')), betrkv_number=k.get('betrkv_nr'),
            betrkv_label=k.get('betrkv_bezeichnung'), invoice_count=k.get('rechnungen'),
            meter_count=k.get('zaehler_anzahl'),
        ) for k in await nkf.get('/api/categories')], MAX_ITEMS)

    @tool(server, 'Dienstleister', READ_ONLY)
    @report_errors
    async def list_providers() -> ItemList[Provider]:
        """Dienstleister bzw. Versorger (Rechnungssteller), die in NebenkostenFix angelegt sind."""
        return truncate([Provider(id=d['id'], name=d['name']) for d in await nkf.get('/api/providers')],
                        MAX_ITEMS)

    @tool(server, 'Rechnungen', READ_ONLY)
    @report_errors
    async def list_invoices(
        property_id: Annotated[int | None, Field(description='nur Rechnungen dieser Immobilie')] = None,
        year: Annotated[int | None, Field(description='nur Rechnungen, deren Zeitraum dieses Jahr berührt')] = None,
        cost_category_id: Annotated[int | None, Field(description='nur diese Kostenart')] = None,
    ) -> Invoices:
        """Rechnungen (Kosten) mit Betrag, Zeitraum, Kostenart und ob ein Beleg hinterlegt ist. Eine Rechnung
        zählt zu einem Jahr, wenn sich ihr Zeitraum mit dem Jahr überschneidet."""
        matching = [r for r in (_invoice(raw) for raw in await nkf.get('/api/invoices'))
                    if (property_id is None or r.property_id == property_id)
                    and (cost_category_id is None or r.cost_category_id == cost_category_id)
                    and (year is None or in_year(r.start_date, r.end_date, year))]
        matching.sort(key=lambda r: (r.start_date or '', r.id))
        items = truncate(matching, MAX_INVOICES)
        return Invoices(**dict(items), total=total(r.amount for r in matching))

    @tool(server, 'Sammelbelege', READ_ONLY)
    @report_errors
    async def list_invoice_documents(
        property_id: Annotated[int | None, Field(description='nur Belege dieser Immobilie')] = None,
    ) -> ItemList[InvoiceDocument]:
        """Hochgeladene Belege, die mehrere Rechnungen enthalten können (Sammelbelege)."""
        return truncate([InvoiceDocument(
            id=b['id'], property_id=b.get('property_id'), filename=b.get('filename'),
            description=b.get('description'), uploaded_at=b.get('upload_date'),
        ) for b in await nkf.get('/api/invoice_documents')
            if property_id is None or b.get('property_id') == property_id], MAX_ITEMS)

    @tool(server, 'Zähler und Stände', READ_ONLY)
    @report_errors
    async def list_meters(
        property_id: PropertyId,
        meter_id: Annotated[int | None, Field(description='nur dieser Zähler')] = None,
    ) -> ItemList[Meter]:
        """Die Zähler einer Immobilie mit ihrem neuesten Stand; mit meter_id die letzten Stände dieses Zählers."""
        readings: dict[int, list[dict[str, Any]]] = {}
        for reading in await nkf.get('/api/readings'):
            readings.setdefault(reading['meter_id'], []).append(reading)
        count = 1 if meter_id is None else READINGS_PER_METER
        return truncate([_meter(z, readings.get(z['id'], []), count) for z in await nkf.get('/api/meters')
                         if z.get('property_id') == property_id and (meter_id is None or z['id'] == meter_id)],
                        MAX_ITEMS)

    @tool(server, 'Zahlungen', READ_ONLY)
    @report_errors
    async def list_payments(
        tenant_id: Annotated[int | None, Field(description='nur Zahlungen dieses Mieters')] = None,
        year: Annotated[int | None, Field(description='nur Zahlungen in diesem Kalenderjahr')] = None,
        payment_type: Annotated[PaymentType | None, Field(description='nur diese Art')] = None,
    ) -> Payments:
        """Eingegangene Zahlungen von Mietern (Miete, Vorauszahlungen, Kaution, Nachzahlungen), neueste zuerst."""
        matching = [z for z in (_payment(raw) for raw in await nkf.get('/api/payments'))
                    if (tenant_id is None or z.tenant_id == tenant_id)
                    and (year is None or z.payment_date.startswith(f'{year}-'))
                    and (payment_type is None or z.payment_type == payment_type)]
        matching.sort(key=lambda z: (z.payment_date, z.id), reverse=True)
        items = truncate(matching, MAX_ITEMS)
        return Payments(**dict(items), total=total(z.amount for z in matching))

    @tool(server, 'Fristen', READ_ONLY)
    @report_errors
    async def list_deadlines(
        until: Annotated[IsoDate | None, Field(description='nur Termine bis zu diesem Datum (YYYY-MM-DD)')] = None,
    ) -> ItemList[Deadline]:
        """Anstehende Termine und Fristen: Abrechnungsfristen, Einwendungsfristen, Eichfristen."""
        events = (await nkf.get('/api/fristen')).get('termine') or []
        return truncate([Deadline(date=t['datum'], kind=t['art'], title=t['titel']) for t in events
                         if until is None or t['datum'] <= until], MAX_ITEMS)

    @tool(server, 'Datenqualität', READ_ONLY)
    @report_errors
    async def get_data_quality(property_id: PropertyId) -> DataQuality:
        """Prüft den Datenbestand einer Immobilie: veraltete Zählerstände und fehlende Zähler."""
        raw = await nkf.get(f'/api/analytics/data-quality/{property_id}')
        return DataQuality(
            meters=[{'meter_id': z.get('meter_id'), 'number': z.get('meter_number'),
                     'cost_category': z.get('category_name'), 'apartment': z.get('apartment_name'),
                     'last_reading_date': z.get('last_reading_date'),
                     'days_since_reading': z.get('days_since_reading'),
                     'reading_count': z.get('reading_count'), 'status': z.get('status')}
                    for z in raw.get('meters') or []],
            warnings=[{'type': w.get('type'), 'severity': w.get('severity'), 'message': w.get('message'),
                       'apartment': w.get('apartment_name'), 'cost_category': w.get('category_name'),
                       'tenant': w.get('tenant_name')} for w in raw.get('warnings') or []])

    @tool(server, 'Jahresabrechnung (Stand)', READ_ONLY)
    @report_errors
    async def get_billing_year_status(property_id: PropertyId, year: BillingYear) -> dict[str, Any]:
        """Stand des Jahresabschlusses einer Immobilie: Kosten je Kostenart im Abrechnungsjahr (zeitanteilig) mit
        Vorjahr, fehlende Ablesungen, Mieter mit ihren Abrechnungen, offene Punkte (open_issues) und die
        Abrechnungsfrist."""
        raw = await nkf.get('/api/jahresabrechnung', property_id=property_id, jahr=year)
        return {
            'property_id': raw.get('property_id'), 'property': raw.get('immobilie'), 'year': raw.get('jahr'),
            'start_date': raw.get('von'), 'end_date': raw.get('bis'), 'statement_deadline': raw.get('frist_ende'),
            'deadline_days': raw.get('frist_tage'), 'deadline_exceeded': raw.get('frist_ueberschritten'),
            'cost_categories': [{'id': k.get('id'), 'name': k.get('name'), 'invoice_count': k.get('anzahl'),
                                 'total': format_money(k.get('summe')),
                                 'total_previous_year': format_money(k.get('summe_vorjahr')),
                                 'change_percent': k.get('aenderung'), 'jump': k.get('sprung'),
                                 'state': k.get('zustand')} for k in raw.get('kostenarten') or []],
            'readings': [{'meter_id': a.get('id'), 'number': a.get('nummer'), 'cost_category': a.get('kostenart'),
                          'apartment': a.get('wohnung'), 'last_reading': a.get('letzte'),
                          'cutoff_ok': a.get('stichtag_ok'), 'missing_change_readings': a.get('wechsel_fehlen')}
                         for a in raw.get('ablesungen') or []],
            'tenants': [{'id': m.get('id'), 'name': m.get('name'), 'apartment': m.get('wohnung'),
                         'start_date': m.get('von'), 'end_date': m.get('bis'), 'statement_id': m.get('abrechnung_id'),
                         'delivered_at': m.get('zugestellt_am')} for m in raw.get('mieter') or []],
            'open_issues': [{'type': p.get('art'), 'message': p.get('text'), 'action': p.get('tun')}
                            for p in raw.get('pruefstand') or []],
        }

    @tool(server, 'Abrechnungsvorschläge', READ_ONLY)
    @report_errors
    async def list_billing_suggestions() -> ItemList[BillingSuggestion]:
        """Mieter, für die eine Nebenkostenabrechnung fällig ist, mit vorgeschlagenem Zeitraum."""
        return truncate([BillingSuggestion(
            tenant_id=v['tenant_id'], tenant=v.get('tenant_name'), apartment=v.get('apartment_name'),
            start_date=v.get('suggested_start'), end_date=v.get('suggested_end'),
            cost_categories=[SuggestedCostCategory(
                cost_category_id=k['category_id'], cost_category=k.get('category_name'),
                start_date=k.get('suggested_start'), end_date=k.get('suggested_end'),
                invoice_count=k.get('invoice_count'),
            ) for k in v.get('categories') or []],
        ) for v in await nkf.get('/api/billing/suggestions')], MAX_ITEMS)

    @tool(server, 'Abrechnungen', READ_ONLY)
    @report_errors
    async def list_statements(
        tenant_id: Annotated[int | None, Field(description='nur Abrechnungen dieses Mieters')] = None,
        property_id: Annotated[int | None, Field(description='nur Abrechnungen dieser Immobilie')] = None,
    ) -> ItemList[Statement]:
        """Erstellte Nebenkostenabrechnungen mit Zeitraum, Zustellung und offenem Betrag."""
        return truncate([a for a in (_statement(raw) for raw in await nkf.get('/api/billing/reports'))
                         if (tenant_id is None or a.tenant_id == tenant_id)
                         and (property_id is None or a.property_id == property_id)], MAX_ITEMS)

    @tool(server, 'Abrechnung im Detail', READ_ONLY)
    @report_errors
    async def get_statement(
        statement_id: Annotated[int, Field(description='ID der Abrechnung, siehe Werkzeug list_statements')],
    ) -> StatementDetails:
        """Ergebnis einer Abrechnung: Kosten je Position mit Anteil des Mieters, Vorauszahlungen, Saldo,
        Anpassung der Vorauszahlung und Fristen."""
        return _statement_details(statement_id, await nkf.get(f'/api/billing/reports/{statement_id}/details'))
