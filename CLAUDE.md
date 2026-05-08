# Wallbox – Ladekosten-Tracker

## Projekt
Django-Anwendung zur Erfassung von Ladevorgängen (Heim-Wallbox + öffentliche Ladesäulen)
und Erstellung monatlicher PDF-Abrechnungen für den Arbeitgeber. Persönliches Projekt
für einen Audi Q6 e-tron als Dienstwagen.

## Tech-Stack
- Python 3.11, Django 5.x
- SQLite für Entwicklung (Produktion eventuell PostgreSQL)
- WeasyPrint für PDF-Generierung
- Django Admin als initiales Backend, später ggf. eigenes Frontend

## Entwicklungsumgebung
- LXC-Container "wallbox" auf Proxmox (Debian 12)
- Code unter `~/projects/wallbox`
- venv unter `.venv/`
- Zugriff via VS Code Remote SSH

## Konventionen
- Code-Kommentare und Variablennamen auf Englisch
- UI-Texte und Templates auf Deutsch
- Datumsformat: ISO 8601 intern, dd.MM.yyyy in der UI
- Geldbeträge: Decimal, niemals Float
- Strommengen in kWh mit 3 Nachkommastellen

## Wichtige Befehle
```bash
source .venv/bin/activate
python manage.py runserver 0.0.0.0:8000   # erreichbar im LAN
python manage.py makemigrations
python manage.py migrate
python manage.py test
```

## Datenmodell (initial)
- `Provider` – Ladestrom-Anbieter (Heim, EnBW, Maingau, Ionity, …)
- `ChargingSession` – einzelner Ladevorgang (Datum, Provider, kWh, Kosten, Ort, Notiz, dienstlich/privat)
- `MonthlyReport` – generiertes PDF pro Monat

## Geplante Features
1. Manuelle Erfassung von Ladevorgängen via Django Admin
2. CSV-Import aus Anbieter-Apps (EnBW Mobility+, Maingau, …)
3. Heim-Wallbox-Strompreis konfigurierbar in den Settings
4. Monatliche PDF-Abrechnung im professionellen Format zur Einreichung beim Arbeitgeber
5. Übersicht/Filter über Web-UI

## Was Claude tun soll
- Bei neuen Features erst Test schreiben, dann Implementierung (TDD-Stil)
- Migrationen niemals editieren, immer neue erzeugen
- Nach jeder Paket-Installation `pip freeze > requirements.txt`
- Vor Commits `python manage.py check` und `python manage.py test` laufen lassen
- Bei Unklarheit lieber kurz nachfragen als raten

## Was Claude NICHT tun soll
- Keine destruktiven DB-Operationen ohne Rückfrage (drop, flush, reset_db)
- Keine Secrets/API-Keys in den Code – immer über `django-environ` aus `.env`
- Nicht ungefragt neue Pakete installieren