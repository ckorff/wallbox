from django.db import models


class ChargingSession(models.Model):
    serial = models.CharField(max_length=32)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    energy_kwh = models.DecimalField(max_digits=10, decimal_places=3)
    raw_row = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["serial", "started_at"],
                name="unique_session_per_wallbox",
            ),
        ]

    def __str__(self):
        return f"{self.serial} – {self.started_at:%Y-%m-%d %H:%M} – {self.energy_kwh} kWh"
