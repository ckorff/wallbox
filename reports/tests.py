from datetime import datetime, timezone

from django.db import IntegrityError
from django.test import TestCase

from reports.models import MonthlyReport


class MonthlyReportTests(TestCase):
    def test_create_with_defaults(self):
        report = MonthlyReport.objects.create(year=2026, month=5)
        report.refresh_from_db()
        self.assertEqual(report.send_status, MonthlyReport.SendStatus.PENDING)
        self.assertIsNone(report.sent_at)
        self.assertFalse(report.pdf)

    def test_year_month_is_unique(self):
        MonthlyReport.objects.create(year=2026, month=5)
        with self.assertRaises(IntegrityError):
            MonthlyReport.objects.create(year=2026, month=5)

    def test_month_below_1_is_rejected(self):
        with self.assertRaises(IntegrityError):
            MonthlyReport.objects.create(year=2026, month=0)

    def test_month_above_12_is_rejected(self):
        with self.assertRaises(IntegrityError):
            MonthlyReport.objects.create(year=2026, month=13)

    def test_can_mark_as_sent(self):
        report = MonthlyReport.objects.create(year=2026, month=5)
        sent_at = datetime(2026, 6, 1, 8, 30, tzinfo=timezone.utc)
        report.send_status = MonthlyReport.SendStatus.SENT
        report.sent_at = sent_at
        report.save()
        report.refresh_from_db()
        self.assertEqual(report.send_status, MonthlyReport.SendStatus.SENT)
        self.assertEqual(report.sent_at, sent_at)
