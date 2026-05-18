"""Send a monthly report as a PDF email attachment (Phase 3).

The recipient address lives in ``AppSettings.report_recipient_email`` (set
via the /settings/ page). SMTP transport config lives in ``.env`` —
secrets stay out of the DB. A missing recipient or missing PDF raises a
``ReportEmailError`` with a specific reason so the UI can show the user
exactly what's wrong instead of a generic SMTP traceback.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

from django.conf import settings
from django.core.mail import EmailMessage

from charging.models import AppSettings, MonthlyReport, Tariff
from charging.services.pdf_merge import merge_report_with_tariff


class ReportEmailError(Exception):
    """Raised when the report email cannot be sent for a known reason."""


@dataclass(frozen=True)
class SentEmail:
    recipient: str
    subject: str
    tariff_attached: bool = False


def _month_label(year: int, month: int) -> str:
    return date(year, month, 1).strftime("%B %Y")


def _active_tariff_with_pdf(report: MonthlyReport) -> Tariff | None:
    """Return the tariff active at the end of the report month if it
    carries an attached PDF, else None. The price-only resolution at
    session time still uses ``Tariff.for_date`` directly; here we only
    care about the document side."""
    last_day = calendar.monthrange(report.year, report.month)[1]
    tariff = Tariff.for_date(date(report.year, report.month, last_day))
    if tariff is not None and tariff.pdf:
        return tariff
    return None


def build_report_email(report: MonthlyReport, recipient: str) -> EmailMessage:
    """Build (but don't send) the EmailMessage for a MonthlyReport.

    Kept separate from ``send_report_email`` so tests can assert on the
    composed message without running through Django's mail outbox. If
    the tariff active for the report month carries an attached supplier
    PDF, the attachment is the report PDF merged with that PDF; else
    it's just the report PDF.
    """
    label = _month_label(report.year, report.month)
    subject = f"Charging report — {label}"
    body = (
        f"Hello,\n\n"
        f"Please find attached the charging cost report for {label}.\n\n"
        f"Total energy: {report.wallbox_kwh_total} kWh\n"
        f"Total amount to be reimbursed: € {report.total_amount_eur}\n\n"
        f"Generated automatically from the KEBA P30 wallbox session log.\n"
    )
    message = EmailMessage(
        subject=subject,
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
    )
    filename = f"charging-report-{report.year}-{report.month:02d}.pdf"
    tariff = _active_tariff_with_pdf(report)
    if tariff is not None:
        pdf_bytes = merge_report_with_tariff(
            report.pdf.path, tariff.pdf.path
        ).getvalue()
    else:
        pdf_bytes = report.pdf.read()
        report.pdf.close()
    message.attach(filename, pdf_bytes, "application/pdf")
    return message


def send_report_email(report: MonthlyReport) -> SentEmail:
    """Send ``report`` as a PDF attachment to the configured recipient.

    Raises ``ReportEmailError`` with a specific message if the recipient
    is missing, the PDF has not been generated yet, or SMTP has not been
    configured in ``.env``. SMTP-level exceptions from Django propagate
    verbatim so the caller (the Reports view) can surface them.
    """
    recipient = AppSettings.current().report_recipient_email.strip()
    if not recipient:
        raise ReportEmailError(
            "No report recipient configured — set one on the /settings/ page."
        )
    if not report.pdf:
        raise ReportEmailError(
            "Report has no PDF attached — generate it first."
        )
    if not settings.EMAIL_HOST:
        raise ReportEmailError(
            "SMTP server is not configured — set EMAIL_HOST in .env."
        )

    message = build_report_email(report, recipient)
    message.send(fail_silently=False)
    return SentEmail(
        recipient=recipient,
        subject=message.subject,
        tariff_attached=_active_tariff_with_pdf(report) is not None,
    )
