from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

from django.test import TestCase

from ocpp.v16.enums import AuthorizationStatus, RegistrationStatus

from charging.models import ChargingSession
from charging.ocpp_handler import KebaChargePoint


def _cp() -> KebaChargePoint:
    return KebaChargePoint(id='keba-home', connection=AsyncMock())


class BootHeartbeatAuthorizeTests(TestCase):
    async def test_boot_notification_returns_accepted(self):
        cp = _cp()
        result = await cp.on_boot_notification(
            charge_point_model='P30',
            charge_point_vendor='KEBA',
        )
        self.assertEqual(result.status, RegistrationStatus.accepted)
        self.assertGreater(result.interval, 0)
        self.assertTrue(result.current_time)

    async def test_heartbeat_returns_current_time(self):
        cp = _cp()
        result = await cp.on_heartbeat()
        self.assertTrue(result.current_time)

    async def test_authorize_accepts_any_id_tag(self):
        cp = _cp()
        result = await cp.on_authorize(id_tag='whatever')
        self.assertEqual(result.id_tag_info['status'], AuthorizationStatus.accepted)

    async def test_status_notification_acked(self):
        cp = _cp()
        # Just needs to return without raising.
        await cp.on_status_notification(
            connector_id=1, error_code='NoError', status='Charging',
        )

    async def test_meter_values_acked(self):
        cp = _cp()
        await cp.on_meter_values(connector_id=1, meter_value=[], transaction_id=1)


class StartTransactionTests(TestCase):
    async def test_creates_session_and_returns_transaction_id(self):
        cp = _cp()
        result = await cp.on_start_transaction(
            connector_id=1,
            id_tag='ABCD1234',
            meter_start=12_345_000,  # Wh -> 12345.000 kWh
            timestamp='2026-05-08T18:00:00+00:00',
        )
        self.assertEqual(await ChargingSession.objects.acount(), 1)
        session = await ChargingSession.objects.afirst()
        self.assertIsNotNone(session.ocpp_transaction_id)
        self.assertEqual(result.transaction_id, session.ocpp_transaction_id)
        self.assertEqual(result.id_tag_info['status'], AuthorizationStatus.accepted)
        self.assertEqual(session.meter_start, Decimal('12345.000'))
        self.assertEqual(session.start, datetime(2026, 5, 8, 18, 0, tzinfo=timezone.utc))
        self.assertIsNone(session.end)
        self.assertIsNone(session.meter_end)
        self.assertIn('ABCD1234', session.note)


class StopTransactionTests(TestCase):
    async def test_closes_session_with_end_meter_and_kwh(self):
        cp = _cp()
        start_result = await cp.on_start_transaction(
            connector_id=1, id_tag='X',
            meter_start=100_000,  # 100 kWh
            timestamp='2026-05-08T18:00:00+00:00',
        )
        await cp.on_stop_transaction(
            transaction_id=start_result.transaction_id,
            timestamp='2026-05-08T20:30:00+00:00',
            meter_stop=142_500,  # 142.5 kWh
        )
        session = await ChargingSession.objects.afirst()
        self.assertEqual(session.meter_end, Decimal('142.500'))
        self.assertEqual(session.kwh, Decimal('42.500'))
        self.assertEqual(session.end, datetime(2026, 5, 8, 20, 30, tzinfo=timezone.utc))

    async def test_unknown_transaction_id_does_not_raise(self):
        cp = _cp()
        # No matching session — should silently no-op rather than crash.
        result = await cp.on_stop_transaction(
            transaction_id=99999,
            timestamp='2026-05-08T20:30:00+00:00',
            meter_stop=1_000,
        )
        self.assertEqual(result.id_tag_info['status'], AuthorizationStatus.accepted)
        self.assertEqual(await ChargingSession.objects.acount(), 0)


class ServerUrlParsingTests(TestCase):
    def test_extract_charge_point_id_normal(self):
        from charging.ocpp_server import extract_charge_point_id
        self.assertEqual(extract_charge_point_id('/ocpp/keba-home'), 'keba-home')

    def test_extract_charge_point_id_with_trailing_slash(self):
        from charging.ocpp_server import extract_charge_point_id
        self.assertEqual(extract_charge_point_id('/ocpp/keba-home/'), 'keba-home')

    def test_extract_charge_point_id_with_query_string(self):
        from charging.ocpp_server import extract_charge_point_id
        self.assertEqual(extract_charge_point_id('/ocpp/keba-home?token=x'), 'keba-home')

    def test_extract_charge_point_id_rejects_non_ocpp_path(self):
        from charging.ocpp_server import extract_charge_point_id
        self.assertIsNone(extract_charge_point_id('/admin/'))
        self.assertIsNone(extract_charge_point_id('/ocpp/'))
        self.assertIsNone(extract_charge_point_id('/'))
