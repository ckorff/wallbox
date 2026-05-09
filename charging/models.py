from django.db import models


class Tariff(models.Model):
    valid_from = models.DateField(unique=True, db_index=True)
    energy_price_ct_per_kwh = models.DecimalField(max_digits=6, decimal_places=3)
    base_fee_eur_per_month = models.DecimalField(max_digits=8, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-valid_from"]

    def __str__(self):
        return (
            f"{self.energy_price_ct_per_kwh} ct/kWh "
            f"+ {self.base_fee_eur_per_month} €/month "
            f"from {self.valid_from:%Y-%m-%d}"
        )

    @classmethod
    def for_date(cls, d):
        return cls.objects.filter(valid_from__lte=d).order_by("-valid_from").first()


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
