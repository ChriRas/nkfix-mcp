# nkfix-mcp

MCP-Server für [NebenkostenFix](https://github.com/Nerbry72/NebenkostenFix). Ein eigener Prozess, der als HTTP-Client die JSON-API einer NebenkostenFix-Instanz anspricht. GPL-3.0.

**Zu Beginn jeder Sitzung `.research/UEBERGABE.md` lesen.** Dort stehen Projekt, Ziele, API-Fakten, Plan und offene Punkte.

## Regeln
- Sprache:
  - **Englisch:** Bezeichner (Funktionen, Variablen, Klassen, Module), Kommentare, Docstrings, Testnamen, Commit-Messages, Umgebungsvariablen. Auch die Namen der Werkzeuge, Parameter und Felder des MCP-Servers; der deutsche Fachbegriff steht dann in der Beschreibung (`interim_reading`: „Zwischenablesung zum Mieterwechsel (§ 9b HeizkostenV)“).
  - **Deutsch:** alles, was Menschen lesen oder das Modell als Text bekommt: Fehlermeldungen, Rückfragen, Hinweise, Beschreibungen der Werkzeuge und Felder, Anweisungen an das Modell, Titel der Werkzeuge. Dazu README und diese Datei.
  - Werte, Schlüssel und Pfade der NebenkostenFix-API bleiben, wie die API sie schreibt, auch wenn sie deutsch sind.
  - Fachbegriffe ohne genaue englische Entsprechung bleiben als Lehnwort (`hausgeld`, `betrkv`). Das Glossar steht in `.research/neufassung/glossar.md`.
  - Den Nutzer duzen.
- Python auf Senior-Niveau: durchgehend typisiert, kleine Funktionen mit klarer Aufgabe, keine stillen Fehler, ruff sauber.
- Commits und PRs **nie** mit `Co-Authored-By:` oder einer Zeile „Generated with Claude Code“.
- Commit-Stil: Betreff auf Englisch im Imperativ („Filter invoices by year“), im Body die Begründung. Bei Fixes zusätzlich: „Test fails without the change (x of y).“
- Arbeitsweise: zuerst eine Plan-Datei in `.research/plaene/`, dann Freigabe durch den Nutzer, dann ein eigener Branch. Tests zuerst (rot → grün), danach gemeinsamer Test in Claude Code. Danach Recap, Refactoring (höchstens 2–3 Punkte) und Review durch einen Agenten mit frischem Kontext.
- Nichts pushen und nichts auf GitHub posten ohne Auftrag. Das Repo auf GitHub legt der Nutzer an bzw. gibt es frei.
- `.research/` ist privat und wird nie committet.
- **Keine echten Mieterdaten**, weder in Tests noch in Fixtures, Issues oder Commits. Nur Fantasiedaten. Docker auf :6060 gehört dem Nutzer: dort nichts anlegen und nichts ändern. Entwickelt und getestet wird gegen den Testserver auf :6061.
- Trainingswissen ist kein Faktenstand: Die API des MCP-SDK (`mcp` 2.x), Versionen und Claude-Konfiguration vor der Nutzung in der aktuellen Doku bzw. auf PyPI prüfen.
- Werkzeuge: `rm` ist gesperrt, gelöscht wird mit Python `Path.unlink`. `curl` leitet ein Hook um, für HTTP-Proben stattdessen `python -c "import urllib.request …"`.
- Befehle, die der Nutzer selbst ausführen soll, stehen unter `### ⚠ Deine Aktion` als ```bash-Block auf oberster Ebene.
