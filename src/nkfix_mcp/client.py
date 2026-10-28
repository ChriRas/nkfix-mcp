"""HTTP client for the JSON API of NebenkostenFix.

Knows only paths and errors, no domain logic. The tools in ``tools/``
decide which endpoints they call; this client makes sure that every
request is authenticated and every error arrives as ``NkfError`` with
German text.

Deliberately never sent: ``Origin``, ``Referer`` and ``Sec-Fetch-Site``. With
them, NebenkostenFix takes a state-changing request for an attack from
another site (CSRF) and answers 403.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx2

from nkfix_mcp import errors
from nkfix_mcp.auth import auth_for
from nkfix_mcp.config import Config
from nkfix_mcp.errors import NkfConfirmationRequired, NkfError

__all__ = ['NkfClient', 'NkfError', 'NkfConfirmationRequired']

log = logging.getLogger(__name__)

TIMEOUT_S = 30.0
# A receipt of up to 32 MB over a slow network to the NAS takes longer.
UPLOAD_TIMEOUT_S = 120.0


class NkfClient:
    def __init__(self, config: Config, transport: httpx2.AsyncBaseTransport | None = None,
                 timeout_s: float = TIMEOUT_S) -> None:
        self.config = config
        self.auth = auth_for(config)
        self._http = httpx2.AsyncClient(
            base_url=config.url, transport=transport, timeout=timeout_s,
            headers={'Accept': 'application/json'}, follow_redirects=False)

    async def __aenter__(self) -> NkfClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    async def get(self, path: str, **params: Any) -> Any:
        values = {k: v for k, v in params.items() if v is not None}
        return await self._request('GET', path, params=values or None)

    async def post(self, path: str, data: dict[str, Any] | None = None, *,
                   form: dict[str, Any] | None = None,
                   files: dict[str, tuple[str, bytes, str]] | None = None) -> Any:
        """JSON (``data``) or multipart (``form`` and ``files``)."""
        if files is not None:
            fields = {k: str(v) for k, v in (form or {}).items() if v is not None}
            return await self._request('POST', path, data=fields, files=files, timeout=UPLOAD_TIMEOUT_S)
        return await self._request('POST', path, json=data or {})

    async def _request(self, method: str, path: str, **options: Any) -> Any:
        auth_method = self.auth.auth_method
        try:
            await self.auth.ensure(self._http)
            seen = self.auth.generation
            response = await self._send(method, path, **options)
            if _session_expired(response) and await self.auth.renew(self._http, seen):
                log.info('Sitzung abgelaufen, neu angemeldet.')
                response = await self._send(method, path, **options)
                if _session_expired(response):
                    # Login works, but the session does not stick: usually a
                    # Secure cookie (BEHIND_HTTPS) with an http:// address.
                    raise NkfError('Die Anmeldung bei NebenkostenFix klappt, aber die Sitzung wird nicht gehalten '
                                   '(Cookie). Läuft die Instanz hinter HTTPS, nutze die https://-Adresse als '
                                   'NKF_URL.', 401, 'auth_required')
        except httpx2.TimeoutException as cause:
            if method == 'POST':
                # The request may have arrived: do not say "not reachable".
                raise NkfError(f'NebenkostenFix hat nicht rechtzeitig geantwortet ({type(cause).__name__}). Ob '
                               'gespeichert wurde, ist unklar: vor einem neuen Versuch mit den Lese-Werkzeugen '
                               'nachsehen.') from cause
            raise NkfError(f'NebenkostenFix unter {self.config.url} antwortet nicht rechtzeitig '
                           f'({type(cause).__name__}). Läuft die Instanz?') from cause
        except httpx2.RequestError as cause:
            raise NkfError(f'NebenkostenFix unter {self.config.url} ist nicht erreichbar '
                           f'({type(cause).__name__}). Läuft die Instanz?') from cause

        if response.is_success:
            if not response.content:
                return None
            try:
                return response.json()
            except ValueError as cause:
                raise NkfError(f'Unter {self.config.url} antwortet keine NebenkostenFix-API '
                               f'(kein JSON auf {path}). Stimmt NKF_URL?') from cause
        data = errors.body(response)
        if response.status_code == 409 and data.get('nachfrage'):
            raise NkfConfirmationRequired(data)
        error = errors.from_response(response, auth_method)
        if error.code in ('nicht_per_schluessel', 'fremder_ursprung'):
            log.error('NebenkostenFix lehnt %s %s ab: %s', method, path, error.code)
        raise error

    async def _send(self, method: str, path: str, **options: Any) -> httpx2.Response:
        return await self._http.request(method, path, headers=self.auth.headers(), **options)


def _session_expired(response: httpx2.Response) -> bool:
    return response.status_code == 401 and errors.body(response).get('code') == 'auth_required'
