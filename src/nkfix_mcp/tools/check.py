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
from nkfix_mcp.tools import READ_ONLY, IsoDate, MeterId, TenantId, report_errors, tool


class Check(BaseModel):
    category: str | None
    meter_type: str | None
    status: str = Field(description='excellent, acceptable, warning oder no_data')
    blocking: bool | None
    message: str | None
    meter_number: str | None
    apartment: str | None


class StatementPrecheck(BaseModel):
    overall: str = Field(
        description='excellent, acceptable, warning oder no_meters (nichts zu prüfen); '
        'warning umfasst Blockaden und bloße Hinweise'
    )
    blocked: bool = Field(description='mindestens eine blockierende Prüfung ist nicht in Ordnung')
    checked_until: str = Field(
        description='Ende des geprüften Zeitraums; ein Ende in der Zukunft kürzt NebenkostenFix auf heute'
    )
    checks: list[Check]


class Plausibility(BaseModel):
    status: str = Field(description='ok, success, warning oder unknown')
    checked: bool = Field(
        description='true: NebenkostenFix hat verglichen, entweder mit einer Ablesung binnen 30 '
        'Tagen (dann steht reference_date) oder, bei warning/success, auch ohne sie. '
        'false: es gab nichts zum Vergleichen; ein „ok“ sagt dann nichts über den '
        'Wert'
    )
    message: str | None
    reference_date: str | None = Field(description='Datum der nächstgelegenen bekannten Ablesung')
    reference_value: float | None
    deviation_percent: float | None


class HausgeldLineItem(BaseModel):
    name: str = Field(description='Bezeichnung der Position wie in der Hausgeldabrechnung')
    total_amount: str = Field(description='Betrag für die ganze Gemeinschaft, z. B. "2400.00"')
    own_amount: str = Field(description='Anteil der eigenen Wohnung laut Abrechnung, z. B. "300.00"')


class HausgeldResult(BaseModel):
    """One line item as NebenkostenFix classified and recalculated it."""

    name: str
    total_amount: str | None
    own_amount: str | None = Field(description='eigener Betrag laut Abrechnung')
    allocable: bool | None = Field(
        description='true: auf den Mieter umlagefähig (BetrKV), false: trägt der Eigentümer, null: steht in keiner '
        'Nummer der BetrKV, umlagefähig nur, wenn der Mietvertrag sie nennt'
    )
    betrkv_number: int | None = Field(description='Nummer in § 2 BetrKV')
    reason: str | None = Field(description='Begründung der Einordnung')
    expected_own_amount: str | None = Field(description='eigener Betrag, nachgerechnet aus den Anteilen')
    deviation: str | None = Field(description='Betrag der Abweichung zwischen own_amount und expected_own_amount')
    matches: bool | None = Field(description='true: own_amount stimmt (bis auf Rundung)')
    note: str | None


class HausgeldSumCheck(BaseModel):
    statement_total: str | None = Field(description='Endsumme laut Einzelabrechnung (statement_total)')
    counted_total: str | None = Field(description='Summe der eigenen Beträge aller Positionen')
    deviation: str | None
    matches: bool | None = Field(description='false: vermutlich fehlt eine Position oder ist doppelt')
    note: str | None


class HausgeldCheck(BaseModel):
    line_items: list[HausgeldResult]
    allocable_total: str | None = Field(description='Summe der umlagefähigen eigenen Beträge')
    owner_total: str | None = Field(description='Summe dessen, was der Eigentümer trägt')
    undecided_total: str | None = Field(description='Summe der Positionen mit allocable=null')
    allocable_count: int | None
    owner_count: int | None
    undecided_count: int | None
    sum_check: HausgeldSumCheck | None = Field(description='nur mit statement_total')
    notes: list[str] = Field(description='Hinweise zum Abrechnungszeitraum und zu den Heizkosten')


def _hausgeld_result(raw: dict[str, Any]) -> HausgeldResult:
    return HausgeldResult(
        name=raw['name'],
        total_amount=format_money(raw.get('gesamtbetrag')),
        own_amount=format_money(raw.get('eigener_betrag')),
        allocable=raw.get('umlagefaehig'),
        betrkv_number=raw.get('betrkv_nr'),
        reason=raw.get('begruendung'),
        expected_own_amount=format_money(raw.get('erwartet')),
        deviation=format_money(raw.get('abweichung')),
        matches=raw.get('stimmt'),
        note=raw.get('hinweis'),
    )


def _hausgeld_sum_check(raw: dict[str, Any] | None) -> HausgeldSumCheck | None:
    # Upstream compares the stated total ("erwartet") with the counted one ("angesetzt").
    if not raw:
        return None
    return HausgeldSumCheck(
        statement_total=format_money(raw.get('erwartet')),
        counted_total=format_money(raw.get('angesetzt')),
        deviation=format_money(raw.get('abweichung')),
        matches=raw.get('stimmt'),
        note=raw.get('hinweis'),
    )


def _hausgeld_check(raw: dict[str, Any]) -> HausgeldCheck:
    # Upstream returns amounts as str(Decimal): "300", "0", "125.0625"; format_money makes them uniform.
    return HausgeldCheck(
        line_items=[_hausgeld_result(p) for p in raw.get('positionen') or []],
        allocable_total=format_money(raw.get('summe_umlagefaehig')),
        owner_total=format_money(raw.get('summe_eigentuemer')),
        undecided_total=format_money(raw.get('summe_offen')),
        allocable_count=raw.get('anzahl_umlagefaehig'),
        owner_count=raw.get('anzahl_eigentuemer'),
        undecided_count=raw.get('anzahl_offen'),
        sum_check=_hausgeld_sum_check(raw.get('summenprobe')),
        notes=[raw[h] for h in ('zeitraum_hinweis', 'heizkosten_hinweis') if raw.get(h)],
    )


def register(server: MCPServer, nkf: NkfClient) -> None:
    @tool(server, 'Abrechnung vorprüfen', READ_ONLY)
    @report_errors
    async def precheck_statement(
        tenant_id: TenantId,
        start_date: Annotated[IsoDate, Field(description='Beginn des Abrechnungszeitraums, YYYY-MM-DD')],
        end_date: Annotated[IsoDate, Field(description='Ende des Abrechnungszeitraums, YYYY-MM-DD')],
        cost_category_ids: Annotated[
            list[int] | None,
            Field(
                description=(
                    'Kostenarten, die abgerechnet werden sollen. Wirkt nur auf die Prüfung auf Überschneidung mit '
                    'bestehenden Abrechnungen; Zähler und Rechnungen werden immer für alle Kostenarten geprüft.'
                )
            ),
        ] = None,
    ) -> StatementPrecheck:
        """Prüft, ob sich eine Nebenkostenabrechnung für den Mieter und Zeitraum erstellen lässt: fehlende
        Rechnungen und Zählerstände, Überschneidung mit bestehenden Abrechnungen, Abrechnungsfrist. Ein Ende in
        der Zukunft kürzt NebenkostenFix auf heute. Erstellt keine Abrechnung und ändert nichts."""
        payload: dict[str, Any] = {'tenant_id': tenant_id, 'start_date': start_date, 'end_date': end_date}
        if cost_category_ids is not None:
            payload['category_ids'] = cost_category_ids
        raw = await nkf.post('/api/billing/preflight', payload)
        checks = [
            Check(
                category=c.get('category'),
                meter_type=c.get('meter_type'),
                status=c['status'],
                blocking=c.get('blocking'),
                message=c.get('message'),
                meter_number=c.get('meter_number'),
                apartment=c.get('apartment'),
            )
            for c in raw.get('checks') or []
        ]
        return StatementPrecheck(
            overall=raw['overall'],
            blocked=any(p.blocking and p.status not in ('excellent', 'acceptable') for p in checks),
            checked_until=min(end_date, date.today().isoformat()),
            checks=checks,
        )

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
        raw = await nkf.post(
            '/api/readings/check_plausibility', {'meter_id': meter_id, 'reading_date': reading_date, 'value': value}
        )
        deviation = raw.get('diff_percent')
        status = raw.get('status') or 'unknown'
        return Plausibility(
            status=status,
            checked=status in ('warning', 'success') or raw.get('closest_date') is not None,
            message=raw.get('message'),
            reference_date=raw.get('closest_date'),
            reference_value=raw.get('closest_value'),
            deviation_percent=None if deviation is None else round(float(deviation), 2),
        )

    @tool(server, 'Hausgeld prüfen', READ_ONLY)
    @report_errors
    async def check_hausgeld(
        line_items: Annotated[
            list[HausgeldLineItem], Field(description='die Positionen der Hausgeld-Einzelabrechnung der WEG')
        ],
        own_shares: Annotated[float, Field(gt=0, description='Miteigentumsanteil der Wohnung, z. B. 125')],
        total_shares: Annotated[float, Field(gt=0, description='Summe aller Miteigentumsanteile, z. B. 1000')],
        statement_total: Annotated[
            str | None, Field(description='Endsumme der Einzelabrechnung für die Summenprobe')
        ] = None,
    ) -> HausgeldCheck:
        """Prüft eine Hausgeld-Einzelabrechnung einer Eigentumswohnung: welche Positionen auf den Mieter
        umlagefähig sind (BetrKV), ob der eigene Anteil richtig berechnet ist und ob die Summe stimmt.
        Speichert nichts."""
        payload: dict[str, Any] = {
            'positionen': [
                {'name': p.name, 'gesamtbetrag': p.total_amount, 'eigener_betrag': p.own_amount} for p in line_items
            ],
            'eigener_anteil': own_shares,
            'gesamtanteile': total_shares,
        }
        if statement_total is not None:
            payload['abrechnungssumme'] = statement_total
        return _hausgeld_check(await nkf.post('/api/hausgeld/pruefen', payload))
