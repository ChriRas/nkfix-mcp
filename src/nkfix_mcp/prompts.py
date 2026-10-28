"""Prompts: ready-made workflows the human starts as a command.

In Claude Code a prompt appears as ``/mcp__nkfix__<name>``; the arguments follow
separated by spaces, each a single word. A prompt only returns an
instruction for the assistant, which then fetches the data with the tools;
the prompt itself never calls the API, so it is always current and cannot
fail on the instance.

A missing required argument fails in the SDK before the prompt runs, and
an exception inside a prompt reaches Claude Code as "Internal server error"
(only an ``MCPError`` keeps its text). Neither gives the human a useful
message. That is why all arguments are optional and a missing or bad value
becomes an instruction to ask the human, never an exception.
"""

from __future__ import annotations

from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import Field

FIRST_YEAR, LAST_YEAR = 2000, 2100

ASK_BEFORE_RECORDING = (
    'Erfassen darfst du nur, wenn der Mensch dem Eintrag zugestimmt hat. Nenne ihm vorher die Werte. Kommt '
    '„saved: false“ mit Bedenken zurück, zeig sie ihm und frag, ob trotzdem gespeichert werden soll.'
)


def _id_problem(value: str) -> str | None:
    if not value:
        return 'Die Immobilie fehlt. Ermittle mit list_properties, welche es gibt, und frag, welche gemeint ist.'
    if not value.isdigit():
        return f'„{value}“ ist keine gültige ID einer Immobilie. Zeig mit list_properties die IDs und frag nach.'
    return None


def _year_problem(value: str) -> str | None:
    if not value:
        return 'Das Abrechnungsjahr fehlt. Frag, welches Jahr abgeschlossen werden soll (z. B. das Vorjahr).'
    if not value.isdigit() or not FIRST_YEAR <= int(value) <= LAST_YEAR:
        return f'„{value}“ ist kein gültiges Jahr. Frag nach dem Abrechnungsjahr, z. B. 2025.'
    return None


def _year_end(property_id: str, year: str, may_record: bool) -> str:
    problems = [p for p in (_id_problem(property_id), _year_problem(year)) if p]
    if problems:
        return (
            'Der Mensch möchte den Jahresabschluss in NebenkostenFix vorbereiten, aber der Befehl ist unvollständig. '
            + ' '.join(problems)
            + ' Frag nach, bevor du weitermachst, und starte dann den Ablauf unten mit den richtigen Werten.\n\n'
            + _year_end_steps('<ID>', '<Jahr>', may_record)
        )
    return _year_end_steps(property_id, year, may_record)


def _year_end_steps(property_id: str, year: str, may_record: bool) -> str:
    closing = (
        'Fehlt etwas, das du erfassen könntest (Rechnung mit record_invoice, Zählerstand mit record_meter_reading), '
        'biete es an. ' + ASK_BEFORE_RECORDING
        if may_record
        else 'Erfassen kann dieser Zugang nicht; was fehlt, trägt der Mensch in NebenkostenFix ein.'
    )
    return (
        f'Bereite den Jahresabschluss für die Immobilie {property_id}, Abrechnungsjahr {year}, in NebenkostenFix vor. '
        'Ändere nichts. Geh so vor:\n'
        f'1. get_billing_year_status für Immobilie {property_id} und Jahr {year}: Kostenarten, Fristen, Zustand.\n'
        f'2. get_data_quality für Immobilie {property_id}: veraltete oder fehlende Zählerstände.\n'
        f'3. list_invoices für Immobilie {property_id}, ohne year (year filtert nach Kalenderjahr, das '
        'Abrechnungsjahr kann z. B. am 01.07. beginnen); nimm die Rechnungen, deren Zeitraum den '
        'Abrechnungszeitraum aus Schritt 1 berührt. Fehlt für eine Kostenart eine Rechnung, oder weicht ein '
        'Betrag stark vom Vorjahr ab?\n'
        '4. Zählerstände zum Ende des Abrechnungszeitraums: Schritt 1 zeigt je Zähler cutoff_ok; für einen '
        'Zähler ohne passenden Stand zeigt list_meters mit meter_id seine letzten Stände.\n'
        '5. Für jeden Mieter aus Schritt 1: Hat er eine statement_id, gibt es die Abrechnung schon. Prüf sie mit '
        'get_statement: Fehlen Kostenarten aus Schritt 1, ist sie veraltet, ist sie zugestellt (delivered_at)? '
        'Sonst precheck_statement mit seinem Zeitraum aus Schritt 1.\n'
        '6. list_deadlines: Welche Fristen laufen?\n\n'
        'Fasse am Ende kurz zusammen: 1. was fehlt, 2. was auffällt, 3. welche Fristen bis wann laufen. Erstellen '
        f'kann der Mensch die Abrechnung danach selbst in NebenkostenFix. {closing}'
    )


def _hausgeld(may_record: bool) -> str:
    closing = (
        'Frag danach, welche umlagefähigen Positionen als Rechnung erfasst werden sollen (record_invoice, '
        f'Kostenart über list_cost_categories). {ASK_BEFORE_RECORDING}'
        if may_record
        else 'Erfassen kann dieser Zugang nicht; die umlagefähigen Positionen trägt der Mensch in NebenkostenFix ein.'
    )
    return (
        'Prüfe eine Hausgeld-Einzelabrechnung der WEG. Der Mensch hängt sie an oder fügt sie ein; fehlt sie, frag '
        'danach. Lies daraus jede Position mit Gesamtbetrag und eigenem Betrag, den eigenen Miteigentumsanteil, '
        'die Summe aller Anteile und die Endsumme. Ruf damit check_hausgeld auf (die Endsumme als statement_total). '
        'Erkläre das Ergebnis: was umlagefähig ist, was der Eigentümer trägt, was unklar ist und warum, welche '
        f'Beträge vom erwarteten Anteil abweichen und ob die Summenprobe stimmt. {closing}'
    )


def _receipt(receipt_path: str) -> str:
    source = (
        f'Lies den Beleg {receipt_path}.'
        if receipt_path
        else 'Frag nach dem Beleg (Pfad zur Datei oder als Anhang) und lies ihn.'
    )
    return (
        f'Erfasse einen Beleg in NebenkostenFix. {source} Dann:\n'
        '1. Bestimme die Immobilie (list_properties) und die Kostenart (list_cost_categories). Betrifft die Rechnung '
        'nur eine Wohnung, auch diese (list_apartments). Rate keine ID.\n'
        '2. Prüfe mit list_invoices, ob es die Rechnung schon gibt (gleiche Nummer oder gleicher Betrag und '
        'Zeitraum).\n'
        '3. Nenne dem Menschen alle Werte: Betrag, Leistungszeitraum, Rechnungsdatum und -nummer, Kostenart, '
        'Immobilie. Erst nach seiner Zustimmung rufst du record_invoice mit receipt_path auf.\n'
        '4. Stehen auf dem Beleg mehrere Rechnungen, lade ihn zuerst mit upload_invoice_document hoch und erfasse '
        'dann jede Rechnung mit invoice_document_id.\n'
        f'{ASK_BEFORE_RECORDING}'
    )


def register(server: MCPServer, may_record: bool) -> None:
    @server.prompt(
        name='prepare_year_end',
        title='Jahresabschluss vorbereiten',
        description='Geht den Stand eines Abrechnungsjahrs durch: Rechnungen, Zählerstände, Vorprüfung je Mieter, '
        'Fristen. Am Ende steht, was fehlt und was auffällt. Ändert nichts.',
    )
    def prepare_year_end(
        property_id: Annotated[str, Field(description='ID der Immobilie, z. B. 1 (siehe list_properties)')] = '',
        year: Annotated[str, Field(description='Abrechnungsjahr, z. B. 2025')] = '',
    ) -> str:
        return _year_end(property_id.strip(), year.strip(), may_record)

    @server.prompt(
        name='check_hausgeld_statement',
        title='Hausgeld-Abrechnung prüfen',
        description='Prüft eine angehängte Hausgeld-Einzelabrechnung der WEG: umlagefähig oder nicht, eigener '
        'Anteil, Summenprobe.',
    )
    def check_hausgeld_statement() -> str:
        return _hausgeld(may_record)

    if not may_record:
        return

    @server.prompt(
        name='record_receipt',
        title='Beleg erfassen',
        description='Liest einen Beleg, prüft auf Dubletten und erfasst die Rechnung nach deiner Zustimmung.',
    )
    def record_receipt(
        receipt_path: Annotated[
            str, Field(description='Pfad zur Belegdatei ohne Leerzeichen, z. B. ~/Belege/wasser-2025.pdf')
        ] = '',
    ) -> str:
        return _receipt(receipt_path.strip())
