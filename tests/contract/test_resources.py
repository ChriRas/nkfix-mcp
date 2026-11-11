"""Resources and prompts against a real instance (fantasy data, fresh data directory)."""

from datetime import date
from decimal import Decimal

import pytest
from mcp.shared.exceptions import MCPError
from mcp.types import ResourceTemplateReference

from tests.contract.helpers import call, connected

pytestmark = pytest.mark.anyio


async def test_overview_shows_the_example_property_with_its_tenants_and_statement(instance):
    async with connected(instance, 'lesen') as mcp:
        tenants = (await call(mcp, 'list_tenants'))['items']
        house = next(i for i in (await call(mcp, 'list_properties'))['items'] if i['is_example'])
        statements = (await call(mcp, 'list_statements'))['items']
        [content] = (await mcp.read_resource('nkfix://overview')).contents
    overview = content.text
    assert f'## {house["name"]} (Immobilie {house["id"]})' in overview
    for tenant in tenants:
        assert f'{tenant["name"]} (Mieter {tenant["id"]})' in overview
    open_statements = [
        s
        for s in statements
        if s['payment_status'] not in ('beglichen', 'ausgeglichen')
        or not s['delivered_at']
        or s['outstanding_amount'] not in (None, '0.00')
    ]
    for statement in open_statements:
        assert f'Abrechnung {statement["id"]}: ' in overview
    assert '## Fristen der nächsten 90 Tage' in overview


async def test_deadlines_resource_matches_the_deadline_tool(instance):
    async with connected(instance, 'lesen') as mcp:
        deadlines = (await call(mcp, 'list_deadlines'))['items']
        statements = {s['id']: s for s in (await call(mcp, 'list_statements'))['items']}
        [content] = (await mcp.read_resource('nkfix://deadlines')).contents
    for deadline in deadlines:
        assert deadline['title'] in content.text
        assert f'({deadline["kind"]})' in content.text
    # Built independently from list_statements: statement and period as the line names them.
    named = [d for d in deadlines if d['statement_id'] is not None]
    assert named, 'Die Beispielimmobilie sollte eine Frist mit Abrechnung haben.'
    for deadline in named:
        statement = statements[deadline['statement_id']]
        start, end = (date.fromisoformat(statement[k]).strftime('%d.%m.%Y') for k in ('start_date', 'end_date'))
        assert f'{deadline["title"]} (Abrechnung {statement["id"]}, {start} bis {end}) ({deadline["kind"]})' in (
            content.text
        )


async def test_prompts_need_no_api_and_follow_the_scope(instance):
    async with connected(instance, 'lesen') as mcp:
        names = {p.name for p in (await mcp.list_prompts()).prompts}
    assert names == {'prepare_year_end', 'check_hausgeld_statement'}
    async with connected(instance, 'erfassen') as mcp:
        names = {p.name for p in (await mcp.list_prompts()).prompts}
        result = await mcp.get_prompt('prepare_year_end', {'property_id': '1', 'year': '2025'})
    assert 'record_receipt' in names
    assert 'get_billing_year_status' in result.messages[0].content.text


async def test_statement_template_shows_the_figures_of_get_statement(instance):
    async with connected(instance, 'lesen') as mcp:
        statement_id = (await call(mcp, 'list_statements'))['items'][0]['id']
        details = await call(mcp, 'get_statement', statement_id=statement_id)
        [content] = (await mcp.read_resource(f'nkfix://statements/{statement_id}')).contents
    text = content.text
    assert text.startswith(f'# Abrechnung {statement_id}: ')
    assert f'- Kosten des Mieters: {details["total_costs"]} €' in text
    assert f'- Vorauszahlungen: {details["advance_payments"]} €' in text
    balance = Decimal(details['balance'])
    expected = {
        1: f'- Nachzahlung: {balance:.2f} €',
        -1: f'- Guthaben: {-balance:.2f} €',
        0: f'- Saldo: {balance:.2f} €',
    }
    assert expected[(balance > 0) - (balance < 0)] in text
    for item in details['line_items']:
        assert f'| {item["tenant_share"]} € |' in text, item


async def test_unknown_statement_is_named(instance):
    async with connected(instance, 'lesen') as mcp:
        with pytest.raises(MCPError, match='Abrechnung 9999 gibt es nicht'):
            await mcp.read_resource('nkfix://statements/9999')


async def test_year_template_shows_the_figures_of_get_billing_year_status(instance):
    async with connected(instance, 'lesen') as mcp:
        house = next(i for i in (await call(mcp, 'list_properties'))['items'] if i['is_example'])
        statement = (await call(mcp, 'list_statements', property_id=house['id']))['items'][0]
        year = int(statement['start_date'][:4])
        status = await call(mcp, 'get_billing_year_status', property_id=house['id'], year=year)
        [content] = (await mcp.read_resource(f'nkfix://properties/{house["id"]}/years/{year}')).contents
    text = content.text
    assert text.startswith(f'# Jahresstand {year}: ')
    for category in status['cost_categories']:
        assert f'| {category["total"]} € |' in text, category
    for tenant in status['tenants']:
        assert f'(Mieter {tenant["id"]})' in text


async def test_completion_offers_the_real_ids(instance):
    async with connected(instance, 'lesen') as mcp:
        ids = [str(s['id']) for s in (await call(mcp, 'list_statements'))['items']]
        result = await mcp.complete(
            ResourceTemplateReference(uri='nkfix://statements/{statement_id}'), {'name': 'statement_id', 'value': ''}
        )
    assert result.completion.values == ids[:100]
