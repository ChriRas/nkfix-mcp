"""Resources: data the human attaches with @, e.g. @nkfix:nkfix://overview.

They are built fresh on every read from the same mapping as the tools. An
error from NebenkostenFix reaches the client as a ResourceError with its
German text; any other exception would only arrive as "Error reading
resource".
"""

from datetime import date, timedelta

import pytest
from mcp.client import Client
from mcp.shared.exceptions import MCPError

from nkfix_mcp.server import build_server
from nkfix_mcp.tools import MAX_ITEMS
from tests.helpers import FakeNkf, client

pytestmark = pytest.mark.anyio

TODAY = date.today()
SOON = (TODAY + timedelta(days=30)).isoformat()
LATER = (TODAY + timedelta(days=200)).isoformat()


def tenant(tenant_id: int, name: str, apartment_id: int = 10, **values) -> dict:
    return {
        'id': tenant_id,
        'name': name,
        'apartment_id': apartment_id,
        'apartment_name': 'EG',
        'property_name': 'Musterhaus',
        'move_in_date': '2024-07-01',
        'move_out_date': None,
        'last_billed_until': '2025-12-31',
        'ist_beispiel': False,
        **values,
    }


def statement(statement_id: int, status: str | None, outstanding: str | None, delivered: str | None = None) -> dict:
    return {
        'id': statement_id,
        'tenant_id': 1,
        'tenant_name': 'Erika Muster',
        'apartment_name': 'EG',
        'property_id': 1,
        'property_name': 'Musterhaus',
        'start_date': '2025-01-01',
        'end_date': '2025-12-31',
        'created_at': '27.10.2026',
        'veraltet': False,
        'zugestellt_am': delivered,
        'offen_betrag': outstanding,
        'zahlungsstand': status,
    }


def sample_data(tenants: list[dict] | None = None) -> FakeNkf:
    fake = FakeNkf()
    fake.json(
        'GET',
        '/api/properties',
        [
            {
                'id': 1,
                'name': 'Musterhaus',
                'abrechnungsjahr_beginn': '01.01.',
                'is_standalone': False,
                'ist_beispiel': False,
            },
            {
                'id': 2,
                'name': 'Gartenweg 3',
                'abrechnungsjahr_beginn': '01.07.',
                'is_standalone': True,
                'ist_beispiel': False,
            },
        ],
    )
    fake.json(
        'GET',
        '/api/apartments',
        [{'id': 10, 'name': 'EG', 'property_id': 1}, {'id': 20, 'name': 'Haus', 'property_id': 2}],
    )
    fake.json(
        'GET',
        '/api/tenants',
        tenants
        if tenants is not None
        else [
            tenant(1, 'Erika Muster'),
            tenant(3, 'Lisa Garten', apartment_id=20, apartment_name='Haus', last_billed_until=None),
        ],
    )
    fake.json(
        'GET',
        '/api/billing/reports',
        [statement(9, 'offen', '108.32'), statement(8, 'beglichen', '0.00', delivered='2026-02-03')],
    )
    fake.json(
        'GET',
        '/api/fristen',
        {
            'termine': [
                {'datum': SOON, 'art': 'abrechnungsfrist', 'titel': 'Ende der Abrechnungsfrist: Erika Muster'},
                {'datum': LATER, 'art': 'eichfrist', 'titel': 'Eichfrist W-1'},
            ]
        },
    )
    return fake


async def read(fake: FakeNkf, uri: str, scope='erfassen') -> str:
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, scope)) as mcp:
        [content] = (await mcp.read_resource(uri)).contents
    assert content.mime_type == 'text/markdown'
    return content.text


def german(day: str) -> str:
    return date.fromisoformat(day).strftime('%d.%m.%Y')


@pytest.mark.parametrize('scope', ['lesen', 'erfassen'])
async def test_resources_are_listed_with_german_titles(scope):
    nkf = client(FakeNkf())
    async with nkf, Client(build_server(nkf, scope)) as mcp:
        found = {str(r.uri): r for r in (await mcp.list_resources()).resources}
    assert {uri: r.title for uri, r in found.items()} == {
        'nkfix://overview': 'Überblick',
        'nkfix://deadlines': 'Fristen',
    }
    assert all(r.mime_type == 'text/markdown' and r.description for r in found.values())


async def test_overview_shows_properties_tenants_open_statements_and_near_deadlines():
    overview = await read(sample_data(), 'nkfix://overview')
    assert '## Musterhaus (Immobilie 1)' in overview
    assert '## Gartenweg 3 (Immobilie 2)' in overview
    assert 'Abrechnungsjahr beginnt am 01.01.' in overview
    assert '- Erika Muster (Mieter 1), EG, eingezogen am 01.07.2024, abgerechnet bis 31.12.2025' in overview
    assert '- Lisa Garten (Mieter 3), Haus, eingezogen am 01.07.2024, noch nicht abgerechnet' in overview
    assert overview.index('Lisa Garten') > overview.index('## Gartenweg 3')
    assert (
        '- Abrechnung 9: Erika Muster, 01.01.2025 bis 31.12.2025, offen, 108.32 € offen, nicht zugestellt' in overview
    )
    assert 'Abrechnung 8' not in overview
    assert '1 weitere Abrechnung ist beglichen und zugestellt.' in overview
    assert f'- {german(SOON)}: Ende der Abrechnungsfrist: Erika Muster' in overview
    assert 'Eichfrist W-1' not in overview


async def test_overview_shortens_long_lists_with_a_note():
    many = [tenant(i, f'Mieter Nummer {i}') for i in range(1, MAX_ITEMS + 6)]
    overview = await read(sample_data(many), 'nkfix://overview')
    assert f'Mieter Nummer {MAX_ITEMS}' in overview
    assert f'Mieter Nummer {MAX_ITEMS + 1}' not in overview
    assert '5 weitere Mieter weggelassen' in overview


async def test_deadlines_lists_all_upcoming_deadlines():
    deadlines = await read(sample_data(), 'nkfix://deadlines')
    assert f'- {german(SOON)}: Ende der Abrechnungsfrist: Erika Muster (abrechnungsfrist)' in deadlines
    assert f'- {german(LATER)}: Eichfrist W-1 (eichfrist)' in deadlines


async def test_resource_works_with_a_read_key():
    assert 'Musterhaus' in await read(sample_data(), 'nkfix://overview', scope='lesen')


async def test_error_from_nebenkostenfix_arrives_as_german_text():
    fake = sample_data().json('GET', '/api/properties', {'error': 'kaputt'}, status=500)
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, 'erfassen')) as mcp:
        with pytest.raises(MCPError, match='Serverfehler in NebenkostenFix'):
            await mcp.read_resource('nkfix://overview')


async def test_instructions_mention_the_overview():
    assert 'nkfix://overview' in build_server(client(FakeNkf()), 'lesen').instructions


def deadline(days: int, title: str, kind: str = 'eichfrist') -> dict:
    return {'datum': (TODAY + timedelta(days=days)).isoformat(), 'art': kind, 'titel': title}


async def test_overview_window_is_ninety_days_inclusive():
    fake = sample_data().json('GET', '/api/fristen', {'termine': [deadline(90, 'Tag 90'), deadline(91, 'Tag 91')]})
    overview = await read(fake, 'nkfix://overview')
    assert 'Tag 90' in overview
    assert 'Tag 91' not in overview


async def test_without_deadlines_both_resources_say_so():
    fake = sample_data().json('GET', '/api/fristen', {'termine': []})
    assert overview_tail(await read(fake, 'nkfix://overview')).endswith('Keine.')
    assert (await read(fake, 'nkfix://deadlines')).strip().endswith('Keine.')


def overview_tail(overview: str) -> str:
    return overview.strip().split('## Fristen der nächsten 90 Tage')[1].strip()


async def test_statement_line_shows_delivery_date_credit_and_missing_status_in_words():
    fake = sample_data()
    fake.json(
        'GET',
        '/api/billing/reports',
        [
            statement(5, 'offen', '-120.00', delivered='2026-02-03'),
            statement(6, None, None),
        ],
    )
    overview = await read(fake, 'nkfix://overview')
    assert (
        'Abrechnung 5: Erika Muster, 01.01.2025 bis 31.12.2025, offen, 120.00 € an den Mieter auszuzahlen, '
        'zugestellt am 03.02.2026' in overview
    )
    assert 'Abrechnung 6: Erika Muster, 01.01.2025 bis 31.12.2025, ohne Zahlungsstand, nicht zugestellt' in overview


async def test_names_cannot_break_the_markdown():
    fake = sample_data()
    fake.json(
        'GET',
        '/api/properties',
        [
            {
                'id': 1,
                'name': 'Haus *A*\n## Fake',
                'abrechnungsjahr_beginn': '01.01.',
                'is_standalone': False,
                'ist_beispiel': False,
            }
        ],
    )
    overview = await read(fake, 'nkfix://overview')
    assert '\n## Fake' not in overview
    assert '## Haus \\*A\\* \\#\\# Fake (Immobilie 1)' in overview


async def test_long_lists_of_open_statements_and_deadlines_are_shortened():
    fake = sample_data()
    fake.json('GET', '/api/billing/reports', [statement(i, 'offen', '1.00') for i in range(1, MAX_ITEMS + 3)])
    fake.json('GET', '/api/fristen', {'termine': [deadline(1, f'Frist {i}') for i in range(MAX_ITEMS + 4)]})
    overview = await read(fake, 'nkfix://overview')
    assert '2 weitere Abrechnungen weggelassen' in overview
    assert '4 weitere Fristen weggelassen' in overview
    assert '4 weitere Fristen weggelassen' in await read(fake, 'nkfix://deadlines')


async def test_deadlines_description_names_all_kinds_of_upstream():
    nkf = client(FakeNkf())
    async with nkf, Client(build_server(nkf, 'lesen')) as mcp:
        found = {str(r.uri): r for r in (await mcp.list_resources()).resources}
    for kind in ('Abrechnungs', 'Einwendungs', 'Eich', 'Ablesung', 'Vorauszahlung', 'Mieterhöhung'):
        assert kind in found['nkfix://deadlines'].description


async def test_settled_statements_are_not_called_further_ones_when_none_is_open():
    fake = sample_data()
    fake.json('GET', '/api/billing/reports', [statement(8, 'beglichen', '0.00', delivered='2026-02-03')])
    overview = await read(fake, 'nkfix://overview')
    assert '1 Abrechnung ist beglichen und zugestellt.' in overview
    assert 'weitere' not in overview


async def test_settled_but_undelivered_statement_stays_visible():
    # The deadline is only kept once the statement reaches the tenant (§ 556 Abs. 3 BGB).
    fake = sample_data()
    fake.json(
        'GET',
        '/api/billing/reports',
        [statement(8, 'beglichen', '0.00'), statement(7, 'beglichen', '0.00', delivered='2026-02-03')],
    )
    overview = await read(fake, 'nkfix://overview')
    assert 'Abrechnungen mit offenem Punkt:' in overview
    assert '- Abrechnung 8: Erika Muster, 01.01.2025 bis 31.12.2025, beglichen, nicht zugestellt' in overview
    assert '0.00 €' not in overview
    assert 'Abrechnung 7' not in overview
    assert '1 weitere Abrechnung ist beglichen und zugestellt.' in overview
