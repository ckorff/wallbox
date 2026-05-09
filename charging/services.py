"""Persistence helpers for KEBA wallbox CSV rows."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from .models import ChargingSession


_KWH_QUANT = Decimal("0.001")
_BERLIN = ZoneInfo("Europe/Berlin")
_DATE_FMT = "%d-%m-%Y %H:%M:%S"


def _parse_local(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    return datetime.strptime(value, _DATE_FMT).replace(tzinfo=_BERLIN)


def ingest_csv_row(row: dict):
    """Upsert a ChargingSession from one parsed CSV row.

    Returns ``(instance, created)`` for billable rows, or ``(None, False)``
    for 0 kWh "touch" sessions (RFID swipe without charging).
    """
    energy_kwh = Decimal(row["Consumption (kWh)"]).quantize(_KWH_QUANT)
    if energy_kwh == 0:
        return None, False

    return ChargingSession.objects.update_or_create(
        serial=row["Serial"],
        started_at=_parse_local(row["Start"]),
        defaults={
            "ended_at": _parse_local(row["End"]),
            "energy_kwh": energy_kwh,
            "raw_row": row,
        },
    )
