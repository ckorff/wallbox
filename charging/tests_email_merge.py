"""Tests for the tariff-PDF merge on outgoing report emails."""
import io
import tempfile
from datetime import date
from decimal import Decimal

from django.core import mail
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from pypdf import PdfReader, PdfWriter

from charging.models import AppSettings, MonthlyReport, Tariff
from charging.services.email import send_report_email
from charging.services.pdf_merge import merge_report_with_tariff


def _make_pdf_bytes(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _tariff(*, valid_from, provider="Vattenfall", pages=None):
    """Create a Tariff with an optional PDF (omit for price-only rows)."""
    pdf = None
    if pages is not None:
        pdf = ContentFile(
            _make_pdf_bytes(pages),
            name=f"{provider.lower()}-{valid_from}.pdf",
        )
    return Tariff.objects.create(
        valid_from=valid_from,
        energy_price_ct_per_kwh=Decimal("38.500"),
        provider_name=provider,
        pdf=pdf or "",
    )


def _report_with_real_pdf(year=2026, month=5, pages=1):
    report = MonthlyReport.objects.create(
        year=year,
        month=month,
        wallbox_kwh_total=Decimal("12.345"),
        energy_cost_eur=Decimal("4.75"),
        total_amount_eur=Decimal("4.75"),
    )
    report.pdf.save(
        f"report-{year}-{month:02d}.pdf",
        ContentFile(_make_pdf_bytes(pages)),
    )
    return report


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="wallbox-test-merge-"))
class MergeReportWithTariffTests(TestCase):
    def test_combined_page_count_is_sum_of_inputs(self):
        report = _report_with_real_pdf(pages=2)
        tariff = _tariff(valid_from=date(2026, 5, 1), pages=3)

        merged = merge_report_with_tariff(report.pdf.path, tariff.pdf.path)
        reader = PdfReader(merged)
        self.assertEqual(len(reader.pages), 5)

    def test_order_is_report_first_then_tariff(self):
        report = _report_with_real_pdf(pages=1)
        tariff = _tariff(valid_from=date(2026, 5, 1), pages=4)

        # If order were tariff-then-report the first page would be one of
        # the tariff blanks. We can't easily diff blank pages, so we re-run
        # the merge with swapped args and verify the byte streams differ.
        forward = merge_report_with_tariff(report.pdf.path, tariff.pdf.path).getvalue()
        reverse = merge_report_with_tariff(tariff.pdf.path, report.pdf.path).getvalue()
        self.assertNotEqual(forward, reverse)

        # And the forward stream's page count matches report+tariff.
        self.assertEqual(len(PdfReader(io.BytesIO(forward)).pages), 5)


@override_settings(
    MEDIA_ROOT=tempfile.mkdtemp(prefix="wallbox-test-merge-send-"),
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_HOST="smtp.example.com",
    DEFAULT_FROM_EMAIL="wallbox@example.com",
)
class SendReportEmailMergesTariffTests(TestCase):
    def setUp(self):
        s = AppSettings.current()
        s.report_recipient_email = "hr@example.com"
        s.save()

    def test_send_attaches_merged_pdf_when_active_tariff_has_pdf(self):
        report = _report_with_real_pdf(pages=2)
        _tariff(valid_from=date(2026, 5, 1), pages=3)

        sent = send_report_email(report)

        self.assertTrue(sent.tariff_attached)
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(len(msg.attachments), 1)
        name, content, mime = msg.attachments[0]
        self.assertEqual(name, "charging-report-2026-05.pdf")
        self.assertEqual(mime, "application/pdf")
        # Merged: 2 (report) + 3 (tariff) = 5 pages.
        reader = PdfReader(io.BytesIO(content))
        self.assertEqual(len(reader.pages), 5)

    def test_send_without_active_tariff_attaches_bare_report(self):
        report = _report_with_real_pdf(pages=2)
        # No tariff at all → bare report goes out.

        sent = send_report_email(report)

        self.assertFalse(sent.tariff_attached)
        msg = mail.outbox[0]
        name, content, mime = msg.attachments[0]
        self.assertEqual(name, "charging-report-2026-05.pdf")
        reader = PdfReader(io.BytesIO(content))
        self.assertEqual(len(reader.pages), 2)

    def test_send_without_pdf_on_active_tariff_attaches_bare_report(self):
        # Tariff row exists for May but carries no PDF — equivalent to
        # the "no document on file" state, so report ships bare.
        _tariff(valid_from=date(2026, 5, 1), pages=None)
        report = _report_with_real_pdf(pages=2)

        sent = send_report_email(report)

        self.assertFalse(sent.tariff_attached)
        _, content, _ = mail.outbox[0].attachments[0]
        reader = PdfReader(io.BytesIO(content))
        self.assertEqual(len(reader.pages), 2)

    def test_send_uses_tariff_active_at_end_of_report_month(self):
        # Two tariffs: older one valid before May (PDF=1 page), newer
        # one from June (PDF=4 pages). For a May report the older one
        # must be picked.
        _tariff(valid_from=date(2026, 1, 1), provider="OldCo", pages=1)
        _tariff(valid_from=date(2026, 6, 1), provider="NewCo", pages=4)
        report = _report_with_real_pdf(pages=1)

        sent = send_report_email(report)

        self.assertTrue(sent.tariff_attached)
        _, content, _ = mail.outbox[0].attachments[0]
        reader = PdfReader(io.BytesIO(content))
        # report(1) + OldCo(1) = 2 — NewCo wasn't active yet.
        self.assertEqual(len(reader.pages), 2)
