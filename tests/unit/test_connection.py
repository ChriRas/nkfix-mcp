import logging

import httpx2
import pytest
from mcp.client import Client

from nkfix_mcp.server import build_server, fetch_scope
from tests.helpers import (
    ABOUT,
    API_KEY,
    ME_OPEN,
    ME_READ,
    ME_RECORD,
    ME_SESSION,
    FakeNkf,
    client,
    json_response,
)

pytestmark = pytest.mark.anyio


async def call(fake: FakeNkf, tool: str, arguments: dict | None = None, **values):
    nkf = client(fake, **values)
    async with nkf, Client(build_server(nkf)) as mcp:
        return await mcp.call_tool(tool, arguments or {})


async def test_connection_reports_account_api_key_and_version():
    fake = FakeNkf().json('GET', '/api/auth/me', ME_RECORD).json('GET', '/api/ueber', ABOUT)
    result = await call(fake, 'check_connection')
    assert not result.is_error
    data = result.structured_content
    assert data['account'] == 'testvermieter'
    assert data['auth_method'] == 'schluessel'
    assert data['api_key'] == {'name': 'Assistent', 'scope': 'erfassen'}
    assert data['version'] == '0.17.1'
    assert data['can_record'] is True
    assert data['warnings'] == []


async def test_read_key_says_that_recording_is_not_possible():
    fake = FakeNkf().json('GET', '/api/auth/me', ME_READ).json('GET', '/api/ueber', ABOUT)
    data = (await call(fake, 'check_connection')).structured_content
    assert data['can_record'] is False
    assert any('nur lesen' in w for w in data['warnings'])


async def test_open_mode_on_remote_address_warns_clearly():
    fake = FakeNkf().json('GET', '/api/auth/me', ME_OPEN).json('GET', '/api/ueber', ABOUT)
    data = (await call(fake, 'check_connection', NKF_API_KEY=None)).structured_content
    assert data['auth_method'] == 'offen'
    assert any('ohne Anmeldung' in w and 'jeder' in w for w in data['warnings'])


async def test_api_key_against_open_instance_is_noticed():
    fake = FakeNkf().json('GET', '/api/auth/me', ME_OPEN).json('GET', '/api/ueber', ABOUT)
    data = (await call(fake, 'check_connection')).structured_content
    assert any('offenen Modus' in w for w in data['warnings'])


async def test_errors_reach_the_model_as_german_text():
    fake = FakeNkf().json('GET', '/api/auth/me', {'error': 'x', 'code': 'schluessel_ungueltig'}, status=401)
    result = await call(fake, 'check_connection')
    assert result.is_error
    assert 'neuen' in result.content[0].text


async def test_read_tool_is_marked_as_read_only():
    nkf = client(FakeNkf())
    async with nkf, Client(build_server(nkf)) as mcp:
        tools = {w.name: w for w in (await mcp.list_tools()).tools}
    note = tools['check_connection'].annotations
    assert note.read_only_hint is True
    assert note.open_world_hint is False


async def test_server_has_instructions_for_the_assistant():
    nkf = client(FakeNkf())
    server = build_server(nkf)
    assert 'NebenkostenFix' in server.instructions


@pytest.mark.parametrize(('me', 'values', 'expected'), [
    (ME_RECORD, {}, 'erfassen'),
    (ME_READ, {}, 'lesen'),
    (ME_SESSION, {'NKF_API_KEY': None, 'NKF_USERNAME': 'a', 'NKF_PASSWORD': 'b'}, 'erfassen'),
    (ME_OPEN, {'NKF_API_KEY': None}, 'erfassen'),
])
async def test_scope_is_fetched_at_startup(me, values, expected):
    fake = FakeNkf().json('GET', '/api/auth/me', me)
    fake.set_route('POST', '/login', json_response(200, {'username': 'a'}, {'Set-Cookie': 'session=a'}))
    async with client(fake, **values) as nkf:
        assert await fetch_scope(nkf) == expected


@pytest.mark.parametrize('response', [
    json_response(500, {'error': 'kaputt'}),
    httpx2.Response(200, text='<html>Proxy-Anmeldung</html>'),
    'verbindung_abgelehnt',
])
async def test_scope_stays_undetermined_when_the_instance_does_not_answer_properly(response):
    if response == 'verbindung_abgelehnt':
        def response(request):
            raise httpx2.ConnectError('abgelehnt', request=request)
    fake = FakeNkf().set_route('GET', '/api/auth/me', response)
    async with client(fake) as nkf:
        assert await fetch_scope(nkf) is None


async def test_unencrypted_remote_address_with_api_key_warns():
    fake = FakeNkf().json('GET', '/api/auth/me', ME_RECORD).json('GET', '/api/ueber', ABOUT)
    data = (await call(fake, 'check_connection', NKF_URL='http://192.168.1.20:6060')).structured_content
    assert any('unverschlüsselt' in w for w in data['warnings'])
    local = FakeNkf().json('GET', '/api/auth/me', ME_RECORD).json('GET', '/api/ueber', ABOUT)
    data = (await call(local, 'check_connection', NKF_URL='http://127.0.0.1:6060')).structured_content
    assert data['warnings'] == []


async def test_secret_appears_in_no_tool_output(caplog):
    caplog.set_level(logging.DEBUG)
    fake = FakeNkf().json('GET', '/api/auth/me', ME_RECORD).json('GET', '/api/ueber', ABOUT)
    good = await call(fake, 'check_connection')
    broken = FakeNkf().json('GET', '/api/auth/me', {'error': 'x', 'code': 'schluessel_ungueltig'}, status=401)
    bad = await call(broken, 'check_connection')
    for result in (good, bad):
        assert API_KEY not in str(result.structured_content) + result.content[0].text
    assert API_KEY not in caplog.text
