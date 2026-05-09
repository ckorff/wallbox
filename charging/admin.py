from django.contrib import admin

from .models import ChargingSession


@admin.register(ChargingSession)
class ChargingSessionAdmin(admin.ModelAdmin):
    list_display = (
        "keba_session_id",
        "started_at",
        "ended_at",
        "energy_kwh",
        "end_reason",
    )
    search_fields = ("keba_session_id",)
    readonly_fields = ("created_at", "updated_at", "raw_report")
