from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from mcp.client import Client

from nkfix_mcp.client import NkfClient
from nkfix_mcp.config import Config
from nkfix_mcp.server import build_server, fetch_scope


@asynccontextmanager
async def connected(instance, access: str = 'erfassen', **extra: str):
    """In-memory MCP client, server set up as on a real start (scope fetched)."""
    nkf = NkfClient(Config.from_env({**instance.env(access), **extra}))
    async with nkf:
        server = build_server(nkf, await fetch_scope(nkf))
        async with Client(server) as mcp:
            yield mcp


async def call(mcp: Client, tool: str, **arguments: Any) -> Any:
    result = await mcp.call_tool(tool, arguments)
    assert not result.is_error, result.content[0].text
    data = result.structured_content
    return data.get('result', data) if isinstance(data, dict) else data
