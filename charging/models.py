from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class Tariff(models.Model):
    valid_from = models.DateField(unique=True, db_index=True)
    energy_price_ct_per_kwh = models.DecimalField(max_digits=6, decimal_places=3)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-valid_from"]

    def __str__(self):
        return (
            f"{self.energy_price_ct_per_kwh} ct/kWh "
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


class MonthlyReport(models.Model):
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(12)],
    )
    pdf = models.FileField(upload_to="reports/", blank=True)
    generated_at = models.DateTimeField(default=timezone.now)
    wallbox_kwh_total = models.DecimalField(max_digits=10, decimal_places=3)
    energy_cost_eur = models.DecimalField(max_digits=8, decimal_places=2)
    total_amount_eur = models.DecimalField(max_digits=8, decimal_places=2)
    tariff_used = models.ForeignKey(
        Tariff,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reports",
    )

    class Meta:
        ordering = ["-year", "-month"]
        constraints = [
            models.UniqueConstraint(
                fields=["year", "month"],
                name="unique_monthly_report",
            ),
        ]

    def __str__(self):
        return f"Report {self.year}-{self.month:02d} (€ {self.total_amount_eur})"
