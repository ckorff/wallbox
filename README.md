# Wallbox — KEBA P30 charging cost tracker

A small Django app that pulls charging-session data from a home
[KEBA P30 x-series][keba] wallbox over its documented REST API,
calculates the monthly cost at the user's current electricity tariff,
renders a PDF report and emails it to a fixed recipient (e.g. an
employer that reimburses charging an EV company car at home).

> **Status:** personal hobby project. Single-user, LAN-only,
> intentionally not hardened for the public internet. Published in
> case the KEBA REST integration is useful to someone else.

## What it does

- **Imports sessions automatically** every time the dashboard loads —
  compares the wallbox's session count to the database and ingests any
  new rows (`charging/services/auto_import.py`).
- **Stores tariff history** so reports stay reproducible across price
  changes; per-session cost uses the tariff valid on the session's
  start date.
- **Renders a monthly PDF** (WeasyPrint) with the sessions, totals and
  an Eichrecht-compliant footer (wallbox serial + MVA public-key
  fingerprint).
- **Emails the PDF** via Django's SMTP backend to the address stored in
  the app's settings.

The wallbox's MVA-signed records (Eichrecht / German calibration law)
are stored verbatim per session, so the raw signed data is available
on request even though the app itself doesn't verify signatures.

## Hardware target

- **Wallbox:** KEBA KeContact P30 x-series with REST API on port 8443
  (firmware that exposes `/v2/sessions`, `/v2/wallboxes/{serial}`, etc.)
- **Vehicle:** Audi Q6 e-tron (just the test rig — the app is
  manufacturer-agnostic, it only talks to the wallbox)

KEBA's REST API isn't part of the wallbox's public documentation; the
relevant endpoints, auth flow and quirks are written up in
[`docs/keba_api.md`](docs/keba_api.md).

## Tech stack

- Python 3.11, Django 5.x, SQLite
- WeasyPrint for PDF rendering
- Gunicorn + WhiteNoise (no reverse proxy, LAN-only)
- Plain server-rendered templates, Tailwind via CDN play-mode (no
  build pipeline)
- Stdlib `urllib` for the wallbox HTTPS client — no third-party HTTP
  library

## Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # fill in REPORTER_*, VEHICLE_*, CHARGING_LOCATION
                           # and (optionally) SMTP credentials
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

The wallbox API URL and credentials go on the `/settings/` page after
first login (encrypted at rest with a Fernet key derived from
`SECRET_KEY`). Add at least one tariff there too — without one, the
report generator refuses to produce a PDF.

See [`CLAUDE.md`](CLAUDE.md) for the architectural reasoning,
conventions and the rationale behind each phase.

## Project history

Built incrementally in phases (see `docs/ROADMAP.md` for the original
plan and `CLAUDE.md` for what actually landed):

1. **Phase 1** — capture sessions via the wallbox CSV export
2. **Phase 2** — tariff history, cost calculation, PDF reports, web UI
3. **Phase 2.5** — drop pro-rated base-fee accounting (it distorts
   marginal cost; only energy at the wallbox is billed back)
4. **Phase 2.6** — replace the CSV-scrape with the documented REST API
5. **Phase 2.7** — Eichrecht-compliant MVA records in DB + PDF footer
6. **Phase 2.8** — consolidated `/settings/` hub, encrypted API creds
7. **Phase 2.9** — dashboard live state + monthly summary
8. **Phase 3** — SMTP email delivery + dashboard-driven auto-import
   (instead of a systemd timer)

## License

MIT. See [LICENSE](LICENSE).

[keba]: https://www.keba.com/en/emobility/products/x-series/x-series
