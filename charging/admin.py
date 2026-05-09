from django.contrib import admin

from .models import ChargingSession, MonthlyHouseUsage, Tariff


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
        "base_fee_eur_per_month",
        "created_at",
    )
    readonly_fields = (
        "valid_from",
        "energy_price_ct_per_kwh",
        "base_fee_eur_per_month",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MonthlyHouseUsage)
class MonthlyHouseUsageAdmin(admin.ModelAdmin):
    list_display = (
        "year",
        "month",
        "meter_start_kwh",
        "meter_end_kwh",
        "kwh_total",
    )
    list_filter = ("year",)
    ordering = ("-year", "-month")
