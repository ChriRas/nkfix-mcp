"""Check tools: NebenkostenFix does the maths but saves nothing.

The three endpoints are POST because the web UI sends a payload in the
request body; a read key may still call them (``PRUEFEN`` in
``zugriffsschluessel.py``).
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from nkfix_mcp.client import NkfClient
from nkfix_mcp.formatting import format_money
from nkfix_mcp.tools import READ_ONLY, IsoDate, report_errors, tool
from nkfix_mcp.tools.read import TenantId

MeterId = Annotated[int, Field(description='ID des Zählers, siehe Werkzeug list_meters')]


class Check(BaseModel):
    category: str | None
    meter_type: str | None
    status: str = Field(description='excellent, acceptable, warning oder no_data')
    blocking: bool | None
    message: str | None
    meter_number: str | None
    apartment: str | None


class StatementPrecheck(BaseModel):
    overall: str = Field(description='excellent, acceptable, warning oder no_meters (nichts zu prüfen); '
                                     'warning umfasst Blockaden und bloße Hinweise')
    blocked: bool = Field(description='mindestens eine blockierende Prüfung ist nicht in Ordnung')
    checked_until: str = Field(description='Ende des geprüften Zeitraums; ein Ende in der Zukunft kürzt '
                                           'NebenkostenFix auf heute')
    checks: list[Check]


class Plausibility(BaseModel):
    status: str = Field(description='ok, success, warning oder unknown')
    checked: bool = Field(description='true: NebenkostenFix hat verglichen, entweder mit einer Ablesung binnen 30 '
                                      'Tagen (dann steht reference_date) oder, bei warning/success, auch ohne sie. '
                                      'false: es gab nichts zum Vergleichen; ein „ok“ sagt dann nichts über den '
                                      'Wert')
    message: str | None
    reference_date: str | None = Field(description='Datum der nächstgelegenen bekannten Ablesung')
    reference_value: float | None
    deviation_percent: float | None


class HausgeldLineItem(BaseModel):
    name: str = Field(description='Bezeichnung der Position wie in der Hausgeldabrechnung')
    total_amount: str = Field(description='Betrag für die ganze Gemeinschaft, z. B. "2400.00"')
    own_amount: str = Field(description='Anteil der eigenen Wohnung laut Abrechnung, z. B. "300.00"')


def register(server: MCPServer, nkf: NkfClient) -> None:
    @tool(server, 'Abrechnung vorprüfen', READ_ONLY)
    @report_errors
    async def precheck_statement(
        tenant_id: TenantId,
        start_date: Annotated[IsoDate, Field(description='Beginn des Abrechnungszeitraums, YYYY-MM-DD')],
        end_date: Annotated[IsoDate, Field(description='Ende des Abrechnungszeitraums, YYYY-MM-DD')],
        cost_category_ids: Annotated[list[int] | None, Field(description=(
            'Kostenarten, die abgerechnet werden sollen. Wirkt nur auf die Prüfung auf Überschneidung mit '
            'bestehenden Abrechnungen; Zähler und Rechnungen werden immer für alle Kostenarten geprüft.'))] = None,
    ) -> StatementPrecheck:
        """Prüft, ob sich eine Nebenkostenabrechnung für den Mieter und Zeitraum erstellen lässt: fehlende
        Rechnungen und Zählerstände, Überschneidung mit bestehenden Abrechnungen, Abrechnungsfrist. Ein Ende in
        der Zukunft kürzt NebenkostenFix auf heute. Erstellt keine Abrechnung und ändert nichts."""
        payload: dict[str, Any] = {'tenant_id': tenant_id, 'start_date': start_date, 'end_date': end_date}
        if cost_category_ids is not None:
            payload['category_ids'] = cost_category_ids
        raw = await nkf.post('/api/billing/preflight', payload)
        checks = [Check(
            category=c.get('category'), meter_type=c.get('meter_type'), status=c['status'],
            blocking=c.get('blocking'), message=c.get('message'), meter_number=c.get('meter_number'),
            apartment=c.get('apartment'),
        ) for c in raw.get('checks') or []]
        return StatementPrecheck(
            overall=raw['overall'],
            blocked=any(p.blocking and p.status not in ('excellent', 'acceptable') for p in checks),
            checked_until=min(end_date, date.today().isoformat()), checks=checks)

    @tool(server, 'Zählerstand prüfen', READ_ONLY)
    @report_errors
    async def check_meter_reading(
        meter_id: MeterId,
        reading_date: IsoDate,
        value: Annotated[float, Field(description='abgelesener Zählerstand')],
    ) -> Plausibility:
        """Prüft, ob ein Zählerstand zu den bisherigen Ablesungen passt. NebenkostenFix vergleicht nur mit
        einer Ablesung, die höchstens 30 Tage entfernt ist (beim Hauptzähler mit der Summe der Unterzähler);
        ohne sie ist checked=false. Speichert nichts."""
        raw = await nkf.post('/api/readings/check_plausibility',
                             {'meter_id': meter_id, 'reading_date': reading_date, 'value': value})
        deviation = raw.get('diff_percent')
        status = raw.get('status') or 'unknown'
        return Plausibility(
            status=status, checked=status in ('warning', 'success') or raw.get('closest_date') is not None,
            message=raw.get('message'), reference_date=raw.get('closest_date'),
            reference_value=raw.get('closest_value'),
            deviation_percent=None if deviation is None else round(float(deviation), 2))

    @tool(server, 'Hausgeld prüfen', READ_ONLY)
    @report_errors
    async def check_hausgeld(
        line_items: Annotated[list[HausgeldLineItem], Field(
            description='die Positionen der Hausgeld-Einzelabrechnung der WEG')],
        own_shares: Annotated[float, Field(gt=0, description='Miteigentumsanteil der Wohnung, z. B. 125')],
        total_shares: Annotated[float, Field(gt=0, description='Summe aller Miteigentumsanteile, z. B. 1000')],
        statement_total: Annotated[str | None, Field(
            description='Endsumme der Einzelabrechnung für die Summenprobe')] = None,
    ) -> dict[str, Any]:
        """Prüft eine Hausgeld-Einzelabrechnung einer Eigentumswohnung: welche Positionen auf den Mieter
        umlagefähig sind (BetrKV), ob der eigene Anteil richtig berechnet ist und ob die Summe stimmt.
        Speichert nichts."""
        payload: dict[str, Any] = {
            'positionen': [{'name': p.name, 'gesamtbetrag': p.total_amount, 'eigener_betrag': p.own_amount}
                           for p in line_items],
            'eigener_anteil': own_shares, 'gesamtanteile': total_shares}
        if statement_total is not None:
            payload['abrechnungssumme'] = statement_total
        raw = await nkf.post('/api/hausgeld/pruefen', payload)
        # Upstream returns str(Decimal): "300", "0", "125.0625". Made uniform here.
        probe = raw.get('summenprobe')
        return {
            'line_items': [{**p, **{k: format_money(p.get(k)) for k in ('gesamtbetrag', 'eigener_betrag', 'erwartet',
                                                                        'abweichung')}}
                           for p in raw.get('positionen') or []],
            **{k: format_money(raw.get(k)) for k in ('summe_umlagefaehig', 'summe_eigentuemer', 'summe_offen')},
            **{k: raw.get(k) for k in ('anzahl_umlagefaehig', 'anzahl_eigentuemer', 'anzahl_offen')},
            'sum_check': None if not probe else {
                **probe, **{k: format_money(probe.get(k)) for k in ('erwartet', 'angesetzt', 'abweichung')}},
            'notes': [raw[h] for h in ('zeitraum_hinweis', 'heizkosten_hinweis') if raw.get(h)],
        }
