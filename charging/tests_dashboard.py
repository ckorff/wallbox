"""Tests for the /dashboard/ view, root redirect and base-template migration."""
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import ANY, patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from charging.models import AppSettings, ChargingSession, MonthlyReport, Tariff
from charging.services import import_runner
from charging.services.import_runner import ImportResult, run_keba_import
from charging.services.wallbox_state import LiveStateView


def _patch_live_state(test_case, view=None):
    """Stop fetch_live_state from touching the real wallbox during tests."""
    view = view or LiveStateView(not_linked=True)
    patcher = patch("charging.views.fetch_live_state", return_value=view)
    patcher.start()
    test_case.addCleanup(patcher.stop)


BERLIN = ZoneInfo("Europe/Berlin")


def _staff_user():
    User = get_user_model()
    return User.objects.create_user(
        username="staff", password="hunter2", is_staff=True
    )


def _session(started_local, energy_kwh, serial="KEBA-1"):
    return ChargingSession.objects.create(
        serial=serial,
        started_at=started_local,
        ended_at=None,
        energy_kwh=Decimal(energy_kwh),
        raw_row={},
    )


class RootRedirectTests(TestCase):
    def test_anonymous_root_redirects_to_login_via_dashboard(self):
        # Root redirects to /dashboard/, which then redirects anonymous
        # users to the admin login page.
        response = self.client.get("/", follow=True)
        # Last hop must be a login page.
        final_url = response.redirect_chain[-1][0]
        self.assertIn("login", final_url)

    def test_staff_root_redirects_to_dashboard(self):
        self.client.force_login(_staff_user())
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard"))


class DashboardEmptyStateTests(TestCase):
    def setUp(self):
        self.client.force_login(_staff_user())
        _patch_live_state(self)

    def test_empty_state_shows_placeholders(self):
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "charging/dashboard.html")
        # No imports yet → "Last imported" card shows "never"
        self.assertContains(response, "never")
        # No report PDFs yet → action card surfaces the missing-report hint
        self.assertContains(response, "No report PDF available yet")
        self.assertEqual(response.context["session_total"], 0)
        self.assertEqual(response.context["total_kwh"], Decimal("0"))
        self.assertIsNone(response.context["last_import_at"])


class DashboardWithDataTests(TestCase):
    def setUp(self):
        self.client.force_login(_staff_user())
        _patch_live_state(self)
        _session(datetime(2026, 5, 1, 10, 0, tzinfo=BERLIN), "4.000")
        _session(datetime(2026, 5, 7, 15, 33, tzinfo=BERLIN), "27.640")
        _session(datetime(2026, 4, 20, 12, 0, tzinfo=BERLIN), "5.500")
        MonthlyReport.objects.create(
            year=2026,
            month=4,
            wallbox_kwh_total=Decimal("5.500"),
            energy_cost_eur=Decimal("2.12"),
            total_amount_eur=Decimal("2.12"),
        )

    def test_renders_counts_total_and_report_month(self):
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        # Counts and totals
        self.assertEqual(response.context["session_total"], 3)
        self.assertEqual(response.context["total_kwh"], Decimal("37.140"))
        # Latest report month appears (2026-04) under the actions card
        self.assertContains(response, "2026-04")
        # The "Total" section replaces the old "Status" header
        self.assertContains(response, "Total")
        self.assertContains(response, "Total sessions")
        # "Last imported" now reflects AppSettings.last_import_at, not
        # the most recent session's started_at. With no import recorded
        # yet, the card shows "never".
        self.assertContains(response, "never")


class DashboardRunImportTests(TestCase):
    def setUp(self):
        self.client.force_login(_staff_user())
        _patch_live_state(self)

    def test_post_run_import_success_message_redirects(self):
        # Seed one existing session so "already known" and "total now" are
        # not trivially zero.
        _session(datetime(2026, 5, 1, 10, 0, tzinfo=BERLIN), "4.000")

        def _fake_import(*, log=None):
            # Simulate the side effect of a real import: insert one new row.
            _session(datetime(2026, 5, 8, 11, 0, tzinfo=BERLIN), "2.500")
            return ImportResult(
                sessions_imported=1,
                sessions_updated=1,
                sessions_skipped=0,
                rows_seen=2,
            )

        with patch(
            "charging.views.run_keba_import", side_effect=_fake_import
        ) as runner:
            response = self.client.post(
                reverse("dashboard"),
                {"action": "run_import"},
            )

        runner.assert_called_once_with(log=ANY)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard"))

        followed = self.client.get(reverse("dashboard"))
        msgs = [str(m) for m in followed.context["messages"]]
        joined = " ".join(msgs)
        self.assertIn("Import finished: 1 new", joined)
        self.assertIn("1 already known", joined)
        self.assertIn("total now 2 sessions", joined)

    def test_post_run_import_renders_per_row_log_block(self):
        def _fake_import(*, log):
            log("Fetched 711 bytes")
            log("Parsed 6 row(s) from CSV")
            log("  created  07-05-2026 15:33:35   27.64 kWh")
            log("  skipped  07-05-2026 15:31:39   0 kWh (RFID swipe)")
            return ImportResult(
                sessions_imported=1, sessions_skipped=1, rows_seen=2
            )

        with patch("charging.views.run_keba_import", side_effect=_fake_import):
            self.client.post(reverse("dashboard"), {"action": "run_import"})

        followed = self.client.get(reverse("dashboard"))
        # Log lines are emitted as a single info-tagged message with the
        # "log" extra tag. Django renders tags alphabetically, so the
        # message carries data-tags="log info" (used as a stable hook
        # for the monospace/pre-wrap styling in base.html).
        self.assertContains(followed, 'data-tags="log info"')
        self.assertContains(followed, "Fetched 711 bytes")
        self.assertContains(followed, "created  07-05-2026 15:33:35")

    def test_post_run_import_failure_queues_error_message(self):
        with patch(
            "charging.views.run_keba_import",
            side_effect=Exception("boom"),
        ):
            response = self.client.post(
                reverse("dashboard"),
                {"action": "run_import"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard"))

        followed = self.client.get(reverse("dashboard"))
        msgs = [str(m) for m in followed.context["messages"]]
        joined = " ".join(msgs)
        self.assertIn("boom", joined)


class DashboardLiveBlockTests(TestCase):
    """The new live-state and monthly-summary sections (Phase 2.9)."""

    def setUp(self):
        self.client.force_login(_staff_user())

    def test_idle_state_renders(self):
        _patch_live_state(
            self,
            LiveStateView(state="IDLE", fetched_at="2026-05-16T08:00:00+00:00"),
        )
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Live state")
        self.assertContains(response, "Idle")

    def test_charging_state_shows_power_and_glow(self):
        _patch_live_state(
            self,
            LiveStateView(
                state="CHARGING",
                power_w=11000,
                fetched_at="2026-05-16T08:00:00+00:00",
            ),
        )
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Charging")
        self.assertContains(response, "11000 W")
        # The live-state card carries the active-glow border when charging.
        self.assertContains(response, "shadow-glow")

    def test_stale_banner_shown_when_unreachable_with_cache(self):
        _patch_live_state(
            self,
            LiveStateView(
                state="IDLE",
                fetched_at="2026-05-15T18:00:00+00:00",
                stale=True,
                last_seen_at="2026-05-15T18:00:00+00:00",
                unreachable_reason="OSError: connection refused",
            ),
        )
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Last known state")
        self.assertContains(response, "2026-05-15T18:00:00+00:00")

    def test_not_linked_view_renders_hint(self):
        _patch_live_state(self, LiveStateView(not_linked=True))
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Wallbox not linked")
        self.assertContains(response, "Run an import once")

    def test_monthly_summary_section_renders(self):
        _patch_live_state(self)
        # Seed data so the section has numbers to display.
        Tariff.objects.create(
            valid_from=date(2026, 1, 1),
            energy_price_ct_per_kwh=Decimal("38.500"),
        )
        # Pass through to the real summary services — we only assert the
        # block renders, not specific numbers (those depend on real "now").
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        # Section is labelled with the current month name (e.g. "May 2026"),
        # and per-card labels carry the month name too.
        this_month_name = response.context["this_month_name"]
        self.assertContains(response, this_month_name)
        self.assertContains(response, f"Sessions in {this_month_name}")
        self.assertContains(response, f"Energy in {this_month_name}")
        self.assertContains(response, f"Cost in {this_month_name}")
        self.assertIn("this_month", response.context)
        self.assertIn("last_month", response.context)
        self.assertIn("trend", response.context)


class LastImportAtTests(TestCase):
    """The "Last imported" indicator is driven by AppSettings.last_import_at,
    which run_keba_import stamps after a successful run."""

    def test_successful_import_stamps_last_import_at(self):
        self.assertIsNone(AppSettings.current().last_import_at)

        with patch.object(
            import_runner, "_import_from_api", return_value=ImportResult()
        ):
            run_keba_import()

        self.assertIsNotNone(AppSettings.current().last_import_at)

    def test_failed_import_does_not_stamp(self):
        with patch.object(
            import_runner,
            "_import_from_api",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(RuntimeError):
                run_keba_import()

        self.assertIsNone(AppSettings.current().last_import_at)

    def test_dashboard_renders_last_imported_timestamp(self):
        app = AppSettings.current()
        app.last_import_at = datetime(2026, 5, 16, 14, 33, tzinfo=BERLIN)
        app.save()

        self.client.force_login(_staff_user())
        _patch_live_state(self)
        response = self.client.get(reverse("dashboard"))

        self.assertContains(response, "16.05.2026")
        self.assertContains(response, "14:33")


class BaseTemplateMigrationTests(TestCase):
    """Smoke tests proving /reports/ and /settings/ render via the shared
    base template with the expected nav links."""

    def setUp(self):
        self.client.force_login(_staff_user())
        Tariff.objects.create(
            valid_from=date(2026, 1, 1),
            energy_price_ct_per_kwh=Decimal("38.500"),
        )

    def test_reports_page_renders_with_nav(self):
        response = self.client.get(reverse("reports_index"))
        self.assertEqual(response.status_code, 200)
        # Nav links from the base template
        self.assertContains(response, 'href="/dashboard/"')
        self.assertContains(response, 'href="/reports/"')
        self.assertContains(response, 'href="/settings/"')

    def test_settings_page_renders_with_nav(self):
        with patch(
            "charging.views.fetch_wallbox_status",
            return_value={"archived": False},
        ):
            response = self.client.get(reverse("settings_page"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="/dashboard/"')
        self.assertContains(response, 'href="/reports/"')
        self.assertContains(response, 'href="/settings/"')
