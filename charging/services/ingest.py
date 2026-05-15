"""Persistence helpers for KEBA wallbox session rows.

The CSV path (`ingest_csv_row`) handles hand-downloaded exports via
`--file`. The JSON path (`ingest_json_row`) handles the live import
from /v2/sessions, including MVA-signed records. Both upsert on the
same (serial, started_at) natural key, so a session first imported
via CSV gains its MVA fields the next time it's re-fetched via the
API path.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from charging.models import ChargingSession


_KWH_QUANT = Decimal("0.001")
_BERLIN = ZoneInfo("Europe/Berlin")
_DATE_FMT = "%d-%m-%Y %H:%M:%S"


def _parse_local(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    return datetime.strptime(value, _DATE_FMT).replace(tzinfo=_BERLIN)


def _epoch_ms_to_berlin(ms) -> datetime | None:
    # Microseconds stripped so the (serial, started_at) natural key keeps
    # matching across paths — CSV is second-precision, JSON has sub-second.
    if not ms:
        return None
    return (
        datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        .astimezone(_BERLIN)
        .replace(microsecond=0)
    )


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


def ingest_json_row(row: dict):
    """Upsert a ChargingSession from one /v2/sessions JSON entry.

    Returns ``(instance, created)`` for billable rows, or ``(None, False)``
    for 0 kWh "touch" sessions (RFID swipe without charging). MVA records,
    when present, are stored verbatim — re-parsing would invalidate the
    cryptographic signature.
    """
    energy_kwh = Decimal(str(row["energyConsumedInKwh"])).quantize(_KWH_QUANT)
    if energy_kwh == 0:
        return None, False

    return ChargingSession.objects.update_or_create(
        serial=row["wallboxSerialNumber"],
        started_at=_epoch_ms_to_berlin(row["startDate"]),
        defaults={
            "ended_at": _epoch_ms_to_berlin(row.get("endDate")),
            "energy_kwh": energy_kwh,
            "raw_row": row,
            "mva_record_data": row.get("mvaRecordData"),
            "mva_record_signature": row.get("mvaRecordSignature"),
        },
    )
