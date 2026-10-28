"""Read tools for tenants: who lives where, what they paid and still owe."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from nkfix_mcp.client import NkfClient
from nkfix_mcp.formatting import ItemList, format_money, total, truncate
from nkfix_mcp.tools import (
    MAX_ITEMS,
    READ_ONLY,
    PaymentType,
    TenantId,
    report_errors,
    tool,
)


class Tenant(BaseModel):
    id: int
    name: str
    property_id: int | None
    property: str | None
    apartment_id: int | None
    apartment: str | None
    move_in: str | None
    move_out: str | None
    billed_until: str | None
    is_example: bool


class Payment(BaseModel):
    id: int
    tenant_id: int
    tenant: str | None
    amount: str | None
    payment_date: str
    payment_type: str


class Payments(ItemList[Payment]):
    total: str


class Month(BaseModel):
    month: str
    expected: str | None
    actual: str | None
    difference: str | None
    arrears: bool | None
    is_current: bool | None = Field(description='laufender Monat: noch nicht fällig, zählt nicht als Rückstand')


class AdvancePayments(BaseModel):
    months: list[Month]
    outstanding: str | None = Field(description='Summe der Rückstände, über alle Monate')
    overpaid: str | None
    balance: str | None = Field(description='outstanding minus overpaid; positiv: der Mieter ist im Rückstand')


def _tenant(raw: dict[str, Any], apartment_to_property: dict[int, int]) -> Tenant:
    return Tenant(
        id=raw['id'],
        name=raw['name'],
        property_id=apartment_to_property.get(raw['apartment_id']) if raw.get('apartment_id') is not None else None,
        property=raw.get('property_name'),
        apartment_id=raw.get('apartment_id'),
        apartment=raw.get('apartment_name'),
        move_in=raw.get('move_in_date'),
        move_out=raw.get('move_out_date'),
        billed_until=raw.get('last_billed_until'),
        is_example=bool(raw.get('ist_beispiel')),
    )


def _payment(raw: dict[str, Any]) -> Payment:
    return Payment(
        id=raw['id'],
        tenant_id=raw['tenant_id'],
        tenant=raw.get('tenant_name'),
        amount=format_money(raw['amount']),
        payment_date=raw['payment_date'],
        payment_type=raw['type'],
    )


async def fetch_tenants(nkf: NkfClient) -> list[Tenant]:
    """All tenants with their property, as list_tenants and the overview resource show them."""
    apartment_to_property = {w['id']: w['property_id'] for w in await nkf.get('/api/apartments')}
    return [_tenant(m, apartment_to_property) for m in await nkf.get('/api/tenants')]


def register(server: MCPServer, nkf: NkfClient) -> None:
    @tool(server, 'Mieter', READ_ONLY)
    @report_errors
    async def list_tenants(
        property_id: Annotated[int | None, Field(description='nur Mieter dieser Immobilie')] = None,
        current_only: Annotated[
            bool, Field(description='nur Mieter, die heute dort wohnen (eingezogen, nicht ausgezogen)')
        ] = False,
    ) -> ItemList[Tenant]:
        """Mieter mit Wohnung, Ein- und Auszug und dem Datum, bis zu dem zuletzt abgerechnet wurde."""
        today = date.today().isoformat()
        tenants = await fetch_tenants(nkf)
        return truncate(
            [
                m
                for m in tenants
                if (property_id is None or m.property_id == property_id)
                and (not current_only or ((m.move_in or '') <= today and (not m.move_out or m.move_out >= today)))
            ],
            MAX_ITEMS,
        )

    @tool(server, 'Vorauszahlungen (Soll/Ist)', READ_ONLY)
    @report_errors
    async def get_advance_payment_status(
        tenant_id: TenantId,
        year: Annotated[
            int | None,
            Field(description='nur die Monate dieses Kalenderjahrs; Summen gelten immer für die ganze Mietzeit'),
        ] = None,
    ) -> AdvancePayments:
        """Soll und Ist der Nebenkostenvorauszahlungen eines Mieters je Monat, mit offenem Betrag und Saldo."""
        raw = await nkf.get(f'/api/tenants/{tenant_id}/vorauszahlungen')
        return AdvancePayments(
            months=[
                Month(
                    month=m['monat'],
                    expected=format_money(m.get('soll')),
                    actual=format_money(m.get('ist')),
                    difference=format_money(m.get('differenz')),
                    arrears=m.get('rueckstand'),
                    is_current=m.get('laufend'),
                )
                for m in raw.get('monate') or []
                if year is None or m['monat'].startswith(f'{year}-')
            ],
            outstanding=format_money(raw.get('offen')),
            overpaid=format_money(raw.get('ueberzahlt')),
            balance=format_money(raw.get('saldo')),
        )

    @tool(server, 'Zahlungen', READ_ONLY)
    @report_errors
    async def list_payments(
        tenant_id: Annotated[int | None, Field(description='nur Zahlungen dieses Mieters')] = None,
        year: Annotated[int | None, Field(description='nur Zahlungen in diesem Kalenderjahr')] = None,
        payment_type: Annotated[PaymentType | None, Field(description='nur diese Art')] = None,
    ) -> Payments:
        """Eingegangene Zahlungen von Mietern (Miete, Vorauszahlungen, Kaution, Nachzahlungen), neueste zuerst."""
        matching = [
            z
            for z in (_payment(raw) for raw in await nkf.get('/api/payments'))
            if (tenant_id is None or z.tenant_id == tenant_id)
            and (year is None or z.payment_date.startswith(f'{year}-'))
            and (payment_type is None or z.payment_type == payment_type)
        ]
        matching.sort(key=lambda z: (z.payment_date, z.id), reverse=True)
        items = truncate(matching, MAX_ITEMS)
        return Payments(**dict(items), total=total(z.amount for z in matching))
