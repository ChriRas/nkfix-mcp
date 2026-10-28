# nkfix-mcp

Ein [MCP-Server](https://modelcontextprotocol.io) für [NebenkostenFix](https://github.com/Nerbry72/NebenkostenFix).
Mit ihm kann ein KI-Assistent wie Claude Code, Claude Desktop oder ein lokales Modell mit dem Datenbestand
deiner NebenkostenFix-Instanz arbeiten:

- Fragen beantworten: „Welche Rechnungen fehlen 2025 noch?“, „Welche Fristen laufen bald ab?“, „Wer ist mit
  den Vorauszahlungen im Rückstand?“
- Abrechnungen vorprüfen, bevor du sie in NebenkostenFix erstellst
- Rechnungen samt Beleg, Zählerstände und Zahlungen erfassen. Den Beleg liest der Assistent selbst, du
  bestätigst, der Server legt an.

Der Server ist ein eigener Prozess. Er spricht die JSON-API von NebenkostenFix über HTTP an und läuft auf
deinem Rechner, neben Claude.

> [!WARNING]
> **Datenschutz: Alles, was der Assistent liest, geht an dessen Anbieter.**
> NebenkostenFix hält deine Daten bei dir. Sobald ein Cloud-Assistent wie Claude über diesen Server Mieter,
> Zahlungen oder Abrechnungen liest, landen diese Daten beim Anbieter des Assistenten. Das betrifft
> personenbezogene Daten deiner Mieter. Prüfe, ob das für dich in Ordnung ist (DSGVO,
> Auftragsverarbeitung). Mit einem **lokalen Modell** bleiben die Daten auf deinem Rechner, siehe
> [Mit einem lokalen Modell](#mit-einem-lokalen-modell).

## Was der Server kann und was nicht

| Bereich | Werkzeuge |
|---|---|
| Verbindung | `check_connection` |
| Lesen | `list_properties`, `list_apartments`, `list_heating_systems`, `list_tenants`, `get_advance_payment_status`, `list_cost_categories`, `list_providers`, `list_invoices`, `list_invoice_documents`, `list_meters`, `list_payments`, `list_deadlines`, `get_data_quality`, `get_billing_year_status`, `list_billing_suggestions`, `list_statements`, `get_statement` |
| Prüfen (ohne Speichern) | `precheck_statement`, `check_meter_reading`, `check_hausgeld` |
| Erfassen (nur anlegen) | `record_payment`, `record_meter_reading`, `record_invoice` (optional mit Beleg), `upload_invoice_document` |

**Nie:** ändern, löschen, Abrechnungen erstellen oder festsetzen, Zähler anlegen, Export, Sicherung, Konten.
Korrekturen machst du in NebenkostenFix. Mit einem Zugriffsschlüssel verweigert NebenkostenFix das ohnehin.

**Rückfragen beim Erfassen:** Gibt es Bedenken, etwa dieselbe Zahlung schon, einen Zählerstand unter dem
vorigen oder eine Rechnung mit derselben Nummer, legt der Server nichts an, sondern fragt nach.
- Claude Code zeigt dir die Frage direkt.
- Andere Clients geben sie an den Assistenten weiter, und der muss dich fragen. Sagst du Ja, speichert er
  mit einem Freigabecode aus der Rückfrage. Der gilt nur für genau diese Werte und Bedenken; ändert sich
  etwas, fragt der Server erneut.
- Claude Code fragt außerdem bei jedem Erfassen nach einer Freigabe, auch wenn du die Werkzeuge allgemein
  erlaubt hast. Im Auto-Modus entfällt diese Freigabe; die Rückfrage bei Bedenken kommt dort trotzdem.
  Abschalten lässt sich die Freigabe mit `NKF_NO_RECORD_PROMPT=1`.

## Voraussetzungen

- **Eine NebenkostenFix-Instanz mit Anmeldung.** Docker, NAS oder `python app.py`. Die Windows-App mit
  eigenem Fenster geht nicht, sie lässt keine fremden Programme an ihre API.
- **Zugang auf einem von zwei Wegen:**
  - **Zugriffsschlüssel (empfohlen).** Den gibt es in NebenkostenFix erst mit
    [PR #48](https://github.com/Nerbry72/NebenkostenFix/pull/48), Stand Oktober 2026 noch nicht gemergt.
  - **Benutzername und Passwort.** Geht mit jeder Version, das Passwort steht dann aber in der
    Konfiguration.
- **[uv](https://docs.astral.sh/uv/)** zum Installieren. Python 3.11 holt uv bei Bedarf selbst.

## Installation

```bash
uv tool install /pfad/zu/nkfix-mcp
```

Danach liegt der Befehl `nkfix-mcp` in `~/.local/bin`. Den vollen Pfad zeigt:

```bash
which nkfix-mcp
```

## Zugriffsschlüssel anlegen

In NebenkostenFix unter **Einstellungen → Konto → „Zugriffsschlüssel (API-Key)“**:

- **Lesen**: Der Assistent kann lesen und prüfen. Die Erfassen-Werkzeuge bekommt er gar nicht erst zu sehen.
- **Lesen und erfassen**: Zusätzlich kann er Rechnungen, Belege, Zählerstände und Zahlungen anlegen.

Der Schlüssel wird genau einmal angezeigt. Kopier ihn direkt in die Konfiguration. Ein gelöschter Schlüssel
gilt sofort nicht mehr.

## Konfiguration

| Variable | Bedeutung |
|---|---|
| `NKF_URL` | Adresse der Instanz, z. B. `http://127.0.0.1:6060` oder `https://nkf.example.org` (Pflicht) |
| `NKF_API_KEY` | Zugriffsschlüssel `nkf_…` (empfohlen) |
| `NKF_USERNAME`, `NKF_PASSWORD` | Anmeldung per Sitzung, wenn deine Version keine Schlüssel kennt |
| `NKF_RECEIPT_DIR` | Wenn gesetzt, lädt der Server nur Belege aus diesem Ordner hoch (empfohlen) |
| `NKF_NO_RECORD_PROMPT` | `1` schaltet die Nachfrage von Claude Code bei jedem Erfassen ab |

Hinweise:
- Ohne `NKF_RECEIPT_DIR` kann der Assistent jede Datei als Beleg hochladen, deren Pfad er kennt. Hochgeladen
  wird nur, was am Inhalt als PDF, JPEG, PNG, WebP oder HEIC erkannt wird.
- Liegt die Instanz nicht auf deinem Rechner, nutze `https`. Sonst gehen Schlüssel und Daten im Klartext
  durchs Netz. `check_connection` warnt dann.
- Zugangsdaten in der URL (`https://user:pass@…`) lehnt der Server ab.

## Einrichten in Claude Code

```bash
claude mcp add nkfix -e NKF_URL=http://127.0.0.1:6060 -e NKF_API_KEY=nkf_DEIN_SCHLUESSEL -e NKF_RECEIPT_DIR=$HOME/Belege -- nkfix-mcp
```

- Ohne `--scope` gilt der Eintrag nur für das aktuelle Projekt und landet in `~/.claude.json`, also nicht
  im Repo. Der Schlüssel steht dort im Klartext.
- Für alle Projekte: `--scope user`.
- Für eine `.mcp.json` im Projekt (Vorlage: [`.mcp.json.example`](.mcp.json.example)) trägst du den
  Schlüssel nicht ein, sondern verweist mit `${NKF_API_KEY}` auf eine Umgebungsvariable. Ist sie beim Start
  von Claude nicht gesetzt, meldet der Server das, statt den Platzhalter als Schlüssel zu schicken.

Danach in Claude Code `/mcp` aufrufen. Der Server sollte als verbunden erscheinen. Ein erster Test:
„Prüf die Verbindung zu NebenkostenFix.“

Claude Code zeigt die Werkzeuge mit deutschem Titel an, z. B. „Rechnung erfassen“ statt `record_invoice`.

Werkzeuge ohne Nachfrage erlaubst du über `/permissions` mit der Regel `mcp__nkfix__*`. Die
Erfassen-Werkzeuge fragen trotzdem jedes Mal nach, laut Doku von Claude Code sogar in den Modi `auto` und
`bypassPermissions` (siehe oben).

## Einrichten in Claude Desktop

Die Konfigurationsdatei liegt
- unter macOS in `~/Library/Application Support/Claude/claude_desktop_config.json`,
- unter Windows in `%APPDATA%\Claude\claude_desktop_config.json`.

```json
{
  "mcpServers": {
    "nkfix": {
      "command": "/Users/DEINNAME/.local/bin/nkfix-mcp",
      "env": {
        "NKF_URL": "http://127.0.0.1:6060",
        "NKF_API_KEY": "nkf_DEIN_SCHLUESSEL",
        "NKF_RECEIPT_DIR": "/Users/DEINNAME/Belege"
      }
    }
  }
}
```

Trag bei `command` den vollen Pfad aus `which nkfix-mcp` ein. Claude Desktop kennt den `PATH` deiner
Shell nicht. Danach Claude Desktop neu starten.

Claude Desktop zeigt Rückfragen des Servers derzeit vermutlich nicht direkt an. Dann kommt die Rückfrage
als Antwort zurück, und der Assistent muss dich fragen.

## Mit einem lokalen Modell

So bleiben die Daten auf deinem Rechner. Ein erprobter Weg ist [Ollama](https://ollama.com) mit dem Client
[ollmcp](https://github.com/jonigl/mcp-client-for-ollama). Er fragt vor jedem Werkzeugaufruf nach deiner
Freigabe.

```bash
uv tool install ollmcp
```

```bash
ollmcp mcp add --env NKF_URL=http://127.0.0.1:6060 --env NKF_API_KEY=nkf_DEIN_SCHLUESSEL nkfix -- nkfix-mcp
```

```bash
ollmcp
```

Nimm ein Modell, das Werkzeuge aufrufen kann. Kleine Modelle verwechseln gern IDs. Für den Anfang reicht ein
Lese-Schlüssel. [LM Studio](https://lmstudio.ai/docs/app/mcp) kann ab Version 0.3.17 ebenfalls MCP-Server
einbinden (`mcp.json` mit `mcpServers`, gleiches Format wie oben).

Stand der Angaben: Oktober 2026. Die Clients ändern sich schnell, prüf im Zweifel deren Doku.

## Grenzen

- Die API von NebenkostenFix ist für die eigene Oberfläche gebaut und hat keine Stabilitätszusage. Dieser
  Server übersetzt sie in eigene Werkzeuge, und Vertragstests fangen Änderungen ab. Nach einem Update von
  NebenkostenFix kann trotzdem ein Update dieses Servers nötig sein.
- NebenkostenFix hat keine Texterkennung. Den Inhalt eines Belegs liest der Assistent selbst, Fehler sind
  möglich. Prüf die Werte, bevor du zustimmst.
- Arbeiten Mensch und Assistent gleichzeitig am selben Eintrag, gewinnt der letzte. Der Server legt nur an
  und ändert nichts, das hält das Risiko klein.

## Entwicklung

```bash
uv venv --python 3.11
```

```bash
uv pip install -e . -r requirements-dev.txt
```

```bash
.venv/bin/python -m pytest
```

```bash
.venv/bin/ruff check .
```

```bash
.venv/bin/ruff format --check .
```

```bash
.venv/bin/mypy
```

Einmal `pre-commit install`, dann laufen ruff und mypy vor jedem Commit. Ein Hook lehnt Commit-Messages mit
`Co-Authored-By:` oder „Generated with Claude Code“ ab. Die CI (`.github/workflows/ci.yml`) prüft dasselbe und
lässt die Einheitstests laufen.

```bash
.venv/bin/pre-commit install
```

Nach einem Pull, der das Paket verschiebt oder umbenennt, einmal neu installieren (`uv pip install -e .`).

**Vertragstests** gegen eine echte Instanz:
- Sie starten NebenkostenFix aus einem Checkout mit frischem Datenordner auf einem freien Port.
- Sie legen die Beispielimmobilie und zwei Zugriffsschlüssel an. Es sind nur Fantasiedaten im Spiel.
- Ohne `NKF_SOURCE` werden sie übersprungen.

```bash
NKF_SOURCE=/pfad/zu/NebenkostenFix NKF_PYTHON=/pfad/zu/NebenkostenFix/.venv/bin/python .venv/bin/python -m pytest
```

`requirements.txt` hält alle Versionen fest (erzeugt mit
`uv pip compile --universal pyproject.toml -o requirements.txt --python-version 3.11`).

## Lizenz

GPL-3.0-or-later, wie NebenkostenFix. Siehe [LICENSE](LICENSE).
