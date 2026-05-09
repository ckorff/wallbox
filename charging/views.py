from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from .forms import TariffForm
from .models import Tariff


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
