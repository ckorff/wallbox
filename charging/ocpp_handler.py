"""OCPP 1.6-J handler — turns wallbox messages into ChargingSession rows.

The wallbox is the OCPP "Charge Point" and connects out over WebSocket to
this backend (the "Central System"). Session lifecycle (StartTransaction,
StopTransaction) is the source of truth for billing; this is what the
Modbus polling architecture could not provide reliably.

Messages we handle:
- BootNotification, Heartbeat: keep-alive plumbing.
- Authorize: any RFID is accepted (single-user personal wallbox).
- StatusNotification, MeterValues: acked but not stored (yet).
- StartTransaction: create a ChargingSession, return its pk as the
  OCPP transaction_id.
- StopTransaction: look up the session by ocpp_transaction_id, set
  end + meter_end + kwh.
"""

from __future__ import annotations

from decimal import Decimal

from asgiref.sync import sync_to_async
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ocpp.routing import on
from ocpp.v16 import ChargePoint, call_result
from ocpp.v16.enums import AuthorizationStatus, RegistrationStatus

from charging.models import ChargingSession


HEARTBEAT_INTERVAL_SECONDS = 60
_WH_PER_KWH = Decimal(1_000)
_KWH_QUANTUM = Decimal('0.001')


def _wh_to_kwh(wh: int) -> Decimal:
    return (Decimal(wh) / _WH_PER_KWH).quantize(_KWH_QUANTUM)


def _now_utc_iso() -> str:
    return timezone.now().isoformat()


def _create_session_for_start_transaction(
    *, timestamp: str, meter_start: int, id_tag: str,
) -> ChargingSession:
    start = parse_datetime(timestamp)
    if start is None:
        raise ValueError(f'Could not parse OCPP timestamp: {timestamp!r}')
    session = ChargingSession.objects.create(
        start=start,
        meter_start=_wh_to_kwh(meter_start),
        kwh=Decimal('0.000'),
        note=f'OCPP id_tag={id_tag}',
    )
    # Use the row pk as the OCPP transaction_id so StopTransaction can find
    # it later. Keeping the ID space at 1 means it is never reused.
    session.ocpp_transaction_id = session.pk
    session.save(update_fields=['ocpp_transaction_id'])
    return session


def _close_session_for_stop_transaction(
    *, transaction_id: int, timestamp: str, meter_stop: int,
) -> ChargingSession | None:
    end = parse_datetime(timestamp)
    if end is None:
        raise ValueError(f'Could not parse OCPP timestamp: {timestamp!r}')
    try:
        session = ChargingSession.objects.get(ocpp_transaction_id=transaction_id)
    except ChargingSession.DoesNotExist:
        return None
    session.end = end
    session.meter_end = _wh_to_kwh(meter_stop)
    session.kwh = (session.meter_end - session.meter_start).quantize(_KWH_QUANTUM)
    session.save(update_fields=['end', 'meter_end', 'kwh'])
    return session


class KebaChargePoint(ChargePoint):
    """OCPP 1.6 handler for the personal KEBA P30 wallbox."""

    @on('BootNotification')
    async def on_boot_notification(self, charge_point_model, charge_point_vendor, **kwargs):
        return call_result.BootNotification(
            current_time=_now_utc_iso(),
            interval=HEARTBEAT_INTERVAL_SECONDS,
            status=RegistrationStatus.accepted,
        )

    @on('Heartbeat')
    async def on_heartbeat(self, **kwargs):
        return call_result.Heartbeat(current_time=_now_utc_iso())

    @on('Authorize')
    async def on_authorize(self, id_tag, **kwargs):
        return call_result.Authorize(
            id_tag_info={'status': AuthorizationStatus.accepted},
        )

    @on('StatusNotification')
    async def on_status_notification(self, **kwargs):
        return call_result.StatusNotification()

    @on('MeterValues')
    async def on_meter_values(self, **kwargs):
        return call_result.MeterValues()

    @on('StartTransaction')
    async def on_start_transaction(
        self, connector_id, id_tag, meter_start, timestamp, **kwargs,
    ):
        session = await sync_to_async(
            _create_session_for_start_transaction, thread_sensitive=True,
        )(timestamp=timestamp, meter_start=meter_start, id_tag=id_tag)
        return call_result.StartTransaction(
            transaction_id=session.ocpp_transaction_id,
            id_tag_info={'status': AuthorizationStatus.accepted},
        )

    @on('StopTransaction')
    async def on_stop_transaction(
        self, transaction_id, timestamp, meter_stop, **kwargs,
    ):
        await sync_to_async(
            _close_session_for_stop_transaction, thread_sensitive=True,
        )(
            transaction_id=transaction_id,
            timestamp=timestamp,
            meter_stop=meter_stop,
        )
        return call_result.StopTransaction(
            id_tag_info={'status': AuthorizationStatus.accepted},
        )
