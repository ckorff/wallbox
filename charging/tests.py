import json
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase

from charging.keba_csv import parse_sessions_csv
from charging.keba_http import (
    KebaAuthError,
    _extract_csrf_token,
    fetch_sessions_csv,
)
from charging.models import ChargingSession
from charging.services import ingest_csv_row


SAMPLE_CSV = (
    "Charging Station ID;Serial;RFID Card;Status;Start;End;Duration (s);"
    "Meter at start (Wh);Meter at end (Wh);Consumption (kWh)\r\n"
    "1;34416115;predefinedTokenId;CLOSED;07-05-2026 15:33:35;"
    "07-05-2026 19:18:53;13518;154.9;27797.0;27.64\r\n"
    "1;34416115;044115CA911E94;CLOSED;07-05-2026 15:31:39;"
    "07-05-2026 15:32:14;34;154.9;154.9;0\r\n"
)
BERLIN = ZoneInfo("Europe/Berlin")


LOGIN_HTML = (
    '<html><head>'
    '<meta name="csrf-token" content="abc123def456">'
    '</head><body>Login</body></html>'
)


class ExtractCsrfTokenTests(TestCase):
    def test_extracts_token_from_meta(self):
        self.assertEqual(_extract_csrf_token(LOGIN_HTML), "abc123def456")

    def test_raises_when_token_missing(self):
        with self.assertRaises(KebaAuthError):
            _extract_csrf_token("<html><body>no token here</body></html>")


class FetchSessionsCsvTests(TestCase):
    @patch("charging.keba_http._open")
    def test_full_login_flow_fetches_csv(self, mock_open):
        mock_open.side_effect = [LOGIN_HTML, '{"status":"ok"}', SAMPLE_CSV]

        body = fetch_sessions_csv("192.0.2.10", "user", "pw")

        self.assertEqual(body, SAMPLE_CSV)
        calls = mock_open.call_args_list
        self.assertEqual(len(calls), 3)
        # 1) GET /
        self.assertEqual(calls[0].args[1], "http://192.0.2.10/")
        # 2) POST /ajax.php with JSON containing the CSRF token from step 1
        self.assertEqual(calls[1].args[1], "http://192.0.2.10/ajax.php")
        login_payload = json.loads(calls[1].kwargs["data"].decode("utf-8"))
        self.assertEqual(
            login_payload,
            {"username": "user", "password": "pw", "csrftoken": "abc123def456"},
        )
        # 3) GET /export.php with cache buster
        export_url = calls[2].args[1]
        self.assertTrue(
            export_url.startswith("http://192.0.2.10/export.php?chargingsessions=&t=")
        )
        cache_buster = export_url.rsplit("=", 1)[1]
        self.assertTrue(cache_buster.isdigit() and int(cache_buster) > 0)

    @patch("charging.keba_http._open")
    def test_passes_timeout_to_every_request(self, mock_open):
        mock_open.side_effect = [LOGIN_HTML, "{}", SAMPLE_CSV]

        fetch_sessions_csv("192.0.2.10", "u", "p", timeout=7.5)

        for call in mock_open.call_args_list:
            self.assertEqual(call.kwargs["timeout"], 7.5)

    @patch("charging.keba_http._open")
    def test_raises_when_export_returns_html(self, mock_open):
        mock_open.side_effect = [
            LOGIN_HTML,
            "{}",
            "<!DOCTYPE html><html>Login</html>",
        ]

        with self.assertRaises(KebaAuthError):
            fetch_sessions_csv("192.0.2.10", "wrong", "creds")


class ParseSessionsCsvTests(TestCase):
    def test_parses_two_rows_with_expected_columns(self):
        rows = parse_sessions_csv(SAMPLE_CSV)

        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(first["Serial"], "34416115")
        self.assertEqual(first["Start"], "07-05-2026 15:33:35")
        self.assertEqual(first["End"], "07-05-2026 19:18:53")
        self.assertEqual(first["Consumption (kWh)"], "27.64")
        self.assertEqual(first["Status"], "CLOSED")

    def test_returns_empty_for_empty_input(self):
        self.assertEqual(parse_sessions_csv(""), [])

    def test_returns_empty_when_only_header_present(self):
        header_only = SAMPLE_CSV.split("\r\n", 1)[0] + "\r\n"

        self.assertEqual(parse_sessions_csv(header_only), [])


class IngestCsvRowTests(TestCase):
    def _row(self, **overrides):
        base = {
            "Charging Station ID": "1",
            "Serial": "34416115",
            "RFID Card": "044115CA911E94",
            "Status": "CLOSED",
            "Start": "07-05-2026 15:33:35",
            "End": "07-05-2026 19:18:53",
            "Duration (s)": "13518",
            "Meter at start (Wh)": "154.9",
            "Meter at end (Wh)": "27797.0",
            "Consumption (kWh)": "27.64",
        }
        base.update(overrides)
        return base

    def test_creates_new_session(self):
        row = self._row()

        obj, created = ingest_csv_row(row)

        self.assertTrue(created)
        self.assertEqual(obj.serial, "34416115")
        self.assertEqual(obj.energy_kwh, Decimal("27.640"))
        self.assertEqual(
            obj.started_at,
            datetime(2026, 5, 7, 15, 33, 35, tzinfo=BERLIN),
        )
        self.assertEqual(
            obj.ended_at,
            datetime(2026, 5, 7, 19, 18, 53, tzinfo=BERLIN),
        )
        self.assertEqual(obj.raw_row, row)

    def test_re_import_updates_existing_row(self):
        ingest_csv_row(self._row())

        obj, created = ingest_csv_row(self._row(**{"Consumption (kWh)": "27.65"}))

        self.assertFalse(created)
        self.assertEqual(obj.energy_kwh, Decimal("27.650"))
        self.assertEqual(ChargingSession.objects.count(), 1)

    def test_skips_zero_kwh_touch_session(self):
        obj, created = ingest_csv_row(self._row(**{"Consumption (kWh)": "0"}))

        self.assertIsNone(obj)
        self.assertFalse(created)
        self.assertEqual(ChargingSession.objects.count(), 0)

    def test_skips_zero_kwh_with_decimal_zero(self):
        obj, created = ingest_csv_row(self._row(**{"Consumption (kWh)": "0.000"}))

        self.assertIsNone(obj)
        self.assertFalse(created)
