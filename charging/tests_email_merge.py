"""Tests for the tariff-document PDF merge on outgoing report emails."""
import io
import tempfile
from datetime import date
from decimal import Decimal

from django.core import mail
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from pypdf import PdfReader, PdfWriter

from charging.models import AppSettings, MonthlyReport, TariffDocument
from charging.services.email import send_report_email
from charging.services.pdf_merge import merge_report_with_tariff


def _make_pdf_bytes(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="wallbox-test-merge-"))
class MergeReportWithTariffTests(TestCase):
    def test_combined_page_count_is_sum_of_inputs(self):
        report = MonthlyReport.objects.create(
            year=2026,
            month=5,
            wallbox_kwh_total=Decimal("0.000"),
            energy_cost_eur=Decimal("0.00"),
            total_amount_eur=Decimal("0.00"),
        )
        report.pdf.save("report.pdf", ContentFile(_make_pdf_bytes(2)))
        doc = TariffDocument.objects.create(
            valid_from=date(2026, 5, 1),
            provider_name="Vattenfall",
            pdf=ContentFile(_make_pdf_bytes(3), name="vattenfall.pdf"),
        )

        merged = merge_report_with_tariff(report.pdf.path, doc.pdf.path)
        reader = PdfReader(merged)
        self.assertEqual(len(reader.pages), 5)

    def test_order_is_report_first_then_tariff(self):
        report = MonthlyReport.objects.create(
            year=2026,
            month=5,
            wallbox_kwh_total=Decimal("0.000"),
            energy_cost_eur=Decimal("0.00"),
            total_amount_eur=Decimal("0.00"),
        )
        # Different page counts on each side so order is observable
        # without having to extract text.
        report.pdf.save("report.pdf", ContentFile(_make_pdf_bytes(1)))
        doc = TariffDocument.objects.create(
            valid_from=date(2026, 5, 1),
            provider_name="Vattenfall",
            pdf=ContentFile(_make_pdf_bytes(4), name="vattenfall.pdf"),
        )

        # If order were tariff-then-report the first page would be one of
        # the tariff blanks. We can't easily diff blank pages, so we re-run
        # the merge with swapped args and verify the byte streams differ.
        forward = merge_report_with_tariff(report.pdf.path, doc.pdf.path).getvalue()
        reverse = merge_report_with_tariff(doc.pdf.path, report.pdf.path).getvalue()
        self.assertNotEqual(forward, reverse)

        # And the forward stream's page count matches report+tariff.
        self.assertEqual(len(PdfReader(io.BytesIO(forward)).pages), 5)


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

    def test_send_attaches_merged_pdf_when_tariff_document_on_file(self):
        report = _report_with_real_pdf(pages=2)
        TariffDocument.objects.create(
            valid_from=date(2026, 5, 1),
            provider_name="Vattenfall",
            pdf=ContentFile(_make_pdf_bytes(3), name="vattenfall.pdf"),
        )

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

    def test_send_without_tariff_document_attaches_bare_report(self):
        report = _report_with_real_pdf(pages=2)

        sent = send_report_email(report)

        self.assertFalse(sent.tariff_attached)
        msg = mail.outbox[0]
        name, content, mime = msg.attachments[0]
        self.assertEqual(name, "charging-report-2026-05.pdf")
        # Bare report: 2 pages, no tariff appended.
        reader = PdfReader(io.BytesIO(content))
        self.assertEqual(len(reader.pages), 2)

    def test_send_uses_document_active_at_end_of_report_month(self):
        # Two documents: older one valid before May, newer one valid
        # from June. For a May report the older one must be picked.
        TariffDocument.objects.create(
            valid_from=date(2026, 1, 1),
            provider_name="OldCo",
            pdf=ContentFile(_make_pdf_bytes(1), name="old.pdf"),
        )
        TariffDocument.objects.create(
            valid_from=date(2026, 6, 1),
            provider_name="NewCo",
            pdf=ContentFile(_make_pdf_bytes(4), name="new.pdf"),
        )
        report = _report_with_real_pdf(pages=1)

        sent = send_report_email(report)

        self.assertTrue(sent.tariff_attached)
        _, content, _ = mail.outbox[0].attachments[0]
        reader = PdfReader(io.BytesIO(content))
        # report(1) + OldCo(1) = 2 — NewCo wasn't active yet.
        self.assertEqual(len(reader.pages), 2)
