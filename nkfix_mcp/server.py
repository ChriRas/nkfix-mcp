"""The MCP server: register the tools and run them over stdio.

stdout belongs to the MCP protocol. Everything the server itself says goes
to stderr; Claude Code and Claude Desktop show it in the MCP log.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import anyio
from mcp.server.mcpserver import MCPServer

from nkfix_mcp import __version__
from nkfix_mcp.client import NkfClient
from nkfix_mcp.config import Config, ConfigError
from nkfix_mcp.tools import check, connection, read, record
from nkfix_mcp.tools.connection import Scope, fetch_scope

__all__ = ['build_server', 'fetch_scope', 'main']

log = logging.getLogger('nkfix_mcp')

INSTRUCTIONS = """\
Dieser Server verbindet dich mit NebenkostenFix, einer App für private Vermieter, die
Nebenkostenabrechnungen nach BetrKV und HeizkostenV erstellt. Du kannst den Bestand lesen
(Immobilien, Wohnungen, Mieter, Rechnungen, Zähler, Zahlungen, Fristen, Abrechnungen) und
Abrechnungen vorprüfen, ohne etwas zu verändern.

- Beginne bei Unklarheit mit check_connection.
- IDs (Immobilie, Mieter, Kostenart, Zähler) ermittelst du über die Lese-Werkzeuge, rate sie nie.
- Geldbeträge sind Strings mit Punkt und zwei Nachkommastellen ("1234.56"), in Euro.
- Ändern oder löschen kann dieser Server nichts; Korrekturen macht der Mensch in NebenkostenFix.
"""

RECORD_INSTRUCTIONS = """\
- Anlegen kannst du mit record_payment, record_meter_reading, record_invoice (auch mit Beleg) und
  upload_invoice_document. Ruf sie nur auf, wenn der Mensch dem Eintrag zugestimmt hat, und nenne ihm
  vorher die Werte. Bei einem Beleg mit mehreren Rechnungen: erst upload_invoice_document, dann jede
  Rechnung mit invoice_document_id.
- Kommt „saved: false“ mit Bedenken zurück, frag den Menschen. Den Freigabecode aus der
  Antwort schickst du nur nach seiner ausdrücklichen Zustimmung mit, nie vorab und nie von dir aus.
- Lehnt der Mensch ab oder lässt sich etwas nicht erfassen, sag ihm, dass er den Eintrag selbst in
  NebenkostenFix anlegen kann.
"""


Lifespan = Callable[[MCPServer], AbstractAsyncContextManager[None]]


def build_server(nkf: NkfClient, scope: Scope | None = None, lifespan: Lifespan | None = None) -> MCPServer:
    """The server with all tools that the access ``scope`` can use.

    ``scope=None`` means: unknown at startup (instance not reachable).
    Then everything is offered; a read-only key fails when recording with
    an understandable message.
    """
    may_record = scope != 'lesen'
    instructions = INSTRUCTIONS + (RECORD_INSTRUCTIONS if may_record else '')
    server = MCPServer('nkfix', instructions=instructions, version=__version__, lifespan=lifespan)
    connection.register(server, nkf)
    read.register(server, nkf)
    check.register(server, nkf)
    if may_record:
        record.register(server, nkf, require_interaction=nkf.config.prompt_on_record)
    return server


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format='%(asctime)s %(name)s %(levelname)s %(message)s')
    try:
        config = Config.from_env(os.environ)
    except ConfigError as error:
        print(f'nkfix-mcp: {error}', file=sys.stderr)
        raise SystemExit(2) from None

    scope = anyio.run(_scope_at_startup, config)
    if scope:
        log.info('Verbunden mit %s (%s), Umfang %s.', config.url, config.auth_method, scope)
    else:
        log.warning('NebenkostenFix unter %s war beim Start nicht erreichbar; alle Werkzeuge werden angeboten.',
                    config.url)
    nkf = NkfClient(config)

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await nkf.close()

    build_server(nkf, scope, lifespan=lifespan).run()


async def _scope_at_startup(config: Config) -> Scope | None:
    # Own client with a short timeout: a hanging startup blocks Claude.
    async with NkfClient(config, timeout_s=5.0) as probe:
        return await fetch_scope(probe)
