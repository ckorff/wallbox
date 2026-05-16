# Reworking the Wallbox App – Roadmap

## Workflow

- Möglichst viele Tasks durch Claude Code erledigen lassen
- Schritte gut erklären, da es sich auch um ein Lernprojekt handelt
- Pro Phase: erst Plan Mode in Claude Code, Plan reviewen, dann Implementierung
- Pro abgeschlossener Phase ein Git-Tag (`v0.2-csv-scrape`, `v0.3-rest-api`, …) als Rollback-Anker
- CLAUDE.md am Ende jeder Phase mit aktualisieren (Phase-Status, Tech-Stack, Konventionen)

## Reihenfolge

Die Phasen bauen aufeinander auf. Empfohlene Reihenfolge:

1. **Phase 2.6** – Migration CSV-Scrape → REST-API
2. **Phase 2.7** – Eichrechtskonformität im PDF
3. **Phase 2.8** – Settings-Seite konsolidieren
4. **Phase 2.9** – Dashboard: Live-State und Monatsübersicht
5. **Phase 3** – Email-Versand + Scheduled Imports
6. **Phase 4** (optional) – Reports-Vollständigkeitsanzeige, Custom-Range-Reports

---

## Phase 2.6: Migration CSV-Scrape → REST-API

**Ziel:** Den bestehenden PHP-Scrape durch die offizielle KeMove REST-API auf Port 8443 ersetzen.

### Vorbereitung

- Git-Tag setzen: `git tag -a v0.2-csv-scrape -m "Last version before REST API migration"`
- DB-Snapshot: `cp db.sqlite3 db.sqlite3.pre-api`
- Neue Datei `docs/keba_api.md` mit den Endpunkten, die wir nutzen, dem Auth-Flow (JWT mit accessToken 15 min, refreshToken 30 Tage) und den TLS-Hinweisen (self-signed Cert). Die vollständige Swagger-UI bleibt für Detail-Recherche auf der Wallbox unter `https://192.168.0.10:8443/docs` erreichbar.

### Konfiguration

- `.env` erweitert um: `KEBA_API_URL=https://192.168.0.10:8443`, `KEBA_API_VERIFY_TLS=false`
- `KEBA_API_USERNAME` und `KEBA_API_PASSWORD` zunächst auch in `.env`; in Phase 2.8 verlagern wir sie in die Settings-UI

### Implementierung

- Neue `KebaApiClient`-Klasse (analog zum bestehenden Scrape-Code):
  - `login()` → POST `/v2/jwt/login`, speichert accessToken + refreshToken
  - `refresh()` → POST `/v2/jwt/refresh`
  - `export_sessions_csv()` → GET `/v2/sessions/export`, gibt CSV als `bytes` zurück
  - `get_state()` → GET `/v2/wallboxes/{serial}/state`
  - `get_wallbox_info()` → GET `/v2/wallboxes/{serial}` (für DIP-Stellung, Firmware, MAC, MeterValue, MVA-Public-Key)
- Token-Cache in `media/.keba_token.json` (chmod 0600). Bei Start einlesen, bei HTTP 401 einmal refreshen, bei Refresh-Fehler erneuter Login
- `keba_import` Management Command schaltet auf den neuen Client um
- `parse_sessions_csv` bleibt **unverändert** – das CSV der API ist byte-identisch zum bisherigen Scrape (verifiziert am 15.05.2026)
- Alter PHP-Scrape-Code wandert in eine `_legacy_keba_scrape.py` als Backup, falls die API mal ausfällt

### Tests

- Login-Mock, Refresh-Mock, Expired-Token-Mock (401 → Refresh → Retry-Erfolg)
- Refresh-Expiry-Pfad: 401 auf Refresh → erneuter Login mit Credentials
- Export-Response gegen das echte CSV-Snippet vom 14.05.2026 parsen
- Truncated-Response-Detektion: HTTP-Content-Length-Check schließt den alten TODO-Punkt

### Dashboard-Anbindung

- Der „Run import now"-Button bleibt, ruft den neuen Client
- Feedback-Format: „N new sessions imported, M skipped (already known), total now X" – schlanker als der `-v 2`-Debug, substanzieller als nur „done"

### Nach der Migration

- TODO „Importer doesn't detect truncated HTTP responses" in CLAUDE.md streichen
- Hardware-Setup-Block in CLAUDE.md neu schreiben: KEBA REST API statt PHP-Scrape, mit Verweis auf `docs/keba_api.md`

---

## Phase 2.7: Eichrechtskonformität im PDF

**Ziel:** Die MVA-signierten Records, die die Wallbox produziert, für den Arbeitgeber sichtbar und nachprüfbar machen.

### Datenmodell-Erweiterung

- `ChargingSession` bekommt zwei neue Felder:
  - `mva_record_data` (TextField, nullable) – das `mvaRecordData`-JSON pro Session
  - `mva_record_signature` (TextField, nullable) – das `mvaRecordSignature`-JSON
- Beide werden beim Import aus `/v2/sessions` gefüllt (sind dort schon enthalten)
- Migration nullable, weil ältere Sessions vor der API-Migration keine MVA-Records haben

### Public Key archivieren

- Beim ersten erfolgreichen API-Login (oder beim ersten `get_wallbox_info()`): den `mvaPublicKey` aus `/v2/wallboxes/{serial}` lesen
- Speichern in `media/wallbox_mva_public_key.json` (Wallbox-Serial + Public-Key-Hex)
- SHA-256-Fingerprint vom Public-Key-Hex berechnen und in den Settings anzeigen, damit jederzeit nachprüfbar

### PDF-Anpassungen

- Footer-Block ergänzen:

  > Charging session data is signed by the KEBA KeContact P30 wallbox (MVA, Eichrecht-compliant).  
  > Wallbox serial: `00000000`. Public key fingerprint: `<SHA-256>`.  
  > Original signed records available on request.

- Pro Zeile in der Sessions-Tabelle ein kleines „✓"-Symbol, wenn ein `mva_record_signature` vorhanden ist
- Die Public-Key-Datei kann als Anhang mitgeschickt werden (siehe Phase 3, Email-Versand) oder als Hinweis im Footer „verfügbar auf Anfrage" stehen bleiben

### Out of Scope für diese Phase

- ECDSA-Signatur-Verifikation in der App selbst (nicht von HR gefordert; strukturell vorbereitet durch die Rohdaten in der DB, falls später benötigt)

---

## Phase 2.8: Settings-Seite konsolidieren

**Ziel:** Tariff-Settings zu einer allgemeinen Settings-Seite erweitern. API-Credentials und Empfänger-Adresse dort verwalten.

### UI

- Eine Seite `/settings/` mit Abschnitten:
  - **Tariff** – wie heute, Tabelle der historischen Tarife plus „Add new tariff"-Formular
  - **Wallbox API** – Username und Passwort. Host/URL bleibt in `.env` (Infrastruktur, nicht Geschäftslogik)
  - **Report recipient** – Email-Adresse für den Versand (wird in Phase 3 wirksam)
  - **Eichrecht info** (read-only) – Wallbox-Serial, Firmware-Version, Public-Key-Fingerprint, „Last DIP read"-Zeitstempel

### Datenmodell

- Neues Singleton-Modell `AppSettings`:
  - `keba_api_username` (CharField)
  - `keba_api_password` (encrypted CharField – Django-Fernet, kein Klartext in der DB)
  - `report_recipient_email` (EmailField, leer erlaubt)
- Helper `AppSettings.current()` mit `get_or_create(pk=1)` garantiert genau eine Instanz

### Migration

- Beim ersten Aufruf der neuen Settings-Seite: bestehende `.env`-Werte als Defaults vorschlagen, sanfter Übergang
- `.env`-Fallback bleibt für CLI-Runs ohne UI-Setup; DB-Werte gewinnen, sobald gesetzt

---

## Phase 2.9: Dashboard – Live-State und Monatsübersicht

**Ziel:** Dashboard zeigt aktuelle Wallbox-Aktivität und kompakte Monatsstatistik.

### Live-State (API-Call pro Pageload)

- Beim Dashboard-Render: `/v2/wallboxes/{serial}/state` (~50 ms)
- Anzeige:
  - State (`IDLE`, `CHARGING`, `ERROR`, …)
  - Bei `CHARGING`: aktuelle Leistung (aus `/v2/wallboxes/{serial}`), gestartet vor X Minuten
  - Bei `ERROR`: errorCode + kurzer Hinweis
- Fehler-Fallback: „Wallbox unreachable – showing last known status" mit Zeitstempel des letzten Erfolgs
- Bewusste Entscheidung: Full-Session-Sync läuft **nicht** bei jedem Pageload (Phase 3 deckt das per systemd-Timer ab)

### Monatsübersicht (aus DB, kein API-Call)

- Aktueller Monat:
  - Anzahl Sessions
  - Total kWh
  - Aufgelaufene Kosten (mit aktuell gültigem Tarif berechnet)
- Vormonatsvergleich als kleiner Trend-Indikator

### Navigation

- Django-Admin bleibt drin, aber als „Raw data" in der Navigation umetikettiert (keine eigene Komplett-Ersatz-UI, das wäre Wochen Arbeit für minimalen Gewinn)
- Reports-Seite bekommt einen Inline-PDF-Viewer (iframe auf das aktuellste PDF, oder PDF.js)
- Settings-Link in die Hauptnavigation aufnehmen

---

## Phase 3: Email-Versand + Scheduled Imports

### Email

- Empfänger aus `AppSettings.report_recipient_email`
- SMTP-Config in `.env` (Host, Port, User, Password – Secrets bleiben aus der DB raus)
- „Send by email"-Button auf der Reports-Seite, hängt das PDF an
- Email-Template mit Standardtext, Subject „Charging report – <Month> <Year>"
- Optional via Settings-Toggle: bei automatischer Report-Generierung gleich mitversenden

### Scheduled Imports

- systemd-Timer alle 15–30 min (kein Celery, kein Cron)
- Unit-Pärchen `wallbox-import.service` + `wallbox-import.timer`, ruft `python manage.py keba_import`
- Retry/Backoff bei API-Fehlern: 3 Versuche mit exponentiellem Backoff
- Ergebnisse in journald (über systemd), in der UI eine „Last import"-Zeile mit Zeitstempel und Ergebnis

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
- API beim Dashboard-Aufruf: Live-State bei jedem Pageload (billig), Full-Session-Sync per systemd-Timer (Phase 3)
