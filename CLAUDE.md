# Wallbox – Charging Cost Tracker

## Project
Personal Django application that automatically captures charging sessions
from a home KEBA P30 x wallbox and produces a monthly cost report sent
to the user's employer. Vehicle: Audi Q6 e-tron (company car).

The employer is international: **all UI, PDF reports and emails are in
English**. Internal notes and this file may stay in English for consistency.

## Phase status

### Phase 1: complete ✅
Charging sessions are reliably imported via the HTTP CSV scrape into the
`ChargingSession` table (`python manage.py keba_import`). From now on,
raw session capture is considered a stable substrate to build on.

### Phase 2: current 🚧
**Turn raw sessions into monthly cost calculations and generate
downloadable PDF reports.** Email delivery, scheduled automation and
any kind of dashboarding belong to Phase 3 and are explicitly NOT part
of this phase. When in doubt, do less, not more.

To keep Claude Code on the rails, Phase 2 is split into four sequentially
delivered tasks. Each is a self-contained Claude Code session and ends
with a green test suite plus a single git commit. Run `/clear` between
tasks so context does not bleed across.

- **Task 2.1** – `Tariff` model with history + tariff settings page
- **Task 2.2** – `MonthlyHouseUsage` model + house-usage page
- **Task 2.3** – Report calculation logic (pure Python, no PDF, no UI)
- **Task 2.4** – PDF generation + reports page (WeasyPrint)

### Phase 3: later
Email delivery, scheduled imports/report generation, dashboards.

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
- **Energy price:** 38.5 ct/kWh
- **Base fee:** 16.40 €/month
- **Pro-rated base fee** = (wallbox kWh ÷ total household kWh) × base fee
- **Household consumption** is entered manually each month (read from the meter).
- Tariffs are stored historically with a `valid_from` date so reports
  remain reproducible across price changes; never edit a tariff in place,
  always create a new one.

## Tech Stack
- Python 3.11, Django 5.x
- SQLite (single-user app, nothing more is needed)
- KEBA integration: HTTP CSV scrape via stdlib `urllib` (no third-party HTTP client)
- WeasyPrint for PDF generation (added in Phase 2.4)
- Email delivery: Django's built-in email framework over SMTP (Phase 3)
- Scheduled tasks: systemd timer (Phase 3, no Celery, no cron-overkill)
- Django i18n: `LANGUAGE_CODE = 'en'`, `TIME_ZONE = 'Europe/Berlin'`

## Development Environment
- LXC container "wallbox" on Proxmox (Debian 12)
- Code at `~/projects/wallbox`
- venv at `.venv/`
- Access via VS Code Remote SSH

## Conventions
- Code, comments, identifiers, UI texts, templates and PDF/email exports: **English**
- Date/time format: ISO 8601 internally; DE numeric ("01.05.2026") or "2026-05" in UI/PDF
- Time zone: Europe/Berlin (sessions stored in local time for clean monthly cuts)
- Currency: EUR, formatted as "€ 12.34" or "12.34 EUR"
- Money: `Decimal`, never `float`
- Energy in kWh with 3 decimal places
- Configuration from `.env` via django-environ; nothing hard-coded

## Useful Commands
```bash
source .venv/bin/activate
python manage.py runserver 0.0.0.0:8000                  # reachable on LAN
python manage.py keba_import                             # fetch + ingest live wallbox CSV
python manage.py keba_import --file <path.csv>           # ingest a CSV downloaded by hand
python manage.py generate_report --year 2026 --month 5   # CLI alternative to the UI button (added in Phase 2.4)
python manage.py makemigrations
python manage.py migrate
python manage.py test
```

## Data Model

**Implemented (Phase 1):**
- `ChargingSession` – `serial`, `started_at`, `ended_at`, `energy_kwh`,
  `raw_row` (full CSV row as JSON). Natural key: `(serial, started_at)`.

**To implement in Phase 2:**

`Tariff` (historical, never edited – new entries instead)
- `valid_from` (DateField, unique, indexed)
- `energy_price_ct_per_kwh` (Decimal 6,3) – e.g. `38.500`
- `base_fee_eur_per_month`  (Decimal 8,2) – e.g. `16.40`
- `created_at` (auto)
- Helper: `Tariff.for_date(d)` → most recent tariff with `valid_from <= d`

`MonthlyHouseUsage` (one row per calendar month)
- `year` (PositiveSmallInteger), `month` (1–12)
- Unique together on `(year, month)`
- `meter_start_kwh` (Decimal 10,3, nullable)
- `meter_end_kwh`   (Decimal 10,3, nullable)
- `kwh_total`       (Decimal 10,3, nullable)
- Validation:
  - At least one of {`meter_end - meter_start`, `kwh_total`} must resolve to a value.
  - If both are provided, they must agree to within 0.001 kWh, else `ValidationError`.
  - Property `effective_kwh` returns the resolved value.

`MonthlyReport` (one row per generated PDF)
- `year`, `month` (unique together)
- `pdf` (FileField, stored under `media/reports/`)
- `generated_at` (DateTime)
- `wallbox_kwh_total` (Decimal 10,3) – sum of session kWh in the month
- `energy_cost_eur` (Decimal 8,2) – Σ(kWh × tariff at session date)
- `house_kwh_total` (Decimal 10,3, nullable) – snapshot at generation
- `prorated_base_fee_eur` (Decimal 8,2) – `0.00` if `house_kwh_total` is null
- `total_amount_eur` (Decimal 8,2)
- `tariff_used` (ForeignKey, nullable; only set when a single tariff covered the whole month)
- `warning_house_usage_missing` (Boolean)
- Re-generating the report for a (year, month) **replaces** the existing row and PDF.

## Phase 2 business rules

- **Session-to-month assignment:** strictly by `started_at` local time
  (Europe/Berlin). A session that starts 23:50 on May 31st belongs to
  May, regardless of when it ends. Documented and tested.
- **Energy cost:** for each session, multiply `energy_kwh` by the
  `Tariff.for_date(session.started_at.date())`. Tariff changes inside a
  month must be handled correctly – do not assume one tariff per month.
- **Pro-rated base fee:**
  `(wallbox_kwh_total / house_kwh_total) × tariff.base_fee_eur_per_month`
  using the tariff valid on the **first day of the report month**.
  Round to 2 decimals at the very end, never on intermediate values.
- **Missing house usage:** generate the report anyway; set
  `prorated_base_fee_eur = 0.00`, `warning_house_usage_missing = True`,
  and render a clearly visible warning box at the top of the PDF reading
  approximately "Household consumption for <month> <year> not yet
  recorded; base fee not included in this report."

## Phase 2 UI (admin-only, `@staff_member_required`)

1. **Tariff settings** at `/settings/tariff/` – form to add a new tariff
   (`valid_from`, prices). Below: list of historical tariffs with the
   currently active one highlighted. No edit/delete of existing tariffs.
2. **House usage** at `/house-usage/` – list of months with recorded
   usage, plus a form to add/edit usage for a given (year, month).
3. **Reports** at `/reports/` – list of months sorted desc, with per-row
   state ("not generated" / "generated on …"), a "Generate" button per
   month and a "Download PDF" link if available.
4. Plain server-rendered Django templates. No SPA, no JS framework. A
   small amount of inline CSS is fine; no Tailwind / Bootstrap unless
   it pays for itself in time saved.

## PDF layout (English, professional, A4 portrait)

- Header block: report title, month + year, generation date, vehicle
  ("Audi Q6 e-tron"), reporting period (`YYYY-MM-01` to last of month).
- Optional warning box (only if house usage missing).
- Table of all charging sessions: date, start, end, kWh, applicable
  tariff (ct/kWh), line cost.
- Summary block:
  - Total energy charged at home: `xx.xxx kWh`
  - Total energy cost: `€ xx.xx`
  - Total household consumption: `xx.xxx kWh` (or "not recorded")
  - Pro-rated base fee calculation, shown as a literal formula:
    `(wallbox_kwh / house_kwh) × base_fee = € xx.xx`
  - **Grand total to be reimbursed:** `€ xx.xx` (bold, larger font)
- Footer with a one-line disclaimer: "Generated automatically from the
  KEBA P30 wallbox session log."

## Out of scope for Phase 2 (do not build)
- Email delivery → Phase 3
- Scheduled / automated report generation → Phase 3
- Per-session manual edits, marking sessions as private/business, etc.
- Multi-vehicle support
- Tax / THG-Quote features

## Open Questions / TODO
- **SMTP server:** to be decided in Phase 3 (own server / IONOS / Mailgun / Gmail SMTP / …)
- **Recipient email:** Phase 3, will go into `.env`
- **Re-import of sessions after report generation:** if new sessions
  arrive for an already-reported month (rare, but possible if
  `keba_import` was behind), the user regenerates the report manually
  via the Reports page. Decide later whether to flag this in the UI.
- **Tariff validation:** prevent creating a tariff with `valid_from`
  in the future-after-existing-reports? Decide if this becomes a real issue.
- **Automation:** `keba_import` is run manually today. Once we know how
  often the wallbox CSV truncates, decide on a systemd timer cadence
  (likely daily) and add retry/backoff for the flaky-WLAN case.

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
- Do not anticipate future tasks (e.g. do not create the `MonthlyReport`
  model when the current task is `Tariff`)