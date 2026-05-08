# Wallbox – Charging Cost Tracker

## Project
Personal Django application that automatically captures charging sessions
from a home KEBA P30 x wallbox and produces a monthly cost report sent
to the user's employer. Vehicle: Audi Q6 e-tron (company car).

The employer is international: **all UI, PDF reports and emails are in
English**. Internal notes and this file may stay in English for consistency.

## Hardware Setup
- **Wallbox:** KEBA P30 x-series (LAN/WLAN, IP configured in app settings)
- **Vehicle:** Audi Q6 e-tron
- **Communication:** Modbus TCP via Python (`pymodbus`)
  - Rationale: the wallbox is on WLAN with ~600 ms latency. UDP
    (KEBA's KeContact protocol) loses packets at that latency without
    retransmission; TCP guarantees delivery.
  - Modbus TCP must be enabled in the KEBA firmware (Setup menu).

## Tariff (as of May 2026)
- **Energy price:** 38.5 ct/kWh
- **Base fee:** 16.40 €/month
- **Pro-rated base fee** = (wallbox kWh ÷ total household kWh) × base fee
- **Household consumption** is entered manually each month (read from the meter).
- Tariffs are stored in a DB model with a `valid_from` date, so price
  changes do not require code changes.

## Tech Stack
- Python 3.11, Django 5.x
- SQLite (single-user app, nothing more is needed)
- WeasyPrint for PDF generation
- KEBA integration: Modbus TCP via `pymodbus` (TCP, not UDP, due to WLAN latency)
- Email delivery: Django's built-in email framework over SMTP (config from `.env`)
- Scheduled tasks: systemd timer (no Celery, no cron-overkill)
- Django i18n: `LANGUAGE_CODE = 'en'`, `TIME_ZONE = 'Europe/Berlin'`

## Development Environment
- LXC container "wallbox" on Proxmox (Debian 12)
- Code at `~/projects/wallbox`
- venv at `.venv/`
- Access via VS Code Remote SSH

## Conventions
- Code, comments, identifiers, UI texts, templates and PDF/email exports: **English**
- Date/time format: ISO 8601 internally; "8 May 2026" or "2026-05" in UI/PDF
- Time zone: Europe/Berlin (sessions stored in local time for clean monthly cuts)
- Currency: EUR, formatted as "€ 12.34" or "12.34 EUR"
- Money: `Decimal`, never `float`
- Energy in kWh with 3 decimal places
- Configuration from `.env` via django-environ; nothing hard-coded

## Useful Commands
```bash
source .venv/bin/activate
python manage.py runserver 0.0.0.0:8000   # reachable on LAN
python manage.py makemigrations
python manage.py migrate
python manage.py test
```

## Initial Data Model (to be refined with Claude Code)
- `Tariff` – energy price (ct/kWh), base fee (€/month), `valid_from`
- `ChargingSession` – start, end, kWh, meter_start, meter_end, note
- `MonthlyHouseUsage` – year, month, household kWh (manual entry)
- `MonthlyReport` – year, month, PDF path, send status, send date

## Planned Features
1. **Automatic capture** of charging sessions from the KEBA P30 x via Modbus TCP
2. **Tariff management** with historical validity
3. **Manual entry** of monthly household consumption
4. **PDF report** in a professional, English-language layout containing:
   - List of all charging sessions of the month (date, start/end, kWh)
   - Total kWh and energy cost
   - Pro-rated base fee with the calculation shown transparently
   - Grand total to be reimbursed
5. **Automatic email delivery** of the monthly report to the work email
6. **Web UI** for overview, manual corrections, tariff maintenance

## Open Questions / TODO
- **SMTP server:** to be decided (own server / IONOS / Mailgun / Gmail SMTP / …)
- **Recipient email:** work email address goes into `.env`
- **KEBA IP:** stored in app settings once the integration begins
- **KEBA register map:** find the registers for charged energy per session,
  total energy, session ID and charging state in the official KEBA Modbus docs
- **Behaviour when household consumption is missing:** pause billing or
  generate the report without the base fee? – decide when implementing reports
- **Modbus TCP** must be enabled in the KEBA firmware (one-time hardware setup)

## What Claude Should Do
- For new features: write the test first, then the implementation (TDD)
- Never edit migrations; always create new ones
- After every package install: `pip freeze > requirements.txt`
- Before commits: run `python manage.py check` and `python manage.py test`
- When in doubt, ask rather than guess

## What Claude Should NOT Do
- No destructive DB operations without confirmation (drop, flush, reset_db)
- No secrets, passwords or email addresses in code – always via `.env`
- Do not install new packages without asking