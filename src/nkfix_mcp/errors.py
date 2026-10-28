"""API responses that are not a success, as German text for the model.

The MCP SDK only passes the text of a ``ToolError`` on to the model; any
other exception arrives there as "Error executing tool …". That is why every
expectable failure is translated here into a sentence that says what is
wrong and what the human can do. Secrets never appear in the text.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import httpx2


class NkfError(Exception):
    """A request to NebenkostenFix failed; ``str()`` is meant for the model."""

    def __init__(self, text: str, status: int | None = None, code: str | None = None) -> None:
        super().__init__(text)
        self.status = status
        self.code = code


class NkfConfirmationRequired(NkfError):
    """409 with ``nachfrage: true``: the instance wants a confirmation (meter reading)."""

    def __init__(self, data: dict[str, Any]) -> None:
        super().__init__(str(data.get('error') or 'NebenkostenFix bittet um Bestätigung.'), 409, 'nachfrage')
        self.data = data


def body(response: httpx2.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _unauthorized_message(response: httpx2.Response, code: Any, auth_method: str) -> str:
    """Why HTTP 401 came back, and what helps."""
    if code == 'schluessel_ungueltig':
        return (
            'Der Zugriffsschlüssel ist ungültig, gelöscht oder sein Konto ist stillgelegt. '
            'Lege in NebenkostenFix unter Einstellungen → Konto → „Zugriffsschlüssel (API-Key)“ '
            'einen neuen an und trage ihn als NKF_API_KEY ein. Ein erneuter Versuch hilft nicht.'
        )
    if code != 'auth_required':
        # NebenkostenFix always answers with a code; without one, the 401
        # came from something in front of it, such as a reverse proxy with its own login.
        return (
            f'Unter {response.request.url.scheme}://{response.request.url.host} verlangt etwas eine '
            'Anmeldung (HTTP 401), aber nicht NebenkostenFix selbst. Steht ein Proxy davor?'
        )
    if auth_method == 'schluessel':
        # A bearer without effect: the instance does not support API keys and
        # asks for the session (upstream without PR #48).
        return (
            'Diese NebenkostenFix-Instanz unterstützt keine Zugriffsschlüssel (Version zu alt). '
            'Nutze stattdessen NKF_USERNAME und NKF_PASSWORD.'
        )
    if auth_method == 'sitzung':
        return 'NebenkostenFix hat die Anmeldung abgelehnt. Prüfe NKF_USERNAME und NKF_PASSWORD.'
    return 'NebenkostenFix verlangt eine Anmeldung. Setze NKF_API_KEY (oder NKF_USERNAME und NKF_PASSWORD).'


def from_response(response: httpx2.Response, auth_method: str) -> NkfError:
    data = body(response)
    status, code = response.status_code, data.get('code')
    # check_plausibility reports errors as {status, message} instead of {error}.
    text = str(data.get('error') or data.get('message') or '').strip()

    if status == HTTPStatus.UNAUTHORIZED:
        message = _unauthorized_message(response, code, auth_method)
    elif status == HTTPStatus.FORBIDDEN and code == 'schluessel_umfang':
        message = (
            'Der Zugriffsschlüssel darf nur lesen. Zum Erfassen braucht es einen Schlüssel mit dem '
            'Umfang „Lesen und erfassen“ (Einstellungen → Konto).'
        )
    elif status == HTTPStatus.FORBIDDEN and code in ('nicht_per_schluessel', 'fremder_ursprung'):
        message = (
            f'Fehler im MCP-Server: NebenkostenFix lehnt diesen Aufruf grundsätzlich ab ({code}). '
            'Bitte als Fehler von nkfix-mcp melden.'
        )
    elif status == HTTPStatus.TOO_MANY_REQUESTS:
        wait = response.headers.get('Retry-After')
        duration = f' für {wait} Sekunden' if wait else ''
        message = (
            f'NebenkostenFix hat die Anmeldung nach zu vielen Fehlversuchen{duration} gesperrt. '
            'Nicht wiederholen, sondern die Zugangsdaten prüfen und warten.'
        )
    elif status == HTTPStatus.BAD_REQUEST:
        field = data.get('feld')
        message = f'Eingabe abgelehnt: {text or "ungültige Eingabe"}' + (f' (Feld {field})' if field else '')
    elif status == HTTPStatus.NOT_FOUND:
        message = f'Nicht gefunden: {text}' if text else 'Nicht gefunden.'
    elif status == HTTPStatus.REQUEST_ENTITY_TOO_LARGE:
        message = text or 'Die Datei ist für NebenkostenFix zu groß.'
    elif status == HTTPStatus.CONFLICT:
        message = f'Konflikt: {text}' if text else 'NebenkostenFix meldet einen Konflikt.'
    elif status >= HTTPStatus.INTERNAL_SERVER_ERROR:
        message = f'Serverfehler in NebenkostenFix (HTTP {status})' + (f': {text}' if text else '.')
    else:
        message = f'NebenkostenFix antwortet mit HTTP {status}' + (f': {text}' if text else '.')
    return NkfError(message, status, code)
