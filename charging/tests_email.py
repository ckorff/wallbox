"""Tests for the Phase 3 email-delivery path (service + Reports view)."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from charging.models import AppSettings, MonthlyReport
from charging.services.email import (
    ReportEmailError,
    build_report_email,
    send_report_email,
)


def _make_staff_user():
    User = get_user_model()
    return User.objects.create_user(
        username="staff", password="hunter2", is_staff=True
    )


def _report_with_pdf(year=2026, month=5):
    report = MonthlyReport.objects.create(
        year=year,
        month=month,
        wallbox_kwh_total=Decimal("12.345"),
        energy_cost_eur=Decimal("4.75"),
        total_amount_eur=Decimal("4.75"),
    )
    report.pdf.save(
        f"report-{year}-{month:02d}.pdf", ContentFile(b"%PDF-1.4 dummy")
    )
    return report


@override_settings(
    MEDIA_ROOT="/tmp/wallbox-test-media-email",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_HOST="smtp.example.com",
    DEFAULT_FROM_EMAIL="wallbox@example.com",
)
class SendReportEmailServiceTests(TestCase):
    def setUp(self):
        s = AppSettings.current()
        s.report_recipient_email = "hr@example.com"
        s.save()
        self.report = _report_with_pdf()

    def test_sends_to_configured_recipient_with_pdf_attached(self):
        sent = send_report_email(self.report)

        self.assertEqual(sent.recipient, "hr@example.com")
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ["hr@example.com"])
        self.assertEqual(msg.from_email, "wallbox@example.com")
        self.assertEqual(msg.subject, "Charging report — May 2026")
        # Exactly one attachment, named after the month and carrying the PDF
        # bytes verbatim — re-encoding would invalidate the MVA signature
        # downstream if HR ever re-derives a fingerprint from the file.
        self.assertEqual(len(msg.attachments), 1)
        name, content, mime = msg.attachments[0]
        self.assertEqual(name, "charging-report-2026-05.pdf")
        self.assertEqual(content, b"%PDF-1.4 dummy")
        self.assertEqual(mime, "application/pdf")

    def test_body_mentions_month_and_totals(self):
        message = build_report_email(self.report, "hr@example.com")
        self.assertIn("May 2026", message.body)
        self.assertIn("12.345", message.body)
        self.assertIn("4.75", message.body)

    def test_missing_recipient_raises(self):
        s = AppSettings.current()
        s.report_recipient_email = ""
        s.save()
        with self.assertRaises(ReportEmailError) as ctx:
            send_report_email(self.report)
        self.assertIn("recipient", str(ctx.exception).lower())
        self.assertEqual(mail.outbox, [])

    def test_missing_pdf_raises(self):
        bare = MonthlyReport.objects.create(
            year=2026,
            month=4,
            wallbox_kwh_total=Decimal("0.000"),
            energy_cost_eur=Decimal("0.00"),
            total_amount_eur=Decimal("0.00"),
        )
        with self.assertRaises(ReportEmailError) as ctx:
            send_report_email(bare)
        self.assertIn("PDF", str(ctx.exception))
        self.assertEqual(mail.outbox, [])

    @override_settings(EMAIL_HOST="")
    def test_missing_smtp_host_raises(self):
        with self.assertRaises(ReportEmailError) as ctx:
            send_report_email(self.report)
        self.assertIn("SMTP", str(ctx.exception))
        self.assertEqual(mail.outbox, [])


@override_settings(
    MEDIA_ROOT="/tmp/wallbox-test-media-email-view",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_HOST="smtp.example.com",
    DEFAULT_FROM_EMAIL="wallbox@example.com",
)
class ReportsViewSendEmailTests(TestCase):
    def setUp(self):
        self.user = _make_staff_user()
        self.client.force_login(self.user)
        s = AppSettings.current()
        s.report_recipient_email = "hr@example.com"
        s.save()
        self.report = _report_with_pdf()

    def test_send_email_action_sends_and_redirects(self):
        response = self.client.post(
            reverse("reports_index"),
            {"action": "send_email", "year": "2026", "month": "5"},
            follow=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("reports_index"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["hr@example.com"])

        followed = self.client.get(reverse("reports_index"))
        msgs = [str(m) for m in followed.context["messages"]]
        self.assertTrue(
            any("hr@example.com" in m for m in msgs),
            f"Expected recipient in flash message, got {msgs!r}",
        )

    def test_send_email_no_report_renders_error(self):
        response = self.client.post(
            reverse("reports_index"),
            {"action": "send_email", "year": "2026", "month": "4"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mail.outbox, [])
        msgs = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("no report" in m.lower() for m in msgs), msgs)

    def test_send_email_no_recipient_renders_error(self):
        s = AppSettings.current()
        s.report_recipient_email = ""
        s.save()
        response = self.client.post(
            reverse("reports_index"),
            {"action": "send_email", "year": "2026", "month": "5"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mail.outbox, [])
        msgs = [str(m) for m in response.context["messages"]]
        self.assertTrue(
            any("recipient" in m.lower() for m in msgs),
            f"Expected a recipient-related error, got {msgs!r}",
        )

    def test_button_renders_when_recipient_set_and_pdf_exists(self):
        response = self.client.get(reverse("reports_index"))
        self.assertContains(response, "Send by email")
        self.assertContains(response, "hr@example.com")
        self.assertContains(response, 'name="action" value="send_email"')

    def test_button_disabled_when_recipient_missing(self):
        s = AppSettings.current()
        s.report_recipient_email = ""
        s.save()
        response = self.client.get(reverse("reports_index"))
        # The "Send by email" label still appears, but the form is locked
        # to the disabled-style <span> (no submit input for that action).
        self.assertContains(response, "Send by email")
        self.assertNotContains(response, 'name="action" value="send_email"')
