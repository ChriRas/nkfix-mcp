import logging

import anyio
import httpx2
import pytest

from nkfix_mcp.client import NkfConfirmationRequired, NkfError
from tests.helpers import API_KEY, URL, FakeNkf, client, json_response

pytestmark = pytest.mark.anyio

SESSION = {'NKF_API_KEY': None, 'NKF_USERNAME': 'testvermieter', 'NKF_PASSWORD': 'pw-1234567890'}


async def test_api_key_is_sent_as_bearer_without_cookie_or_origin():
    fake = FakeNkf().json('GET', '/api/properties', [])
    async with client(fake) as nkf:
        assert await nkf.get('/api/properties') == []
    request = fake.requests[0]
    assert request.headers['Authorization'] == f'Bearer {API_KEY}'
    assert request.headers['Accept'] == 'application/json'
    for head in ('Cookie', 'Origin', 'Referer', 'Sec-Fetch-Site'):
        assert head not in request.headers


async def test_params_end_up_in_the_query():
    fake = FakeNkf().json('GET', '/api/jahresabrechnung', {})
    async with client(fake) as nkf:
        await nkf.get('/api/jahresabrechnung', property_id=3, jahr=2025)
    assert dict(fake.requests[0].url.params) == {'property_id': '3', 'jahr': '2025'}


async def test_post_sends_json():
    fake = FakeNkf().json('POST', '/api/billing/preflight', {'overall': 'excellent', 'checks': []})
    async with client(fake) as nkf:
        await nkf.post('/api/billing/preflight', {'tenant_id': 1})
    request = fake.requests[0]
    assert request.headers['Content-Type'] == 'application/json'
    assert FakeNkf.body(request) == {'tenant_id': 1}


async def fail_on(response: httpx2.Response, **values) -> NkfError:
    fake = FakeNkf().set_route('GET', '/api/properties', response)
    async with client(fake, **values) as nkf:
        with pytest.raises(NkfError) as error:
            await nkf.get('/api/properties')
    error.value.requests = len(fake.requests)
    return error.value


async def test_invalid_api_key_is_not_retried_and_names_the_way_out():
    error = await fail_on(json_response(401, {'error': 'x', 'code': 'schluessel_ungueltig'}))
    assert error.requests == 1
    assert 'neuen' in str(error) and 'Einstellungen' in str(error)
    assert API_KEY not in str(error)


async def test_instance_without_api_key_support_is_detected():
    error = await fail_on(json_response(401, {'error': 'Nicht angemeldet.', 'code': 'auth_required'}))
    assert 'unterstützt keine Zugriffsschlüssel' in str(error)
    assert 'NKF_USERNAME' in str(error)


async def test_read_key_when_recording():
    error = await fail_on(json_response(403, {'error': 'x', 'code': 'schluessel_umfang'}))
    assert 'Lesen und erfassen' in str(error)


@pytest.mark.parametrize('code', ['nicht_per_schluessel', 'fremder_ursprung'])
async def test_blocked_endpoint_is_an_error_of_the_mcp_server(code):
    error = await fail_on(json_response(403, {'error': 'x', 'code': code}))
    assert 'Fehler im MCP-Server' in str(error)


async def test_lock_names_the_wait_time_and_does_not_retry():
    error = await fail_on(json_response(429, {'error': 'x', 'code': 'anmeldung_gesperrt'}, {'Retry-After': '120'}))
    assert error.requests == 1
    assert '120 Sekunden' in str(error)


async def test_input_error_names_message_and_field():
    error = await fail_on(json_response(400, {'error': 'Das Datum fehlt.', 'feld': 'start_date'}))
    assert str(error) == 'Eingabe abgelehnt: Das Datum fehlt. (Feld start_date)'


async def test_not_found():
    error = await fail_on(json_response(404, {'error': 'Mieter nicht gefunden.'}))
    assert 'Mieter nicht gefunden.' in str(error)


async def test_upload_too_large():
    error = await fail_on(json_response(413, {'error': 'Der Upload ist zu groß. Erlaubt sind höchstens 32 MB.'}))
    assert '32 MB' in str(error)


async def test_server_error_passes_the_message_through():
    error = await fail_on(json_response(500, {'error': 'Interner Fehler, Vorgang 4711.'}))
    assert 'Vorgang 4711' in str(error)
    assert 'Serverfehler' in str(error)


async def test_response_without_json_is_no_crash():
    error = await fail_on(httpx2.Response(502, text='<html>Bad Gateway</html>'))
    assert '502' in str(error)


async def test_confirmation_is_its_own_exception_with_the_body():
    fake = FakeNkf().json('POST', '/api/readings', {'error': 'Unplausibel.', 'nachfrage': True}, status=409)
    async with client(fake) as nkf:
        with pytest.raises(NkfConfirmationRequired) as confirmation:
            await nkf.post('/api/readings', {'meter_id': 1})
    assert confirmation.value.data['nachfrage'] is True


async def test_unreachable():
    def broken(request):
        raise httpx2.ConnectError('Verbindung abgelehnt', request=request)
    error = await fail_on(broken)
    assert 'nicht erreichbar' in str(error)
    assert URL in str(error)


async def test_session_logs_in_first_and_then_sends_the_cookie():
    fake = FakeNkf()
    fake.set_route('POST', '/login', json_response(200, {'username': 'testvermieter'},
                                                 {'Set-Cookie': 'session=abc; Path=/; HttpOnly'}))
    fake.json('GET', '/api/properties', [])
    async with client(fake, **SESSION) as nkf:
        await nkf.get('/api/properties')
        await nkf.get('/api/properties')
    assert [a.url.path for a in fake.requests] == ['/login', '/api/properties', '/api/properties']
    assert FakeNkf.body(fake.requests[0]) == {'username': 'testvermieter', 'password': 'pw-1234567890'}
    assert 'Authorization' not in fake.requests[1].headers
    assert 'session=abc' in fake.requests[1].headers['Cookie']


async def test_expired_session_is_renewed_exactly_once():
    fake = FakeNkf()
    fake.set_route('POST', '/login', json_response(200, {'username': 'testvermieter'}, {'Set-Cookie': 'session=a'}))
    expired = json_response(401, {'error': 'Nicht angemeldet.', 'code': 'auth_required'})
    fake.set_route('GET', '/api/properties', expired, json_response(200, []))
    async with client(fake, **SESSION) as nkf:
        assert await nkf.get('/api/properties') == []
    assert [a.url.path for a in fake.requests] == ['/login', '/api/properties', '/login', '/api/properties']


async def test_session_that_does_not_hold_is_named_as_cookie_problem():
    # Login works, the request after it stays 401: Secure cookie over http://.
    fake = FakeNkf()
    fake.set_route('POST', '/login', json_response(200, {'username': 'testvermieter'}, {'Set-Cookie': 'session=a'}))
    fake.json('GET', '/api/properties', {'error': 'Nicht angemeldet.', 'code': 'auth_required'}, status=401)
    async with client(fake, **SESSION) as nkf:
        with pytest.raises(NkfError) as error:
            await nkf.get('/api/properties')
    assert 'Sitzung wird nicht gehalten' in str(error.value)
    assert 'https://' in str(error.value)
    assert len(fake.sent('POST', '/login')) == 2


async def test_wrong_password_is_named_clearly_without_showing_it():
    fake = FakeNkf().json('POST', '/login', {'error': 'Benutzername oder Passwort falsch.'}, status=401)
    async with client(fake, **SESSION) as nkf:
        with pytest.raises(NkfError) as error:
            await nkf.get('/api/properties')
    assert 'NKF_USERNAME' in str(error.value)
    assert 'pw-1234567890' not in str(error.value)
    assert len(fake.requests) == 1


async def test_open_access_sends_no_credentials():
    fake = FakeNkf().json('GET', '/api/properties', [])
    async with client(fake, NKF_API_KEY=None) as nkf:
        await nkf.get('/api/properties')
    assert 'Authorization' not in fake.requests[0].headers
    assert fake.sent('POST', '/login') == []


async def test_wrong_password_is_tried_only_once():
    """Five failed attempts lock the account in NebenkostenFix, also for the human in the browser."""
    fake = FakeNkf().json('POST', '/login', {'error': 'Benutzername oder Passwort falsch.'}, status=401)
    async with client(fake, **SESSION) as nkf:
        for _ in range(3):
            with pytest.raises(NkfError, match='neu starten'):
                await nkf.get('/api/properties')
    assert len(fake.requests) == 1


async def test_locked_login_is_not_retried():
    fake = FakeNkf().json('POST', '/login', {'error': 'gesperrt', 'code': 'anmeldung_gesperrt'}, status=429,
                          **{'Retry-After': '900'})
    async with client(fake, **SESSION) as nkf:
        for _ in range(2):
            with pytest.raises(NkfError, match='900 Sekunden'):
                await nkf.get('/api/properties')
    assert len(fake.requests) == 1


async def test_open_instance_with_credentials():
    fake = FakeNkf().json('POST', '/login', {'error': 'Ohne Anmeldung.'}, status=409)
    async with client(fake, **SESSION) as nkf:
        with pytest.raises(NkfError, match='ohne Anmeldung'):
            await nkf.get('/api/properties')


async def test_parallel_calls_renew_the_session_only_once():
    logins = []

    def login(request):
        logins.append(request)
        return json_response(200, {'username': 'testvermieter'}, {'Set-Cookie': f'session={len(logins)}'})

    def items(request):
        # The first session has expired, only the second one is valid.
        if 'session=2' in request.headers.get('Cookie', ''):
            return json_response(200, [])
        return json_response(401, {'error': 'Nicht angemeldet.', 'code': 'auth_required'})

    fake = FakeNkf().set_route('POST', '/login', login).set_route('GET', '/api/properties', items)
    async with client(fake, **SESSION) as nkf, anyio.create_task_group() as group:
        for _ in range(3):
            group.start_soon(nkf.get, '/api/properties')
    assert len(logins) == 2


async def test_conflict_without_confirmation():
    error = await fail_on(json_response(409, {'error': 'Gibt es schon.'}))
    assert not isinstance(error, NkfConfirmationRequired)
    assert str(error) == 'Konflikt: Gibt es schon.'


async def test_401_without_code_does_not_come_from_nebenkostenfix():
    error = await fail_on(httpx2.Response(401, text='Authorization Required'))
    assert 'Proxy' in str(error)
    assert 'Zugriffsschlüssel' not in str(error)


async def test_success_without_json_means_wrong_address():
    error = await fail_on(httpx2.Response(200, text='<html>Fritz!Box</html>'))
    assert 'keine NebenkostenFix-API' in str(error)


async def test_blocked_endpoint_is_logged_with_path(caplog):
    await fail_on(json_response(403, {'error': 'x', 'code': 'nicht_per_schluessel'}))
    assert 'GET /api/properties' in caplog.text
    assert 'nicht_per_schluessel' in caplog.text


async def test_no_secret_in_the_log(caplog):
    caplog.set_level(logging.DEBUG)
    await fail_on(json_response(401, {'error': 'x', 'code': 'schluessel_ungueltig'}))
    fake = FakeNkf().json('POST', '/login', {'error': 'falsch'}, status=401)
    async with client(fake, **SESSION) as nkf:
        with pytest.raises(NkfError):
            await nkf.get('/api/properties')
    assert API_KEY not in caplog.text
    assert 'pw-1234567890' not in caplog.text
