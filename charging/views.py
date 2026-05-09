from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from .forms import HouseUsageForm, TariffForm
from .models import MonthlyHouseUsage, Tariff


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
