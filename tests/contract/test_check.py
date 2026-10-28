from datetime import date, timedelta

import pytest

from nkfix_mcp.client import NkfClient
from nkfix_mcp.config import Config
from tests.contract.helpers import call, connected

pytestmark = pytest.mark.anyio

SAMPLE_DATA = ['/api/invoices', '/api/readings', '/api/payments', '/api/billing/reports', '/api/invoice_documents']


async def sample_data(instance) -> dict:
    async with NkfClient(Config.from_env(instance.env('sitzung'))) as nkf:
        return {path: await nkf.get(path) for path in SAMPLE_DATA}


async def test_checks_work_with_read_key_and_change_nothing(instance):
    previous = await sample_data(instance)
    async with connected(instance, 'lesen') as mcp:
        tenants = (await call(mcp, 'list_tenants'))['items']
        anna = next(m['id'] for m in tenants if m['name'] == 'Anna Beispiel')
        house = next(i['id'] for i in (await call(mcp, 'list_properties'))['items'] if i['is_example'])

        # 2025 is already billed for Anna: the precheck reports the overlap.
        precheck = await call(mcp, 'precheck_statement', tenant_id=anna, start_date='2025-01-01', end_date='2025-12-31')
        assert precheck['overall'] == 'warning'
        assert any(p['category'] == 'Zeitraum' and p['blocking'] for p in precheck['checks'])
        assert all(p['status'] for p in precheck['checks'])

        meter = next(z for z in (await call(mcp, 'list_meters', property_id=house))['items'] if z['readings'])
        last = meter['readings'][0]
        # Only a reading within 30 days is compared against: one day later.
        next_day = (date.fromisoformat(last['reading_date']) + timedelta(days=1)).isoformat()
        implausible = await call(
            mcp, 'check_meter_reading', meter_id=meter['id'], reading_date=next_day, value=(last['value'] or 0) / 1000
        )
        assert (implausible['status'], implausible['checked']) == ('warning', True)
        assert implausible['reference_date']

        hausgeld = await call(
            mcp,
            'check_hausgeld',
            line_items=[
                {'name': 'Hausmeister', 'total_amount': '2400.00', 'own_amount': '300.00'},
                {'name': 'Instandhaltungsrücklage', 'total_amount': '8000.00', 'own_amount': '1000.00'},
            ],
            own_shares=125,
            total_shares=1000,
        )
        assert hausgeld['line_items'][0]['allocable'] is True
        assert hausgeld['line_items'][1]['allocable'] is False
        # Every field is mapped from a real key: a renamed key upstream would leave it None.
        janitor = hausgeld['line_items'][0]
        assert None not in (
            janitor['total_amount'],
            janitor['own_amount'],
            janitor['reason'],
            janitor['expected_own_amount'],
            janitor['deviation'],
            janitor['matches'],
        )
        assert janitor['betrkv_number'] == 14
        assert (hausgeld['allocable_count'], hausgeld['owner_count'], hausgeld['undecided_count']) == (1, 1, 0)
        assert (hausgeld['allocable_total'], hausgeld['owner_total']) == ('300.00', '1000.00')
        assert hausgeld['notes']
    assert await sample_data(instance) == previous
