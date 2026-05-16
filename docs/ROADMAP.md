# Reworking the Wallbox App – Roadmap

## Workflow

- Möglichst viele Tasks durch Claude Code erledigen lassen
- Schritte gut erklären, da es sich auch um ein Lernprojekt handelt
- Pro Phase: erst Plan Mode in Claude Code, Plan reviewen, dann Implementierung
- Pro abgeschlossener Phase ein Git-Tag (`v0.2-csv-scrape`, `v0.3-rest-api`, …) als Rollback-Anker
- CLAUDE.md am Ende jeder Phase mit aktualisieren (Phase-Status, Tech-Stack, Konventionen)

## Reihenfolge

Die Phasen 2.6 bis 3 sind abgeschlossen (Kurzfassung unten, Details
in CLAUDE.md → Phase-Status). Offen:

1. **Phase 4** (optional) – Reports-Vollständigkeitsanzeige, Custom-Range-Reports

---

## Phase 2.6: Migration CSV-Scrape → REST-API ✅ erledigt

REST-API auf `:8443` ersetzt den PHP-Scrape. Client in
`charging/keba_api.py`, vollständige Endpunkt-Referenz in
`docs/keba_api.md`. Details siehe CLAUDE.md → Phase 2.6.

---

## Phase 2.7: Eichrechtskonformität im PDF ✅ erledigt

MVA-signierte Records (`mva_record_data`, `mva_record_signature`)
landen via `ingest_json_row` in der DB; der Public-Key-Fingerprint
und die Wallbox-Serial erscheinen im PDF-Footer, signierte Sessions
sind in der Tabelle mit ✓ markiert. ECDSA-Verifikation in-app bleibt
out of scope. Details siehe CLAUDE.md → Phase 2.7.

---

## Phase 2.8: Settings-Seite konsolidieren ✅ erledigt

`/settings/` mit vier Sektionen: Tariff, Wallbox API
(encrypted-at-rest via `EncryptedField`), Report recipient,
Eichrecht info (read-only, mit live gefetchter Firmware).
API-Credentials liegen im `AppSettings`-Singleton; `.env` bleibt
CLI-Fallback. Details siehe CLAUDE.md → Phase 2.8.

---

## Phase 2.9: Dashboard – Live-State und Monatsübersicht ✅ erledigt

Dashboard zeigt Live-State (`IDLE` / `CHARGING` / `ERROR`) per Pageload,
mit Last-Known-State-Cache bei unreachable Wallbox, plus kompakte
Monatsübersicht und Vormonatsvergleich. Reports-Seite hat einen
Inline-PDF-Viewer; Admin-Link heißt jetzt "Raw data". Details siehe
CLAUDE.md → Phase 2.9.

---

## Phase 3: Email-Versand + Auto-Import ✅ erledigt

Email-Versand über Strato SMTP (`smtp.strato.de:465`, implicit TLS) von
`wallbox@ingko.de`. "Send by email"-Button pro Reports-Zeile;
SMTP-Credentials in `.env`, Empfänger in `AppSettings`.

**Achtung – Plan-Abweichung:** der ursprünglich geplante systemd-Timer
für Scheduled Imports wurde verworfen. Stattdessen löst jeder
Dashboard-Pageload einen Import aus, wenn die Wallbox mehr Billable
Sessions hat als die DB (`charging/services/auto_import.py`, ein
einziger `/v2/sessions`-Call deckt Count-Check und Ingest ab). Gründe
und Mechanik: CLAUDE.md → Phase 3.

---

## Phase 4: Optional, nur bei echtem Bedarf

- **Reports-Vollständigkeitsanzeige** – Liste aller Monate mit Sessions, Indikator „Report vorhanden / fehlt", Aktion „Generate missing reports"
- **Custom-Range-Reports** – neues `CustomReport`-Modell mit `start_date` und `end_date`. Nur wenn HR oder du einen konkreten Use Case dafür habt; aktuell nicht angefragt.
- **MVA-Signatur-Verifikation in der App** – ECDSA-Check gegen den Public Key. Nur falls HR plötzlich kryptografische Nachweise im PDF verlangt.

---

## Out of Scope

- **Django-Admin komplett ersetzen** – gibt RFID-Editor, Tariff-CRUD, Roh-Sessions-Browser gratis. Custom-Ersatz wäre 5–10 Tage Arbeit für minimalen UX-Gewinn. Bleibt drin, nur als „Raw data" rebranded.
- **Multi-Wallbox-Support** – App ist explizit für die eine Box im Haus
- **THG-Quote / Tax-Features** – für Arbeitgeber-Erstattung nicht relevant
- **Public-Facing Webserver** – LAN-only bleibt, kein Reverse-Proxy, keine externe Erreichbarkeit, kein HTTPS

---

## Offene Fragen

Keine kritischen mehr offen.

- Eichrecht: über HR-Policy geklärt (Footer-Hinweis + Public-Key reichen aus)
- API beim Dashboard-Aufruf: Live-State und Auto-Import beide bei jedem Pageload (ein gemeinsamer `/v2/sessions`-Call)
