"""A real NebenkostenFix instance for the contract tests.

The NebenkostenFix API is shaped by its UI and has no stability promise. These
tests catch it when upstream renames a field or shapes a response differently.
They start ``app.py`` from ``NKF_SOURCE`` with a fresh data directory on a free
port, set up an account, create the sample property (fantasy data only) and
one read key and one record key.

- ``NKF_SOURCE``: checkout of NebenkostenFix; without the variable the tests
  are skipped.
- ``NKF_PYTHON``: Python with the dependencies of NebenkostenFix,
  default ``$NKF_SOURCE/.venv/bin/python``.
- ``NKF_CONTRACT_REQUIRED=1`` (CI): a test that would be skipped fails
  instead, so a green run means the contract was checked.

If the instance does not support API keys (NebenkostenFix before 0.19.0), the key
tests are skipped; the session tests still run.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

import httpx2
import pytest

USERNAME = 'testvermieter'


def skip_or_fail(reason: str) -> NoReturn:
    """Skip locally; with NKF_CONTRACT_REQUIRED=1 (CI) a skip would hide that nothing was checked."""
    if os.environ.get('NKF_CONTRACT_REQUIRED') == '1':
        pytest.fail(reason)
    pytest.skip(reason)


PASSWORD = 'vertrag-' + 'x' * 12


@dataclass(frozen=True)
class Instance:
    url: str
    data_dir: Path
    read_key: str | None
    record_key: str | None

    def env(self, access: str) -> dict[str, str]:
        """Environment for ``Config.from_env``: lesen, erfassen or sitzung."""
        if access == 'sitzung':
            return {'NKF_URL': self.url, 'NKF_USERNAME': USERNAME, 'NKF_PASSWORD': PASSWORD}
        api_key = self.read_key if access == 'lesen' else self.record_key
        if api_key is None:
            skip_or_fail('Diese NebenkostenFix-Instanz kennt keine Zugriffsschlüssel.')
        return {'NKF_URL': self.url, 'NKF_API_KEY': api_key}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _wait_until_ready(url: str, process: subprocess.Popen, log: Path) -> None:
    end = time.monotonic() + 90
    while time.monotonic() < end:
        if process.poll() is not None:
            raise RuntimeError(f'NebenkostenFix ist beim Start abgestürzt:\n{log.read_text()[-3000:]}')
        try:
            if httpx2.get(f'{url}/api/health', timeout=2).status_code == 200:
                return
        except httpx2.TransportError:
            pass
        time.sleep(0.3)
    raise RuntimeError(f'NebenkostenFix antwortet nicht unter {url}:\n{log.read_text()[-3000:]}')


def _set_up(url: str, data_dir: Path) -> tuple[str | None, str | None]:
    with httpx2.Client(base_url=url, headers={'Accept': 'application/json'}, timeout=30) as http:
        # GET /einrichtung creates the one-time code in the data directory.
        http.get('/einrichtung', headers={'Accept': 'text/html'})
        code = json.loads((data_dir / 'ersteinrichtung_code').read_text())['code']
        response = http.post(
            '/einrichtung', json={'username': USERNAME, 'password': PASSWORD, 'password2': PASSWORD, 'code': code}
        )
        assert response.status_code == 201, response.text
        response = http.post('/api/analytics/beispiel-immobilie', json={})
        assert response.status_code == 201, response.text
        # Fantasy data the sample property does not have (for the field guard).
        response = http.post('/api/providers', json={'name': 'Stadtwerke Musterstadt'})
        assert response.status_code == 201, response.text

        api_keys: list[str | None] = []
        for scope in ('lesen', 'erfassen'):
            response = http.post('/api/zugriffsschluessel', json={'name': f'Vertrag {scope}', 'umfang': scope})
            if response.status_code == 404:
                return None, None
            assert response.status_code == 201, response.text
            api_keys.append(response.json()['schluessel'])
        return api_keys[0], api_keys[1]


@pytest.fixture(scope='session')
def instance(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Instance]:
    source = os.environ.get('NKF_SOURCE')
    if not source:
        skip_or_fail('NKF_SOURCE ist nicht gesetzt (Checkout von NebenkostenFix).')
    source_path = Path(source).expanduser()
    python = os.environ.get('NKF_PYTHON') or str(source_path / '.venv' / 'bin' / 'python')

    data_dir = tmp_path_factory.mktemp('nkf-daten')
    port = _free_port()
    url = f'http://127.0.0.1:{port}'
    log = data_dir.parent / 'nkf-server.log'
    env = {**os.environ, 'DATA_DIR': str(data_dir), 'FLASK_PORT': str(port), 'FLASK_HOST': '127.0.0.1'}
    for name in ('NKF_URL', 'NKF_API_KEY', 'NKF_USERNAME', 'NKF_PASSWORD'):
        env.pop(name, None)
    with log.open('w') as output:
        process = subprocess.Popen(
            [python, 'app.py'], cwd=source_path, env=env, stdout=output, stderr=subprocess.STDOUT
        )
    try:
        _wait_until_ready(url, process, log)
        read, record = _set_up(url, data_dir)
        yield Instance(url, data_dir, read, record)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if '/contract/' in str(item.path):
            item.add_marker(pytest.mark.contract)
