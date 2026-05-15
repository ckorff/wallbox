import json
import stat
import tempfile
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase

from charging.keba_api import (
    KebaApiClient,
    KebaAuthError,
    KebaTruncatedError,
)
from charging.keba_csv import parse_sessions_csv
from charging.models import ChargingSession
from charging.services import ingest_csv_row, ingest_json_row


SAMPLE_CSV = (
    "Charging Station ID;Serial;RFID Card;Status;Start;End;Duration (s);"
    "Meter at start (Wh);Meter at end (Wh);Consumption (kWh)\r\n"
    "1;34416115;predefinedTokenId;CLOSED;07-05-2026 15:33:35;"
    "07-05-2026 19:18:53;13518;154.9;27797.0;27.64\r\n"
    "1;34416115;044115CA911E94;CLOSED;07-05-2026 15:31:39;"
    "07-05-2026 15:32:14;34;154.9;154.9;0\r\n"
)
BERLIN = ZoneInfo("Europe/Berlin")


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


def _login_body(access="ACC", refresh="REF"):
    return json.dumps(
        {"accessToken": access, "refreshToken": refresh}
    ).encode("utf-8")


class KebaApiClientTests(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self._tmp.name) / ".keba_token.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _client(self):
        return KebaApiClient(
            base_url="https://wb.test:8443",
            username="admin",
            password="pw",
            verify_tls=False,
            token_cache_path=self.cache,
        )

    def _seed_cache(self, access="ACC", refresh="REF"):
        self.cache.write_text(
            json.dumps(
                {
                    "access_token": access,
                    "refresh_token": refresh,
                    "obtained_at": time.time(),
                }
            )
        )

    @patch("charging.keba_api.KebaApiClient._request")
    def test_login_persists_tokens_with_mode_0600(self, mock_req):
        mock_req.return_value = (200, {}, _login_body("A1", "R1"))

        client = self._client()
        client.login()

        self.assertTrue(self.cache.exists())
        self.assertEqual(stat.S_IMODE(self.cache.stat().st_mode), 0o600)
        saved = json.loads(self.cache.read_text())
        self.assertEqual(saved["access_token"], "A1")
        self.assertEqual(saved["refresh_token"], "R1")
        self.assertIn("obtained_at", saved)
        call = mock_req.call_args
        self.assertEqual(call.args[0], "POST")
        self.assertEqual(call.args[1], "/v2/jwt/login")
        self.assertEqual(
            call.kwargs.get("json_body"),
            {"username": "admin", "password": "pw"},
        )
        # login itself must not carry an Authorization header
        self.assertIsNone(call.kwargs.get("auth"))

    @patch("charging.keba_api.KebaApiClient._request")
    def test_cached_token_used_without_login(self, mock_req):
        self._seed_cache(access="CACHED")
        mock_req.return_value = (200, {}, b'{"state":"IDLE"}')

        client = self._client()
        result = client.get_state("34416115")

        self.assertEqual(result, {"state": "IDLE"})
        mock_req.assert_called_once()
        call = mock_req.call_args
        self.assertEqual(call.args, ("GET", "/v2/wallboxes/34416115/state"))
        self.assertEqual(call.kwargs.get("auth"), "Bearer CACHED")

    @patch("charging.keba_api.KebaApiClient._request")
    def test_refresh_on_401_then_retry(self, mock_req):
        self._seed_cache(access="OLD", refresh="REF1")
        mock_req.side_effect = [
            (401, {}, b""),                          # original with OLD
            (200, {}, _login_body("NEW", "REF1")),   # refresh response
            (200, {}, b'{"state":"IDLE"}'),          # retry with NEW
        ]

        client = self._client()
        result = client.get_state("34416115")

        self.assertEqual(result, {"state": "IDLE"})
        self.assertEqual(mock_req.call_count, 3)
        # refresh call used the refresh token, not access
        refresh_call = mock_req.call_args_list[1]
        self.assertEqual(refresh_call.args, ("POST", "/v2/jwt/refresh"))
        self.assertEqual(refresh_call.kwargs.get("auth"), "Bearer REF1")
        # retry used the new access token
        retry_call = mock_req.call_args_list[2]
        self.assertEqual(retry_call.kwargs.get("auth"), "Bearer NEW")
        # cache rewritten with the new access token
        saved = json.loads(self.cache.read_text())
        self.assertEqual(saved["access_token"], "NEW")

    @patch("charging.keba_api.KebaApiClient._request")
    def test_refresh_expired_falls_back_to_login(self, mock_req):
        self._seed_cache(access="OLD", refresh="DEAD")
        mock_req.side_effect = [
            (401, {}, b""),                          # original
            (401, {}, b""),                          # refresh fails
            (200, {}, _login_body("FRESH", "REF2")), # login
            (200, {}, b'{"state":"IDLE"}'),          # retry
        ]

        client = self._client()
        result = client.get_state("34416115")

        self.assertEqual(result, {"state": "IDLE"})
        self.assertEqual(mock_req.call_count, 4)
        saved = json.loads(self.cache.read_text())
        self.assertEqual(saved["access_token"], "FRESH")
        self.assertEqual(saved["refresh_token"], "REF2")

    @patch("charging.keba_api.KebaApiClient._request")
    def test_auth_failure_after_relogin_raises(self, mock_req):
        self._seed_cache()
        mock_req.side_effect = [
            (401, {}, b""),                              # original
            (200, {}, _login_body("X", "REF")),          # refresh succeeds
            (401, {}, b""),                              # retry still 401
            (200, {}, _login_body("Y", "REF2")),         # login succeeds
            (401, {}, b""),                              # retry still 401
        ]

        client = self._client()
        with self.assertRaises(KebaAuthError):
            client.get_state("34416115")

    @patch("charging.keba_api.KebaApiClient._request")
    def test_export_sessions_csv_returns_bytes(self, mock_req):
        self._seed_cache()
        body = b"Charging Station ID;Serial\r\n1;34416115\r\n"
        mock_req.return_value = (
            200,
            {"Content-Length": str(len(body))},
            body,
        )

        client = self._client()
        result = client.export_sessions_csv()

        self.assertIsInstance(result, bytes)
        self.assertEqual(result, body)
        call = mock_req.call_args
        self.assertEqual(call.args, ("GET", "/v2/sessions/export"))

    @patch("charging.keba_api.KebaApiClient._request")
    def test_export_truncated_response_raises(self, mock_req):
        self._seed_cache()
        mock_req.return_value = (
            200,
            {"Content-Length": "1000"},
            b"way too short",
        )

        client = self._client()
        with self.assertRaises(KebaTruncatedError):
            client.export_sessions_csv()

    @patch("charging.keba_api.KebaApiClient._request")
    def test_export_no_content_length_skips_check(self, mock_req):
        # Chunked responses have no Content-Length; don't fake a check.
        self._seed_cache()
        body = b"Charging Station ID;Serial\r\n1;34416115\r\n"
        mock_req.return_value = (200, {}, body)

        client = self._client()
        self.assertEqual(client.export_sessions_csv(), body)

    @patch("charging.keba_api.KebaApiClient._request")
    def test_get_wallbox_info_returns_dict(self, mock_req):
        self._seed_cache()
        info = {
            "serialNumber": "34416115",
            "state": "IDLE",
            "mvaPublicKey": "{\"UK\":\"...\"}",
        }
        mock_req.return_value = (200, {}, json.dumps(info).encode("utf-8"))

        client = self._client()
        result = client.get_wallbox_info("34416115")

        self.assertEqual(result, info)
        call = mock_req.call_args
        self.assertEqual(call.args, ("GET", "/v2/wallboxes/34416115"))

    @patch("charging.keba_api.KebaApiClient._request")
    def test_list_sessions_returns_sessions_array(self, mock_req):
        self._seed_cache()
        payload = json.dumps(
            {
                "sessions": [
                    {
                        "id": 1,
                        "wallboxSerialNumber": "34416115",
                        "startDate": 1778707926397,
                        "endDate": 1778745614889,
                        "energyConsumedInKwh": 51.5707,
                    }
                ]
            }
        ).encode("utf-8")
        mock_req.return_value = (200, {}, payload)

        client = self._client()
        result = client.list_sessions()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["wallboxSerialNumber"], "34416115")
        call = mock_req.call_args
        self.assertEqual(call.args, ("GET", "/v2/sessions"))


class IngestJsonRowTests(TestCase):
    """Counterpart of IngestCsvRowTests for the /v2/sessions JSON shape."""

    def _row(self, **overrides):
        base = {
            "id": 591466681,
            "wallboxSerialNumber": "34416115",
            "tokenId": "044115CA911E94",
            "status": "CLOSED",
            "startDate": 1778707926397,   # 2026-05-13 23:32:06.397 CEST
            "endDate": 1778745614889,     # 2026-05-14 10:00:14.889 CEST
            "duration": 37688492,
            "energyConsumedInKwh": 27.64,
            "mvaRecordData": '{"FV":"1.1","GI":"KEBA_KCP30"}',
            "mvaRecordSignature": '{"SD":"3046..."}',
        }
        base.update(overrides)
        return base

    def test_creates_new_session_with_mva_fields(self):
        row = self._row()

        obj, created = ingest_json_row(row)

        self.assertTrue(created)
        self.assertEqual(obj.serial, "34416115")
        self.assertEqual(obj.energy_kwh, Decimal("27.640"))
        # Microseconds stripped so the (serial, started_at) natural key
        # aligns with the CSV path's second-precision timestamps.
        self.assertEqual(
            obj.started_at,
            datetime(2026, 5, 13, 23, 32, 6, tzinfo=BERLIN),
        )
        self.assertEqual(obj.started_at.microsecond, 0)
        self.assertEqual(
            obj.ended_at,
            datetime(2026, 5, 14, 10, 0, 14, tzinfo=BERLIN),
        )
        self.assertEqual(obj.raw_row, row)
        self.assertEqual(obj.mva_record_data, '{"FV":"1.1","GI":"KEBA_KCP30"}')
        self.assertEqual(obj.mva_record_signature, '{"SD":"3046..."}')

    def test_handles_missing_mva_fields(self):
        # Older firmware / not-yet-signed sessions might lack these.
        row = self._row()
        del row["mvaRecordData"]
        del row["mvaRecordSignature"]

        obj, created = ingest_json_row(row)

        self.assertTrue(created)
        self.assertIsNone(obj.mva_record_data)
        self.assertIsNone(obj.mva_record_signature)

    def test_skips_zero_kwh_touch_session(self):
        obj, created = ingest_json_row(self._row(energyConsumedInKwh=0))

        self.assertIsNone(obj)
        self.assertFalse(created)

    def test_re_import_updates_existing_row(self):
        ingest_json_row(self._row())

        obj, created = ingest_json_row(
            self._row(
                energyConsumedInKwh=27.65,
                mvaRecordData='{"UPDATED":"true"}',
            )
        )

        self.assertFalse(created)
        self.assertEqual(obj.energy_kwh, Decimal("27.650"))
        self.assertEqual(obj.mva_record_data, '{"UPDATED":"true"}')
        self.assertEqual(ChargingSession.objects.count(), 1)

    def test_winter_timestamp_uses_cet_not_cest(self):
        # DST safety: a January timestamp lands at UTC+01:00 in Berlin.
        expected = datetime(2026, 1, 15, 12, 0, 0, tzinfo=BERLIN)
        ms = int(expected.timestamp() * 1000)
        row = self._row(startDate=ms, endDate=ms + 1_000_000)

        obj, _ = ingest_json_row(row)

        self.assertEqual(obj.started_at, expected)


class WallboxKeyTests(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.key_path = Path(self._tmp.name) / "wallbox_mva_public_key.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_ensure_writes_file_then_is_idempotent(self):
        from unittest.mock import Mock

        from charging.services.wallbox_key import ensure_wallbox_key_archived

        client = Mock()
        client.get_wallbox_info.return_value = {
            "serialNumber": "34416115",
            "mvaPublicKey": '{"UK":"3059301306072A8648CE3D"}',
        }

        record = ensure_wallbox_key_archived(
            client, "34416115", path=self.key_path
        )

        self.assertEqual(record["wallbox_serial"], "34416115")
        self.assertEqual(record["public_key_hex"], "3059301306072A8648CE3D")
        self.assertTrue(self.key_path.exists())
        client.get_wallbox_info.assert_called_once_with("34416115")

        # Second call: read from disk, no additional API hit.
        again = ensure_wallbox_key_archived(
            client, "34416115", path=self.key_path
        )

        self.assertEqual(again, record)
        client.get_wallbox_info.assert_called_once()  # still 1

    def test_ensure_returns_none_when_wallbox_has_no_mva_key(self):
        from unittest.mock import Mock

        from charging.services.wallbox_key import ensure_wallbox_key_archived

        client = Mock()
        client.get_wallbox_info.return_value = {"serialNumber": "34416115"}

        result = ensure_wallbox_key_archived(
            client, "34416115", path=self.key_path
        )

        self.assertIsNone(result)
        self.assertFalse(self.key_path.exists())

    def test_fingerprint_is_lowercase_64char_sha256(self):
        import hashlib

        from charging.services.wallbox_key import public_key_fingerprint

        record = {"public_key_hex": "3059ABCD"}
        expected = hashlib.sha256(b"3059ABCD").hexdigest()

        result = public_key_fingerprint(record)

        self.assertEqual(result, expected)
        self.assertEqual(len(result), 64)
        self.assertTrue(result.islower())
