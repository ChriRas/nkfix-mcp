"""Shared helpers for the unit tests: a simulated NebenkostenFix API.

``FakeNkf`` answers requests from a table (method, path) and records every
request. If a path has a list of responses, they come one after another
(for "first 401, then 200"); the last one stays. There is no network.
"""

from __future__ import annotations

import json as jsonlib
from collections.abc import Callable
from typing import Any

import httpx2

from nkfix_mcp.client import NkfClient
from nkfix_mcp.config import Config

API_KEY = 'nkf_' + 'A' * 43
URL = 'https://nkf.test'

Reply = httpx2.Response | Callable[[httpx2.Request], httpx2.Response]


def json_response(status: int = 200, data: Any = None, headers: dict[str, str] | None = None) -> httpx2.Response:
    return httpx2.Response(status, json=data, headers=headers)


class FakeNkf:
    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], list[Reply]] = {}
        self.requests: list[httpx2.Request] = []

    def set_route(self, method: str, path: str, *answers: Reply) -> FakeNkf:
        self.routes[(method, path)] = list(answers)
        return self

    def json(self, method: str, path: str, data: Any = None, status: int = 200, **headers: str) -> FakeNkf:
        return self.set_route(method, path, json_response(status, data, headers or None))

    def _answer(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        key = (request.method, request.url.path)
        answers = self.routes.get(key)
        if not answers:
            return json_response(404, {'error': f'Testroute fehlt: {request.method} {request.url.path}'})
        response = answers.pop(0) if len(answers) > 1 else answers[0]
        return response(request) if callable(response) else response

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._answer)

    def sent(self, method: str, path: str) -> list[httpx2.Request]:
        return [a for a in self.requests if a.method == method and a.url.path == path]

    @staticmethod
    def body(request: httpx2.Request) -> Any:
        return jsonlib.loads(request.content or b'null')


def config(**values: str) -> Config:
    env = {'NKF_URL': URL, 'NKF_API_KEY': API_KEY}
    env.update(values)
    return Config.from_env({k: v for k, v in env.items() if v is not None})


def client(fake: FakeNkf, **values: str) -> NkfClient:
    return NkfClient(config(**values), transport=fake.transport())


ME_RECORD = {
    'username': 'testvermieter',
    'ohne_anmeldung': False,
    'leerlauf_s': 3600,
    'desktop': False,
    'schluessel': {'name': 'Assistent', 'umfang': 'erfassen'},
}
ME_READ = {**ME_RECORD, 'schluessel': {'name': 'Nur lesen', 'umfang': 'lesen'}}
ME_SESSION = {**ME_RECORD, 'schluessel': None}
ME_OPEN = {'username': None, 'ohne_anmeldung': True, 'leerlauf_s': None, 'desktop': False}
ABOUT = {'version': '0.19.0', 'weg': 'Docker'}
