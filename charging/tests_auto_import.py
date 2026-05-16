"""Tests for the per-dashboard-pageload auto-import (Phase 3, second half).

The shape under test is "wallbox is the source of truth for *new* rows":
on every dashboard load we ask the wallbox how many billable sessions
it has, compare to our DB count, and if higher we ingest the difference
without making a second network call.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from charging.models import AppSettings, ChargingSession
from charging.services.auto_import import (
    AutoImportOutcome,
    auto_import_if_new_sessions,
)
from charging.services.wallbox_state import LiveStateView


BERLIN = ZoneInfo("Europe/Berlin")


def _staff_user():
    User = get_user_model()
    return User.objects.create_user(
        username="staff", password="hunter2", is_staff=True
    )


def _session(serial, started_local, energy_kwh):
    """A pre-existing DB row, equivalent to one already ingested before."""
    return ChargingSession.objects.create(
        serial=serial,
        started_at=started_local,
        ended_at=None,
        energy_kwh=Decimal(energy_kwh),
        raw_row={},
    )


def _api_row(serial, started_at_ms, energy_kwh, *, with_mva=True):
    """Shape mirroring the /v2/sessions JSON entries used by ingest_json_row."""
    row = {
        "wallboxSerialNumber": serial,
        "startDate": started_at_ms,
        "endDate": started_at_ms + 30 * 60 * 1000,
        "energyConsumedInKwh": str(energy_kwh),
    }
    if with_mva:
        row["mvaRecordData"] = "{}"
        row["mvaRecordSignature"] = "{}"
    return row


def _ms(year, month, day, hour=12, minute=0):
    """Berlin-local wall time → UTC epoch ms (matches the wallbox JSON)."""
    dt = datetime(year, month, day, hour, minute, tzinfo=BERLIN)
    return int(dt.timestamp() * 1000)


@override_settings(
    KEBA_API_URL="https://wb.test:8443",
    KEBA_API_USERNAME="u",
    KEBA_API_PASSWORD="p",
    MEDIA_ROOT="/tmp/wallbox-test-media-auto-import",
)
class AutoImportServiceTests(TestCase):
    def _patch_client(self, rows, **kwargs):
        client = MagicMock()
        client.list_sessions.return_value = rows
        for k, v in kwargs.items():
            setattr(client, k, v)
        # ensure_wallbox_key_archived is a best-effort step that touches
        # the filesystem; stub it so tests stay hermetic.
        return patch.multiple(
            "charging.services.auto_import",
            build_keba_client=MagicMock(return_value=client),
            ensure_wallbox_key_archived=MagicMock(return_value=None),
        )

    def test_no_op_when_counts_match(self):
        _session("KEBA-1", datetime(2026, 5, 1, 9, 0, tzinfo=BERLIN), "4.000")

        rows = [_api_row("KEBA-1", _ms(2026, 5, 1, 9, 0), "4.000")]
        with self._patch_client(rows):
            out = auto_import_if_new_sessions()

        self.assertTrue(out.checked)
        self.assertEqual(out.wallbox_count, 1)
        self.assertEqual(out.db_count, 1)
        self.assertEqual(out.imported, 0)
        self.assertIsNone(out.error)
        # No second DB row created
        self.assertEqual(ChargingSession.objects.count(), 1)

    def test_imports_when_wallbox_has_more(self):
        _session("KEBA-1", datetime(2026, 5, 1, 9, 0, tzinfo=BERLIN), "4.000")

        rows = [
            _api_row("KEBA-1", _ms(2026, 5, 1, 9, 0), "4.000"),
            _api_row("KEBA-1", _ms(2026, 5, 2, 9, 0), "5.500"),
            _api_row("KEBA-1", _ms(2026, 5, 3, 9, 0), "3.250"),
        ]
        with self._patch_client(rows):
            out = auto_import_if_new_sessions()

        self.assertTrue(out.checked)
        self.assertEqual(out.wallbox_count, 3)
        self.assertEqual(out.imported, 2)
        self.assertEqual(out.db_count, 3)
        self.assertEqual(ChargingSession.objects.count(), 3)

    def test_zero_kwh_rows_dont_inflate_wallbox_count(self):
        """RFID-swipe rows (0 kWh) are filtered before the comparison.

        Otherwise the DB would *always* look "behind" since 0-kWh rows
        never become ChargingSession rows.
        """
        _session("KEBA-1", datetime(2026, 5, 1, 9, 0, tzinfo=BERLIN), "4.000")
        rows = [
            _api_row("KEBA-1", _ms(2026, 5, 1, 9, 0), "4.000"),
            _api_row("KEBA-1", _ms(2026, 5, 2, 9, 0), "0"),       # swipe
            _api_row("KEBA-1", _ms(2026, 5, 3, 9, 0), "0.000"),    # swipe
        ]
        with self._patch_client(rows):
            out = auto_import_if_new_sessions()

        self.assertEqual(out.wallbox_count, 1)  # billable only
        self.assertEqual(out.imported, 0)
        self.assertEqual(ChargingSession.objects.count(), 1)

    def test_updates_last_import_at_only_when_something_was_imported(self):
        AppSettings.current()  # singleton with last_import_at=None
        self.assertIsNone(AppSettings.current().last_import_at)

        # First call: nothing new → last_import_at stays None.
        with self._patch_client([]):
            auto_import_if_new_sessions()
        self.assertIsNone(AppSettings.current().last_import_at)

        # Second call: new row arrives → last_import_at is stamped.
        rows = [_api_row("KEBA-1", _ms(2026, 5, 1, 9, 0), "4.000")]
        with self._patch_client(rows):
            auto_import_if_new_sessions()
        self.assertIsNotNone(AppSettings.current().last_import_at)

    def test_network_error_returns_outcome_not_exception(self):
        with patch(
            "charging.services.auto_import.build_keba_client",
            side_effect=ConnectionError("flaky wlan"),
        ):
            out = auto_import_if_new_sessions()

        self.assertFalse(out.checked)
        self.assertEqual(out.imported, 0)
        self.assertIn("ConnectionError", out.error)
        self.assertIn("flaky wlan", out.error)

    def test_missing_credentials_returns_outcome_not_exception(self):
        # build_keba_client raises RuntimeError when no creds are configured.
        with patch(
            "charging.services.auto_import.build_keba_client",
            side_effect=RuntimeError("Wallbox API credentials missing"),
        ):
            out = auto_import_if_new_sessions()

        self.assertFalse(out.checked)
        self.assertIn("credentials", out.error.lower())


@override_settings(MEDIA_ROOT="/tmp/wallbox-test-media-auto-import-view")
class DashboardAutoImportIntegrationTests(TestCase):
    def setUp(self):
        self.user = _staff_user()
        self.client.force_login(self.user)
        # Stub live state so the dashboard renders without a real wallbox.
        live_patcher = patch(
            "charging.views.fetch_live_state",
            return_value=LiveStateView(not_linked=True),
        )
        live_patcher.start()
        self.addCleanup(live_patcher.stop)

    def test_dashboard_calls_auto_import(self):
        with patch(
            "charging.views.auto_import_if_new_sessions",
            return_value=AutoImportOutcome(
                checked=True, wallbox_count=0, db_count=0, imported=0, error=None
            ),
        ) as auto_mock:
            response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        auto_mock.assert_called_once_with()

    def test_imported_count_surfaces_as_success_message(self):
        with patch(
            "charging.views.auto_import_if_new_sessions",
            return_value=AutoImportOutcome(
                checked=True,
                wallbox_count=3,
                db_count=3,
                imported=2,
                error=None,
            ),
        ):
            response = self.client.get(reverse("dashboard"))
        msgs = [str(m) for m in response.context["messages"]]
        self.assertTrue(
            any("Auto-imported 2 new sessions" in m for m in msgs),
            f"Expected an auto-import success message, got {msgs!r}",
        )

    def test_no_message_when_nothing_imported(self):
        with patch(
            "charging.views.auto_import_if_new_sessions",
            return_value=AutoImportOutcome(
                checked=True, wallbox_count=5, db_count=5, imported=0, error=None
            ),
        ):
            response = self.client.get(reverse("dashboard"))
        msgs = [str(m) for m in response.context["messages"]]
        self.assertFalse(
            any("Auto-imported" in m for m in msgs),
            f"Expected no auto-import flash when imported=0, got {msgs!r}",
        )

    def test_dashboard_still_renders_when_auto_import_failed(self):
        with patch(
            "charging.views.auto_import_if_new_sessions",
            return_value=AutoImportOutcome(
                checked=False,
                wallbox_count=0,
                db_count=0,
                imported=0,
                error="ConnectionError: offline",
            ),
        ):
            response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        # No success/error flash — live_state UI is the unreachable signal.
        msgs = [str(m) for m in response.context["messages"]]
        self.assertFalse(any("Auto-import" in m for m in msgs))
