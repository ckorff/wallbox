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
Charging sessions are reliably imported via `python manage.py keba_import`
into the `ChargingSession` table. Raw session capture is a stable
substrate.

### Phase 2: complete ✅
Tariff history, monthly cost calculation, PDF generation, reports page
and `generate_report` CLI all in place.

### Phase 2.5: complete ✅ – dropped base-fee accounting
Removed the pro-rated base fee mechanism entirely:
- The grid base fee is a **sunk cost** the user pays regardless of
  charging at home, so charging it back to the employer would not
  reflect actual marginal cost.
- Reading the household meter exactly at month boundaries is impractical
  and forces interpolation, which would distort results in non-linear
  consumption profiles (heating season, holidays, …).
- Reports now reflect only marginal cost: energy used at the wallbox
  multiplied by the energy price valid at session time.

Removed in migration `0006`: `Tariff.base_fee_eur_per_month`, the
`MonthlyHouseUsage` model, and the prorated-base-fee fields on
`MonthlyReport`.

### Phase 2.6: complete ✅ – REST API migration
The legacy PHP-scrape against the wallbox web UI was replaced by the
documented KeMove REST API on `:8443`. Client at
`charging/keba_api.py`, full reference in `docs/keba_api.md`. The
dormant scrape backup was removed during the Phase 3 cleanup pass —
recover from git history if the REST API ever needs replacing again.

### Phase 2.7: complete ✅ – Eichrechtskonformität im PDF
MVA-signed records (`mva_record_data`, `mva_record_signature`) flow
from `/v2/sessions` into the DB via `ingest_json_row`. The wallbox's
MVA public key is archived once at `media/wallbox_mva_public_key.json`;
its SHA-256 fingerprint and the wallbox serial appear in the monthly
PDF footer, with a ✓ marker in a new "Signed" column for every signed
session. ECDSA verification in-app stays out of scope — the raw
signature data is preserved in the DB if HR ever asks.

### Phase 2.8: complete ✅ – consolidated settings UI
Tariff settings expanded into a `/settings/` hub with four sections:
Tariff, Wallbox API (encrypted-at-rest credentials), Report recipient
(consumed by Phase 3), and a read-only Eichrecht info block (serial,
public-key fingerprint, plus live-fetched firmware version). API
credentials live in the `AppSettings` singleton;
`charging/services/keba_client.py` picks DB-over-`.env`, with `.env`
retained as fallback for CLI runs that never touched the UI.

### Phase 2.9: complete ✅ – dashboard live state + monthly summary
Dashboard now shows live wallbox state (`IDLE` / `CHARGING` / `ERROR`)
fetched per pageload via `/v2/wallboxes/{serial}/state`, with a compact
current-month summary (sessions, kWh, accrued cost) and a vs.-previous-month
trend on energy. Unreachable wallbox falls back to a last-known cache at
`media/.wallbox_state.json`. Reports page gained an inline PDF viewer
(iframe on the most recent report). Admin nav link rebranded to
"Raw data". Shared per-session × `Tariff.for_date` cost helper
(`session_energy_cost_eur` in `charging/services/reports.py`) is reused
by both the monthly-report calculation and the dashboard summary.

### Phase 3: complete ✅ – email delivery + dashboard-driven auto-import
Reports page gained a per-row "Send by email" button that attaches the
generated PDF and sends it to `AppSettings.report_recipient_email` via
SMTP. The dispatcher lives in `charging/services/email.py`; SMTP
transport (`EMAIL_HOST`/`EMAIL_PORT`/`EMAIL_HOST_USER`/`EMAIL_HOST_PASSWORD`/
`EMAIL_USE_TLS`/`DEFAULT_FROM_EMAIL`) stays in `.env` — secrets do not
land in the DB. With `EMAIL_HOST` blank, the backend falls back to
Django's console backend so unit tests and unconfigured dev runs are
safe; `send_report_email` still refuses to dispatch in that state.

Scheduled imports were replaced by a dashboard-driven check
(`charging/services/auto_import.py`): every dashboard pageload fetches
`/v2/sessions` once, counts *billable* rows (0-kWh swipes are filtered
to match the ingest path), and if the wallbox has more than the DB has
it ingests the new rows from the same response — one network call, not
two. Wallbox/auth errors are returned in the outcome rather than
propagated, so the dashboard always renders. Newly imported rows
surface as a one-time Django success flash; no-ops are silent. The
manual "Run import now" button remains for forced re-imports.

### Phase 3.1: complete ✅ – tariff document attachment
Reports emailed from the Reports page now carry the energy-supplier
tariff PDF as cost evidence. Uploaded via `/settings/#tariff` (new
"Tariff document" sub-section under the existing tariff price area),
stored as a `TariffDocument` row with the same `valid_from` history
shape as `Tariff`. Selection at dispatch resolves
`TariffDocument.for_date` against the **last day of the report
month**. Merge is at *send time* in
`charging/services/pdf_merge.py` via `pypdf` — the stored
`media/reports/*.pdf` is untouched; the attachment is built fresh
in memory (report pages first, then tariff) under the unchanged
filename `charging-report-YYYY-MM.pdf`. Missing-document flow is
non-blocking: the report is sent on its own and the UI flashes
"No tariff document on file — report sent without attachment."
`SentEmail` gained a `tariff_attached: bool` so the Reports view
can pick the right success message.

### Later
Phase 4 — see `docs/ROADMAP.md` for sequencing and content.
The web UI itself already runs as a permanent systemd-managed Gunicorn
service (see Development Environment → Deployment).

## Hardware Setup
- **Wallbox:** KEBA P30 x-series (LAN/WLAN, IP configured in `.env`)
- **Vehicle:** Audi Q6 e-tron
- **Communication:** documented KeMove REST API on port `:8443`,
  HTTPS with a self-signed cert. The client is `charging/keba_api.py`;
  JWT auth, token cache under `media/`. Full endpoint reference and
  the 401 ladder are in `docs/keba_api.md`.
- **Why REST, not OCPP / UDP / Modbus:** OCPP and UDP `report 1xx`
  polling were both tried and discarded before we found the REST API.
  The REST API wins on substance: a documented endpoint surface, JSON
  with proper HTTP status codes, signed MVA records alongside the
  CSV-equivalent export, and the wallbox itself persists session
  history — so backend downtime cannot lose data, and we just diff the
  session list rather than detect transitions.
- **Eichrecht artifact:** the wallbox's MVA public key is archived once
  at `media/wallbox_mva_public_key.json` (serial + hex). Its SHA-256
  fingerprint is printed in every monthly PDF's footer so the employer
  can verify the signed-session data on request.
- **API credentials:** stored in the `AppSettings` singleton row,
  password encrypted via `charging.fields.EncryptedField` (Fernet, key
  HKDF-derived from `SECRET_KEY`). Editable at `/settings/#wallbox-api`.
  `.env` (`KEBA_API_USERNAME`/`PASSWORD`) is the CLI-only fallback
  before the settings page has been filled in.
- **Live-state cache:** the dashboard fetches `/state` per pageload and
  stores the last successful read in `media/.wallbox_state.json`. When
  the wallbox is unreachable, the dashboard surfaces this cache as
  "Last known state — wallbox unreachable" rather than failing the page.

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
- KEBA integration: documented REST API on :8443, stdlib `urllib` over HTTPS (no third-party HTTP client)
- WeasyPrint for PDF generation; `pypdf` for merging the active
  `TariffDocument` onto outgoing report emails (see Phase 3.1)
- Web serving: Gunicorn (WSGI) behind no reverse proxy — LAN-only, no HTTPS.
  WhiteNoise serves static files directly from the Gunicorn process.
- Email delivery: Django's built-in email framework over SMTP; SMTP creds
  live in `.env`, dispatcher at `charging/services/email.py`
- Imports: triggered by dashboard pageloads via
  `charging/services/auto_import.py`; no external scheduler (no cron,
  no systemd timer, no Celery)
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

## SMTP / email
SMTP transport for the monthly report PDF (Phase 3). Read from `.env`
via django-environ — secrets stay out of the DB. The recipient address
lives in `AppSettings.report_recipient_email` (set on `/settings/`),
not in `.env`.
- `EMAIL_HOST` — e.g. `smtp.strato.de`. Blank disables SMTP and falls
  back to Django's console backend so dev runs without credentials are
  safe; `send_report_email` still refuses to dispatch in that state.
- `EMAIL_PORT` — default `587`
- `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`
- `EMAIL_USE_TLS` — default `True` (STARTTLS); set `EMAIL_USE_SSL=True`
  instead for implicit-TLS providers
- `DEFAULT_FROM_EMAIL` — appears as the `From:` header

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
python manage.py keba_import                             # fetch + ingest live wallbox sessions
python manage.py keba_import -v 2                        # verbose: per-stage + per-row outcomes
KEBA_DUMP_DIR=debug python manage.py keba_import         # tee the raw HTTP body to debug/ for inspection
python manage.py generate_report --year 2026 --month 5   # CLI alternative to the UI button
python manage.py collectstatic --noinput                 # after touching static files
python manage.py makemigrations
python manage.py migrate
python manage.py test
```

## Data Model

`ChargingSession` (Phase 1; MVA fields added in 2.7)
- `serial`, `started_at`, `ended_at`, `energy_kwh`, `raw_row` (full source row as JSON)
- `mva_record_data`, `mva_record_signature` (TextField, nullable) — verbatim
  JSON strings from the wallbox's MVA-signed record; null for sessions
  imported via the CSV path (which doesn't carry MVA data)
- Natural key: `(serial, started_at)`

`Tariff` (historical, never edited – new entries instead)
- `valid_from` (DateField, unique, indexed)
- `energy_price_ct_per_kwh` (Decimal 6,3) – e.g. `38.500`
- `created_at` (auto)
- Helper: `Tariff.for_date(d)` → most recent tariff with `valid_from <= d`

`TariffDocument` (Phase 3.1 – supplier PDF history, parallel to `Tariff`)
- `valid_from` (DateField, unique, indexed)
- `pdf` (FileField, stored under `media/tariff_documents/`)
- `provider_name` (CharField, max 100; required)
- `notes` (TextField, blank)
- `uploaded_at` (auto)
- Helper: `TariffDocument.for_date(d)` → most recent document with
  `valid_from <= d`, mirroring `Tariff.for_date`
- Editable at `/settings/#tariff` (separate POST endpoints
  `tariff_document_create` / `tariff_document_delete`); delete also
  removes the file from storage via `pdf.delete(save=False)`

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
- **Tariff document attachment:** selected at email-send time, not
  at report-generation time. The stored report PDF in
  `media/reports/` stays untouched; the merge is built fresh in
  memory by `charging/services/pdf_merge.py`. Selection key is
  `TariffDocument.for_date(end_of_report_month)`. No document on
  file → send the bare report (non-blocking, informational flash).
- **Billable sessions:** the wallbox emits a short 0-kWh entry for each
  RFID authorization event immediately before the real charge.
  Currently these are tagged with `tokenId = "predefinedTokenId"`
  because remote-start authorisation comes via OCPP_RS_TLS, not the
  swiped card. `ingest_json_row` drops them at the ingest boundary
  (`energy_kwh == 0` → no DB row), so monthly totals, session counts
  and the PDF table inherit billable-only state automatically. The
  dashboard auto-import applies the equivalent `> 0` filter against
  the raw API field `energyConsumedInKwh` so its "new rows?" diff
  matches what the DB will accept.

## UI (admin-only, `@staff_member_required`)

All pages share a base template `templates/base.html` with a top
navigation bar showing: Wallbox (logo/title, links to dashboard),
Settings, Reports, Admin, Logout (right-aligned, with the
current username next to it). The active page's nav link is visually
highlighted.

1. **Dashboard** at `/dashboard/` (root `/` redirects here):
   - Auto-import: every pageload calls
     `auto_import_if_new_sessions()`, which fetches `/v2/sessions`,
     compares the billable count to the DB and ingests any new rows.
     Imported-count surfaces as a one-time success flash; wallbox-
     unreachable is silent (the live-state UI already says so).
   - Status block (read-only): total number of charging sessions,
     timestamp of the most recently imported session, total energy
     captured (kWh), and the last generated `MonthlyReport`
     (year-month + generated_at) if any.
   - Action block:
     - "Run import now" – POST form that runs `keba_import`
       synchronously and redirects back with a success message
       (number of newly imported sessions) or error message. Kept as
       a manual override even though auto-import covers the common case.
     - "Open latest report" – link to the most recent `MonthlyReport`'s
       PDF if any, else disabled with a hint.
2. **Settings** at `/settings/` — four anchored sub-sections:
   - **Tariff** (`#tariff`): "Add new tariff" form + history table,
     followed by a "Tariff document" sub-section (upload form +
     history table with download / delete) — the supplier PDF that
     gets appended to outgoing reports (Phase 3.1)
   - **Wallbox API** (`#wallbox-api`): username + password for the
     REST API (encrypted via `EncryptedField`; blank-on-submit
     preserves the stored value)
   - **Report recipient** (`#report-recipient`): email for the
     monthly PDF send
   - **Eichrecht info** (`#eichrecht`, read-only): wallbox serial,
     public-key fingerprint, live-fetched firmware version with
     graceful "unreachable" fallback
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
   the neutral `border-[rgba(94,234,255,0.10)]`. On the dashboard, the
   live-state card picks up the glow while the wallbox is `CHARGING`
   or `ERROR` (so the user's attention follows the wallbox); otherwise
   no card is promoted, because auto-import handles the common case
   and "Run import now" is a manual override styled the same as
   "Open latest report".

The "Run import now" button is intentionally synchronous — it blocks
for a few seconds while the wallbox is queried. It exists only as a
manual override; the per-pageload auto-import (see
`charging/services/auto_import.py`) pulls new sessions on its own.

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
- Per-session manual edits, marking sessions as private/business
- Multi-vehicle support
- Tax / THG-Quote features
- Any kind of base fee / fixed cost accounting (deliberately removed in 2.5)

## Open Questions / TODO
- **SMTP server:** outbound mail goes through Strato
  (`smtp.strato.de:465`, implicit TLS) from a dedicated mailbox
  `wallbox@ingko.de`. Credentials live in `.env`.
- **Re-import of sessions after report generation:** if new sessions
  arrive for an already-reported month (rare, but possible if
  `keba_import` was behind), the user regenerates the report manually
  via the Reports page. Decide later whether to flag this in the UI.

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