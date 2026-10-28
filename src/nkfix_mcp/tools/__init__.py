"""The server's tools and what they share.

Read tools sit in one module per domain (properties, tenants, invoices,
meters, statements); ``check``, ``record`` and ``connection`` are split by
effect. Every read tool translates the UI-shaped fields of the API (a mix
of English and German) into fields of its own, in English. If upstream
renames a field, the contract tests fail at exactly that spot. The API
barely filters (``/api/invoices`` returns all invoices); the filtering
happens here.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Annotated, Any, Literal, ParamSpec, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import AfterValidator, Field

from nkfix_mcp.errors import NkfError
from nkfix_mcp.receipts import ReceiptError

P = ParamSpec('P')
R = TypeVar('R')

MAX_ITEMS = 100

PaymentType = Literal['Miete', 'Nebenkostenvorauszahlung', 'Kaution', 'Nebenkostenzahlung']

PropertyId = Annotated[int, Field(description='ID der Immobilie, siehe Werkzeug list_properties')]
TenantId = Annotated[int, Field(description='ID des Mieters, siehe Werkzeug list_tenants')]
MeterId = Annotated[int, Field(description='ID des Zählers, siehe Werkzeug list_meters')]

# Hints for the client. Claude Code lets read tools run in parallel with
# them; they do not replace a permission prompt.
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)

T = TypeVar('T', bound=Callable[..., Any])


def tool(
    server: MCPServer, title: str, annotations: ToolAnnotations, meta: dict[str, Any] | None = None
) -> Callable[[T], T]:
    """Registers a tool with its German title, which people see instead of the English name.

    Claude Code shows ``annotations.title`` (checked against version 2.1.312), the MCP
    specification has ``Tool.title`` for the same purpose; both get the title.
    """
    return server.tool(title=title, annotations=annotations.model_copy(update={'title': title}), meta=meta)


def _calendar_day(text: str) -> str:
    # The pattern alone lets 2025-02-30 through; that would later give a
    # ValueError without a text for the model, or go to the API as is.
    try:
        date.fromisoformat(text)
    except ValueError:
        raise ValueError(f'{text} ist kein gültiges Datum.') from None
    return text


IsoDate = Annotated[
    str, Field(pattern=r'^\d{4}-\d{2}-\d{2}$', description='Datum als YYYY-MM-DD'), AfterValidator(_calendar_day)
]


def report_errors(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """``NkfError`` and ``ReceiptError`` as ``ToolError``: only its text reaches the model.

    Also for the resolvers of the record tools: their errors would otherwise
    reach the model as "Error executing tool" without a reason.
    """

    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await func(*args, **kwargs)
        except (NkfError, ReceiptError) as error:
            raise ToolError(str(error)) from error

    return wrapper
