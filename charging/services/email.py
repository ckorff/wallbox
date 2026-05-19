"""Send a monthly report as a PDF email attachment (Phase 3).

The recipient address lives in ``AppSettings.report_recipient_email`` (set
via the /settings/ page). SMTP transport config lives in ``.env`` —
secrets stay out of the DB. A missing recipient or missing PDF raises a
``ReportEmailError`` with a specific reason so the UI can show the user
exactly what's wrong instead of a generic SMTP traceback.

Tariff document attachment (extended from Phase 3.1): every tariff that
a session in the report month actually references is attached, in
reverse-chronological order after the report PDF. Referenced tariffs
without a PDF on file are surfaced to the caller in ``SentEmail`` so the
UI can flash them.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date

from django.conf import settings
from django.core.mail import EmailMessage

from charging.models import AppSettings, ChargingSession, MonthlyReport, Tariff
from charging.services.pdf_merge import merge_report_with_tariffs
from charging.services.reports import BERLIN, _month_bounds


class ReportEmailError(Exception):
    """Raised when the report email cannot be sent for a known reason."""


@dataclass(frozen=True)
class SentEmail:
    recipient: str
    subject: str
    tariffs_attached: tuple[str, ...] = field(default_factory=tuple)
    tariffs_missing: tuple[str, ...] = field(default_factory=tuple)

    @property
    def tariff_attached(self) -> bool:
        return bool(self.tariffs_attached)


def _month_label(year: int, month: int) -> str:
    return date(year, month, 1).strftime("%B %Y")


def _tariff_label(tariff: Tariff) -> str:
    name = tariff.provider_name or "Tariff"
    return f"{name} (from {tariff.valid_from.isoformat()})"


def _referenced_tariffs_for_report(
    report: MonthlyReport,
) -> tuple[list[Tariff], list[Tariff]]:
    """Resolve every distinct tariff used by a session in the report month.

    Returns ``(with_pdf, missing_pdf)``, both reverse-chronological by
    ``valid_from``. Falls back to the tariff active at the end of the
    month if no sessions exist (so e.g. a regenerated quiet-month report
    still ships the currently active supplier's PDF as cost evidence).
    """
    start_dt, end_dt, _ = _month_bounds(report.year, report.month)
    sessions = ChargingSession.objects.filter(
        started_at__gte=start_dt, started_at__lt=end_dt
    ).only("started_at")

    per_date_cache: dict[date, Tariff | None] = {}
    by_pk: dict[int, Tariff] = {}
    for session in sessions:
        d = session.started_at.astimezone(BERLIN).date()
        if d not in per_date_cache:
            per_date_cache[d] = Tariff.for_date(d)
        tariff = per_date_cache[d]
        if tariff is not None and tariff.pk not in by_pk:
            by_pk[tariff.pk] = tariff

    if not by_pk:
        last_day = calendar.monthrange(report.year, report.month)[1]
        fallback = Tariff.for_date(date(report.year, report.month, last_day))
        if fallback is not None:
            by_pk[fallback.pk] = fallback

    tariffs = sorted(by_pk.values(), key=lambda t: t.valid_from, reverse=True)
    with_pdf = [t for t in tariffs if t.pdf]
    missing = [t for t in tariffs if not t.pdf]
    return with_pdf, missing


def build_report_email(report: MonthlyReport, recipient: str) -> EmailMessage:
    """Build (but don't send) the EmailMessage for a MonthlyReport.

    Kept separate from ``send_report_email`` so tests can assert on the
    composed message without running through Django's mail outbox. Any
    referenced tariffs with PDFs on file are merged onto the report PDF
    in reverse-chronological order; if none have PDFs, the bare report
    PDF is attached.
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
    with_pdf, _missing = _referenced_tariffs_for_report(report)
    if with_pdf:
        pdf_bytes = merge_report_with_tariffs(
            report.pdf.path, [t.pdf.path for t in with_pdf]
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
    with_pdf, missing = _referenced_tariffs_for_report(report)
    return SentEmail(
        recipient=recipient,
        subject=message.subject,
        tariffs_attached=tuple(_tariff_label(t) for t in with_pdf),
        tariffs_missing=tuple(_tariff_label(t) for t in missing),
    )
