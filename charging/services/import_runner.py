"""Service entry point for KEBA session imports.

Wraps the REST-API fetch + per-row ingest pipeline so it can be called
from both the ``keba_import`` management command and the dashboard
"Run import now" button without duplicating logic. The live path uses
``/v2/sessions`` (JSON, MVA records included); ``--file`` keeps the
legacy CSV path for hand-downloaded exports.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from django.conf import settings

from charging.keba_api import KebaApiClient
from charging.keba_csv import parse_sessions_csv
from charging.services import ingest_csv_row, ingest_json_row
from charging.services.wallbox_key import ensure_wallbox_key_archived


_BERLIN = ZoneInfo("Europe/Berlin")


@dataclass
class ImportResult:
    sessions_imported: int = 0
    sessions_skipped: int = 0
    sessions_updated: int = 0
    rows_seen: int = 0
    error_message: str | None = None


def run_keba_import(
    *,
    file: Path | None = None,
    log: Callable[[str], None] | None = None,
) -> ImportResult:
    """Fetch wallbox sessions and upsert them.

    With ``file``, ingest a hand-downloaded CSV (no MVA data). Without,
    pull from ``/v2/sessions`` over the REST API (MVA records included).
    Exceptions from the API client or parsing propagate to the caller —
    the management command turns them into ``CommandError``, the
    dashboard view catches them for a flash message.

    Pass ``log`` to receive progress messages (one per stage and per row).
    """
    say = log or (lambda _: None)

    if file is not None:
        return _import_from_csv_file(file, say)
    return _import_from_api(say)


def _import_from_csv_file(
    file: Path, say: Callable[[str], None]
) -> ImportResult:
    say(f"Reading CSV from file: {file}")
    text = file.read_text(encoding="utf-8")
    rows = parse_sessions_csv(text)
    say(f"Parsed {len(rows)} row(s) from CSV")

    result = ImportResult(rows_seen=len(rows))
    for row in rows:
        obj, was_created = ingest_csv_row(row)
        start = row.get("Start", "?")
        kwh = row.get("Consumption (kWh)", "?")
        _record(result, say, obj, was_created, start, kwh)
    return result


def _import_from_api(say: Callable[[str], None]) -> ImportResult:
    if not settings.KEBA_API_URL:
        raise RuntimeError("KEBA_API_URL is not set in .env.")
    if not (settings.KEBA_API_USERNAME and settings.KEBA_API_PASSWORD):
        raise RuntimeError(
            "KEBA_API_USERNAME and KEBA_API_PASSWORD must be set in .env."
        )
    say(
        f"Fetching sessions from {settings.KEBA_API_URL} "
        f"(user={settings.KEBA_API_USERNAME})"
    )
    client = KebaApiClient(
        base_url=settings.KEBA_API_URL,
        username=settings.KEBA_API_USERNAME,
        password=settings.KEBA_API_PASSWORD,
        verify_tls=settings.KEBA_API_VERIFY_TLS,
        token_cache_path=Path(settings.MEDIA_ROOT) / ".keba_token.json",
    )
    rows = client.list_sessions()
    say(f"Fetched {len(rows)} session(s)")

    if rows:
        key_path = Path(settings.MEDIA_ROOT) / "wallbox_mva_public_key.json"
        existed = key_path.exists()
        serial = rows[0]["wallboxSerialNumber"]
        record = ensure_wallbox_key_archived(client, serial, path=key_path)
        if record and not existed:
            say(f"Wallbox MVA public key archived → {key_path}")

    if settings.KEBA_DUMP_DIR:
        dump_dir = Path(settings.KEBA_DUMP_DIR)
        dump_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        dest = dump_dir / f"keba_sessions_{stamp}.json"
        dest.write_text(json.dumps(rows, indent=2))
        say(f"Dumped sessions to {dest}")

    result = ImportResult(rows_seen=len(rows))
    for row in rows:
        obj, was_created = ingest_json_row(row)
        start = _format_json_start(row.get("startDate"))
        kwh = row.get("energyConsumedInKwh", "?")
        _record(result, say, obj, was_created, start, kwh)
    return result


def _format_json_start(ms) -> str:
    if not ms:
        return "?"
    return (
        datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        .astimezone(_BERLIN)
        .strftime("%d-%m-%Y %H:%M:%S")
    )


def _record(
    result: ImportResult,
    say: Callable[[str], None],
    obj,
    was_created: bool,
    start: str,
    kwh,
) -> None:
    if obj is None:
        result.sessions_skipped += 1
        say(f"  skipped  {start:<20}  0 kWh (RFID swipe)")
    elif was_created:
        result.sessions_imported += 1
        say(f"  created  {start:<20}  {kwh:>6} kWh")
    else:
        result.sessions_updated += 1
        say(f"  updated  {start:<20}  {kwh:>6} kWh")
