"""Service entry point for KEBA CSV imports.

Wraps the REST-API fetch + CSV parse + per-row ingest pipeline so it can
be called from both the ``keba_import`` management command and the
dashboard "Run import now" button without duplicating logic.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from django.conf import settings

from charging.keba_api import KebaApiClient
from charging.keba_csv import parse_sessions_csv
from charging.services import ingest_csv_row


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
    """Fetch the wallbox CSV (or read from ``file``) and upsert sessions.

    Exceptions from the API client or parsing propagate to the caller –
    the management command turns them into ``CommandError``, the
    dashboard view catches them for a flash message.

    Pass ``log`` to receive progress messages (one per stage and per row).
    """
    say = log or (lambda _: None)

    if file is not None:
        say(f"Reading CSV from file: {file}")
        text = file.read_text(encoding="utf-8")
    else:
        if not settings.KEBA_API_URL:
            raise RuntimeError("KEBA_API_URL is not set in .env.")
        if not (settings.KEBA_API_USERNAME and settings.KEBA_API_PASSWORD):
            raise RuntimeError(
                "KEBA_API_USERNAME and KEBA_API_PASSWORD must be set in .env."
            )
        say(
            f"Fetching CSV from {settings.KEBA_API_URL} "
            f"(user={settings.KEBA_API_USERNAME})"
        )
        client = KebaApiClient(
            base_url=settings.KEBA_API_URL,
            username=settings.KEBA_API_USERNAME,
            password=settings.KEBA_API_PASSWORD,
            verify_tls=settings.KEBA_API_VERIFY_TLS,
            token_cache_path=Path(settings.MEDIA_ROOT) / ".keba_token.json",
        )
        body = client.export_sessions_csv()
        if settings.KEBA_DUMP_DIR:
            dump_dir = Path(settings.KEBA_DUMP_DIR)
            dump_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
            dest = dump_dir / f"keba_export_{stamp}.csv"
            dest.write_bytes(body)
            say(f"Dumped raw body to {dest}")
        text = body.decode("utf-8")
        say(f"Fetched {len(text)} bytes")

    rows = parse_sessions_csv(text)
    say(f"Parsed {len(rows)} row(s) from CSV")

    result = ImportResult(rows_seen=len(rows))
    for row in rows:
        obj, was_created = ingest_csv_row(row)
        start = row.get("Start", "?")
        kwh = row.get("Consumption (kWh)", "?")
        if obj is None:
            result.sessions_skipped += 1
            say(f"  skipped  {start:<20}  0 kWh (RFID swipe)")
        elif was_created:
            result.sessions_imported += 1
            say(f"  created  {start:<20}  {kwh:>6} kWh")
        else:
            result.sessions_updated += 1
            say(f"  updated  {start:<20}  {kwh:>6} kWh")
    return result
