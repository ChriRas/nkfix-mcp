import os
import subprocess
import sys
from pathlib import Path

import pytest
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from nkfix_mcp.client import NkfClient
from nkfix_mcp.config import Config
from nkfix_mcp.server import build_server
from tests.contract.helpers import call, connected

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(('access', 'scope'), [('lesen', 'lesen'), ('erfassen', 'erfassen')])
async def test_connection_with_api_key(instance, access, scope):
    async with connected(instance, access) as mcp:
        data = await call(mcp, 'check_connection')
    assert data['auth_method'] == 'schluessel'
    assert data['account'] == 'testvermieter'
    assert data['api_key'] == {'name': f'Vertrag {access}', 'scope': scope}
    assert data['version']
    assert data['can_record'] is (scope == 'erfassen')


async def test_connection_with_session(instance):
    async with connected(instance, 'sitzung') as mcp:
        data = await call(mcp, 'check_connection')
    assert data['auth_method'] == 'sitzung'
    assert data['api_key'] is None
    assert data['can_record'] is True


async def test_wrong_api_key_is_reported_as_such(instance):
    wrong = {'NKF_URL': instance.url, 'NKF_API_KEY': 'nkf_' + 'Z' * 43}
    async with NkfClient(Config.from_env(wrong)) as nkf, Client(build_server(nkf)) as mcp:
        result = await mcp.call_tool('check_connection', {})
    assert result.is_error
    assert 'ungültig' in result.content[0].text


async def test_start_via_stdio_as_in_claude(instance):
    """The real path: console script, environment, stdio, scope at startup."""
    command = Path(sys.executable).with_name('nkfix-mcp')
    params = StdioServerParameters(command=str(command), env=instance.env('lesen'))
    async with Client(params) as mcp:
        result = await mcp.call_tool('check_connection', {})
    assert not result.is_error, result.content[0].text
    assert result.structured_content['api_key']['scope'] == 'lesen'


async def test_start_without_url_exits_with_message_on_stderr():
    command = Path(sys.executable).with_name('nkfix-mcp')
    run = subprocess.run([str(command)], env={'PATH': os.environ['PATH']}, capture_output=True, text=True,
                         timeout=30)
    assert run.returncode == 2
    assert 'NKF_URL fehlt' in run.stderr
    assert run.stdout == ''


async def test_wrong_password_is_tried_once_and_named_clearly(instance):
    env = {**instance.env('sitzung'), 'NKF_PASSWORD': 'falsch-falsch-falsch'}
    async with NkfClient(Config.from_env(env)) as nkf, Client(build_server(nkf)) as mcp:
        for _ in range(2):
            result = await mcp.call_tool('check_connection', {})
            assert result.is_error
            assert 'NKF_PASSWORD' in result.content[0].text
