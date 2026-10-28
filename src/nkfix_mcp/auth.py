"""How the server authenticates with NebenkostenFix.

Three adapters with the same interface:

- ``ApiKeyAuth``: ``Authorization: Bearer nkf_…``, without cookie and
  without session. The main path; needs an instance with API keys.
- ``SessionAuth``: ``POST /login``, then the session cookie. For
  instances without API keys. The session expires after 60 minutes idle,
  so it logs in again after a 401.
- ``OpenAccess``: instance in open mode, without accounts.
"""

from __future__ import annotations

from http import HTTPStatus

import anyio
import httpx2

from nkfix_mcp import errors
from nkfix_mcp.config import AuthMethod, Config


class OpenAccess:
    auth_method: AuthMethod = 'offen'
    # Counts the logins. Whoever notes the value before a request can tell
    # after a 401 whether someone else has already logged in again meanwhile.
    generation = 0

    def headers(self) -> dict[str, str]:
        return {}

    async def ensure(self, http: httpx2.AsyncClient) -> None:
        return None

    async def renew(self, http: httpx2.AsyncClient, seen: int) -> bool:
        """After a 401: is a second attempt worth it? ``seen`` is the generation before the request."""
        return False


class ApiKeyAuth(OpenAccess):
    auth_method: AuthMethod = 'schluessel'

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def headers(self) -> dict[str, str]:
        return {'Authorization': f'Bearer {self._api_key}'}

    def __repr__(self) -> str:
        return 'ApiKeyAuth(…)'


class SessionAuth(OpenAccess):
    auth_method: AuthMethod = 'sitzung'

    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password
        self._logged_in = False
        self.generation = 0
        # Rejected credentials are not retried: NebenkostenFix locks the
        # account for 15 minutes after five failed attempts, and that would
        # also hit the human in the browser.
        self._rejected: errors.NkfError | None = None
        self._lock = anyio.Lock()

    def __repr__(self) -> str:
        return f'SessionAuth({self._username!r})'

    async def ensure(self, http: httpx2.AsyncClient) -> None:
        if self._rejected:
            raise self._rejected
        if self._logged_in:
            return
        async with self._lock:
            if not self._logged_in:
                await self._log_in(http)

    async def renew(self, http: httpx2.AsyncClient, seen: int) -> bool:
        async with self._lock:
            if self.generation == seen:
                self._logged_in = False
                http.cookies.clear()
                await self._log_in(http)
        return True

    async def _log_in(self, http: httpx2.AsyncClient) -> None:
        if self._rejected:
            raise self._rejected
        response = await http.post('/login', json={'username': self._username, 'password': self._password})
        if response.status_code == HTTPStatus.OK:
            self._logged_in = True
            self.generation += 1
            return
        if response.status_code == HTTPStatus.CONFLICT:
            error = errors.NkfError(
                'Diese NebenkostenFix-Instanz läuft ohne Anmeldung. Lass NKF_USERNAME und NKF_PASSWORD weg.',
                HTTPStatus.CONFLICT,
            )
        elif response.status_code == HTTPStatus.UNAUTHORIZED:
            # /login answers without ``code``, unlike the API routes.
            error = errors.NkfError(
                'NebenkostenFix hat die Anmeldung abgelehnt. Prüfe NKF_USERNAME und NKF_PASSWORD.',
                HTTPStatus.UNAUTHORIZED,
            )
        else:
            error = errors.from_response(response, self.auth_method)
        if response.status_code in (HTTPStatus.UNAUTHORIZED, HTTPStatus.CONFLICT, HTTPStatus.TOO_MANY_REQUESTS):
            error = errors.NkfError(f'{error} Nach der Korrektur den MCP-Server neu starten.', error.status, error.code)
            self._rejected = error
        raise error


def auth_for(config: Config) -> OpenAccess:
    if config.api_key:
        return ApiKeyAuth(config.api_key)
    if config.username and config.password:
        return SessionAuth(config.username, config.password)
    return OpenAccess()
