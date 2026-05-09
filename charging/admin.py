from django.contrib import admin

from .models import ChargingSession


@admin.register(ChargingSession)
class ChargingSessionAdmin(admin.ModelAdmin):
    list_display = (
        "started_at",
        "ended_at",
        "energy_kwh",
        "serial",
    )
    list_filter = ("serial",)
    search_fields = ("serial",)
    readonly_fields = ("created_at", "updated_at", "raw_row")
