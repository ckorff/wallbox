from django.db import models


class ChargingSession(models.Model):
    keba_session_id = models.PositiveIntegerField(unique=True)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    energy_kwh = models.DecimalField(max_digits=10, decimal_places=3)
    end_reason = models.CharField(max_length=64, blank=True, default="")
    raw_report = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"Session {self.keba_session_id} – {self.energy_kwh} kWh"
