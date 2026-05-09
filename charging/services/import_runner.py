"""Service entry point for KEBA CSV imports.

Wraps the HTTP fetch + CSV parse + per-row ingest pipeline so it can be
called from both the ``keba_import`` management command and the
dashboard "Run import now" button without duplicating logic.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from charging.keba_http import fetch_sessions_csv, parse_sessions_csv
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
    host: str | None = None,
) -> ImportResult:
    """Fetch the wallbox CSV (or read from ``file``) and upsert sessions.

    Exceptions from the HTTP layer or parsing propagate to the caller –
    the management command turns them into ``CommandError``, the
    dashboard view catches them for a flash message.
    """
    if file is not None:
        text = file.read_text(encoding="utf-8")
    else:
        host = host or settings.KEBA_HOST
        if not host:
            raise RuntimeError("KEBA_HOST is not set in .env (or pass --host).")
        if not (settings.KEBA_USERNAME and settings.KEBA_PASSWORD):
            raise RuntimeError(
                "KEBA_USERNAME and KEBA_PASSWORD must be set in .env."
            )
        text = fetch_sessions_csv(
            host, settings.KEBA_USERNAME, settings.KEBA_PASSWORD
        )

    rows = parse_sessions_csv(text)
    result = ImportResult(rows_seen=len(rows))
    for row in rows:
        obj, was_created = ingest_csv_row(row)
        if obj is None:
            result.sessions_skipped += 1
        elif was_created:
            result.sessions_imported += 1
        else:
            result.sessions_updated += 1
    return result
