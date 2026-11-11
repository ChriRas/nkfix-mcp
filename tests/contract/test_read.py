"""Read tools against the sample property of a real instance.

The field guard is the actual contract: every field that the read tools read
from a response must be present in the real response. Otherwise, if upstream
renames a field, ``.get()`` silently returns ``None``.
"""

import re

import pytest

from nkfix_mcp.client import NkfClient
from nkfix_mcp.config import Config
from tests.contract.helpers import call, connected

pytestmark = pytest.mark.anyio

# Endpoint -> fields that the read tools read (for lists: fields of one entry).
FIELDS = {
    '/api/properties': {'id', 'name', 'abrechnungsjahr_beginn', 'is_standalone', 'ist_beispiel'},
    '/api/apartments': {'id', 'property_id'},
    '/api/properties/{property}/apartments': {
        'id',
        'name',
        'sqm',
        'is_active',
        'nutzungsart',
        'eigennutzung',
        'selbstversorger',
    },
    '/api/tenants': {
        'id',
        'name',
        'apartment_id',
        'apartment_name',
        'property_name',
        'move_in_date',
        'move_out_date',
        'last_billed_until',
        'ist_beispiel',
    },
    '/api/categories': {
        'id',
        'name',
        'allocation_method',
        'requires_meter',
        'betrkv_nr',
        'betrkv_bezeichnung',
        'rechnungen',
        'zaehler_anzahl',
    },
    '/api/invoices': {
        'id',
        'property_id',
        'property_name',
        'category_id',
        'category_name',
        'amount',
        'start_date',
        'end_date',
        'rechnungsdatum',
        'invoice_number',
        'description',
        'provider_id',
        'provider_name',
        'apartment_id',
        'invoice_document_id',
        'document_path',
        'heizungsanlage_id',
        'heizkostenart',
        'grundpreis',
        'lohnanteil',
        'co2_kosten',
    },
    '/api/meters': {
        'id',
        'meter_number',
        'category_id',
        'category_name',
        'einheit',
        'property_id',
        'apartment_id',
        'apartment_name',
        'is_main_meter',
        'has_dual_tariff',
        'geeicht_bis',
        'ort',
        'ersetzt_durch',
    },
    '/api/readings': {'id', 'meter_id', 'reading_date', 'value', 'value_nt', 'ablesungsart', 'plausibility_status'},
    '/api/payments': {'id', 'tenant_id', 'tenant_name', 'amount', 'payment_date', 'type'},
    '/api/providers': {'id', 'name'},
    '/api/billing/reports': {
        'id',
        'tenant_id',
        'tenant_name',
        'apartment_name',
        'property_id',
        'property_name',
        'start_date',
        'end_date',
        'created_at',
        'veraltet',
        'zugestellt_am',
        'offen_betrag',
        'zahlungsstand',
    },
}


async def raw(instance, path: str, **params):
    async with NkfClient(Config.from_env(instance.env('sitzung'))) as nkf:
        return await nkf.get(path, **params)


async def sample_property(instance) -> int:
    return next(i['id'] for i in await raw(instance, '/api/properties') if i['ist_beispiel'])


@pytest.mark.parametrize('path', sorted(FIELDS))
async def test_field_guard(instance, path):
    response = await raw(instance, path.format(property=await sample_property(instance)))
    assert response, f'{path} ist leer, die Beispielimmobilie sollte Daten haben'
    missing = FIELDS[path] - set(response[0])
    assert not missing, f'{path}: diese Felder gibt es nicht mehr: {sorted(missing)}'


async def test_field_guard_nested(instance):
    house = await sample_property(instance)
    anna = next(m['id'] for m in await raw(instance, '/api/tenants') if m['name'] == 'Anna Beispiel')
    advance = await raw(instance, f'/api/tenants/{anna}/vorauszahlungen')
    assert {'monate', 'offen', 'ueberzahlt', 'saldo'} <= set(advance)
    assert {'monat', 'soll', 'ist', 'differenz', 'rueckstand'} <= set(advance['monate'][0])

    year = await raw(instance, '/api/jahresabrechnung', property_id=house, jahr=2025)
    assert {
        'property_id',
        'immobilie',
        'jahr',
        'von',
        'bis',
        'frist_ende',
        'frist_tage',
        'frist_ueberschritten',
        'kostenarten',
        'ablesungen',
        'mieter',
        'pruefstand',
    } <= set(year)
    assert {'id', 'name', 'anzahl', 'summe', 'summe_vorjahr', 'aenderung', 'sprung', 'zustand'} <= set(
        year['kostenarten'][0]
    )
    assert {'id', 'name', 'wohnung', 'von', 'bis', 'abrechnung_id', 'zugestellt_am'} <= set(year['mieter'][0])

    quality = await raw(instance, f'/api/analytics/data-quality/{house}')
    assert {
        'meter_id',
        'meter_number',
        'category_name',
        'apartment_name',
        'last_reading_date',
        'days_since_reading',
        'reading_count',
        'status',
    } <= set(quality['meters'][0])
    assert {'type', 'severity', 'message'} <= set(quality['warnings'][0])

    events = (await raw(instance, '/api/fristen'))['termine']
    assert {'datum', 'art', 'titel', 'uid'} <= set(events[0])

    report = (await raw(instance, '/api/billing/reports'))[0]['id']
    details = await raw(instance, f'/api/billing/reports/{report}/details')
    assert {
        'tenant_id',
        'start_date',
        'end_date',
        'version',
        'veraltet',
        'frist_ende',
        'einwendungsfrist_ende',
        'zugestellt_am',
        'offen_betrag',
        'zahlungsstand',
        'vorauszahlung',
        'ergebnis',
    } <= set(details)
    assert {
        'tenant_name',
        'apartment',
        'property',
        'total_amount',
        'prepaid_amount',
        'balance',
        'warnings',
        'line_items',
    } <= set(details['ergebnis'])
    assert {'category', 'billing_type', 'period', 'invoice_id', 'total_amount', 'tenant_cost', 'description'} <= set(
        details['ergebnis']['line_items'][0]
    )
    assert {'bisher', 'neu', 'ab'} <= set(details['vorauszahlung'])


async def test_every_read_tool_runs_with_read_key(instance):
    async with connected(instance, 'lesen') as mcp:
        houses = (await call(mcp, 'list_properties'))['items']
        house_id = next(i['id'] for i in houses if i['is_example'])
        assert len((await call(mcp, 'list_apartments', property_id=house_id))['items']) == 2
        tenants = (await call(mcp, 'list_tenants', property_id=house_id))['items']
        assert {m['name'] for m in tenants} == {'Anna Beispiel', 'Ben Beispiel'}
        anna = next(m['id'] for m in tenants if m['name'] == 'Anna Beispiel')

        invoices = await call(mcp, 'list_invoices', property_id=house_id, year=2025)
        assert invoices['count'] > 0
        assert all(isinstance(r['amount'], str) and '.' in r['amount'] for r in invoices['items'])

        meters = (await call(mcp, 'list_meters', property_id=house_id))['items']
        assert any(z['reading_count'] > 0 for z in meters)

        assert (await call(mcp, 'list_payments', tenant_id=anna))['count'] > 0
        assert (await call(mcp, 'get_advance_payment_status', tenant_id=anna))['months']
        assert (await call(mcp, 'list_cost_categories'))['count'] > 0
        await call(mcp, 'list_providers')
        await call(mcp, 'list_invoice_documents')
        await call(mcp, 'list_heating_systems', property_id=house_id)
        assert (await call(mcp, 'list_deadlines'))['count'] > 0
        assert (await call(mcp, 'get_data_quality', property_id=house_id))['meters']
        year = await call(mcp, 'get_billing_year_status', property_id=house_id, year=2025)
        assert year['cost_categories']
        assert year['tenants']
        await call(mcp, 'list_billing_suggestions')

        statements = (await call(mcp, 'list_statements', tenant_id=anna))['items']
        assert statements, 'für Anna legt die Beispielimmobilie die Vorjahresabrechnung an'
        details = await call(mcp, 'get_statement', statement_id=statements[0]['id'])
        assert details['line_items']
        assert details['balance'] is not None


async def test_field_guard_check_tools(instance):
    house = await sample_property(instance)
    async with NkfClient(Config.from_env(instance.env('sitzung'))) as nkf:
        anna = next(m['id'] for m in await nkf.get('/api/tenants') if m['name'] == 'Anna Beispiel')
        precheck = await nkf.post(
            '/api/billing/preflight', {'tenant_id': anna, 'start_date': '2025-01-01', 'end_date': '2025-12-31'}
        )
        meter = next(z for z in await nkf.get('/api/meters') if z['property_id'] == house)
        reading = max(
            (s for s in await nkf.get('/api/readings') if s['meter_id'] == meter['id']), key=lambda s: s['reading_date']
        )
        plausibility = await nkf.post(
            '/api/readings/check_plausibility',
            {'meter_id': meter['id'], 'reading_date': reading['reading_date'], 'value': reading['value'] * 3},
        )
        hausgeld = await nkf.post(
            '/api/hausgeld/pruefen',
            {
                'positionen': [{'name': 'Hausmeister', 'gesamtbetrag': '2400', 'eigener_betrag': '300'}],
                'eigener_anteil': 125,
                'gesamtanteile': 1000,
                'abrechnungssumme': '300',
            },
        )
        year = await nkf.get('/api/jahresabrechnung', property_id=house, jahr=2025)
        quality = await nkf.get(f'/api/analytics/data-quality/{house}')
    assert {'overall', 'checks'} <= set(precheck)
    assert {'category', 'meter_type', 'status', 'blocking', 'message', 'meter_number', 'apartment'} <= set(
        precheck['checks'][0]
    )
    assert {'status', 'message', 'closest_date', 'closest_value', 'diff_percent'} <= set(plausibility)
    assert {
        'positionen',
        'summe_umlagefaehig',
        'summe_eigentuemer',
        'summe_offen',
        'anzahl_umlagefaehig',
        'anzahl_eigentuemer',
        'anzahl_offen',
        'summenprobe',
        'zeitraum_hinweis',
        'heizkosten_hinweis',
    } <= set(hausgeld)
    assert {'name', 'gesamtbetrag', 'eigener_betrag', 'umlagefaehig', 'erwartet', 'abweichung'} <= set(
        hausgeld['positionen'][0]
    )
    assert {'erwartet', 'angesetzt', 'abweichung'} <= set(hausgeld['summenprobe'])
    assert {'id', 'nummer', 'kostenart', 'wohnung', 'letzte', 'stichtag_ok', 'wechsel_fehlen'} <= set(
        year['ablesungen'][0]
    )
    assert {'art', 'text', 'tun'} <= set(year['pruefstand'][0])
    assert {'apartment_name', 'category_name', 'tenant_name'} <= set(quality['warnings'][0])


async def test_statement_deadline_names_the_statement_and_its_period(instance):
    """The uid of a statement deadline carries the statement id (calendar export,
    no promised interface). If upstream changes it, this test fails instead of
    the deadlines silently losing their statement."""
    events = (await raw(instance, '/api/fristen'))['termine']
    statement_events = [e for e in events if e['art'] in ('abrechnungsfrist', 'einwendungsfrist')]
    assert statement_events, 'Die Beispielimmobilie sollte eine offene Abrechnungsfrist haben.'
    for event in statement_events:
        assert re.fullmatch(rf'{event["art"]}-\d+@nebenkostenfix', event['uid']), event['uid']

    async with connected(instance, 'lesen') as mcp:
        deadlines = (await call(mcp, 'list_deadlines'))['items']
        statements = {s['id']: s for s in (await call(mcp, 'list_statements'))['items']}
    named = [d for d in deadlines if d['kind'] in ('abrechnungsfrist', 'einwendungsfrist')]
    assert len(named) == len(statement_events)
    for deadline in named:
        statement = statements[deadline['statement_id']]
        assert (deadline['period_start'], deadline['period_end']) == (statement['start_date'], statement['end_date'])
