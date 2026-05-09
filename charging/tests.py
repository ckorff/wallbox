from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase

from charging.keba import KebaClient, KebaError
from charging.models import ChargingSession
from charging.services import ingest_session_report


SAMPLE_REPORT = {
    "ID": "100",
    "Session ID": 42,
    "Curr HW": 32000,
    "E start": 12_345_678,
    "E pres": 567_890,
    "started": "2024-01-15 18:30:45.000",
    "ended": "2024-01-15 22:45:30.000",
    "started[s]": 1_705_339_845,  # 2024-01-15 17:30:45 UTC
    "ended[s]": 1_705_355_130,    # 2024-01-15 21:45:30 UTC
    "reason": 1,
    "Serial": "12345678",
}


class KebaClientTests(TestCase):
    @patch("charging.keba.socket.socket")
    def test_request_sends_command_and_parses_json(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_sock.recvfrom.return_value = (
            b'{"ID": "100", "Session ID": 1}',
            ("192.0.2.10", 7090),
        )
        mock_socket_cls.return_value.__enter__.return_value = mock_sock

        client = KebaClient("192.0.2.10")
        result = client.request("report 100")

        mock_sock.settimeout.assert_called_once_with(2.0)
        mock_sock.sendto.assert_called_once_with(
            b"report 100", ("192.0.2.10", 7090)
        )
        self.assertEqual(result, {"ID": "100", "Session ID": 1})

    @patch("charging.keba.socket.socket")
    def test_request_raises_keba_error_on_invalid_json(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_sock.recvfrom.return_value = (b"not json", ("192.0.2.10", 7090))
        mock_socket_cls.return_value.__enter__.return_value = mock_sock

        client = KebaClient("192.0.2.10")
        with self.assertRaises(KebaError):
            client.request("report 100")


class IngestSessionReportTests(TestCase):
    def test_creates_new_session(self):
        obj, created = ingest_session_report(SAMPLE_REPORT)

        self.assertTrue(created)
        self.assertEqual(obj.keba_session_id, 42)
        self.assertEqual(obj.energy_kwh, Decimal("56.789"))
        self.assertEqual(
            obj.started_at,
            datetime(2024, 1, 15, 17, 30, 45, tzinfo=timezone.utc),
        )
        self.assertEqual(
            obj.ended_at,
            datetime(2024, 1, 15, 21, 45, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(obj.end_reason, "1")
        self.assertEqual(obj.raw_report, SAMPLE_REPORT)

    def test_updates_existing_session_idempotently(self):
        ingest_session_report(SAMPLE_REPORT)
        updated_payload = dict(SAMPLE_REPORT, **{"E pres": 600_000, "reason": 10})

        obj, created = ingest_session_report(updated_payload)

        self.assertFalse(created)
        self.assertEqual(obj.energy_kwh, Decimal("60.000"))
        self.assertEqual(obj.end_reason, "10")
        self.assertEqual(ChargingSession.objects.count(), 1)

    def test_skips_empty_slot(self):
        empty = {"ID": "115", "Session ID": 0}

        obj, created = ingest_session_report(empty)

        self.assertIsNone(obj)
        self.assertFalse(created)
        self.assertEqual(ChargingSession.objects.count(), 0)

    def test_running_session_has_no_ended_at_or_reason(self):
        running = dict(SAMPLE_REPORT, **{"ended[s]": 0, "reason": 0})

        obj, created = ingest_session_report(running)

        self.assertTrue(created)
        self.assertIsNone(obj.ended_at)
        self.assertEqual(obj.end_reason, "")
