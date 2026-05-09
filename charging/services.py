"""Persistence helpers for KEBA wallbox session reports."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from .models import ChargingSession


# KEBA reports energy in 0.1 Wh ticks: divide by 10_000 to get kWh.
_TICKS_PER_KWH = Decimal(10_000)
_KWH_QUANT = Decimal("0.001")


def _seconds_to_datetime(seconds) -> Optional[datetime]:
    if not seconds:
        return None
    return datetime.fromtimestamp(int(seconds), tz=timezone.utc)


def ingest_session_report(report: dict):
    """Upsert a ChargingSession from a KEBA `report 1xx` payload.

    Returns ``(instance, created)`` for valid rows, or ``(None, False)``
    when the report represents an empty slot or has no usable start.
    """
    session_id = report.get("Session ID") or 0
    started_at = _seconds_to_datetime(report.get("started[s]"))
    if not session_id or started_at is None:
        return None, False

    energy_ticks = int(report.get("E pres") or 0)
    energy_kwh = (Decimal(energy_ticks) / _TICKS_PER_KWH).quantize(_KWH_QUANT)

    reason = report.get("reason")
    end_reason = "" if not reason else str(reason)

    return ChargingSession.objects.update_or_create(
        keba_session_id=session_id,
        defaults={
            "started_at": started_at,
            "ended_at": _seconds_to_datetime(report.get("ended[s]")),
            "energy_kwh": energy_kwh,
            "end_reason": end_reason,
            "raw_report": report,
        },
    )
