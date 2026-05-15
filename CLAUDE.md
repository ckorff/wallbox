# Wallbox – Charging Cost Tracker

## Project
Personal Django application that automatically captures charging sessions
from a home KEBA P30 x wallbox and produces a monthly cost report sent
to the user's employer. Vehicle: Audi Q6 e-tron (company car).

The employer is international: **all UI, PDF reports and emails are in
English**. Internal notes and this file may stay in English for consistency.

## Companion docs

- `docs/ROADMAP.md` – upcoming phases and their concrete deliverables
- `docs/keba_api.md` – reference for the KEBA KeMove REST API on port 8443

Both should be read at the start of any task that touches their respective scope.

## Phase status

### Phase 1: complete ✅
Charging sessions are reliably imported via the HTTP CSV scrape into the
`ChargingSession` table (`python manage.py keba_import`). Raw session
capture is a stable substrate.

### Phase 2: complete ✅
Tariff history, monthly cost calculation, PDF generation, reports page
and `generate_report` CLI all in place.

### Phase 2.5: current 🚧 – drop base fee accounting
Removing the pro-rated base fee mechanism entirely:
- The grid base fee is a **sunk cost** the user pays regardless of
  charging at home, so charging it back to the employer would not
  reflect actual marginal cost.
- Reading the household meter exactly at month boundaries is impractical
  and forces interpolation, which would distort results in non-linear
  consumption profiles (heating season, holidays, …).
- Reports now reflect only marginal cost: energy used at the wallbox
  multiplied by the energy price valid at session time.

Affected: `Tariff.base_fee_eur_per_month`, the entire `MonthlyHouseUsage`
feature, and the corresponding fields on `MonthlyReport`. PDF layout
and tariff settings UI simplify accordingly.

### Phase 3: later
Email delivery, scheduled imports/report generation, dashboards.
(The web UI itself already runs as a permanent systemd-managed Gunicorn
service — see Development Environment → Deployment.)

## Hardware Setup
- **Wallbox:** KEBA P30 x-series (LAN/WLAN, IP configured in app settings)
- **Vehicle:** Audi Q6 e-tron
- **Communication:** HTTP against the wallbox web UI (stdlib `urllib`, no
  extra library). The browser login flow is replayed:
  1. `GET /` to receive a `PHPSESSID` cookie + CSRF token from
     `<meta name="csrf-token">`
  2. `POST /ajax.php` with JSON `{username, password, csrftoken}`
  3. `GET /export.php?chargingsessions=&t=<ms>` returns the
     semicolon-separated session CSV
- **Why HTTP, not OCPP / UDP / Modbus:** OCPP and UDP `report 1xx` polling
  were both tried and discarded. The CSV export wins because TCP is
  reliable on the flaky Wi-Fi link, the wallbox itself persists session
  history (so backend downtime cannot lose data), and we don't need to
  detect transitions — we just diff the session list.
- **Caveat:** the web UI has no documented API; a KEBA firmware update may
  break the scrape. If `/export.php` ever returns HTML instead of CSV,
  `KebaAuthError` is raised and points at credentials, but a real
  endpoint or format change will need re-inspecting `/js/webui.js`.

## Tariff (as of May 2026)
- **Energy price:** 38.5 ct/kWh (or whatever is currently configured)
- Tariffs are stored historically with a `valid_from` date so reports
  remain reproducible across price changes; never edit a tariff in place,
  always create a new one.
- The grid base fee (e.g. 16.40 €/month) is **deliberately not modelled**;
  see Phase 2.5 above for the rationale.

## Tech Stack
- Python 3.11, Django 5.x
- SQLite (single-user app, nothing more is needed)
- KEBA integration: HTTP CSV scrape via stdlib `urllib` (no third-party HTTP client)
- WeasyPrint for PDF generation
- Web serving: Gunicorn (WSGI) behind no reverse proxy — LAN-only, no HTTPS.
  WhiteNoise serves static files directly from the Gunicorn process.
- Email delivery: Django's built-in email framework over SMTP (Phase 3)
- Scheduled tasks: systemd timer (Phase 3, no Celery, no cron-overkill)
- Django i18n: `LANGUAGE_CODE = 'en'`, `TIME_ZONE = 'Europe/Berlin'`

## Development Environment
- LXC container "wallbox" on Proxmox (Debian 12)
- Code at `~/projects/wallbox`
- venv at `.venv/`
- Access via VS Code Remote SSH

### Deployment
The web UI runs as a permanent systemd-managed Gunicorn service.
- **Service name:** `wallbox.service`
- **Unit file:** `/etc/systemd/system/wallbox.service`
- **Bind:** `0.0.0.0:8000`, 2 workers, 90 s timeout
- **Logs:** stdout/stderr to journald (`journalctl -u wallbox`)
- **Restart:** `sudo systemctl restart wallbox` after code/template changes;
  reload the unit itself with `sudo systemctl daemon-reload`
- After changing static files: `python manage.py collectstatic --noinput`,
  then restart the service.
- `python manage.py runserver` is still fine for ad-hoc dev — stop the
  service first (`sudo systemctl stop wallbox`) so port 8000 is free.

## Reporter and vehicle profile
The PDF needs identifying data that is not in the database. It is read
from `.env` via django-environ:
- `REPORTER_NAME`, `REPORTER_EMPLOYEE_ID`
- `VEHICLE_MAKE_MODEL`, `VEHICLE_LICENSE_PLATE`
- `CHARGING_LOCATION` (single-line string; embed punctuation directly)
A missing required value should fail loudly at startup, not silently
render a blank field in the PDF.

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
sudo systemctl restart wallbox                           # apply code/template changes (service is the runtime)
journalctl -u wallbox -f                                 # tail web-UI logs
python manage.py keba_import                             # fetch + ingest live wallbox CSV
python manage.py keba_import --file <path.csv>           # ingest a CSV downloaded by hand
python manage.py keba_import -v 2                        # verbose: per-stage + per-row outcomes
KEBA_DUMP_DIR=debug python manage.py keba_import         # tee the raw HTTP body to debug/ for inspection
python manage.py generate_report --year 2026 --month 5   # CLI alternative to the UI button
python manage.py collectstatic --noinput                 # after touching static files
python manage.py makemigrations
python manage.py migrate
python manage.py test
```

## Data Model

`ChargingSession` (Phase 1)
- `serial`, `started_at`, `ended_at`, `energy_kwh`, `raw_row` (full CSV row as JSON)
- Natural key: `(serial, started_at)`

`Tariff` (historical, never edited – new entries instead)
- `valid_from` (DateField, unique, indexed)
- `energy_price_ct_per_kwh` (Decimal 6,3) – e.g. `38.500`
- `created_at` (auto)
- Helper: `Tariff.for_date(d)` → most recent tariff with `valid_from <= d`

`MonthlyReport` (one row per generated PDF)
- `year`, `month` (unique together)
- `pdf` (FileField, stored under `media/reports/`)
- `generated_at` (DateTime, default `timezone.now`)
- `wallbox_kwh_total` (Decimal 10,3) – sum of session kWh in the month
- `energy_cost_eur` (Decimal 8,2) – Σ(kWh × tariff at session date)
- `total_amount_eur` (Decimal 8,2) – equal to `energy_cost_eur`; kept as
  its own field for forward compatibility (other surcharges may appear later)
- `tariff_used` (FK Tariff, null=True; only set when a single tariff
  covered the whole month, on_delete=PROTECT)
- Re-generating the report for a (year, month) **replaces** the existing
  row and PDF.

## Business rules

- **Session-to-month assignment:** strictly by `started_at` local time
  (Europe/Berlin). A session that starts 23:50 on May 31st belongs to
  May, regardless of when it ends.
- **Energy cost:** for each session, multiply `energy_kwh` by the
  `Tariff.for_date(session.started_at.date())`. Tariff changes inside a
  month must be handled correctly – do not assume one tariff per month.
- **Total amount:** `total_amount_eur = energy_cost_eur`. The base fee
  is intentionally not part of the calculation.

## UI (admin-only, `@staff_member_required`)

All pages share a base template `templates/base.html` with a top
navigation bar showing: Wallbox (logo/title, links to dashboard),
Tariff settings, Reports, Admin, Logout (right-aligned, with the
current username next to it). The active page's nav link is visually
highlighted.

1. **Dashboard** at `/dashboard/` (root `/` redirects here):
   - Status block (read-only): total number of charging sessions,
     timestamp of the most recently imported session, total energy
     captured (kWh), and the last generated `MonthlyReport`
     (year-month + generated_at) if any.
   - Action block:
     - "Run import now" – POST form that runs `keba_import`
       synchronously and redirects back with a success message
       (number of newly imported sessions) or error message.
     - "Open latest report" – link to the most recent `MonthlyReport`'s
       PDF if any, else disabled with a hint.
2. **Tariff settings** at `/settings/tariff/`
3. **Reports** at `/reports/`
4. Plain server-rendered Django templates. No SPA, no JS framework.
   Styling: Tailwind via CDN play-mode, no build pipeline, no other
   framework, no npm. Loaded in `templates/base.html` only.
   - Inter via fonts.googleapis.com (weights 400 / 500 only)
   - Tabler icons via the icon-font CDN, used inline as
     `<i class="ti ti-name"></i>` where it adds clarity

   Design tokens (do not invent variants — registered under the same
   names in the inline `tailwind.config` block in `base.html`):

   | Token            | Value                          |
   |------------------|--------------------------------|
   | bg               | `#0a1729`                      |
   | card             | `#142844`                      |
   | accent           | `#5beaff`                      |
   | accent-hover     | `#2dd4ff`                      |
   | text-primary     | `#e5f1fb`                      |
   | text-secondary   | `#7892ac`                      |
   | text-muted       | `#5b7a99`                      |
   | row-hover        | `#1a3252`                      |
   | card border      | `rgba(94,234,255,0.10)`         |
   | active border    | `#5beaff` + box-shadow `0 0 24px rgba(91,234,255,0.25)` (`shadow-glow`) |
   | radii            | 12 px cards (`rounded-card`) / 8 px buttons (`rounded-btn`) |
   | section label    | uppercase, `tracking-[0.14em]`, colour `text-text-muted` |
   | font             | Inter, weights 400 and 500 only |

   Active-card glow pattern: on each page the action the user is most
   likely to use gets `border-accent shadow-glow`; other cards stay on
   the neutral `border-[rgba(94,234,255,0.10)]`. On the dashboard this
   is "Run import now".

The "Run import now" button is intentionally synchronous for now – it
blocks for a few seconds while the wallbox is queried. Async/queued
imports are a Phase 3 concern.

## PDF layout (English, professional, A4 portrait)
- Header block:
  - Title (h1): "Charging Cost Report — <Month> <Year>"
  - First info row (split): Reporter on the left, Generated date on the right
  - Remaining info rows (single column): Employee ID, Vehicle,
    License plate, Charging location
- Date format: explicit English long form everywhere ("7 May 2026"). Never
  rely on system locale; always use Django's `|date:"j F Y"` filter.
- Sessions table: Date, Start, End, kWh, Tariff (ct/kWh), Line cost (€).
  Numeric columns right-aligned. Footer row with totals.
- Summary block:
  - Total energy charged at home: `xx.xxx kWh`
  - Total energy cost: `€ xx.xx`
  - **Grand total to be reimbursed:** `€ xx.xx` (bold, larger font, distinct row)
- Footer: "Generated automatically from the KEBA P30 wallbox session log."

## Out of scope right now (do not build)
- Email delivery → Phase 3
- Scheduled / automated report generation → Phase 3
- Per-session manual edits, marking sessions as private/business
- Multi-vehicle support
- Tax / THG-Quote features
- Any kind of base fee / fixed cost accounting (deliberately removed in 2.5)

## Open Questions / TODO
- **SMTP server:** Phase 3 (own server / IONOS / Mailgun / Gmail SMTP / …)
- **Recipient email:** Phase 3, will go into `.env`
- **Re-import of sessions after report generation:** if new sessions
  arrive for an already-reported month (rare, but possible if
  `keba_import` was behind), the user regenerates the report manually
  via the Reports page. Decide later whether to flag this in the UI.
- **Automation:** `keba_import` is run manually today. Once we know how
  often the wallbox CSV truncates, decide on a systemd timer cadence
  (likely daily) and add retry/backoff for the flaky-WLAN case.
- **Importer doesn't detect truncated HTTP responses:** observed on
  2026-05-14 — flaky WLAN delivered a short CSV body, `parse_sessions_csv`
  ingested it and two real sessions were silently lost. Hardening
  (e.g. a row-count floor, or "newest fetched row must not predate the
  newest DB row") is not in place yet. Until it is, use
  `KEBA_DUMP_DIR=debug python manage.py keba_import` and diff the dump
  against a manual UI export whenever sessions look missing.

## What Claude Should Do
- For new features: write the test first, then the implementation (TDD)
- Never edit migrations; always create new ones
- After every package install: `pip freeze > requirements.txt`
- Before commits: run `python manage.py check` and `python manage.py test`
- When in doubt, ask rather than guess
- Stay strictly within the current task's scope; no helpful extras

## What Claude Should NOT Do
- No destructive DB operations without confirmation (drop, flush, reset_db)
- No secrets, passwords or email addresses in code – always via `.env`
- Do not install new packages without asking
- Do not anticipate future tasks