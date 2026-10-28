"""Read tools for properties: buildings, apartments and heating systems."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from nkfix_mcp.client import NkfClient
from nkfix_mcp.formatting import ItemList, truncate
from nkfix_mcp.tools import (
    MAX_ITEMS,
    READ_ONLY,
    PropertyId,
    report_errors,
    tool,
)


class Property(BaseModel):
    id: int
    name: str
    billing_year_start: str | None = Field(description='Tag und Monat, an dem das Abrechnungsjahr beginnt')
    standalone: bool = Field(description='Einfamilienhaus bzw. einzeln vermietetes Objekt')
    is_example: bool


class Apartment(BaseModel):
    id: int
    name: str
    area_sqm: float | None
    active: bool | None
    usage_type: str | None
    owner_occupied: bool | None
    self_supplied: bool | None


class HeatingSystem(BaseModel):
    id: int
    name: str
    supplies: str | None
    consumption_share_percent: float | None
    hot_water_method: str | None


def register(server: MCPServer, nkf: NkfClient) -> None:
    @tool(server, 'Immobilien', READ_ONLY)
    @report_errors
    async def list_properties() -> ItemList[Property]:
        """Alle Immobilien (Häuser bzw. Objekte) mit ID und Name. Ausgangspunkt für fast alle anderen Werkzeuge."""
        return truncate(
            [
                Property(
                    id=i['id'],
                    name=i['name'],
                    billing_year_start=i.get('abrechnungsjahr_beginn'),
                    standalone=bool(i.get('is_standalone')),
                    is_example=bool(i.get('ist_beispiel')),
                )
                for i in await nkf.get('/api/properties')
            ],
            MAX_ITEMS,
        )

    @tool(server, 'Wohnungen', READ_ONLY)
    @report_errors
    async def list_apartments(property_id: PropertyId) -> ItemList[Apartment]:
        """Die Wohnungen bzw. Einheiten einer Immobilie mit Fläche und Nutzungsart."""
        return truncate(
            [
                Apartment(
                    id=w['id'],
                    name=w['name'],
                    area_sqm=w.get('sqm'),
                    active=w.get('is_active'),
                    usage_type=w.get('nutzungsart'),
                    owner_occupied=w.get('eigennutzung'),
                    self_supplied=w.get('selbstversorger'),
                )
                for w in await nkf.get(f'/api/properties/{property_id}/apartments')
            ],
            MAX_ITEMS,
        )

    @tool(server, 'Heizungsanlagen', READ_ONLY)
    @report_errors
    async def list_heating_systems(property_id: PropertyId) -> ItemList[HeatingSystem]:
        """Die Heizungsanlagen einer Immobilie (für Heizkostenrechnungen nach HeizkostenV)."""
        return truncate(
            [
                HeatingSystem(
                    id=a['id'],
                    name=a['name'],
                    supplies=a.get('versorgt'),
                    consumption_share_percent=a.get('verbrauchsanteil_prozent'),
                    hot_water_method=a.get('warmwasser_weg'),
                )
                for a in await nkf.get(f'/api/properties/{property_id}/heizungsanlagen')
            ],
            MAX_ITEMS,
        )
