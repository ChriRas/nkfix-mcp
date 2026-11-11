"""Resource templates (plan 06): one statement or one billing year, attached with ``@``.

Claude Code 2.1.322 lists templates in the ``@`` menu, asks for completion
when a template is picked there and reads the URI with ``resources/read``
(spike S0, 12.11.2026). It asks for no completion of prompt arguments.
"""

from datetime import date

import pytest
from mcp.client import Client
from mcp.shared.exceptions import MCPError
from mcp.types import PromptReference, ResourceTemplateReference

from nkfix_mcp.server import build_server
from tests.helpers import FakeNkf, client, json_response

pytestmark = pytest.mark.anyio

STATEMENT_TEMPLATE = 'nkfix://statements/{statement_id}'
YEAR_TEMPLATE = 'nkfix://properties/{property_id}/years/{year}'

DETAILS = {
    'tenant_id': 1,
    'start_date': '2025-01-01',
    'end_date': '2025-12-31',
    'version': 2,
    'veraltet': False,
    'frist_ende': '2026-12-31',
    'einwendungsfrist_ende': None,
    'zugestellt_am': None,
    'offen_betrag': '108.32',
    'zahlungsstand': 'offen',
    'vorauszahlung': {'bisher': '60.00', 'neu': '69.00', 'ab': '2026-12-01'},
    'ergebnis': {
        'tenant_name': 'Erika *Muster*',
        'apartment': 'EG',
        'property': 'Musterhaus',
        'total_amount': 828.32,
        'prepaid_amount': 720.0,
        'balance': 108.32,
        'line_items': [
            {
                'category': 'Grundsteuer',
                'billing_type': 'nach Wohnfläche',
                'period': '01.01.2025 - 31.12.2025',
                'invoice_id': 3,
                'total_amount': 460.0,
                'tenant_cost': 211.68,
                'description': '52 von 113 m² | Anteil',
            }
        ],
        'warnings': ['Die Rechnung für Wasser fehlt.'],
    },
}

YEAR = {
    'property_id': 1,
    'immobilie': 'Musterhaus',
    'jahr': 2025,
    'von': '2025-01-01',
    'bis': '2025-12-31',
    'frist_ende': '2026-12-31',
    'frist_tage': 65,
    'frist_ueberschritten': False,
    'kostenarten': [
        {
            'id': 1,
            'name': 'Grundsteuer',
            'anzahl': 1,
            'summe': 460.0,
            'summe_vorjahr': 450.5,
            'aenderung': 2,
            'sprung': False,
            'zustand': 'erfasst',
        },
        {
            'id': 2,
            'name': 'Wasser',
            'anzahl': 0,
            'summe': 0,
            'summe_vorjahr': 300.0,
            'aenderung': None,
            'sprung': True,
            'zustand': 'fehlt',
        },
    ],
    'ablesungen': [
        {
            'id': 5,
            'nummer': 'W-1',
            'kostenart': 'Wasser',
            'wohnung': 'EG',
            'letzte': '2025-12-01',
            'stichtag_ok': False,
            'wechsel_fehlen': [],
        }
    ],
    'mieter': [
        {
            'id': 1,
            'name': 'Erika Muster',
            'wohnung': 'EG',
            'von': '2025-01-01',
            'bis': '2025-12-31',
            'abrechnung_id': 9,
            'zugestellt_am': None,
        },
        {
            'id': 3,
            'name': 'Lisa Garten',
            'wohnung': 'OG',
            'von': '2025-04-01',
            'bis': '2025-12-31',
            'abrechnung_id': None,
            'zugestellt_am': None,
        },
    ],
    'pruefstand': [
        {'art': 'ohne_beleg', 'text': 'Eine Rechnung hat keinen Beleg.', 'tun': 'Laden Sie den Beleg hoch.'},
        {'art': 'hinweis', 'text': 'Der Abrechnungszeitraum ist kurz.', 'tun': None},
    ],
}


async def read(fake: FakeNkf, uri: str, scope: str = 'lesen') -> str:
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, scope)) as mcp:
        [content] = (await mcp.read_resource(uri)).contents
    assert content.mime_type == 'text/markdown'
    return content.text


async def read_error(fake: FakeNkf, uri: str) -> str:
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, 'lesen')) as mcp:
        with pytest.raises(MCPError) as error:
            await mcp.read_resource(uri)
    return str(error.value)


def statement_fake(statement_id: int = 9, **changes) -> FakeNkf:
    return FakeNkf().json('GET', f'/api/billing/reports/{statement_id}/details', {**DETAILS, **changes})


def result_with(**changes) -> dict:
    return {**DETAILS['ergebnis'], **changes}


# --- Listing -------------------------------------------------------------------


@pytest.mark.parametrize('scope', ['lesen', 'erfassen'])
async def test_both_templates_are_listed_in_german_and_call_no_api(scope):
    fake = FakeNkf()
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, scope)) as mcp:
        found = {t.uri_template: t for t in (await mcp.list_resource_templates()).resource_templates}
    assert {uri: t.title for uri, t in found.items()} == {
        STATEMENT_TEMPLATE: 'Abrechnung',
        YEAR_TEMPLATE: 'Jahresstand',
    }
    assert all(t.mime_type == 'text/markdown' and t.description for t in found.values())
    assert fake.requests == []


# --- T1: statement ---------------------------------------------------------------


async def test_statement_shows_head_line_items_result_and_deadlines():
    text = await read(statement_fake(), 'nkfix://statements/9')
    assert text.startswith('# Abrechnung 9: Erika \\*Muster\\*\n')
    assert '- Wohnung: EG, Musterhaus' in text
    assert '- Zeitraum: 01.01.2025 bis 31.12.2025' in text
    assert '- Version 2' in text
    assert 'veraltet' not in text
    assert '| Kostenart | Umlage | Zeitraum | Rechnungsbetrag | Anteil Mieter | Erläuterung |' in text
    assert (
        '| Grundsteuer | nach Wohnfläche | 01.01.2025 - 31.12.2025 | 460.00 € | 211.68 € | 52 von 113 m² \\| Anteil |'
        in text
    )
    for line in (
        '- Kosten des Mieters: 828.32 €',
        '- Vorauszahlungen: 720.00 €',
        '- Nachzahlung: 108.32 €',
        '- Zahlungsstand: offen, 108.32 € offen',
        '- Zustellung: noch nicht zugestellt',
        '- Abrechnungsfrist: 31.12.2026',
        '- Neue Vorauszahlung: von 60.00 € auf 69.00 € je Monat ab 01.12.2026',
    ):
        assert line in text, line
    assert 'Einwendungsfrist' not in text
    assert '## Hinweise\n\n- Die Rechnung für Wasser fehlt.' in text


async def test_statement_with_credit_delivery_and_objection_deadline():
    fake = statement_fake(
        ergebnis=result_with(balance=-50.0, warnings=[]),
        veraltet=True,
        zugestellt_am='2026-02-03',
        einwendungsfrist_ende='2027-02-28',
        offen_betrag='0.00',
        zahlungsstand='beglichen',
        vorauszahlung={'neu': None, 'grund': 'Der Mieter ist ausgezogen.'},
    )
    text = await read(fake, 'nkfix://statements/9')
    for line in (
        '- Version 2, veraltet: nach einem älteren Regelstand gerechnet, eine Korrektur könnte heute anders ausfallen',
        '- Guthaben: 50.00 €',
        '- Zahlungsstand: beglichen',
        '- Zustellung: am 03.02.2026',
        '- Einwendungsfrist: 28.02.2027',
        '- Keine neue Vorauszahlung: Der Mieter ist ausgezogen.',
    ):
        assert line in text, line
    assert 'Nachzahlung' not in text
    assert '## Hinweise' not in text


async def test_statement_with_zero_balance_and_no_line_items():
    fake = statement_fake(ergebnis=result_with(balance=0, line_items=[]), vorauszahlung=None)
    text = await read(fake, 'nkfix://statements/9')
    assert '- Saldo: 0.00 €' in text
    assert '## Positionen\n\nKeine.' in text
    assert 'Vorauszahlung:' not in text.replace('- Vorauszahlungen:', '')


@pytest.mark.parametrize(
    'value',
    ['abc', '-1', '1.5', '\u0661', '1234567890', '9' * 5000],
    ids=['letters', 'negative', 'decimal', 'arabic-indic', 'ten-digits', 'huge'],
)
async def test_statement_id_that_is_no_number_is_named_in_german(value):
    message = await read_error(FakeNkf(), f'nkfix://statements/{value}')
    # A long value is quoted only with its start.
    assert f'„{value[:9]}' in message
    assert '“ ist keine gültige Nummer einer Abrechnung' in message
    assert 'Offene Abrechnungen zeigt nkfix://overview, alle das Werkzeug list_statements' in message


async def test_unknown_statement_is_named_in_german():
    fake = FakeNkf().json('GET', '/api/billing/reports/99/details', {'error': 'Not Found'}, status=404)
    message = await read_error(fake, 'nkfix://statements/99')
    assert 'Abrechnung 99 gibt es nicht' in message
    assert 'alle das Werkzeug list_statements' in message


async def test_other_errors_of_the_statement_arrive_as_german_text():
    fake = FakeNkf().json('GET', '/api/billing/reports/9/details', {'error': 'kaputt'}, status=500)
    assert 'Serverfehler in NebenkostenFix' in await read_error(fake, 'nkfix://statements/9')


async def test_statement_tool_and_template_read_the_same_endpoint():
    fake = statement_fake()
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, 'lesen')) as mcp:
        tool = (await mcp.call_tool('get_statement', {'statement_id': 9})).structured_content
        await mcp.read_resource('nkfix://statements/9')
    assert len(fake.sent('GET', '/api/billing/reports/9/details')) == 2
    assert tool['balance'] == '108.32'


# --- T2: billing year --------------------------------------------------------------


def year_fake(**changes) -> FakeNkf:
    return FakeNkf().json('GET', '/api/jahresabrechnung', {**YEAR, **changes})


async def test_year_shows_period_deadline_costs_readings_tenants_and_open_issues():
    fake = year_fake()
    text = await read(fake, 'nkfix://properties/1/years/2025')
    assert dict(fake.sent('GET', '/api/jahresabrechnung')[0].url.params) == {'property_id': '1', 'jahr': '2025'}
    assert text.startswith('# Jahresstand 2025: Musterhaus (Immobilie 1)\n')
    for line in (
        '- Zeitraum: 01.01.2025 bis 31.12.2025',
        '- Abrechnungsfrist: 31.12.2026, noch 65 Tage',
        '| Kostenart | Rechnungen | Summe | Vorjahr | Änderung | Zustand |',
        '| Grundsteuer | 1 | 460.00 € | 450.50 € | +2 % | erfasst |',
        '| Wasser | 0 | 0.00 € | 300.00 € | Sprung | fehlt |',
        '| Zähler | Kostenart | Wohnung | Letzte Ablesung | Stichtag |',
        '| W-1 | Wasser | EG | 01.12.2025 | fehlt |',
        '- Erika Muster (Mieter 1), EG, 01.01.2025 bis 31.12.2025: Abrechnung 9, nicht zugestellt',
        '- Lisa Garten (Mieter 3), OG, 01.04.2025 bis 31.12.2025: noch keine Abrechnung',
        '- Eine Rechnung hat keinen Beleg. Laden Sie den Beleg hoch.\n- Der Abrechnungszeitraum ist kurz.\n',
    ):
        assert line in text, line


async def test_year_with_exceeded_deadline_and_empty_sections():
    fake = year_fake(
        frist_ueberschritten=True,
        frist_tage=-3,
        kostenarten=[],
        pruefstand=[],
        ablesungen=[
            {
                'id': 6,
                'nummer': 'S-2',
                'kostenart': 'Strom',
                'wohnung': 'ganzes Haus',
                'letzte': None,
                'stichtag_ok': True,
                'wechsel_fehlen': ['2025-04-01'],
            }
        ],
        mieter=[{**YEAR['mieter'][0], 'zugestellt_am': '2026-02-03'}],
    )
    text = await read(fake, 'nkfix://properties/1/years/2025')
    for line in (
        '- Abrechnungsfrist: 31.12.2026, überschritten',
        '## Kostenarten\n\nKeine.',
        '| S-2 | Strom | ganzes Haus | keine | ok, Zwischenablesung fehlt: 01.04.2025 |',
        ': Abrechnung 9, zugestellt am 03.02.2026',
        '## Offene Punkte\n\nKeine.',
    ):
        assert line in text, line


@pytest.mark.parametrize(
    ('uri', 'expected'),
    [
        ('nkfix://properties/x/years/2025', '„x“ ist keine gültige Nummer einer Immobilie'),
        ('nkfix://properties/1/years/abc', '„abc“ ist kein gültiges Abrechnungsjahr'),
        ('nkfix://properties/1/years/1999', '„1999“ ist kein gültiges Abrechnungsjahr'),
        ('nkfix://properties/1/years/2101', '„2101“ ist kein gültiges Abrechnungsjahr'),
    ],
)
async def test_wrong_year_arguments_are_named_in_german(uri, expected):
    fake = FakeNkf()
    assert expected in await read_error(fake, uri)
    assert fake.requests == []


async def test_unknown_property_keeps_the_message_of_nebenkostenfix():
    fake = FakeNkf().json('GET', '/api/jahresabrechnung', {'error': 'Diese Immobilie gibt es nicht.'}, status=400)
    assert 'Diese Immobilie gibt es nicht.' in await read_error(fake, 'nkfix://properties/99/years/2025')


async def test_year_tool_keeps_its_output():
    """The tool and the template share the mapping; the tool's output stays as it was."""
    fake = year_fake()
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, 'lesen')) as mcp:
        data = (await mcp.call_tool('get_billing_year_status', {'property_id': 1, 'year': 2025})).structured_content
    assert data['cost_categories'][1] == {
        'id': 2,
        'name': 'Wasser',
        'invoice_count': 0,
        'total': '0.00',
        'total_previous_year': '300.00',
        'change_percent': None,
        'jump': True,
        'state': 'fehlt',
    }
    assert data['tenants'][0]['statement_id'] == 9


# --- Completion --------------------------------------------------------------------

STATEMENTS = [{'id': i, 'tenant_name': 'X', 'start_date': '2025-01-01', 'end_date': '2025-12-31'} for i in (1, 9, 12)]
PROPERTIES = [{'id': 1, 'name': 'Musterhaus'}, {'id': 2, 'name': 'Gartenweg'}]


async def complete(fake: FakeNkf, ref, name: str, value: str) -> list[str]:
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, 'lesen')) as mcp:
        result = await mcp.complete(ref, {'name': name, 'value': value})
    return result.completion.values


async def test_statement_ids_are_offered_as_typed():
    fake = FakeNkf().json('GET', '/api/billing/reports', STATEMENTS)
    ref = ResourceTemplateReference(uri=STATEMENT_TEMPLATE)
    assert await complete(fake, ref, 'statement_id', '') == ['1', '9', '12']
    assert await complete(fake, ref, 'statement_id', '1') == ['1', '12']


async def test_property_ids_and_years_are_offered_for_the_year_template():
    fake = FakeNkf().json('GET', '/api/properties', PROPERTIES)
    ref = ResourceTemplateReference(uri=YEAR_TEMPLATE)
    assert await complete(fake, ref, 'property_id', '') == ['1', '2']
    this_year = date.today().year
    assert await complete(fake, ref, 'year', '') == [str(this_year - k) for k in range(3)]
    assert await complete(fake, ref, 'year', '1') == []


async def test_at_most_one_hundred_values():
    many = [{**STATEMENTS[0], 'id': i} for i in range(1, 151)]
    fake = FakeNkf().json('GET', '/api/billing/reports', many)
    values = await complete(fake, ResourceTemplateReference(uri=STATEMENT_TEMPLATE), 'statement_id', '')
    assert len(values) == 100


async def test_error_of_the_api_gives_an_empty_list():
    fake = FakeNkf().set_route('GET', '/api/billing/reports', json_response(500, {'error': 'kaputt'}))
    assert await complete(fake, ResourceTemplateReference(uri=STATEMENT_TEMPLATE), 'statement_id', '') == []


async def test_prompt_arguments_get_no_values():
    """Claude Code does not ask for them (S0); the handler answers empty without calling the API."""
    fake = FakeNkf()
    assert await complete(fake, PromptReference(name='prepare_year_end'), 'property_id', '') == []
    assert fake.requests == []


# --- Review: further cases -----------------------------------------------------------


async def test_credit_still_to_pay_out_is_named_like_in_the_overview():
    fake = statement_fake(ergebnis=result_with(balance=-50.0), offen_betrag='-50.00', zahlungsstand='offen')
    text = await read(fake, 'nkfix://statements/9')
    assert '- Zahlungsstand: offen, 50.00 € an den Mieter auszuzahlen' in text
    assert '-50.00 € offen' not in text


async def test_zero_outstanding_shows_when_not_settled():
    text = await read(statement_fake(offen_betrag='0.00', zahlungsstand='teilweise'), 'nkfix://statements/9')
    assert '- Zahlungsstand: teilweise, 0.00 € offen' in text


async def test_old_statement_without_version_and_tenant_name():
    """Statements before NK-061 have no snapshot and no version; NebenkostenFix computes them live."""
    fake = statement_fake(version=None, ergebnis=result_with(tenant_name=None))
    text = await read(fake, 'nkfix://statements/9')
    assert text.startswith('# Abrechnung 9\n')
    assert '- Version' not in text


async def test_leading_zeros_are_the_same_statement():
    fake = statement_fake()
    assert (await read(fake, 'nkfix://statements/009')).startswith('# Abrechnung 9: ')


async def test_year_without_tenants_and_without_previous_year():
    category = {**YEAR['kostenarten'][0], 'summe_vorjahr': None, 'aenderung': None, 'sprung': False}
    fake = year_fake(mieter=[], kostenarten=[category], frist_tage=1)
    text = await read(fake, 'nkfix://properties/1/years/2025')
    assert '## Mieter\n\nKeine.' in text
    assert '| Grundsteuer | 1 | 460.00 € | keine | - | erfasst |' in text
    assert '- Abrechnungsfrist: 31.12.2026, noch 1 Tag\n' in text


async def test_deadline_on_the_last_day():
    text = await read(year_fake(frist_tage=0), 'nkfix://properties/1/years/2025')
    assert '- Abrechnungsfrist: 31.12.2026, läuft heute ab' in text


async def test_billing_year_starting_in_july_shows_its_period():
    fake = year_fake(von='2025-07-01', bis='2026-06-30')
    assert '- Zeitraum: 01.07.2025 bis 30.06.2026' in await read(fake, 'nkfix://properties/1/years/2025')


async def test_completion_reports_total_and_has_more():
    many = [{**STATEMENTS[0], 'id': i} for i in range(1, 151)]
    fake = FakeNkf().json('GET', '/api/billing/reports', many)
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, 'lesen')) as mcp:
        result = await mcp.complete(
            ResourceTemplateReference(uri=STATEMENT_TEMPLATE), {'name': 'statement_id', 'value': ''}
        )
    assert (len(result.completion.values), result.completion.total, result.completion.has_more) == (100, 150, True)


async def test_unknown_argument_gets_no_values():
    fake = FakeNkf()
    assert await complete(fake, ResourceTemplateReference(uri=STATEMENT_TEMPLATE), 'other', '') == []
    assert fake.requests == []


async def test_error_during_completion_is_logged(caplog):
    fake = FakeNkf().set_route('GET', '/api/billing/reports', json_response(500, {'error': 'kaputt'}))
    with caplog.at_level('WARNING'):
        assert await complete(fake, ResourceTemplateReference(uri=STATEMENT_TEMPLATE), 'statement_id', '') == []
    assert any('statement_id' in r.getMessage() and r.levelname == 'WARNING' for r in caplog.records)
