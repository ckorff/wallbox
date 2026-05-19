"""HTTP views for the charging app — dashboard, settings hub, reports.

All views are ``@staff_member_required``; the app is intentionally
single-user (LAN-only) so there's no public surface. Heavy work
(imports, PDF rendering, SMTP) lives in ``charging.services.*`` —
views only orchestrate forms, redirects and flash messages.
"""
import calendar
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

from .forms import ReportRecipientForm, TariffForm, WallboxApiForm
from .models import AppSettings, ChargingSession, MonthlyReport, Tariff
from .services.auto_import import auto_import_if_new_sessions
from .services.email import ReportEmailError, send_report_email
from .services.import_runner import run_keba_import
from .services.monthly_summary import (
    current_month_summary,
    kwh_trend,
    previous_month_summary,
)
from .services.pdf import attach_pdf_to_report
from .services.reports import MissingTariffError, generate_monthly_report
from .services.wallbox_key import fetch_wallbox_status
from .services.wallbox_state import fetch_live_state


BERLIN = ZoneInfo("Europe/Berlin")
logger = logging.getLogger(__name__)


@staff_member_required
def dashboard(request):
    if request.method == "POST" and request.POST.get("action") == "run_import":
        log_lines: list[str] = []
        try:
            result = run_keba_import(log=log_lines.append)
        except Exception as exc:
            logger.exception("keba_import failed from dashboard")
            messages.error(
                request,
                f"Import failed ({type(exc).__name__}): {exc}",
            )
            if log_lines:
                messages.info(request, "\n".join(log_lines), extra_tags="log")
        else:
            total_now = ChargingSession.objects.count()
            messages.success(
                request,
                f"Import finished: {result.sessions_imported} new, "
                f"{result.sessions_updated} already known, "
                f"total now {total_now} sessions.",
            )
            if log_lines:
                messages.info(request, "\n".join(log_lines), extra_tags="log")
        return redirect(reverse("dashboard"))

    # Per-pageload auto-import: pull any sessions the wallbox knows about
    # that we don't. Silent unless something was actually imported — the
    # wallbox-unreachable case is already surfaced by the live_state UI.
    auto = auto_import_if_new_sessions()
    if auto.imported:
        messages.success(
            request,
            f"Auto-imported {auto.imported} new "
            f"session{'s' if auto.imported != 1 else ''} from the wallbox.",
        )

    session_total = ChargingSession.objects.count()
    total_kwh = (
        ChargingSession.objects.aggregate(s=Sum("energy_kwh"))["s"]
        or Decimal("0")
    )
    latest_report = MonthlyReport.objects.order_by("-year", "-month").first()
    last_import_at = AppSettings.current().last_import_at
    live_state = fetch_live_state()
    this_month = current_month_summary()
    last_month = previous_month_summary()
    trend = kwh_trend(this_month, last_month)

    return render(
        request,
        "charging/dashboard.html",
        {
            "session_total": session_total,
            "total_kwh": total_kwh,
            "latest_report": latest_report,
            "last_import_at": last_import_at,
            "live_state": live_state,
            "this_month": this_month,
            "this_month_name": calendar.month_name[this_month.month],
            "last_month": last_month,
            "trend": trend,
        },
    )


def _render_settings(request, *, section="", overrides=None):
    today = timezone.localdate()
    context = {
        "tariff_form": TariffForm(),
        "wallbox_form": WallboxApiForm(),
        "recipient_form": ReportRecipientForm(),
        "tariffs": Tariff.objects.all(),
        "active_tariff": Tariff.for_date(today),
        "eichrecht": fetch_wallbox_status(),
        "section": section,
    }
    if overrides:
        context.update(overrides)
    return render(request, "charging/settings.html", context)


@staff_member_required
def settings_page(request):
    if request.method == "POST":
        which = request.POST.get("form_name")
        if which == "tariff":
            form = TariffForm(request.POST, request.FILES)
            if form.is_valid():
                form.save()
                messages.success(request, "New tariff saved.")
                return redirect(reverse("settings_page") + "#tariff")
            return _render_settings(
                request, section="tariff", overrides={"tariff_form": form}
            )
        if which == "wallbox_api":
            form = WallboxApiForm(request.POST)
            if form.is_valid():
                form.save()
                messages.success(request, "Wallbox API credentials saved.")
                return redirect(reverse("settings_page") + "#wallbox-api")
            return _render_settings(
                request, section="wallbox-api", overrides={"wallbox_form": form}
            )
        if which == "report_recipient":
            form = ReportRecipientForm(request.POST)
            if form.is_valid():
                form.save()
                messages.success(request, "Report recipient saved.")
                return redirect(reverse("settings_page") + "#report-recipient")
            return _render_settings(
                request,
                section="report-recipient",
                overrides={"recipient_form": form},
            )

    return _render_settings(request)


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
        action = request.POST.get("action", "generate")
        target = _parse_year_month(
            request.POST.get("year"), request.POST.get("month")
        )
        if target is None:
            messages.error(request, "Invalid year/month.")
        elif action == "send_email":
            year, month = target
            label = date(year, month, 1).strftime("%B %Y")
            report = MonthlyReport.objects.filter(year=year, month=month).first()
            if report is None:
                messages.error(
                    request,
                    f"Cannot send {label}: no report has been generated yet.",
                )
            else:
                try:
                    sent = send_report_email(report)
                except ReportEmailError as exc:
                    messages.error(request, f"Could not send {label}: {exc}")
                except Exception as exc:
                    logger.exception("send_report_email failed")
                    messages.error(
                        request,
                        f"Could not send {label} ({type(exc).__name__}): {exc}",
                    )
                else:
                    attached = sent.tariffs_attached
                    missing = sent.tariffs_missing
                    if attached:
                        n = len(attached)
                        noun = "document" if n == 1 else "documents"
                        msg = (
                            f"{label} report emailed to {sent.recipient} "
                            f"with {n} tariff {noun} attached: "
                            f"{', '.join(attached)}."
                        )
                        if missing:
                            msg += (
                                f" No PDF on file for: {', '.join(missing)}."
                            )
                        messages.success(request, msg)
                    elif missing:
                        messages.success(
                            request,
                            f"{label} report emailed to {sent.recipient}. "
                            f"No tariff document on file for "
                            f"{', '.join(missing)} — report sent without "
                            f"attachment.",
                        )
                    else:
                        messages.success(
                            request,
                            f"{label} report emailed to {sent.recipient}. "
                            f"No tariff document on file — report sent "
                            f"without attachment.",
                        )
                    return redirect(reverse("reports_index"))
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
    latest_report = (
        MonthlyReport.objects.exclude(pdf="")
        .order_by("-year", "-month")
        .first()
    )
    recipient = AppSettings.current().report_recipient_email
    return render(
        request,
        "charging/reports.html",
        {
            "entries": entries,
            "latest_report": latest_report,
            "report_recipient_email": recipient,
        },
    )
