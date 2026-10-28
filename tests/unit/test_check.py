from datetime import date

import pytest
from mcp.client import Client

from nkfix_mcp.server import build_server
from tests.helpers import FakeNkf, client

pytestmark = pytest.mark.anyio


async def call(fake: FakeNkf, tool: str, **arguments):
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, 'lesen')) as mcp:
        return await mcp.call_tool(tool, arguments)


async def test_precheck_sends_the_payload_and_translates_the_tiles():
    fake = FakeNkf().json(
        'POST',
        '/api/billing/preflight',
        {
            'overall': 'warning',
            'checks': [
                {
                    'category': 'Zeitraum',
                    'meter_type': '§ 556 Abs. 3 BGB',
                    'status': 'no_data',
                    'blocking': True,
                    'message': 'Der Zeitraum überschneidet sich mit einer Abrechnung.',
                    'meter_number': None,
                    'apartment': None,
                },
                {
                    'category': 'Wasser',
                    'meter_type': 'Kaltwasser',
                    'status': 'excellent',
                    'blocking': False,
                    'message': 'Stände vollständig.',
                    'meter_number': 'W-1',
                    'apartment': 'EG',
                },
            ],
        },
    )
    result = await call(
        fake,
        'precheck_statement',
        tenant_id=1,
        start_date='2025-01-01',
        end_date='2025-12-31',
        cost_category_ids=[1, 3],
    )
    assert FakeNkf.body(fake.requests[0]) == {
        'tenant_id': 1,
        'start_date': '2025-01-01',
        'end_date': '2025-12-31',
        'category_ids': [1, 3],
    }
    data = result.structured_content
    assert data['overall'] == 'warning'
    assert data['blocked'] is True
    assert data['checked_until'] == '2025-12-31'
    assert data['checks'][0] == {
        'category': 'Zeitraum',
        'meter_type': '§ 556 Abs. 3 BGB',
        'status': 'no_data',
        'blocking': True,
        'message': 'Der Zeitraum überschneidet sich mit einer Abrechnung.',
        'meter_number': None,
        'apartment': None,
    }


async def test_precheck_without_cost_categories_omits_the_field():
    fake = FakeNkf().json('POST', '/api/billing/preflight', {'overall': 'excellent', 'checks': []})
    await call(fake, 'precheck_statement', tenant_id=1, start_date='2025-01-01', end_date='2025-12-31')
    assert 'category_ids' not in FakeNkf.body(fake.requests[0])


async def test_date_is_checked_before_the_call():
    fake = FakeNkf()
    result = await call(fake, 'precheck_statement', tenant_id=1, start_date='1.1.2025', end_date='2025-12-31')
    assert result.is_error
    assert fake.requests == []


async def test_plausibility():
    fake = FakeNkf().json(
        'POST',
        '/api/readings/check_plausibility',
        {
            'status': 'warning',
            'message': 'Achtung! Der Wert weicht stark ab.',
            'closest_date': '2026-09-27',
            'closest_value': 2450.0,
            'diff_percent': 99.959,
        },
    )
    data = (await call(fake, 'check_meter_reading', meter_id=1, reading_date='2026-10-27', value=1)).structured_content
    assert FakeNkf.body(fake.requests[0]) == {'meter_id': 1, 'reading_date': '2026-10-27', 'value': 1.0}
    assert data == {
        'status': 'warning',
        'checked': True,
        'message': 'Achtung! Der Wert weicht stark ab.',
        'reference_date': '2026-09-27',
        'reference_value': 2450.0,
        'deviation_percent': 99.96,
    }


async def test_ok_without_reference_value_is_not_checked():
    fake = FakeNkf().json(
        'POST', '/api/readings/check_plausibility', {'status': 'ok', 'message': 'Keine zeitnahen Ablesungen gefunden.'}
    )
    data = (await call(fake, 'check_meter_reading', meter_id=1, reading_date='2026-10-27', value=1)).structured_content
    assert (data['status'], data['checked']) == ('ok', False)


async def test_plausibility_reports_errors_as_message_instead_of_error():
    fake = FakeNkf().json(
        'POST', '/api/readings/check_plausibility', {'status': 'unknown', 'message': 'Meter not found'}, status=404
    )
    result = await call(fake, 'check_meter_reading', meter_id=99, reading_date='2026-10-27', value=1)
    assert result.is_error
    assert 'Meter not found' in result.content[0].text


async def test_check_hausgeld():
    fake = FakeNkf().json(
        'POST',
        '/api/hausgeld/pruefen',
        {
            'positionen': [
                {
                    'name': 'Hausmeister',
                    'gesamtbetrag': '2400.00',
                    'eigener_betrag': '300.00',
                    'umlagefaehig': True,
                    'betrkv_nr': 14,
                    'begruendung': '§ 2 Nr. 14 BetrKV',
                    'erwartet': '300.00',
                    'abweichung': '0.00',
                    'stimmt': True,
                    'hinweis': None,
                }
            ],
            'summe_umlagefaehig': '300',
            'summe_eigentuemer': '0',
            'summe_offen': '0',
            'anzahl_umlagefaehig': 1,
            'anzahl_eigentuemer': 0,
            'anzahl_offen': 0,
            'summenprobe': None,
            'zeitraum_hinweis': 'Abrechnungszeitraum beachten',
            'heizkosten_hinweis': 'Heizkosten gesondert',
        },
    )
    result = await call(
        fake,
        'check_hausgeld',
        line_items=[{'name': 'Hausmeister', 'total_amount': '2400.00', 'own_amount': '300.00'}],
        own_shares=125,
        total_shares=1000,
    )
    assert FakeNkf.body(fake.requests[0]) == {
        'positionen': [{'name': 'Hausmeister', 'gesamtbetrag': '2400.00', 'eigener_betrag': '300.00'}],
        'eigener_anteil': 125.0,
        'gesamtanteile': 1000.0,
    }
    data = result.structured_content
    assert data['summe_umlagefaehig'] == '300.00'
    assert data['summe_eigentuemer'] == '0.00'
    assert data['line_items'][0]['umlagefaehig'] is True
    assert data['notes'] == ['Abrechnungszeitraum beachten', 'Heizkosten gesondert']


async def test_end_in_the_future_is_reported_as_checked_until_today():
    fake = FakeNkf().json(
        'POST',
        '/api/billing/preflight',
        {
            'overall': 'acceptable',
            'checks': [{'category': 'Wasser', 'status': 'acceptable', 'blocking': True, 'message': 'interpoliert'}],
        },
    )
    data = (
        await call(fake, 'precheck_statement', tenant_id=1, start_date='2026-01-01', end_date='2099-12-31')
    ).structured_content
    assert data['checked_until'] == date.today().isoformat()
    assert data['blocked'] is False


async def test_hausgeld_requires_the_shares():
    result = await call(FakeNkf(), 'check_hausgeld', line_items=[])
    assert result.is_error


@pytest.mark.parametrize(
    ('tool', 'arguments'),
    [
        ('check_meter_reading', {'meter_id': 5, 'reading_date': '2025-02-30', 'value': 1}),
        ('precheck_statement', {'tenant_id': 1, 'start_date': '2025-01-01', 'end_date': '2025-02-30'}),
        ('list_deadlines', {'until': '2027-13-01'}),
    ],
)
async def test_impossible_date_is_rejected_clearly_and_not_sent(tool, arguments):
    fake = FakeNkf()
    result = await call(fake, tool, **arguments)
    assert result.is_error
    assert 'kein gültiges Datum' in result.content[0].text
    assert not fake.requests
