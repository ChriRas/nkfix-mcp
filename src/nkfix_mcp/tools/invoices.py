"""Read tools for costs: cost categories, providers, invoices and receipts."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from nkfix_mcp.client import NkfClient
from nkfix_mcp.formatting import ItemList, format_money, in_year, total, truncate
from nkfix_mcp.tools import (
    MAX_ITEMS,
    READ_ONLY,
    report_errors,
    tool,
)

# Measured (review S2-6): 100 invoices are about 20,000 tokens, Claude Code warns above 10,000.
MAX_INVOICES = 40


class CostCategory(BaseModel):
    id: int
    name: str
    allocation: str | None = Field(description='Umlageschlüssel, z. B. qm, personen, verbrauch')
    requires_meter: bool
    betrkv_number: int | None
    betrkv_label: str | None
    invoice_count: int | None
    meter_count: int | None


class Provider(BaseModel):
    id: int
    name: str


class Invoice(BaseModel):
    id: int
    property_id: int
    property: str | None
    cost_category_id: int
    cost_category: str | None
    amount: str | None
    start_date: str | None
    end_date: str | None
    invoice_date: str | None
    invoice_number: str | None
    description: str | None
    provider_id: int | None
    provider: str | None
    apartment_id: int | None = Field(description='nur bei Rechnungen, die eine einzelne Wohnung betreffen')
    invoice_document_id: int | None
    has_receipt: bool
    heating_system_id: int | None
    heating_cost_type: str | None
    base_price: str | None
    labor_cost: str | None
    co2_cost: str | None


class Invoices(ItemList[Invoice]):
    total: str = Field(
        description='Summe der vollen Rechnungsbeträge aller passenden Rechnungen, auch der '
        'weggelassenen; nicht zeitanteilig. Die Kosten eines Jahres je Kostenart '
        'liefert get_billing_year_status.'
    )


class InvoiceDocument(BaseModel):
    id: int
    property_id: int | None
    filename: str | None
    description: str | None
    uploaded_at: str | None


def _invoice(raw: dict[str, Any]) -> Invoice:
    return Invoice(
        id=raw['id'],
        property_id=raw['property_id'],
        property=raw.get('property_name'),
        cost_category_id=raw['category_id'],
        cost_category=raw.get('category_name'),
        amount=format_money(raw['amount']),
        start_date=raw.get('start_date'),
        end_date=raw.get('end_date'),
        invoice_date=raw.get('rechnungsdatum'),
        invoice_number=raw.get('invoice_number'),
        description=raw.get('description'),
        provider_id=raw.get('provider_id'),
        provider=raw.get('provider_name'),
        apartment_id=raw.get('apartment_id'),
        invoice_document_id=raw.get('invoice_document_id'),
        has_receipt=bool(raw.get('document_path') or raw.get('invoice_document_id')),
        heating_system_id=raw.get('heizungsanlage_id'),
        heating_cost_type=raw.get('heizkostenart'),
        base_price=format_money(raw.get('grundpreis')),
        labor_cost=format_money(raw.get('lohnanteil')),
        co2_cost=format_money(raw.get('co2_kosten')),
    )


def register(server: MCPServer, nkf: NkfClient) -> None:
    @tool(server, 'Kostenarten', READ_ONLY)
    @report_errors
    async def list_cost_categories() -> ItemList[CostCategory]:
        """Kostenarten (Betriebskostenarten nach BetrKV) mit Umlageschlüssel. Die ID braucht man zum Erfassen
        von Rechnungen."""
        return truncate(
            [
                CostCategory(
                    id=k['id'],
                    name=k['name'],
                    allocation=k.get('allocation_method'),
                    requires_meter=bool(k.get('requires_meter')),
                    betrkv_number=k.get('betrkv_nr'),
                    betrkv_label=k.get('betrkv_bezeichnung'),
                    invoice_count=k.get('rechnungen'),
                    meter_count=k.get('zaehler_anzahl'),
                )
                for k in await nkf.get('/api/categories')
            ],
            MAX_ITEMS,
        )

    @tool(server, 'Dienstleister', READ_ONLY)
    @report_errors
    async def list_providers() -> ItemList[Provider]:
        """Dienstleister bzw. Versorger (Rechnungssteller), die in NebenkostenFix angelegt sind."""
        return truncate([Provider(id=d['id'], name=d['name']) for d in await nkf.get('/api/providers')], MAX_ITEMS)

    @tool(server, 'Rechnungen', READ_ONLY)
    @report_errors
    async def list_invoices(
        property_id: Annotated[int | None, Field(description='nur Rechnungen dieser Immobilie')] = None,
        year: Annotated[int | None, Field(description='nur Rechnungen, deren Zeitraum dieses Jahr berührt')] = None,
        cost_category_id: Annotated[int | None, Field(description='nur diese Kostenart')] = None,
    ) -> Invoices:
        """Rechnungen (Kosten) mit Betrag, Zeitraum, Kostenart und ob ein Beleg hinterlegt ist. Eine Rechnung
        zählt zu einem Jahr, wenn sich ihr Zeitraum mit dem Jahr überschneidet."""
        matching = [
            r
            for r in (_invoice(raw) for raw in await nkf.get('/api/invoices'))
            if (property_id is None or r.property_id == property_id)
            and (cost_category_id is None or r.cost_category_id == cost_category_id)
            and (year is None or in_year(r.start_date, r.end_date, year))
        ]
        matching.sort(key=lambda r: (r.start_date or '', r.id))
        items = truncate(matching, MAX_INVOICES)
        return Invoices(**dict(items), total=total(r.amount for r in matching))

    @tool(server, 'Sammelbelege', READ_ONLY)
    @report_errors
    async def list_invoice_documents(
        property_id: Annotated[int | None, Field(description='nur Belege dieser Immobilie')] = None,
    ) -> ItemList[InvoiceDocument]:
        """Hochgeladene Belege, die mehrere Rechnungen enthalten können (Sammelbelege)."""
        return truncate(
            [
                InvoiceDocument(
                    id=b['id'],
                    property_id=b.get('property_id'),
                    filename=b.get('filename'),
                    description=b.get('description'),
                    uploaded_at=b.get('upload_date'),
                )
                for b in await nkf.get('/api/invoice_documents')
                if property_id is None or b.get('property_id') == property_id
            ],
            MAX_ITEMS,
        )
