"""Django-admin registrations — surfaced in the UI as the "Raw data" tab.

Sessions are read/write so the user can drop a row to force a re-import
(see ``charging.services.auto_import``). Tariffs and MonthlyReports are
deliberately read-only here: tariffs are historical and must never be
edited in place, reports must be regenerated through the Reports page.
"""
from django.contrib import admin

from .models import ChargingSession, MonthlyReport, Tariff, TariffDocument


@admin.register(ChargingSession)
class ChargingSessionAdmin(admin.ModelAdmin):
    list_display = (
        "started_at",
        "ended_at",
        "energy_kwh",
        "serial",
        "updated_at",
    )
    list_filter = ("serial",)
    search_fields = ("serial",)
    readonly_fields = ("created_at", "updated_at", "raw_row")


@admin.register(Tariff)
class TariffAdmin(admin.ModelAdmin):
    list_display = (
        "valid_from",
        "energy_price_ct_per_kwh",
        "created_at",
    )
    readonly_fields = (
        "valid_from",
        "energy_price_ct_per_kwh",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TariffDocument)
class TariffDocumentAdmin(admin.ModelAdmin):
    list_display = ("valid_from", "provider_name", "uploaded_at")
    readonly_fields = ("uploaded_at",)


@admin.register(MonthlyReport)
class MonthlyReportAdmin(admin.ModelAdmin):
    list_display = (
        "year",
        "month",
        "wallbox_kwh_total",
        "energy_cost_eur",
        "total_amount_eur",
    )
    list_filter = ("year",)
    ordering = ("-year", "-month")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
