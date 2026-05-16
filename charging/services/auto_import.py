"""Dashboard auto-import: pull new sessions when the wallbox has more than us.

Called once per dashboard pageload. The cheap path (wallbox count == DB
count) is just a single `/v2/sessions` GET and a `COUNT(*)`; the
expensive path (wallbox has new sessions) reuses the same response to
ingest without a second network call.

Errors are caught and surfaced via the returned outcome — the dashboard
already has live_state's "Unreachable" UI for the missing-wallbox case,
so we don't need to crash the page over an auto-import failure.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from django.conf import settings

from charging.models import AppSettings, ChargingSession
from charging.services import ingest_json_row
from charging.services.keba_client import build_keba_client
from charging.services.wallbox_key import ensure_wallbox_key_archived


@dataclass(frozen=True)
class AutoImportOutcome:
    """Result of a single auto-import check.

    ``checked`` is False only when we couldn't even ask the wallbox
    (offline, missing credentials, etc.). When True, ``wallbox_count``
    is authoritative — even if it equals ``db_count`` (nothing to do).
    """
    checked: bool
    wallbox_count: int
    db_count: int
    imported: int
    error: str | None


def _billable(row: dict) -> bool:
    """Match the ingest filter: zero-kWh swipes never become DB rows."""
    try:
        return Decimal(str(row.get("energyConsumedInKwh", 0))) > 0
    except (TypeError, ArithmeticError, ValueError):
        return False


def auto_import_if_new_sessions() -> AutoImportOutcome:
    """Compare wallbox session count to DB; ingest the new ones if higher."""
    db_count = ChargingSession.objects.count()
    try:
        client = build_keba_client()
        rows = client.list_sessions()
    except Exception as exc:
        return AutoImportOutcome(
            checked=False,
            wallbox_count=0,
            db_count=db_count,
            imported=0,
            error=f"{type(exc).__name__}: {exc}",
        )

    wallbox_count = sum(1 for r in rows if _billable(r))
    if wallbox_count <= db_count:
        return AutoImportOutcome(
            checked=True,
            wallbox_count=wallbox_count,
            db_count=db_count,
            imported=0,
            error=None,
        )

    # Best-effort: archive the MVA public key while we already hold a
    # client (no-op if the file already exists). Failure here must not
    # block ingest — the key archive is an Eichrecht nicety, not a hard
    # prerequisite for storing rows.
    if rows:
        try:
            key_path = Path(settings.MEDIA_ROOT) / "wallbox_mva_public_key.json"
            ensure_wallbox_key_archived(
                client, rows[0]["wallboxSerialNumber"], path=key_path
            )
        except Exception:
            pass

    imported = 0
    for row in rows:
        _, was_created = ingest_json_row(row)
        if was_created:
            imported += 1

    if imported:
        app = AppSettings.current()
        app.last_import_at = datetime.now(tz=timezone.utc)
        app.save()

    return AutoImportOutcome(
        checked=True,
        wallbox_count=wallbox_count,
        db_count=ChargingSession.objects.count(),
        imported=imported,
        error=None,
    )
