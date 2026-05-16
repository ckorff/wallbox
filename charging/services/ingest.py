"""Persistence helper for KEBA wallbox session rows.

``ingest_json_row`` upserts one entry from ``/v2/sessions`` on the
natural key ``(serial, started_at)``. MVA-signed records are stored
verbatim — re-parsing them would invalidate the wallbox's ECDSA
signature, so the raw JSON strings are kept exactly as received.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from charging.models import ChargingSession


_KWH_QUANT = Decimal("0.001")
_BERLIN = ZoneInfo("Europe/Berlin")


def _epoch_ms_to_berlin(ms) -> datetime | None:
    # Sub-second precision stripped so the (serial, started_at) natural
    # key stays stable across re-imports — the wallbox occasionally
    # returns slightly different microsecond values for the same session.
    if not ms:
        return None
    return (
        datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        .astimezone(_BERLIN)
        .replace(microsecond=0)
    )


def ingest_json_row(row: dict):
    """Upsert a ChargingSession from one /v2/sessions JSON entry.

    Returns ``(instance, created)`` for billable rows, or ``(None, False)``
    for 0 kWh "touch" sessions (RFID swipe without charging) — those are
    deliberately not persisted, since they have no cost and would inflate
    the wallbox-vs-DB count comparison the dashboard auto-import uses.
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
