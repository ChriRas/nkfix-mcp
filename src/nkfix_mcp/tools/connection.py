"""``check_connection``: who is logged in, what may they do, which version is running."""

from __future__ import annotations

from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel

from nkfix_mcp.client import NkfClient, NkfError
from nkfix_mcp.tools import READ_ONLY, report_errors, tool

Scope = Literal['lesen', 'erfassen']


class ApiKeyInfo(BaseModel):
    name: str
    scope: str


class Connection(BaseModel):
    url: str
    auth_method: Literal['schluessel', 'sitzung', 'offen']
    account: str | None
    api_key: ApiKeyInfo | None
    version: str | None
    can_record: bool
    warnings: list[str]


async def fetch_scope(nkf: NkfClient) -> Scope | None:
    """What the access may do, or None if the instance does not answer.

    A session or the open mode may do everything the account may do; for
    the server, all that counts is whether it may record.
    """
    try:
        me = await nkf.get('/api/auth/me')
    except NkfError:
        return None
    api_key = me.get('schluessel') if isinstance(me, dict) else None
    if isinstance(api_key, dict):
        scope = api_key.get('umfang')
        return scope if scope in ('lesen', 'erfassen') else None
    return 'erfassen'


async def _version(nkf: NkfClient) -> str | None:
    """The instance version; None if the instance does not say (it is only for information)."""
    try:
        about = await nkf.get('/api/ueber')
    except NkfError:
        return None
    version = about.get('version') if isinstance(about, dict) else None
    return version if isinstance(version, str) else None


def register(server: MCPServer, nkf: NkfClient) -> None:
    config = nkf.config

    @tool(server, 'Verbindung prüfen', READ_ONLY)
    @report_errors
    async def check_connection() -> Connection:
        """Prüft die Verbindung zu NebenkostenFix: angemeldetes Konto, Zugriffsschlüssel und dessen
        Umfang (lesen oder erfassen), Version der Instanz und Warnungen. Liest nur."""
        me = await nkf.get('/api/auth/me')
        if not isinstance(me, dict):
            raise ToolError(
                'NebenkostenFix hat auf /api/auth/me keine gültige Antwort geliefert (kein JSON-Objekt). '
                'Prüf, ob NKF_URL auf eine NebenkostenFix-Instanz zeigt.'
            )
        version = await _version(nkf)

        open_mode = bool(me.get('ohne_anmeldung'))
        api_key = me.get('schluessel') if not open_mode else None
        warnings: list[str] = []
        if open_mode:
            if config.is_local:
                warnings.append(
                    'NebenkostenFix läuft ohne Anmeldung. Das ist nur auf diesem Rechner '
                    'erreichbar und damit vertretbar.'
                )
            else:
                warnings.append(
                    f'NebenkostenFix läuft ohne Anmeldung und ist unter {config.url} '
                    'erreichbar: jeder, der diese Adresse erreicht, kann alle Daten lesen und '
                    'ändern. Richte in NebenkostenFix ein Konto ein.'
                )
            if config.auth_method != 'offen':
                warnings.append(
                    'Die Instanz ist im offenen Modus; NKF_API_KEY bzw. NKF_USERNAME werden nicht gebraucht.'
                )
        elif config.url.startswith('http://') and not config.is_local:
            warnings.append(
                f'{config.url} ist unverschlüsselt (http) und nicht lokal: Zugangsdaten und '
                'alle Daten gehen im Klartext durchs Netz. Nutze https, z. B. über einen Reverse '
                'Proxy.'
            )
        scope = api_key.get('umfang') if isinstance(api_key, dict) else None
        if scope == 'lesen':
            warnings.append(
                'Der Zugriffsschlüssel darf nur lesen. Rechnungen, Zählerstände und Zahlungen '
                'lassen sich damit nicht erfassen.'
            )

        return Connection(
            url=config.url,
            auth_method='offen' if open_mode else config.auth_method,
            account=me.get('username'),
            api_key=(
                ApiKeyInfo(name=api_key.get('name'), scope=api_key.get('umfang')) if isinstance(api_key, dict) else None
            ),
            version=version,
            can_record=scope != 'lesen',
            warnings=warnings,
        )
