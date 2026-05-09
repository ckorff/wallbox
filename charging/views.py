from datetime import date
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from .forms import HouseUsageForm, TariffForm
from .models import ChargingSession, MonthlyHouseUsage, MonthlyReport, Tariff
from .services.pdf import attach_pdf_to_report
from .services.reports import MissingTariffError, generate_monthly_report


BERLIN = ZoneInfo("Europe/Berlin")


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


@staff_member_required
def house_usage(request):
    edit_target = None

    if request.method == "POST":
        target = _parse_year_month(
            request.POST.get("year"), request.POST.get("month")
        )
        existing = None
        if target is not None:
            existing = MonthlyHouseUsage.objects.filter(
                year=target[0], month=target[1]
            ).first()
        form = HouseUsageForm(request.POST, instance=existing)
        if form.is_valid():
            form.save()
            messages.success(
                request, "Monthly household usage saved."
            )
            return redirect(reverse("house_usage"))
        if existing is not None:
            edit_target = target
    else:
        target = _parse_year_month(
            request.GET.get("year"), request.GET.get("month")
        )
        existing = None
        if target is not None:
            existing = MonthlyHouseUsage.objects.filter(
                year=target[0], month=target[1]
            ).first()
        if existing is not None:
            form = HouseUsageForm(instance=existing)
            edit_target = target
        else:
            form = HouseUsageForm()

    entries = MonthlyHouseUsage.objects.all()
    return render(
        request,
        "charging/house_usage.html",
        {
            "form": form,
            "entries": entries,
            "edit_target": edit_target,
        },
    )


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
