"""Tests for the /dashboard/ view, root redirect and base-template migration."""
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import ANY, patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from charging.models import ChargingSession, MonthlyReport, Tariff
from charging.services.import_runner import ImportResult


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

    def test_empty_state_shows_placeholders(self):
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "charging/dashboard.html")
        self.assertContains(response, "no sessions yet")
        self.assertContains(response, "no reports yet")
        self.assertEqual(response.context["session_total"], 0)
        self.assertEqual(response.context["total_kwh"], Decimal("0"))


class DashboardWithDataTests(TestCase):
    def setUp(self):
        self.client.force_login(_staff_user())
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

    def test_renders_counts_total_last_session_and_report_month(self):
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        # Counts and totals
        self.assertEqual(response.context["session_total"], 3)
        self.assertEqual(response.context["total_kwh"], Decimal("37.140"))
        # Last session is the most recent by started_at (May 7)
        self.assertEqual(
            response.context["last_session"].started_at,
            datetime(2026, 5, 7, 15, 33, tzinfo=BERLIN),
        )
        # Latest report month appears (2026-04)
        self.assertContains(response, "2026-04")
        # And the human-readable last-session date appears
        self.assertContains(response, "07.05.2026")


class DashboardRunImportTests(TestCase):
    def setUp(self):
        self.client.force_login(_staff_user())

    def test_post_run_import_success_message_redirects(self):
        with patch(
            "charging.views.run_keba_import",
            return_value=ImportResult(
                sessions_imported=3, sessions_skipped=2, sessions_updated=0
            ),
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
        self.assertTrue(
            any("3" in m for m in msgs),
            f"Expected success message containing '3', got {msgs!r}",
        )

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
        # rendered class is "log info".
        self.assertContains(followed, 'class="log info"')
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


class BaseTemplateMigrationTests(TestCase):
    """Smoke tests proving /reports/ and /settings/tariff/ still render
    after switching to the shared base template."""

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
        self.assertContains(response, 'href="/settings/tariff/"')

    def test_tariff_page_renders_with_nav(self):
        response = self.client.get(reverse("tariff_settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="/dashboard/"')
        self.assertContains(response, 'href="/reports/"')
        self.assertContains(response, 'href="/settings/tariff/"')
