"""PDF rendering for MonthlyReport rows."""
from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.files.base import ContentFile
from django.template.loader import render_to_string
from django.utils import timezone

from charging.models import ChargingSession, MonthlyReport, Tariff


BERLIN = ZoneInfo("Europe/Berlin")
_MONEY = Decimal("0.01")
_HUNDRED = Decimal(100)


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY, rounding=ROUND_HALF_UP)


def _month_bounds(year: int, month: int) -> tuple[datetime, datetime, date]:
    first = date(year, month, 1)
    if month == 12:
        next_first = date(year + 1, 1, 1)
    else:
        next_first = date(year, month + 1, 1)
    start_dt = datetime(year, first.month, 1, tzinfo=BERLIN)
    end_dt = datetime(next_first.year, next_first.month, 1, tzinfo=BERLIN)
    return start_dt, end_dt, next_first


def _last_day_of_month(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    from datetime import timedelta
    return date(year, month + 1, 1) - timedelta(days=1)


def build_report_context(report: MonthlyReport) -> dict:
    """Build the template context for rendering a MonthlyReport to HTML/PDF."""
    year = report.year
    month = report.month
    start_dt, end_dt, _ = _month_bounds(year, month)
    first_of_month = date(year, month, 1)
    last_of_month = _last_day_of_month(year, month)

    sessions_qs = ChargingSession.objects.filter(
        started_at__gte=start_dt,
        started_at__lt=end_dt,
    ).order_by("started_at")

    rows = []
    for session in sessions_qs:
        local_start = session.started_at.astimezone(BERLIN)
        local_end = (
            session.ended_at.astimezone(BERLIN) if session.ended_at else None
        )
        tariff = Tariff.for_date(local_start.date())
        if tariff is None:
            line_cost = None
            tariff_ct = None
        else:
            tariff_ct = tariff.energy_price_ct_per_kwh
            line_cost = _quantize_money(
                session.energy_kwh * tariff_ct / _HUNDRED
            )
        rows.append(
            {
                "date": local_start.date(),
                "start_time": local_start.time().replace(microsecond=0),
                "end_time": local_end.time().replace(microsecond=0) if local_end else None,
                "energy_kwh": session.energy_kwh,
                "tariff_ct_per_kwh": tariff_ct,
                "line_cost_eur": line_cost,
            }
        )

    return {
        "report": report,
        "year": year,
        "month": month,
        "period_start": first_of_month,
        "period_end": last_of_month,
        "generation_date": timezone.localtime(report.generated_at).date(),
        "reporter_name": settings.REPORTER_NAME,
        "reporter_employee_id": settings.REPORTER_EMPLOYEE_ID,
        "vehicle_make_model": settings.VEHICLE_MAKE_MODEL,
        "vehicle_license_plate": settings.VEHICLE_LICENSE_PLATE,
        "charging_location": settings.CHARGING_LOCATION,
        "session_rows": rows,
        "session_kwh_total": report.wallbox_kwh_total,
        "session_cost_total": report.energy_cost_eur,
    }


def render_report_pdf(report: MonthlyReport) -> bytes:
    """Render the given MonthlyReport to PDF bytes via WeasyPrint."""
    # Imported lazily so importing this module never depends on WeasyPrint.
    from weasyprint import HTML

    context = build_report_context(report)
    html_str = render_to_string("charging/report_pdf.html", context)
    return HTML(string=html_str, base_url=str(settings.BASE_DIR)).write_pdf()


def attach_pdf_to_report(report: MonthlyReport) -> MonthlyReport:
    """Render the report to PDF and save it onto the report.pdf FileField.

    If a previous PDF exists, it is deleted from storage first so we don't
    leak orphan files when regenerating.
    """
    pdf_bytes = render_report_pdf(report)
    filename = f"report-{report.year}-{report.month:02d}.pdf"

    if report.pdf:
        report.pdf.delete(save=False)

    report.pdf.save(filename, ContentFile(pdf_bytes), save=True)
    return report
