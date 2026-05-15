# KEBA KeMove REST API – integration reference

The wallbox exposes a documented REST API on port 8443. This file is the
authoritative reference for our integration, replacing the PHP CSV scrape
used in earlier versions. Discovered and verified on 14–15 May 2026.

For anything not covered here, fall back to the **on-board** docs:

- Swagger UI: `https://192.168.1.12:8443/docs`
- OpenAPI spec (JSON): `https://192.168.1.12:8443/openapi_orig.json`

The on-board docs are complete and authoritative – the API is otherwise
not publicly documented by KEBA.

## Overview

- **Base URL:** `https://192.168.1.12:8443` (kept in `.env` as `KEBA_API_URL`)
- **Protocol:** HTTPS only, self-signed certificate
- **API identifier:** "KeMove REST API", version visible at `GET /version` (currently `2.4.1`)
- **Implementation on the wallbox:** Python / Flask (Werkzeug-style 404s)

Enabled on the wallbox via the web UI under **API Access Settings → API State: ON**.
The corresponding DIP switch `D1.3` (smartHomeInterface) being `ON` is **not**
required for the REST API itself; it gates the legacy UDP / Modbus TCP interfaces.

## Authentication

JWT-based, Flask-JWT-Extended convention.

### Login

```
POST /v2/jwt/login
Content-Type: application/json

{
  "username": "admin",
  "password": "<web UI password>"
}
```

Response:

```json
{
  "accessToken": "eyJ0eXAiOiJKV1QiLC...",
  "refreshToken": "eyJ0eXAiOiJKV1QiLC..."
}
```

### Token lifetimes (verified from JWT `exp` claim)

- **accessToken:** 15 minutes
- **refreshToken:** 30 days

### Using the token

Every authenticated request:

```
Authorization: Bearer <accessToken>
```

### Refresh

```
POST /v2/jwt/refresh
Authorization: Bearer <refreshToken>
```

Response: `{ "accessToken": "..." }`

### Recommended retry sequence

1. Send request with `accessToken`
2. On HTTP 401: refresh once with `refreshToken`, retry the original request
3. On HTTP 401 even after refresh, or refresh itself returns 401:
   perform a full login with the username/password from settings
4. On HTTP 401 even after re-login: surface a real auth failure to the user

## Endpoints we use

Everything we need is under `/v2`. The `/v1` paths exist but we don't touch them.

### Sessions

#### `GET /v2/sessions/export` — CSV (primary import path)

Returns the same semicolon-separated CSV format that the legacy
`/export.php` produced. **Verified byte-identical** on 15 May 2026.

```
Charging Station ID;Serial;RFID Card;Status;Start;End;Duration (s);Meter at start (Wh);Meter at end (Wh);Consumption (kWh)
1;34416115;predefinedTokenId;CLOSED;13-05-2026 23:32:06;14-05-2026 10:00:14;37688;58189.2;109759.9;51.57
1;34416115;044115CA911E94;CLOSED;13-05-2026 23:30:16;13-05-2026 23:30:22;6;58189.2;58189.2;0
...
```

- Content-Type: `text/csv; charset=utf-8`
- Date format: `DD-MM-YYYY HH:MM:SS`, Europe/Berlin local time
- This is a drop-in for `parse_sessions_csv` — no parser changes needed

#### `GET /v2/sessions` — JSON list (for MVA records)

Same sessions, richer metadata. Use this when we need the cryptographic
records for Eichrecht compliance (see ROADMAP Phase 2.7):

```json
{
  "sessions": [
    {
      "id": 591466681,
      "wallboxSerialNumber": "34416115",
      "tokenId": "predefinedTokenId",
      "status": "CLOSED",
      "terminationReason": "UNPLUG_EV",
      "startDate": 1778707926397,
      "endDate": 1778745614889,
      "duration": 37688492,
      "startingMeterValue": 58189200,
      "endingMeterValue": 109759900,
      "energyConsumed": 51570700,
      "energyConsumedInKwh": 51.5707,
      "mvaRecordData": "{\"FV\":\"1.1\",\"GI\":\"KEBA_KCP30\",\"GS\":\"34416115\",...}",
      "mvaRecordSignature": "{\"SD\":\"3046022100B47A24F231...\"}",
      "tariffModel": "PerEnergyConsumed"
    }
  ]
}
```

Notes:

- Timestamps are **epoch milliseconds** here (different format from the CSV)
- `mvaRecordData` and `mvaRecordSignature` are JSON-encoded strings — store them verbatim, do not re-parse and re-serialize (the signature is over the original bytes)
- Pagination via `?limit=N&offset=M` is honoured; default returns all
- `mvaRecordData` and `mvaRecordSignature` land in the DB in Phase 2.7
  — see `ROADMAP.md`

#### `GET /v2/sessions/count`

```json
{ "total": 6 }
```

Useful for sanity-checking imports.

#### `GET /v2/sessions/{id}` — single session

Returns a **leaner** subset of fields (no MVA data, no `transactionToken`,
no `terminationReason`). Counter-intuitive but real. For full data, fetch
from the list endpoint and filter by ID.

#### Other session endpoints (we don't currently use)

- `GET /v2/sessions/stats` — aggregate metrics
- `GET /v2/sessions/filter-fields` — available filter values
- `GET /v2/sessions/exportAsync` + `/v2/sessions/exportAsync/status` — async export, irrelevant at our volumes

### Wallbox

#### `GET /v2/wallboxes/{serialNumber}` — full info snapshot

```json
{
  "serialNumber": "34416115",
  "model": "KC-P30-EC2204B2-L0R-CC",
  "firmwareVersion": "P30 v 3.10.80 (251002-115400) : ML v 2.9.2",
  "macAddress": "00:60:B5:64:88:6E",
  "ipAddress": "192.168.25.11",
  "maxCurrent": 13000,
  "maxPhases": 3,
  "state": "IDLE",
  "vehiclePlugged": false,
  "sessionActive": false,
  "meter": {
    "meterValue": 109759900,
    "totalActivePower": 0,
    "lines": [...],
    "temperature": 1962
  },
  "mvaPublicKey": "{\"UK\":\"3059301306072A8648CE3D...\"}",
  "dipSwitchSettings": [false,false,true,false,false,true,...]
}
```

Notes:

- `ipAddress` here is the wallbox's **internal management IP**, not the customer LAN address
- `maxCurrent` is in mA, mirrors the DIP switch state (13000 = 13 A)
- `meter.temperature` is in 1/100 °C (1962 → 19.62 °C)
- `meter.meterValue` is the lifetime energy meter in Wh
- `mvaPublicKey` is the wallbox's Eichrecht public key — archive it once
  to `media/wallbox_mva_public_key.json` in Phase 2.7 (see `ROADMAP.md`)
- `dipSwitchSettings` is a raw 16-element boolean array; use `/v2/wallboxes/dipswitch/{serial}` for the parsed version

#### `GET /v2/wallboxes/{serialNumber}/state` — fast live state

```json
{ "state": "IDLE" }
```

Returns in ~50 ms. Suitable for every dashboard pageload. Known values:
`IDLE`, `CHARGING`, `ERROR`. Other values may exist; treat unknowns as a
display-only string.

#### `GET /v2/wallboxes/dipswitch/{serialNumber}` — parsed DIP state

```json
{
  "smartHomeInterface": true,
  "current": 13,
  "commisioningMode": false,
  "externalEnableX1": false,
  "externalEnableX2": false,
  "communicationHubMode": false,
  "deactivatePlcModem": false,
  "chargingSessionMode": false,
  "ds14": false,
  "ds15": false,
  "ipAddress": "192.168.25.10"
}
```

Useful for confirming hardware configuration (max current, smart-home
interface state) without physically opening the wallbox. Note that
`commisioningMode` is misspelled in the API response — preserve the typo
in any DTO; the API will not change it.

### Configuration

#### `GET /v2/configs/system/price`

Returns a bare JSON number (e.g. `0.0`). The wallbox can hold a price per
kWh, but we don't use it — pricing lives in our `Tariff` model.

## TLS

- Self-signed certificate
- For the initial integration: `verify=False` (or `ssl.CERT_NONE` on a custom
  SSL context). This is acceptable because the wallbox sits on the LAN and
  we're authenticating with credentials regardless.
- **Future hardening:** extract the wallbox cert once, pin it via a CA bundle,
  switch to `verify=<path>`. Not urgent for a LAN-only app.

## Token caching strategy

The cache file lives at `media/.keba_token.json`. `media/` is chosen
because it is already writable by the Gunicorn service user and is
gitignored, so token material can never leak into the repo.

- Persist both tokens in this file, file mode `0600`
- Include the issued-at timestamp so we can decide whether to refresh
  proactively (e.g. accessToken older than 12 minutes)
- On process start: read the cache. If accessToken is fresh, use it. If
  expired but refreshToken is fresh, refresh first. If both expired, log in
  from credentials.
- After every successful refresh or login: rewrite the cache file atomically
  (write to temp, then `os.replace`)
- Never log token contents — they grant full admin access to the wallbox

## Where this code lives

Concrete file paths for the integration in this repo:

- **New API client:** `charging/keba_api.py`
  - Class `KebaApiClient` exposing `login()`, `refresh()`,
    `export_sessions_csv()`, `get_state()`, `get_wallbox_info()`
  - Naming mirrors the existing `charging/keba_http.py` so imports stay
    readable (`from charging.keba_api import KebaApiClient`)
- **Legacy scrape:** `charging/keba_http.py` is renamed to
  `charging/_legacy_keba_scrape.py` once the API path is wired up.
  Kept as an emergency fallback; the underscore prefix flags it as
  outside the normal call graph.
- **CSV parser:** `parse_sessions_csv` is reused — API CSV is
  byte-identical to the scrape CSV. Extract it into
  `charging/keba_csv.py` *before* the rename, so neither the new client
  nor the orchestration layer ends up importing from a `_legacy_*`
  module.
- **Orchestration:** `charging/services/import_runner.py` swaps the
  client it instantiates; the rest of its shape stays put.
- **CLI entry point:** `charging/management/commands/keba_import.py`
  is unchanged in this phase — no new flags, same exit codes.
- **Token cache:** `media/.keba_token.json` (see *Token caching
  strategy*). Add the filename to `.gitignore` explicitly; `media/` is
  already whole-tree ignored, but spelling out the secret file is good
  hygiene.

## Test plan

The existing scrape is tested by patching
`charging.keba_http._open`. For the new client, mirror that pattern:

- Patch target: the single HTTP seam on `KebaApiClient` (e.g.
  `charging.keba_api.KebaApiClient._request`) — keeps tests
  deterministic without TLS or network access.
- Reuse the existing CSV fixture for `parse_sessions_csv`. Add
  login / refresh / 401-retry / refresh-expiry fixtures alongside it in
  `charging/tests.py`. If that file grows past ~500 lines, split into
  `charging/tests/test_keba_api.py` at implementation time — not now.
- Add a `Content-Length` mismatch test for `export_sessions_csv()` —
  this closes the open CLAUDE.md TODO about silent CSV truncation.

## Pitfalls and gotchas

- **Self-signed cert.** `urllib`/`requests` will refuse by default.
- **30-day refresh token expiry.** If `keba_import` doesn't run for 30 days,
  the cached refresh token is dead. Always fall back to full login on
  refresh failure.
- **DIP switch state can be stale.** The wallbox reads DIP only at startup.
  Flipping switches without rebooting → API reports the old state.
- **`/v2/sessions/{id}` is leaner than `/v2/sessions`.** Counter-intuitive
  but real. Use the list endpoint when you need MVA records.
- **`commisioningMode` is misspelled.** Preserve the typo.
- **Two different date formats.** JSON endpoints return epoch milliseconds;
  the CSV export returns `DD-MM-YYYY HH:MM:SS` in local time. Normalise on import.
- **`ipAddress` in the wallbox object is the internal IP.** It is not the
  address you use to reach the API; use `KEBA_API_URL` from `.env`.

## What MVA records are (Eichrecht background)

MVA = Measurement Value Authentication. Each completed charging session is
signed by the wallbox using its private key; the signature covers the meter
readings, timestamps, and metadata. With the matching public key
(`mvaPublicKey` from the wallbox info endpoint), any third party can verify
that the readings have not been tampered with.

For our use case (employer reimbursement), the relevant deliverables are:

1. Archive `mvaRecordData` and `mvaRecordSignature` per session
2. Archive `mvaPublicKey` once per wallbox
3. Mention the signed records in the monthly PDF footer
4. Provide the public key on request

Implementing the actual ECDSA verification in our app is out of scope
unless HR specifically requests it — all the raw data is in the DB if it
ever becomes necessary.
