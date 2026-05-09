import logging
from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Sum
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from .forms import TariffForm
from .models import ChargingSession, MonthlyReport, Tariff
from .services.import_runner import run_keba_import
from .services.pdf import attach_pdf_to_report
from .services.reports import MissingTariffError, generate_monthly_report


BERLIN = ZoneInfo("Europe/Berlin")
logger = logging.getLogger(__name__)


@staff_member_required
def dashboard(request):
    if request.method == "POST" and request.POST.get("action") == "run_import":
        try:
            result = run_keba_import()
        except Exception as exc:
            logger.exception("keba_import failed from dashboard")
            messages.error(
                request,
                f"Import failed ({type(exc).__name__}): {exc}",
            )
        else:
            messages.success(
                request,
                f"Import finished: {result.sessions_imported} new session(s) "
                f"imported, {result.sessions_updated} updated, "
                f"{result.sessions_skipped} skipped.",
            )
        return redirect(reverse("dashboard"))

    session_total = ChargingSession.objects.count()
    last_session = ChargingSession.objects.order_by("-started_at").first()
    total_kwh = (
        ChargingSession.objects.aggregate(s=Sum("energy_kwh"))["s"]
        or Decimal("0")
    )
    latest_report = MonthlyReport.objects.order_by("-year", "-month").first()

    return render(
        request,
        "charging/dashboard.html",
        {
            "session_total": session_total,
            "last_session": last_session,
            "total_kwh": total_kwh,
            "latest_report": latest_report,
        },
    )


@staff_member_required
def tariff_settings(request):
    if request.method == "POST":
        form = TariffForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "New tariff saved.")
            return redirect(reverse("tariff_settings"))
    else:
        form = TariffForm()

    tariffs = Tariff.objects.all()
    active_tariff = Tariff.for_date(timezone.localdate())
    return render(
        request,
        "charging/tariff_settings.html",
        {
            "form": form,
            "tariffs": tariffs,
            "active_tariff": active_tariff,
        },
    )


def _parse_year_month(raw_year, raw_month):
    try:
        year = int(raw_year)
        month = int(raw_month)
    except (TypeError, ValueError):
        return None
    if not (1 <= month <= 12):
        return None
    return year, month


def _collect_report_entries():
    """Distinct (year, month) over sessions (local time) ∪ existing reports.

    Returned newest first, each as a dict with year, month, report, has_pdf.
    """
    months: set[tuple[int, int]] = set()

    # Months that contain at least one charging session, by local start time.
    for session in ChargingSession.objects.only("started_at").iterator():
        local = session.started_at.astimezone(BERLIN)
        months.add((local.year, local.month))

    reports = {(r.year, r.month): r for r in MonthlyReport.objects.all()}
    months.update(reports.keys())

    entries = []
    for year, month in sorted(months, key=lambda ym: (-ym[0], -ym[1])):
        report = reports.get((year, month))
        entries.append(
            {
                "year": year,
                "month": month,
                "label": date(year, month, 1).strftime("%B %Y"),
                "report": report,
                "has_pdf": bool(report and report.pdf),
            }
        )
    return entries


@staff_member_required
def reports_index(request):
    if request.method == "POST":
        target = _parse_year_month(
            request.POST.get("year"), request.POST.get("month")
        )
        if target is None:
            messages.error(request, "Invalid year/month.")
        else:
            year, month = target
            label = date(year, month, 1).strftime("%B %Y")
            try:
                report = generate_monthly_report(year, month)
                attach_pdf_to_report(report)
            except MissingTariffError as exc:
                messages.error(
                    request,
                    f"Cannot generate report for {label}: no tariff "
                    f"is configured covering this period ({exc}).",
                )
            else:
                messages.success(
                    request,
                    f"Report for {label} generated "
                    f"(€ {report.total_amount_eur}).",
                )
                return redirect(reverse("reports_index"))

    entries = _collect_report_entries()
    return render(
        request,
        "charging/reports.html",
        {"entries": entries},
    )
