"""Read tools against a mocked-up dataset (fantasy data).

The raw shapes match the responses of NebenkostenFix 0.17.1, taken on
27.10.2026 from the sample property. The contract tests check that they
still look like that.
"""

import re
from datetime import date, timedelta

import pytest
from mcp.client import Client

from nkfix_mcp.server import build_server
from tests.helpers import ABOUT, ME_READ, FakeNkf, client

pytestmark = pytest.mark.anyio

TODAY = date.today()
YESTERDAY = (TODAY - timedelta(days=1)).isoformat()


def invoice(id, property_id=1, category_id=1, start='2025-01-01', end='2025-12-31', amount=460.0, **more):
    return {'id': id, 'property_id': property_id, 'property_name': 'Musterhaus', 'category_id': category_id,
            'category_name': 'Grundsteuer', 'start_date': start, 'end_date': end, 'amount': amount,
            'invoice_number': f'R-{id}', 'rechnungsdatum': '2026-01-11', 'description': None,
            'provider_id': None, 'provider_name': None, 'apartment_id': None, 'invoice_document_id': None,
            'document_path': None, 'heizungsanlage_id': None, 'heizkostenart': None, 'grundpreis': None,
            'lohnanteil': None, 'co2_kosten': None, **more}


def sample_data() -> FakeNkf:
    fake = FakeNkf()
    fake.json('GET', '/api/properties', [
        {'id': 1, 'name': 'Musterhaus', 'abrechnungsjahr_beginn': '01.01.', 'is_standalone': False,
         'ist_beispiel': False},
        {'id': 2, 'name': 'Gartenweg 3', 'abrechnungsjahr_beginn': '01.07.', 'is_standalone': True,
         'ist_beispiel': False}])
    fake.json('GET', '/api/apartments', [
        {'id': 10, 'name': 'EG', 'property_id': 1, 'sqm': 52.0},
        {'id': 11, 'name': 'OG', 'property_id': 1, 'sqm': 61.5},
        {'id': 20, 'name': 'Haus', 'property_id': 2, 'sqm': 120.0}])
    fake.json('GET', '/api/properties/1/apartments', [
        {'id': 10, 'name': 'EG', 'sqm': 52.0, 'is_active': True, 'nutzungsart': 'wohnen', 'eigennutzung': False,
         'selbstversorger': False}])
    fake.json('GET', '/api/tenants', [
        {'id': 1, 'name': 'Erika Muster', 'apartment_id': 10, 'apartment_name': 'EG', 'property_name': 'Musterhaus',
         'move_in_date': '2024-07-01', 'move_out_date': None, 'last_billed_until': '2025-12-31',
         'ist_beispiel': False},
        {'id': 2, 'name': 'Max Probe', 'apartment_id': 11, 'apartment_name': 'OG', 'property_name': 'Musterhaus',
         'move_in_date': '2020-01-01', 'move_out_date': YESTERDAY, 'last_billed_until': None,
         'ist_beispiel': False},
        {'id': 3, 'name': 'Lisa Garten', 'apartment_id': 20, 'apartment_name': 'Haus',
         'property_name': 'Gartenweg 3', 'move_in_date': '2023-01-01', 'move_out_date': None,
         'last_billed_until': None, 'ist_beispiel': False}])
    fake.json('GET', '/api/invoices', [
        invoice(1),
        invoice(2, start='2024-12-01', end='2025-01-31', amount=99.999),
        invoice(3, start='2024-01-01', end='2024-12-31'),
        invoice(4, property_id=2, document_path='2025/abc.pdf'),
        invoice(5, category_id=7, invoice_document_id=3)])
    fake.json('GET', '/api/meters', [
        {'id': 5, 'meter_number': 'W-1', 'category_id': 3, 'category_name': 'Wasser', 'einheit': 'm³',
         'property_id': 1, 'apartment_id': 10, 'apartment_name': 'EG', 'is_main_meter': False,
         'geeicht_bis': '2027-12-31', 'has_dual_tariff': False, 'ersetzt_durch': None, 'ort': 'Keller'},
        {'id': 6, 'meter_number': 'G-9', 'category_id': 4, 'category_name': 'Gas', 'einheit': 'kWh',
         'property_id': 2, 'apartment_id': None, 'apartment_name': None, 'is_main_meter': True,
         'geeicht_bis': None, 'has_dual_tariff': False, 'ersetzt_durch': None, 'ort': None}])
    fake.json('GET', '/api/readings', [
        {'id': 100 + i, 'meter_id': 5, 'reading_date': f'2025-{i:02d}-01', 'value': 10.0 + i, 'value_nt': None,
         'ablesungsart': 'ablesung', 'plausibility_status': 'ok', 'plausibility_message': ''}
        for i in range(1, 13)] + [
        {'id': 200, 'meter_id': 6, 'reading_date': '2025-12-31', 'value': 4711.0, 'value_nt': None,
         'ablesungsart': 'ablesung', 'plausibility_status': 'unknown', 'plausibility_message': ''}])
    fake.json('GET', '/api/payments', [
        {'id': 1, 'tenant_id': 1, 'tenant_name': 'Erika Muster', 'amount': 650.0, 'payment_date': '2025-11-03',
         'type': 'Miete'},
        {'id': 2, 'tenant_id': 1, 'tenant_name': 'Erika Muster', 'amount': 60.0, 'payment_date': '2025-11-03',
         'type': 'Nebenkostenvorauszahlung'},
        {'id': 3, 'tenant_id': 1, 'tenant_name': 'Erika Muster', 'amount': 650.0, 'payment_date': '2024-11-03',
         'type': 'Miete'},
        {'id': 4, 'tenant_id': 3, 'tenant_name': 'Lisa Garten', 'amount': 800.0, 'payment_date': '2025-11-04',
         'type': 'Miete'}])
    fake.json('GET', '/api/fristen', {'termine': [
        {'datum': '2026-12-31', 'art': 'abrechnungsfrist', 'titel': 'Ende der Abrechnungsfrist', 'uid': 'a@x'},
        {'datum': '2027-06-30', 'art': 'eichfrist', 'titel': 'Eichfrist W-1', 'uid': 'b@x'}]})
    fake.json('GET', '/api/categories', [
        {'id': 1, 'name': 'Grundsteuer', 'allocation_method': 'qm', 'requires_meter': False, 'betrkv_nr': 1,
         'betrkv_bezeichnung': 'Laufende öffentliche Last', 'rechnungen': 3, 'zaehler_anzahl': 0}])
    fake.json('GET', '/api/providers', [{'id': 4, 'name': 'Stadtwerke Musterstadt'}])
    fake.json('GET', '/api/invoice_documents', [
        {'id': 3, 'property_id': 1, 'property_name': 'Musterhaus', 'filename': 'sammel.pdf',
         'description': 'Jahresrechnung', 'upload_date': '2026-01-12', 'document_path': '2026/x.pdf'},
        {'id': 4, 'property_id': 2, 'property_name': 'Gartenweg 3', 'filename': 'b.pdf', 'description': None,
         'upload_date': '2026-01-13', 'document_path': '2026/y.pdf'}])
    fake.json('GET', '/api/properties/1/heizungsanlagen', [
        {'id': 1, 'property_id': 1, 'name': 'Gastherme', 'versorgt': 'heizung_warmwasser',
         'verbrauchsanteil_prozent': 70, 'sonderfall_70': False, 'warmwasser_weg': 'waermemenge',
         'warmwasser_kwh': None, 'warmwasser_volumen_m3': None, 'warmwasser_temperatur_c': None,
         'brennstoff_menge': None, 'heizwert_kwh': None}])
    fake.json('GET', '/api/tenants/1/vorauszahlungen', {
        'monate': [{'monat': '2025-11', 'soll': '60.00', 'ist': '60.00', 'differenz': '0.00', 'rueckstand': False,
                    'laufend': False}],
        'offen': '0.00', 'ueberzahlt': '0.00', 'saldo': '0.00', 'soll': [], 'erinnerung': None})
    fake.json('GET', '/api/analytics/data-quality/1', {
        'meters': [{'meter_id': 5, 'meter_number': 'W-1', 'category_name': 'Wasser', 'apartment_name': 'EG',
                    'is_main_meter': False, 'last_reading_date': '2025-12-01', 'days_since_reading': 330,
                    'reading_count': 12, 'status': 'red'}],
        'warnings': [{'type': 'missing_submeter', 'severity': 'error', 'message': 'Zähler fehlt',
                      'apartment_name': 'OG', 'category_name': 'Wasser', 'tenant_name': 'Max Probe'}]})
    fake.json('GET', '/api/jahresabrechnung', {
        'property_id': 1, 'immobilie': 'Musterhaus', 'jahr': 2025, 'von': '2025-01-01', 'bis': '2025-12-31',
        'frist_ende': '2026-12-31', 'frist_tage': 65, 'frist_ueberschritten': False,
        'kostenarten': [{'id': 1, 'name': 'Grundsteuer', 'anzahl': 1, 'summe': 460.0, 'summe_vorjahr': 450.5,
                         'aenderung': 2.1, 'sprung': False, 'zustand': 'erfasst'}],
        'ablesungen': [{'id': 5, 'nummer': 'W-1', 'kostenart': 'Wasser', 'wohnung': 'EG', 'letzte': '2025-12-01',
                        'stichtag_ok': False, 'wechsel_fehlen': []}],
        'mieter': [{'id': 1, 'name': 'Erika Muster', 'wohnung': 'EG', 'von': '2025-01-01', 'bis': '2025-12-31',
                    'abrechnung_id': 9, 'detailliert': True, 'einfach': False, 'zugestellt_am': None}],
        'pruefstand': [{'art': 'ohne_beleg', 'text': 'Rechnung ohne Beleg', 'tun': 'Beleg hochladen',
                        'ziel': {'aktion': 'rechnung', 'id': 1}}]})
    fake.json('GET', '/api/billing/suggestions', [
        {'tenant_id': 1, 'tenant_name': 'Erika Muster', 'apartment_name': 'EG', 'suggested_start': '2025-01-01',
         'suggested_end': '2025-12-31', 'categories': [
             {'category_id': 1, 'category_name': 'Grundsteuer', 'suggested_start': '2025-01-01',
              'suggested_end': '2025-12-31', 'invoice_count': 1}]}])
    fake.json('GET', '/api/billing/reports', [
        {'id': 9, 'tenant_id': 1, 'tenant_name': 'Erika Muster', 'apartment_name': 'EG', 'property_id': 1,
         'property_name': 'Musterhaus', 'start_date': '2025-01-01', 'end_date': '2025-12-31',
         'created_at': '27.10.2026', 'veraltet': False, 'zugestellt_am': None, 'offen_betrag': '108.32',
         'zahlungsstand': 'offen', 'document_path': None},
        {'id': 8, 'tenant_id': 3, 'tenant_name': 'Lisa Garten', 'apartment_name': 'Haus', 'property_id': 2,
         'property_name': 'Gartenweg 3', 'start_date': '2024-01-01', 'end_date': '2024-12-31',
         'created_at': '01.03.2025', 'veraltet': True, 'zugestellt_am': '2025-03-02', 'offen_betrag': '0.00',
         'zahlungsstand': 'bezahlt', 'document_path': None}])
    fake.json('GET', '/api/billing/reports/9/details', {
        'tenant_id': 1, 'start_date': '2025-01-01', 'end_date': '2025-12-31', 'version': 2, 'veraltet': False,
        'frist_ende': '2026-12-31', 'einwendungsfrist_ende': None, 'zugestellt_am': None,
        'offen_betrag': '108.32', 'zahlungsstand': 'offen',
        'vorauszahlung': {'bisher': '60.00', 'neu': '69.00', 'ab': '2026-12-01', 'aenderung': True,
                          'kosten': '828.32', 'monate': '12.00', 'kuenftige': 0},
        'versionen': [{'nummer': 1, 'erstellt_am': '2026-10-01'}, {'nummer': 2, 'erstellt_am': '2026-10-27'}],
        'ergebnis': {
            'tenant_name': 'Erika Muster', 'apartment': 'EG', 'property': 'Musterhaus',
            'total_amount': '828.32', 'prepaid_amount': '720.00', 'balance': '108.32', 'warnings': ['Hinweis'],
            'vorbehalt': None,
            'line_items': [{'category': 'Grundsteuer', 'billing_type': 'qm', 'period': '01.01.2025 - 31.12.2025',
                            'invoice_id': 1, 'invoice_number': 'R-1', 'total_amount': '460.00',
                            'prorated_amount': '460.00', 'tenant_cost': '211.68',
                            'description': 'Umlage nach Wohnfläche (52 von 113 m²)',
                            'rechenweg': [['Rechnungsbetrag', '460,00 €']]}]}})
    return fake


async def call(fake: FakeNkf, tool: str, **arguments):
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, 'lesen')) as mcp:
        result = await mcp.call_tool(tool, arguments)
    assert not result.is_error, result.content[0].text
    return result.structured_content


async def test_list_properties():
    data = await call(sample_data(), 'list_properties')
    assert data['count'] == 2
    assert data['items'][1] == {'id': 2, 'name': 'Gartenweg 3', 'billing_year_start': '01.07.',
                                'standalone': True, 'is_example': False}


async def test_list_apartments():
    entry = (await call(sample_data(), 'list_apartments', property_id=1))['items'][0]
    assert entry == {'id': 10, 'name': 'EG', 'area_sqm': 52.0, 'active': True, 'usage_type': 'wohnen',
                     'owner_occupied': False, 'self_supplied': False}


async def test_tenants_are_assigned_to_the_property_via_their_apartment():
    data = await call(sample_data(), 'list_tenants', property_id=1)
    assert [m['name'] for m in data['items']] == ['Erika Muster', 'Max Probe']
    assert data['items'][0]['property_id'] == 1
    assert data['items'][0]['move_in'] == '2024-07-01'


async def test_current_only_tenants_leaves_out_those_who_moved_out():
    data = await call(sample_data(), 'list_tenants', current_only=True)
    assert [m['name'] for m in data['items']] == ['Erika Muster', 'Lisa Garten']


async def test_invoices_by_year_with_overlap_and_money_as_string():
    data = await call(sample_data(), 'list_invoices', property_id=1, year=2025)
    # sorted by start of the period
    assert [r['id'] for r in data['items']] == [2, 1, 5]
    assert data['items'][0]['amount'] == '100.00'
    assert data['total'] == '1020.00'


async def test_invoices_by_cost_category_and_receipt_flag():
    data = await call(sample_data(), 'list_invoices', cost_category_id=7)
    assert [r['id'] for r in data['items']] == [5]
    assert data['items'][0]['invoice_document_id'] == 3
    assert data['items'][0]['has_receipt'] is True
    all_invoices = {r['id']: r['has_receipt'] for r in (await call(sample_data(), 'list_invoices'))['items']}
    assert all_invoices == {1: False, 2: False, 3: False, 4: True, 5: True}


async def test_many_invoices_are_truncated_with_note():
    fake = sample_data().json('GET', '/api/invoices', [invoice(i) for i in range(1, 251)])
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, 'lesen')) as mcp:
        result = await mcp.call_tool('list_invoices', {})
    data = result.structured_content
    assert data['count'] == 250
    assert len(data['items']) == 40
    assert data['omitted'] == 210
    assert data['total'] == '115000.00'
    # Claude Code warns from 10,000 tokens on; roughly 3 to 4 characters per token.
    assert len(result.content[0].text) < 30_000


async def test_meters_with_the_latest_reading():
    data = await call(sample_data(), 'list_meters', property_id=1)
    assert [z['id'] for z in data['items']] == [5]
    meter = data['items'][0]
    assert meter['number'] == 'W-1'
    assert meter['reading_count'] == 12
    assert meter['readings'] == [{'id': 112, 'reading_date': '2025-12-01', 'value': 22.0, 'value_off_peak': None,
                                  'reading_type': 'ablesung'}]


async def test_one_meter_with_its_last_ten_readings():
    meter = (await call(sample_data(), 'list_meters', property_id=1, meter_id=5))['items'][0]
    assert [s['id'] for s in meter['readings']] == list(range(112, 102, -1))


async def test_many_meters_stay_below_the_warning_limit():
    fake = sample_data()
    fake.json('GET', '/api/meters', [
        {'id': i, 'meter_number': f'HKV-{i}', 'category_id': 4, 'category_name': 'Heizung', 'einheit': 'Einheiten',
         'property_id': 1, 'apartment_id': 10, 'apartment_name': 'EG', 'is_main_meter': False,
         'geeicht_bis': None, 'has_dual_tariff': False, 'ersetzt_durch': None, 'ort': 'Wohnzimmer'}
        for i in range(1, 41)])
    fake.json('GET', '/api/readings', [
        {'id': 1000 * i + j, 'meter_id': i, 'reading_date': f'20{10 + j}-12-31', 'value': float(j), 'value_nt': None,
         'ablesungsart': 'ablesung', 'plausibility_status': 'unknown'} for i in range(1, 41) for j in range(10)])
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, 'lesen')) as mcp:
        result = await mcp.call_tool('list_meters', {'property_id': 1})
    assert len(result.content[0].text) < 30_000


async def test_payments_by_tenant_year_and_type():
    data = await call(sample_data(), 'list_payments', tenant_id=1, year=2025, payment_type='Miete')
    assert [z['id'] for z in data['items']] == [1]
    assert data['items'][0]['amount'] == '650.00'
    assert data['total'] == '650.00'


async def test_advance_payments_keep_the_strings():
    data = await call(sample_data(), 'get_advance_payment_status', tenant_id=1)
    assert data['months'][0] == {'month': '2025-11', 'expected': '60.00', 'actual': '60.00', 'difference': '0.00',
                                 'arrears': False, 'is_current': False}
    assert data['balance'] == '0.00'
    assert (await call(sample_data(), 'get_advance_payment_status', tenant_id=1, year=2024))['months'] == []


async def test_deadlines_until_a_date():
    data = await call(sample_data(), 'list_deadlines', until='2027-01-01')
    assert data['items'] == [{'date': '2026-12-31', 'kind': 'abrechnungsfrist',
                              'title': 'Ende der Abrechnungsfrist'}]


async def test_cost_categories_providers_invoice_documents_heating_systems():
    fake = sample_data()
    cost_category = (await call(fake, 'list_cost_categories'))['items'][0]
    assert cost_category == {'id': 1, 'name': 'Grundsteuer', 'allocation': 'qm', 'requires_meter': False,
                             'betrkv_number': 1, 'betrkv_label': 'Laufende öffentliche Last',
                             'invoice_count': 3, 'meter_count': 0}
    assert (await call(fake, 'list_providers'))['items'] == [{'id': 4, 'name': 'Stadtwerke Musterstadt'}]
    receipts = (await call(fake, 'list_invoice_documents', property_id=1))['items']
    assert receipts == [{'id': 3, 'property_id': 1, 'filename': 'sammel.pdf', 'description': 'Jahresrechnung',
                         'uploaded_at': '2026-01-12'}]
    heating_system = (await call(fake, 'list_heating_systems', property_id=1))['items'][0]
    assert heating_system['name'] == 'Gastherme' and heating_system['supplies'] == 'heizung_warmwasser'


async def test_data_quality():
    data = await call(sample_data(), 'get_data_quality', property_id=1)
    assert data['meters'][0]['days_since_reading'] == 330
    assert data['warnings'][0]['severity'] == 'error'


async def test_billing_year_status_normalises_the_totals():
    fake = sample_data()
    data = await call(fake, 'get_billing_year_status', property_id=1, year=2025)
    assert dict(fake.sent('GET', '/api/jahresabrechnung')[0].url.params) == {'property_id': '1',
                                                                             'jahr': '2025'}
    assert data['cost_categories'][0]['total'] == '460.00'
    assert data['cost_categories'][0]['total_previous_year'] == '450.50'
    assert data['open_issues'][0] == {'type': 'ohne_beleg', 'message': 'Rechnung ohne Beleg',
                                      'action': 'Beleg hochladen'}
    assert data['tenants'][0]['statement_id'] == 9


async def test_billing_suggestions_and_statements():
    fake = sample_data()
    suggestion = (await call(fake, 'list_billing_suggestions'))['items'][0]
    assert suggestion == {'tenant_id': 1, 'tenant': 'Erika Muster', 'apartment': 'EG', 'start_date': '2025-01-01',
                          'end_date': '2025-12-31', 'cost_categories': [
                              {'cost_category_id': 1, 'cost_category': 'Grundsteuer', 'start_date': '2025-01-01',
                               'end_date': '2025-12-31', 'invoice_count': 1}]}
    statements = (await call(fake, 'list_statements', tenant_id=1))['items']
    assert [a['id'] for a in statements] == [9]
    assert statements[0]['outstanding_amount'] == '108.32'


async def test_statement_details_are_condensed():
    data = await call(sample_data(), 'get_statement', statement_id=9)
    assert data['total_costs'] == '828.32'
    assert data['advance_payments'] == '720.00'
    assert data['balance'] == '108.32'
    assert data['line_items'][0] == {'cost_category': 'Grundsteuer', 'allocation': 'qm',
                                     'period': '01.01.2025 - 31.12.2025', 'invoice_id': 1,
                                     'invoice_amount': '460.00', 'tenant_share': '211.68',
                                     'explanation': 'Umlage nach Wohnfläche (52 von 113 m²)'}
    assert data['new_advance_payment'] == {'previous': '60.00', 'new': '69.00', 'effective_from': '2026-12-01'}
    assert data['version'] == 2


async def test_unknown_property_is_reported_understandably():
    fake = sample_data().json('GET', '/api/properties/99/apartments', {'error': 'Nicht gefunden.'}, status=404)
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, 'lesen')) as mcp:
        result = await mcp.call_tool('list_apartments', {'property_id': 99})
    assert result.is_error
    assert 'Nicht gefunden' in result.content[0].text


# Transcribed by hand from NebenkostenFix nebenkostenfix/zugriffsschluessel.py, SCHLUESSEL_LESEN (as of beadd7f).
# A tool that calls any other endpoint would get 403 with an API key.
ALLOWED_READ_ENDPOINTS = [
    r'GET /api/auth/me', r'GET /api/ueber', r'GET /api/properties', r'GET /api/properties/\d+/apartments',
    r'GET /api/apartments', r'GET /api/properties/\d+/heizungsanlagen', r'GET /api/tenants',
    r'GET /api/tenants/\d+/vorauszahlungen', r'GET /api/categories', r'GET /api/providers', r'GET /api/invoices',
    r'GET /api/invoice_documents', r'GET /api/meters', r'GET /api/readings', r'GET /api/payments',
    r'GET /api/fristen', r'GET /api/jahresabrechnung', r'GET /api/analytics/data-quality/\d+',
    r'GET /api/billing/suggestions', r'GET /api/billing/reports', r'GET /api/billing/reports/\d+/details',
    r'POST /api/billing/preflight', r'POST /api/readings/check_plausibility', r'POST /api/hausgeld/pruefen',
]

SAMPLE_ARGUMENTS = {
    'property_id': 1, 'tenant_id': 1, 'statement_id': 9, 'year': 2025, 'meter_id': 5,
    'start_date': '2025-01-01', 'end_date': '2025-12-31', 'reading_date': '2026-01-01', 'value': 30.0,
    'line_items': [], 'own_shares': 125, 'total_shares': 1000,
}


async def test_guard_every_read_tool_is_marked_and_calls_only_allowed_endpoints():
    fake = sample_data()
    fake.json('POST', '/api/billing/preflight', {'overall': 'excellent', 'checks': []})
    fake.json('POST', '/api/readings/check_plausibility', {'status': 'ok', 'message': ''})
    fake.json('POST', '/api/hausgeld/pruefen', {'positionen': []})
    fake.json('GET', '/api/auth/me', ME_READ).json('GET', '/api/ueber', ABOUT)
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, 'lesen')) as mcp:
        tools = (await mcp.list_tools()).tools
        for tool in tools:
            assert tool.annotations.read_only_hint is True, tool.name
            required = tool.input_schema.get('required', [])
            result = await mcp.call_tool(tool.name, {k: SAMPLE_ARGUMENTS[k] for k in required})
            assert not result.is_error, (tool.name, result.content[0].text)
    called = {f'{a.method} {a.url.path}' for a in fake.requests}
    for call_args in called:
        assert any(re.fullmatch(pattern, call_args) for pattern in ALLOWED_READ_ENDPOINTS), call_args
    assert len(tools) >= 18


async def test_old_statement_without_snapshot_returns_the_result_flat():
    fake = sample_data().json('GET', '/api/billing/reports/9/details', {
        'tenant_id': 1, 'tenant_name': 'Erika Muster', 'start_date': '2023-01-01', 'end_date': '2023-12-31',
        'total_amount': 700.0, 'prepaid_amount': '720.00', 'balance': '-20.00', 'warnings': [],
        'line_items': [{'category': 'Grundsteuer', 'tenant_cost': '200.00', 'total_amount': '450.00'}],
        'vorauszahlung': {'grund': 'Der Mieter ist ausgezogen.'}})
    data = await call(fake, 'get_statement', statement_id=9)
    assert (data['total_costs'], data['balance']) == ('700.00', '-20.00')
    assert data['line_items'][0]['tenant_share'] == '200.00'
    assert data['tenant'] == 'Erika Muster'
    assert data['new_advance_payment'] == {'reason': 'Der Mieter ist ausgezogen.'}


async def test_tenant_moving_in_in_the_future_is_not_current():
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    fake = sample_data()
    fake.json('GET', '/api/tenants', [
        {'id': 9, 'name': 'Nora Neu', 'apartment_id': 10, 'apartment_name': 'EG', 'property_name': 'Musterhaus',
         'move_in_date': tomorrow, 'move_out_date': None, 'last_billed_until': None, 'ist_beispiel': False}])
    assert (await call(fake, 'list_tenants', current_only=True))['items'] == []


async def test_statements_by_property():
    data = await call(sample_data(), 'list_statements', property_id=2)
    assert [a['id'] for a in data['items']] == [8]


async def test_unknown_payment_type_is_rejected_by_the_schema():
    nkf = client(sample_data())
    async with nkf, Client(build_server(nkf, 'lesen')) as mcp:
        result = await mcp.call_tool('list_payments', {'payment_type': 'Mite'})
    assert result.is_error
