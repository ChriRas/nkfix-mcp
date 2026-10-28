"""Recording against a real instance (fantasy data, fresh data directory)."""

from datetime import date, timedelta

import pytest
from mcp.client import Client

from nkfix_mcp.client import NkfClient
from nkfix_mcp.config import Config
from nkfix_mcp.server import build_server
from tests.contract.helpers import call, connected

pytestmark = pytest.mark.anyio

TODAY = date.today()
RECORD_TOOLS = {'record_payment', 'record_meter_reading', 'record_invoice'}


async def test_read_key_sees_no_record_tools(instance):
    async with connected(instance, 'lesen') as mcp:
        names = {w.name for w in (await mcp.list_tools()).tools}
    assert not names & RECORD_TOOLS


async def test_read_key_gets_clear_message_when_recording(instance):
    # Scope unknown at startup: all tools present, the instance answers 403.
    async with NkfClient(Config.from_env(instance.env('lesen'))) as nkf, Client(build_server(nkf, None)) as mcp:
        result = await mcp.call_tool(
            'record_payment',
            {'tenant_id': 1, 'amount': '1', 'payment_date': TODAY.isoformat(), 'payment_type': 'Miete'},
        )
    assert result.is_error
    assert 'Lesen und erfassen' in result.content[0].text


async def test_create_payment_find_it_again_and_detect_duplicate(instance):
    async with connected(instance, 'erfassen') as mcp:
        assert {w.name for w in (await mcp.list_tools()).tools} >= RECORD_TOOLS
        ben = next(m['id'] for m in (await call(mcp, 'list_tenants'))['items'] if m['name'] == 'Ben Beispiel')
        payment = {'tenant_id': ben, 'amount': '711,11', 'payment_date': TODAY.isoformat(), 'payment_type': 'Miete'}

        first = await call(mcp, 'record_payment', **payment)
        assert first['saved'] is True
        assert first['id']
        found = (await call(mcp, 'list_payments', tenant_id=ben, year=TODAY.year, payment_type='Miete'))['items']
        assert any(z['id'] == first['id'] and z['amount'] == '711.11' for z in found)

        second = await call(mcp, 'record_payment', **payment)
        assert second['saved'] is False
        assert second['concerns']

        # The human agrees: with the code from the response it is saved.
        third = await call(mcp, 'record_payment', **payment, approval_code=second['approval_code'])
        assert third['saved'] is True
        assert third['id'] not in (None, first['id'])

        # The same code a second time: the new payment is a new concern.
        fourth = await call(mcp, 'record_payment', **payment, approval_code=second['approval_code'])
        assert fourth['saved'] is False
        assert 'passt nicht' in fourth['note']


async def test_settlement_payment_with_statement_settles_it(instance):
    async with connected(instance, 'erfassen') as mcp:
        tenants = (await call(mcp, 'list_tenants'))['items']
        anna = next(m['id'] for m in tenants if m['name'] == 'Anna Beispiel')
        ben = next(m['id'] for m in tenants if m['name'] == 'Ben Beispiel')
        statement = (await call(mcp, 'list_statements', tenant_id=anna))['items'][0]
        assert statement['payment_status'] == 'offen'
        assert statement['outstanding_amount'] not in (None, '0.00')
        payment = {
            'tenant_id': anna,
            'amount': statement['outstanding_amount'],
            'payment_date': TODAY.isoformat(),
            'payment_type': 'Nebenkostenzahlung',
        }

        without = await call(mcp, 'record_payment', **payment)
        assert without['saved'] is False
        assert 'keiner Abrechnung zugeordnet' in without['concerns'][0]

        # Ben has no statement of his own; Anna's is not his.
        foreign = await mcp.call_tool('record_payment', {**payment, 'tenant_id': ben, 'statement_id': statement['id']})
        assert foreign.is_error
        assert 'gibt es für Mieter' in foreign.content[0].text

        linked = await call(mcp, 'record_payment', **payment, statement_id=statement['id'])
        assert linked['saved'] is True
        assert linked['concerns'] == []
        after = (await call(mcp, 'list_statements', tenant_id=anna))['items'][0]
        assert (after['payment_status'], after['outstanding_amount']) == ('beglichen', '0.00')


async def test_backwards_reading_asks_for_confirmation_forward_reading_is_saved(instance):
    async with connected(instance, 'erfassen') as mcp:
        house = next(i['id'] for i in (await call(mcp, 'list_properties'))['items'] if i['is_example'])
        meter = next(
            z
            for z in (await call(mcp, 'list_meters', property_id=house))['items']
            if z['readings'] and not z['dual_tariff']
        )
        last = meter['readings'][0]
        reading_date = max(date.fromisoformat(last['reading_date']) + timedelta(days=1), TODAY - timedelta(days=1))

        backwards = await call(
            mcp,
            'record_meter_reading',
            meter_id=meter['id'],
            reading_date=reading_date.isoformat(),
            value=max(last['value'] - 1, 0),
        )
        assert backwards['saved'] is False
        assert any('rückwärts' in b for b in backwards['concerns'])

        # Without approval: a reading that runs forwards needs no confirmation prompt.
        forwards = await call(
            mcp,
            'record_meter_reading',
            meter_id=meter['id'],
            reading_date=reading_date.isoformat(),
            value=last['value'] + 0.5,
        )
        assert forwards['saved'] is True, forwards
        assert forwards['concerns'] == []
        new = (await call(mcp, 'list_meters', property_id=house, meter_id=meter['id']))['items'][0]
        assert new['readings'][0]['id'] == forwards['id']


async def test_instance_rejects_meter_reading_in_the_future(instance):
    async with connected(instance, 'erfassen') as mcp:
        house = next(i['id'] for i in (await call(mcp, 'list_properties'))['items'] if i['is_example'])
        meter = (await call(mcp, 'list_meters', property_id=house))['items'][0]
        reading = {'meter_id': meter['id'], 'reading_date': (TODAY + timedelta(days=30)).isoformat(), 'value': 1e9}
        result = await mcp.call_tool('record_meter_reading', reading)
        if not result.is_error:
            # If plausibility warns, it only reaches the instance with approval.
            approval_code = result.structured_content['approval_code']
            result = await mcp.call_tool('record_meter_reading', {**reading, 'approval_code': approval_code})
    assert result.is_error
    assert 'Zukunft' in result.content[0].text


async def test_create_invoice_find_it_again_and_number_counts_as_duplicate(instance):
    async with connected(instance, 'erfassen') as mcp:
        house = next(i['id'] for i in (await call(mcp, 'list_properties'))['items'] if i['is_example'])
        cost_category = next(k for k in (await call(mcp, 'list_cost_categories'))['items'] if not k['requires_meter'])
        invoice = {
            'property_id': house,
            'cost_category_id': cost_category['id'],
            'amount': '123.45',
            'start_date': f'{TODAY.year}-01-01',
            'end_date': f'{TODAY.year}-03-31',
            'invoice_number': 'VERTRAG-1',
            'description': 'Vertragstest',
        }
        new = await call(mcp, 'record_invoice', **invoice)
        assert new['saved'] is True
        assert new['id']

        items = (
            await call(mcp, 'list_invoices', property_id=house, year=TODAY.year, cost_category_id=cost_category['id'])
        )['items']
        found = next(r for r in items if r['id'] == new['id'])
        assert (found['amount'], found['invoice_number'], found['has_receipt']) == ('123.45', 'VERTRAG-1', False)

        again = await call(mcp, 'record_invoice', **{**invoice, 'amount': '1.00'})
        assert again['saved'] is False
        assert any(f'Rechnung {new["id"]}' in b for b in again['concerns'])


async def test_field_guard_billing_suggestions(instance):
    """The sample property has no suggestions; after a recorded invoice for Ben it does."""
    async with connected(instance, 'erfassen') as mcp:
        house = next(i['id'] for i in (await call(mcp, 'list_properties'))['items'] if i['is_example'])
        cost_category = next(k for k in (await call(mcp, 'list_cost_categories'))['items'] if not k['requires_meter'])
        await call(
            mcp,
            'record_invoice',
            property_id=house,
            cost_category_id=cost_category['id'],
            amount='10',
            start_date=f'{TODAY.year}-04-10',
            end_date=f'{TODAY.year}-05-31',
            invoice_number='VORSCHLAG-1',
        )
    async with NkfClient(Config.from_env(instance.env('sitzung'))) as nkf:
        suggestions = await nkf.get('/api/billing/suggestions')
    assert suggestions, 'nach der Rechnung sollte es einen Vorschlag geben'
    assert {'tenant_id', 'tenant_name', 'apartment_name', 'suggested_start', 'suggested_end', 'categories'} <= set(
        suggestions[0]
    )
    assert {'category_id', 'category_name', 'suggested_start', 'suggested_end', 'invoice_count'} <= set(
        suggestions[0]['categories'][0]
    )


async def test_invoice_with_labor_cost_under_35a(instance):
    async with connected(instance, 'erfassen') as mcp:
        house = next(i['id'] for i in (await call(mcp, 'list_properties'))['items'] if i['is_example'])
        cost_category = next(k for k in (await call(mcp, 'list_cost_categories'))['items'] if not k['requires_meter'])
        new = await call(
            mcp,
            'record_invoice',
            property_id=house,
            cost_category_id=cost_category['id'],
            amount='300',
            start_date=f'{TODAY.year}-06-01',
            end_date=f'{TODAY.year}-06-30',
            invoice_number='35A-1',
            section_35a_category='handwerker',
            labor_cost='120,00',
        )
        assert new['saved'] is True
