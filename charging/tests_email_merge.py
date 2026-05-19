"""Tests for the tariff-PDF merge on outgoing report emails."""
import io
import tempfile
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core import mail
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from pypdf import PdfReader, PdfWriter

from charging.models import AppSettings, ChargingSession, MonthlyReport, Tariff
from charging.services.email import send_report_email
from charging.services.pdf_merge import merge_report_with_tariffs


BERLIN = ZoneInfo("Europe/Berlin")


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


def _session(year, month, day, kwh="1.000"):
    return ChargingSession.objects.create(
        serial="KEBA-1",
        started_at=datetime(year, month, day, 12, 0, tzinfo=BERLIN),
        ended_at=datetime(year, month, day, 13, 0, tzinfo=BERLIN),
        energy_kwh=Decimal(kwh),
        raw_row={},
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
class MergeReportWithTariffsTests(TestCase):
    def test_single_tariff_combined_page_count_is_sum_of_inputs(self):
        report = _report_with_real_pdf(pages=2)
        tariff = _tariff(valid_from=date(2026, 5, 1), pages=3)

        merged = merge_report_with_tariffs(report.pdf.path, [tariff.pdf.path])
        reader = PdfReader(merged)
        self.assertEqual(len(reader.pages), 5)

    def test_order_is_report_first_then_tariffs(self):
        report = _report_with_real_pdf(pages=1)
        tariff = _tariff(valid_from=date(2026, 5, 1), pages=4)

        # If order were tariff-then-report the first page would be one of
        # the tariff blanks. We can't easily diff blank pages, so we re-run
        # the merge with swapped args and verify the byte streams differ.
        forward = merge_report_with_tariffs(
            report.pdf.path, [tariff.pdf.path]
        ).getvalue()
        reverse = merge_report_with_tariffs(
            tariff.pdf.path, [report.pdf.path]
        ).getvalue()
        self.assertNotEqual(forward, reverse)

        self.assertEqual(len(PdfReader(io.BytesIO(forward)).pages), 5)

    def test_multiple_tariffs_appended_in_supplied_order(self):
        report = _report_with_real_pdf(pages=1)
        t_new = _tariff(valid_from=date(2026, 5, 1), provider="NewCo", pages=3)
        t_old = _tariff(valid_from=date(2026, 1, 1), provider="OldCo", pages=2)

        merged = merge_report_with_tariffs(
            report.pdf.path, [t_new.pdf.path, t_old.pdf.path]
        )
        self.assertEqual(len(PdfReader(merged).pages), 1 + 3 + 2)


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
        _session(2026, 5, 15)

        sent = send_report_email(report)

        self.assertTrue(sent.tariff_attached)
        self.assertEqual(len(sent.tariffs_attached), 1)
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
        self.assertEqual(sent.tariffs_attached, ())
        self.assertEqual(sent.tariffs_missing, ())
        msg = mail.outbox[0]
        name, content, mime = msg.attachments[0]
        self.assertEqual(name, "charging-report-2026-05.pdf")
        reader = PdfReader(io.BytesIO(content))
        self.assertEqual(len(reader.pages), 2)

    def test_send_without_pdf_on_active_tariff_attaches_bare_report(self):
        # Tariff row exists for May but carries no PDF — equivalent to
        # the "no document on file" state, so report ships bare and the
        # missing tariff is reported.
        _tariff(valid_from=date(2026, 5, 1), pages=None)
        _session(2026, 5, 15)
        report = _report_with_real_pdf(pages=2)

        sent = send_report_email(report)

        self.assertFalse(sent.tariff_attached)
        self.assertEqual(len(sent.tariffs_missing), 1)
        _, content, _ = mail.outbox[0].attachments[0]
        reader = PdfReader(io.BytesIO(content))
        self.assertEqual(len(reader.pages), 2)

    def test_send_uses_tariff_active_at_end_of_report_month_when_no_sessions(self):
        # No sessions in the month → fallback to the tariff active at the
        # end of the month (Phase 3.1 behaviour for quiet periods).
        _tariff(valid_from=date(2026, 1, 1), provider="OldCo", pages=1)
        _tariff(valid_from=date(2026, 6, 1), provider="NewCo", pages=4)
        report = _report_with_real_pdf(pages=1)

        sent = send_report_email(report)

        self.assertTrue(sent.tariff_attached)
        _, content, _ = mail.outbox[0].attachments[0]
        reader = PdfReader(io.BytesIO(content))
        # report(1) + OldCo(1) = 2 — NewCo wasn't active yet.
        self.assertEqual(len(reader.pages), 2)

    def test_send_attaches_all_referenced_tariffs_when_month_spans_change(self):
        # Two tariffs, both referenced by at least one session in May.
        t_old = _tariff(valid_from=date(2026, 1, 1), provider="OldCo", pages=1)
        t_new = _tariff(valid_from=date(2026, 5, 15), provider="NewCo", pages=4)
        _session(2026, 5, 1)   # priced via OldCo
        _session(2026, 5, 20)  # priced via NewCo
        report = _report_with_real_pdf(pages=2)

        sent = send_report_email(report)

        self.assertEqual(len(sent.tariffs_attached), 2)
        # Reverse-chronological: NewCo first, OldCo after.
        self.assertEqual(
            sent.tariffs_attached,
            (
                f"NewCo (from {t_new.valid_from.isoformat()})",
                f"OldCo (from {t_old.valid_from.isoformat()})",
            ),
        )
        self.assertEqual(sent.tariffs_missing, ())
        _, content, _ = mail.outbox[0].attachments[0]
        reader = PdfReader(io.BytesIO(content))
        # report(2) + NewCo(4) + OldCo(1) = 7
        self.assertEqual(len(reader.pages), 7)

    def test_send_attaches_only_tariffs_with_a_session_in_the_month(self):
        # Three tariffs in the DB, but only the May one is referenced by
        # any session that month — Jan and Sept must be ignored.
        _tariff(valid_from=date(2026, 1, 1), provider="JanCo", pages=2)
        t_may = _tariff(valid_from=date(2026, 5, 1), provider="MayCo", pages=3)
        _tariff(valid_from=date(2026, 9, 1), provider="SepCo", pages=2)
        _session(2026, 5, 10)
        report = _report_with_real_pdf(pages=1)

        sent = send_report_email(report)

        self.assertEqual(
            sent.tariffs_attached,
            (f"MayCo (from {t_may.valid_from.isoformat()})",),
        )
        _, content, _ = mail.outbox[0].attachments[0]
        reader = PdfReader(io.BytesIO(content))
        # report(1) + MayCo(3) = 4 — Jan and Sept absent.
        self.assertEqual(len(reader.pages), 4)

    def test_send_flags_missing_pdfs_but_attaches_available_ones(self):
        # Two referenced tariffs: older has a PDF, newer does not.
        t_old = _tariff(valid_from=date(2026, 1, 1), provider="OldCo", pages=2)
        t_new = _tariff(valid_from=date(2026, 5, 15), provider="NewCo", pages=None)
        _session(2026, 5, 1)
        _session(2026, 5, 20)
        report = _report_with_real_pdf(pages=1)

        sent = send_report_email(report)

        self.assertEqual(
            sent.tariffs_attached,
            (f"OldCo (from {t_old.valid_from.isoformat()})",),
        )
        self.assertEqual(
            sent.tariffs_missing,
            (f"NewCo (from {t_new.valid_from.isoformat()})",),
        )
        _, content, _ = mail.outbox[0].attachments[0]
        reader = PdfReader(io.BytesIO(content))
        # report(1) + OldCo(2) = 3; NewCo skipped (no PDF).
        self.assertEqual(len(reader.pages), 3)
