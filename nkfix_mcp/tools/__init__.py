"""The server's tools, split by effect: read, check, record."""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from nkfix_mcp.errors import NkfError

P = ParamSpec('P')
R = TypeVar('R')

# Hints for the client. Claude Code lets read tools run in parallel with
# them; they do not replace a permission prompt.
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)

T = TypeVar('T')


def tool(server: MCPServer, title: str, annotations: ToolAnnotations,
         meta: dict[str, Any] | None = None) -> Callable[[T], T]:
    """Registers a tool with its German title, which people see instead of the English name.

    Claude Code shows ``annotations.title`` (checked against version 2.1.312), the MCP
    specification has ``Tool.title`` for the same purpose; both get the title.
    """
    return server.tool(title=title, annotations=annotations.model_copy(update={'title': title}), meta=meta)


def report_errors(tool: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """``NkfError`` as ``ToolError``: only its text reaches the model."""

    @functools.wraps(tool)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await tool(*args, **kwargs)
        except NkfError as error:
            raise ToolError(str(error)) from error

    return wrapper
