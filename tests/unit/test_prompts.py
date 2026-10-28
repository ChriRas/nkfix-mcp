"""Prompts: ready-made workflows the human starts as a command, e.g. /nkfix:prepare_year_end 1 2025.

A prompt only returns an instruction; the assistant then fetches the data
with the tools. A prompt therefore never calls the API itself. A missing
required argument fails in the SDK, and an exception in a prompt reaches
Claude Code as "Internal server error", so a bad or missing argument
becomes an instruction to ask the human, not an error.
"""

import pytest
from mcp.client import Client

from nkfix_mcp.server import build_server
from tests.helpers import FakeNkf, client

pytestmark = pytest.mark.anyio

READ_PROMPTS = {'prepare_year_end', 'check_hausgeld_statement'}
ALL_PROMPTS = READ_PROMPTS | {'record_receipt'}


async def prompts(scope):
    nkf = client(FakeNkf())
    async with nkf, Client(build_server(nkf, scope)) as mcp:
        return {p.name: p for p in (await mcp.list_prompts()).prompts}


async def text(name, arguments=None, scope='erfassen'):
    fake = FakeNkf()
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, scope)) as mcp:
        result = await mcp.get_prompt(name, arguments or {})
    assert fake.requests == [], 'a prompt must not call the API'
    [message] = result.messages
    assert message.role == 'user'
    return message.content.text


@pytest.mark.parametrize(
    ('scope', 'expected'), [('lesen', READ_PROMPTS), ('erfassen', ALL_PROMPTS), (None, ALL_PROMPTS)]
)
async def test_record_receipt_exists_only_for_access_that_may_record(scope, expected):
    assert set(await prompts(scope)) == expected


async def test_prompts_have_german_titles_and_described_arguments():
    found = await prompts('erfassen')
    assert {name: p.title for name, p in found.items()} == {
        'prepare_year_end': 'Jahresabschluss vorbereiten',
        'check_hausgeld_statement': 'Hausgeld-Abrechnung prüfen',
        'record_receipt': 'Beleg erfassen',
    }
    for p in found.values():
        assert p.description
        for argument in p.arguments or []:
            assert argument.description, (p.name, argument.name)
            assert argument.required is False, 'missing arguments are asked for, the SDK would only fail'
    assert [a.name for a in found['prepare_year_end'].arguments] == ['property_id', 'year']
    assert [a.name for a in found['record_receipt'].arguments] == ['receipt_path']


async def test_prepare_year_end_names_property_year_and_tools_in_order():
    instruction = await text('prepare_year_end', {'property_id': '1', 'year': '2025'})
    assert 'Immobilie 1' in instruction
    assert 'Abrechnungsjahr 2025' in instruction
    steps = [
        'get_billing_year_status',
        'get_data_quality',
        'list_invoices',
        'list_meters',
        'precheck_statement',
        'list_deadlines',
    ]
    positions = [instruction.index(step) for step in steps]
    assert positions == sorted(positions)
    assert 'Ändere nichts' in instruction


async def test_prepare_year_end_offers_recording_only_with_a_recording_key():
    assert 'record_' in await text('prepare_year_end', {'property_id': '1', 'year': '2025'}, scope='erfassen')
    assert 'record_' not in await text('prepare_year_end', {'property_id': '1', 'year': '2025'}, scope='lesen')


@pytest.mark.parametrize(
    ('arguments', 'expected'),
    [
        ({}, 'list_properties'),
        ({'property_id': 'abc', 'year': '2025'}, '„abc“ ist keine gültige ID'),
        ({'property_id': '1', 'year': '20x5'}, '„20x5“ ist kein gültiges Jahr'),
        ({'property_id': '1', 'year': '1999'}, '„1999“ ist kein gültiges Jahr'),
    ],
)
async def test_prepare_year_end_asks_for_missing_or_bad_arguments(arguments, expected):
    instruction = await text('prepare_year_end', arguments)
    assert expected in instruction
    assert 'Frag' in instruction


async def test_check_hausgeld_statement_leads_to_the_check_and_offers_recording_only_with_a_recording_key():
    recording = await text('check_hausgeld_statement')
    assert 'check_hausgeld' in recording
    assert 'record_invoice' in recording
    reading = await text('check_hausgeld_statement', scope='lesen')
    assert 'check_hausgeld' in reading
    assert 'record_' not in reading


async def test_record_receipt_with_path_checks_duplicates_and_asks_before_recording():
    instruction = await text('record_receipt', {'receipt_path': '~/Belege/wasser-2025.pdf'})
    assert '~/Belege/wasser-2025.pdf' in instruction
    for step in (
        'list_properties',
        'list_cost_categories',
        'list_invoices',
        'record_invoice',
        'upload_invoice_document',
    ):
        assert step in instruction
    assert instruction.index('list_invoices') < instruction.index('record_invoice')
    assert 'Zustimmung' in instruction


async def test_record_receipt_without_path_asks_for_the_receipt():
    assert 'Frag nach dem Beleg' in await text('record_receipt')


async def test_prepare_year_end_takes_the_period_from_the_billing_year_not_the_calendar_year():
    # A billing year can start on 01.07.; list_invoices(year=...) filters by calendar year.
    instruction = await text('prepare_year_end', {'property_id': '2', 'year': '2025'})
    step3 = next(line for line in instruction.splitlines() if line.startswith('3.'))
    assert 'list_invoices' in step3
    assert 'ohne year' in step3
    assert 'Abrechnungszeitraum aus Schritt 1' in step3
    step4 = next(line for line in instruction.splitlines() if line.startswith('4.'))
    assert 'cutoff_ok' in step4
    assert 'meter_id' in step4
