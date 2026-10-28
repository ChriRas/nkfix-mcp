"""Read tools for meters and their readings."""

from __future__ import annotations

from typing import Annotated, Any

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

READINGS_PER_METER = 10


class Reading(BaseModel):
    id: int
    reading_date: str
    value: float | None
    value_off_peak: float | None = Field(description='Nebentarif bei Zweitarifzählern')
    reading_type: str | None = Field(description='ablesung oder zwischenablesung (Mieterwechsel)')


class Meter(BaseModel):
    id: int
    number: str | None
    cost_category_id: int | None
    cost_category: str | None
    unit: str | None
    property_id: int | None
    apartment_id: int | None
    apartment: str | None = Field(description='leer bei Hauptzählern des Hauses')
    main_meter: bool
    dual_tariff: bool
    calibrated_until: str | None
    location: str | None
    replaced_by: int | None
    reading_count: int
    readings: list[Reading] = Field(
        description=f'neuester Stand zuerst; ohne meter_id nur der neueste, mit '
        f'meter_id die neuesten {READINGS_PER_METER}'
    )


def _reading(raw: dict[str, Any]) -> Reading:
    return Reading(
        id=raw['id'],
        reading_date=raw['reading_date'],
        value=raw.get('value'),
        value_off_peak=raw.get('value_nt'),
        reading_type=raw.get('ablesungsart'),
    )


def _meter(raw: dict[str, Any], readings: list[dict[str, Any]], count: int) -> Meter:
    latest = sorted(readings, key=lambda s: (s['reading_date'], s['id']), reverse=True)
    return Meter(
        id=raw['id'],
        number=raw.get('meter_number'),
        cost_category_id=raw.get('category_id'),
        cost_category=raw.get('category_name'),
        unit=raw.get('einheit'),
        property_id=raw.get('property_id'),
        apartment_id=raw.get('apartment_id'),
        apartment=raw.get('apartment_name'),
        main_meter=bool(raw.get('is_main_meter')),
        dual_tariff=bool(raw.get('has_dual_tariff')),
        calibrated_until=raw.get('geeicht_bis'),
        location=raw.get('ort'),
        replaced_by=raw.get('ersetzt_durch'),
        reading_count=len(readings),
        readings=[_reading(s) for s in latest[:count]],
    )


def register(server: MCPServer, nkf: NkfClient) -> None:
    @tool(server, 'Zähler und Stände', READ_ONLY)
    @report_errors
    async def list_meters(
        property_id: PropertyId,
        meter_id: Annotated[int | None, Field(description='nur dieser Zähler')] = None,
    ) -> ItemList[Meter]:
        """Die Zähler einer Immobilie mit ihrem neuesten Stand; mit meter_id die letzten Stände dieses Zählers."""
        readings: dict[int, list[dict[str, Any]]] = {}
        for reading in await nkf.get('/api/readings'):
            readings.setdefault(reading['meter_id'], []).append(reading)
        count = 1 if meter_id is None else READINGS_PER_METER
        return truncate(
            [
                _meter(z, readings.get(z['id'], []), count)
                for z in await nkf.get('/api/meters')
                if z.get('property_id') == property_id and (meter_id is None or z['id'] == meter_id)
            ],
            MAX_ITEMS,
        )
