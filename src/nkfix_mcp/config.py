"""Configuration from environment variables.

Claude Code and Claude Desktop start the server with an ``env`` block.
Whatever is missing or broken there should show up at startup with a clear
message, not only at the first tool call as a 401.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

KEY_PREFIX = 'nkf_'
# .mcp.json often contains "${NKF_API_KEY}". If the variable is not set,
# Claude Code passes the text through unchanged (docs, as of 2026-10-27).
# Something like that must never reach the instance as an API key.
_UNRESOLVED = re.compile(r'\$\{[^}]*\}')
_YES = frozenset({'1', 'ja', 'true', 'yes', 'on'})

AuthMethod = Literal['schluessel', 'sitzung', 'offen']


class ConfigError(Exception):
    """The environment is not enough to start. The text names the variable."""


@dataclass(frozen=True)
class Config:
    url: str
    api_key: str | None = field(default=None, repr=False)
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    receipt_dir: Path | None = None
    prompt_on_record: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Config:
        values = {
            name: _env_value(env, name)
            for name in (
                'NKF_URL',
                'NKF_API_KEY',
                'NKF_USERNAME',
                'NKF_PASSWORD',
                'NKF_RECEIPT_DIR',
                'NKF_NO_RECORD_PROMPT',
            )
        }

        url = values['NKF_URL']
        if not url:
            raise ConfigError(
                'NKF_URL fehlt. Gib die Adresse deiner NebenkostenFix-Instanz an, z. B. NKF_URL=http://127.0.0.1:6060.'
            )
        parts = urlsplit(url)
        if parts.username is not None or parts.password is not None:
            # Do not name the value: the password would end up in the message,
            # and httpx2 would silently send it as Basic auth.
            raise ConfigError(
                'NKF_URL enthält Zugangsdaten (benutzer:passwort@…). Das ist nicht erlaubt, sie landeten in '
                'Meldungen und Logs. Für NebenkostenFix nutze NKF_API_KEY.'
            )
        if parts.scheme not in ('http', 'https') or not parts.hostname:
            raise ConfigError(f'NKF_URL ist keine gültige Adresse: {url!r}. Erwartet wird http://… oder https://….')

        api_key = values['NKF_API_KEY']
        if api_key and not api_key.startswith(KEY_PREFIX):
            raise ConfigError(
                f'NKF_API_KEY beginnt nicht mit „{KEY_PREFIX}“. Kopiere den Zugriffsschlüssel '
                'vollständig aus NebenkostenFix (Einstellungen → Konto).'
            )

        username, password = values['NKF_USERNAME'], values['NKF_PASSWORD']
        if username and not password:
            raise ConfigError('NKF_USERNAME ist gesetzt, NKF_PASSWORD fehlt.')
        if password and not username:
            raise ConfigError('NKF_PASSWORD ist gesetzt, NKF_USERNAME fehlt.')

        receipt_dir = None
        if values['NKF_RECEIPT_DIR']:
            receipt_dir = Path(values['NKF_RECEIPT_DIR']).expanduser().resolve()
            if not receipt_dir.is_dir():
                raise ConfigError(f'NKF_RECEIPT_DIR ist kein vorhandener Ordner: {receipt_dir}')

        skip_prompt = (values['NKF_NO_RECORD_PROMPT'] or '').lower() in _YES
        return cls(
            url=url.rstrip('/'),
            api_key=api_key,
            username=username,
            password=password,
            receipt_dir=receipt_dir,
            prompt_on_record=not skip_prompt,
        )

    @property
    def auth_method(self) -> AuthMethod:
        if self.api_key:
            return 'schluessel'
        if self.username:
            return 'sitzung'
        return 'offen'

    @property
    def is_local(self) -> bool:
        """Does the address point to this machine? Only then is open mode harmless."""
        host = urlsplit(self.url).hostname or ''
        if host == 'localhost':
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False


def _env_value(env: Mapping[str, str], name: str) -> str | None:
    value = (env.get(name) or '').strip()
    if _UNRESOLVED.search(value):
        # Do not name the value: NKF_API_KEY might contain half a key.
        raise ConfigError(
            f'{name} enthält eine nicht aufgelöste Variable (${{…}}). Setze die Variable in der '
            'Umgebung, bevor Claude startet, oder trage den Wert direkt ein.'
        )
    return value or None
