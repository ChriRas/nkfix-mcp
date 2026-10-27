"""Every tool carries a German title: people see it instead of the English name."""

import pytest
from mcp.client import Client

from nkfix_mcp.server import build_server
from tests.helpers import FakeNkf, client

pytestmark = pytest.mark.anyio

TITLES = {
    'check_connection': 'Verbindung prüfen',
    'list_properties': 'Immobilien',
    'list_apartments': 'Wohnungen',
    'list_heating_systems': 'Heizungsanlagen',
    'list_tenants': 'Mieter',
    'get_advance_payment_status': 'Vorauszahlungen (Soll/Ist)',
    'list_cost_categories': 'Kostenarten',
    'list_providers': 'Dienstleister',
    'list_invoices': 'Rechnungen',
    'list_invoice_documents': 'Sammelbelege',
    'list_meters': 'Zähler und Stände',
    'list_payments': 'Zahlungen',
    'list_deadlines': 'Fristen',
    'get_data_quality': 'Datenqualität',
    'get_billing_year_status': 'Jahresabrechnung (Stand)',
    'list_billing_suggestions': 'Abrechnungsvorschläge',
    'list_statements': 'Abrechnungen',
    'get_statement': 'Abrechnung im Detail',
    'precheck_statement': 'Abrechnung vorprüfen',
    'check_meter_reading': 'Zählerstand prüfen',
    'check_hausgeld': 'Hausgeld prüfen',
    'record_payment': 'Zahlung erfassen',
    'record_meter_reading': 'Zählerstand erfassen',
    'record_invoice': 'Rechnung erfassen',
    'upload_invoice_document': 'Sammelbeleg hochladen',
}


async def test_every_tool_has_its_german_title_where_claude_code_and_the_spec_look():
    # Claude Code 2.1.312 shows annotations.title, the MCP specification has Tool.title.
    nkf = client(FakeNkf())
    async with nkf, Client(build_server(nkf, 'erfassen')) as mcp:
        tools = (await mcp.list_tools()).tools
    assert {t.name: t.title for t in tools} == TITLES
    assert {t.name: t.annotations.title for t in tools} == TITLES
