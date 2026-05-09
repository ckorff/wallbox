"""Tests for the /reports/ view and the generate_report command (Phase 2.4)."""
from datetime import date, datetime
from decimal import Decimal
from io import StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from charging.models import ChargingSession, MonthlyReport, Tariff


BERLIN = ZoneInfo("Europe/Berlin")


def _session(started_local, energy_kwh, ended_local=None, serial="KEBA-1"):
    return ChargingSession.objects.create(
        serial=serial,
        started_at=started_local,
        ended_at=ended_local,
        energy_kwh=Decimal(energy_kwh),
        raw_row={},
    )


def _dt(year, month, day, hour=12, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=BERLIN)


def _make_staff_user():
    User = get_user_model()
    return User.objects.create_user(
        username="staff",
        password="hunter2",
        is_staff=True,
    )


class ReportsViewAccessTests(TestCase):
    def test_anonymous_redirected(self):
        response = self.client.get(reverse("reports_index"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)

    def test_staff_can_access(self):
        user = _make_staff_user()
        self.client.force_login(user)

        response = self.client.get(reverse("reports_index"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "charging/reports.html")


class ReportsViewListingTests(TestCase):
    def setUp(self):
        self.user = _make_staff_user()
        self.client.force_login(self.user)

    def test_entries_cover_session_months_and_existing_reports(self):
        # Sessions in three distinct months
        _session(_dt(2026, 3, 12), Decimal("2.000"))
        _session(_dt(2026, 5, 15), Decimal("4.000"))
        _session(_dt(2026, 5, 20), Decimal("3.000"))
        _session(_dt(2026, 6, 1), Decimal("1.500"))

        # An existing report row in a month with NO session of its own
        MonthlyReport.objects.create(
            year=2025,
            month=12,
            wallbox_kwh_total=Decimal("0.000"),
            energy_cost_eur=Decimal("0.00"),
            total_amount_eur=Decimal("0.00"),
        )

        response = self.client.get(reverse("reports_index"))
        entries = response.context["entries"]

        keys = [(e["year"], e["month"]) for e in entries]
        self.assertEqual(
            keys,
            [(2026, 6), (2026, 5), (2026, 3), (2025, 12)],
        )
        # Each entry has the documented shape
        for entry in entries:
            self.assertIn("year", entry)
            self.assertIn("month", entry)
            self.assertIn("report", entry)
            self.assertIn("has_pdf", entry)

    def test_session_local_time_drives_month_grouping(self):
        # Late-evening 2026-05-31 Berlin -> May; just-after-midnight 2026-06-01 -> June
        _session(datetime(2026, 5, 31, 23, 50, tzinfo=BERLIN), Decimal("3.000"))
        _session(datetime(2026, 6, 1, 0, 1, tzinfo=BERLIN), Decimal("4.000"))

        response = self.client.get(reverse("reports_index"))
        entries = response.context["entries"]
        keys = {(e["year"], e["month"]) for e in entries}

        self.assertIn((2026, 5), keys)
        self.assertIn((2026, 6), keys)

    def test_has_pdf_false_when_no_report(self):
        _session(_dt(2026, 5, 10), Decimal("1.000"))
        response = self.client.get(reverse("reports_index"))
        entries = response.context["entries"]

        self.assertEqual(len(entries), 1)
        self.assertIsNone(entries[0]["report"])
        self.assertFalse(entries[0]["has_pdf"])


class ReportsViewGenerateTests(TestCase):
    def setUp(self):
        self.user = _make_staff_user()
        self.client.force_login(self.user)
        Tariff.objects.create(
            valid_from=date(2026, 1, 1),
            energy_price_ct_per_kwh=Decimal("38.500"),
        )
        _session(_dt(2026, 5, 10), Decimal("4.000"))

    def test_post_triggers_generation_and_attach(self):
        with patch(
            "charging.views.attach_pdf_to_report",
            side_effect=lambda r: r,
        ) as attach_mock, patch(
            "charging.views.generate_monthly_report",
            wraps=__import__(
                "charging.services.reports", fromlist=["generate_monthly_report"]
            ).generate_monthly_report,
        ) as generate_mock:
            response = self.client.post(
                reverse("reports_index"),
                {"year": "2026", "month": "5"},
                follow=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("reports_index"))

        generate_mock.assert_called_once_with(2026, 5)
        attach_mock.assert_called_once()
        # The report passed to attach_pdf_to_report must be the generated one
        attached = attach_mock.call_args.args[0]
        self.assertIsInstance(attached, MonthlyReport)
        self.assertEqual((attached.year, attached.month), (2026, 5))

        # And a success message naming the month should be queued.
        followed = self.client.get(reverse("reports_index"))
        msgs = [str(m) for m in followed.context["messages"]]
        self.assertTrue(
            any("May 2026" in m for m in msgs),
            f"Expected a 'May 2026' success message, got {msgs!r}",
        )

    def test_post_missing_tariff_renders_error_no_report(self):
        # New session in a month with no tariff coverage
        _session(_dt(2025, 12, 5), Decimal("2.000"))

        with patch(
            "charging.views.attach_pdf_to_report"
        ) as attach_mock:
            response = self.client.post(
                reverse("reports_index"),
                {"year": "2025", "month": "12"},
            )

        # No PDF attach attempted
        attach_mock.assert_not_called()
        # No report row created
        self.assertFalse(
            MonthlyReport.objects.filter(year=2025, month=12).exists()
        )
        # Re-render (200) with an error message naming the missing date
        self.assertEqual(response.status_code, 200)
        msgs = [str(m) for m in response.context["messages"]]
        joined = " ".join(msgs).lower()
        self.assertTrue(
            any("2025-12" in m or "december 2025" in m.lower() for m in msgs),
            f"Expected the missing-tariff date in the error, got {msgs!r}",
        )
        self.assertIn("tariff", joined)

    def test_post_invalid_month_re_renders(self):
        response = self.client.post(
            reverse("reports_index"),
            {"year": "2026", "month": "13"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(MonthlyReport.objects.exists())


class GenerateReportCommandTests(TestCase):
    def setUp(self):
        Tariff.objects.create(
            valid_from=date(2026, 1, 1),
            energy_price_ct_per_kwh=Decimal("38.500"),
        )

    def test_runs_and_attaches_pdf(self):
        _session(_dt(2026, 5, 10), Decimal("4.000"))

        out = StringIO()
        with patch(
            "charging.management.commands.generate_report.attach_pdf_to_report",
            side_effect=lambda r: r,
        ) as attach_mock:
            call_command(
                "generate_report",
                "--year",
                "2026",
                "--month",
                "5",
                stdout=out,
            )

        self.assertTrue(
            MonthlyReport.objects.filter(year=2026, month=5).exists()
        )
        attach_mock.assert_called_once()
        # Output mentions the resolved total
        self.assertIn("2026-05", out.getvalue())

    def test_missing_args_raises(self):
        with self.assertRaises(CommandError):
            call_command("generate_report")

    def test_out_of_range_month_raises_no_db_write(self):
        with self.assertRaises(CommandError):
            call_command(
                "generate_report", "--year", "2026", "--month", "13"
            )
        self.assertFalse(MonthlyReport.objects.exists())

        with self.assertRaises(CommandError):
            call_command(
                "generate_report", "--year", "2026", "--month", "0"
            )
        self.assertFalse(MonthlyReport.objects.exists())

    def test_missing_tariff_raises_command_error(self):
        # Session in a month with no covering tariff
        _session(_dt(2025, 12, 5), Decimal("2.000"))

        with patch(
            "charging.management.commands.generate_report.attach_pdf_to_report"
        ) as attach_mock:
            with self.assertRaises(CommandError) as ctx:
                call_command(
                    "generate_report", "--year", "2025", "--month", "12"
                )

        attach_mock.assert_not_called()
        self.assertIn("2025-12", str(ctx.exception))
        self.assertFalse(
            MonthlyReport.objects.filter(year=2025, month=12).exists()
        )
