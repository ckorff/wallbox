from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
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


class MonthlyHouseUsage(models.Model):
    KWH_TOLERANCE = Decimal("0.001")

    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(12)],
    )
    meter_start_kwh = models.DecimalField(
        max_digits=10, decimal_places=3, null=True, blank=True
    )
    meter_end_kwh = models.DecimalField(
        max_digits=10, decimal_places=3, null=True, blank=True
    )
    kwh_total = models.DecimalField(
        max_digits=10, decimal_places=3, null=True, blank=True
    )

    class Meta:
        ordering = ["-year", "-month"]
        constraints = [
            models.UniqueConstraint(
                fields=["year", "month"],
                name="unique_house_usage_per_month",
            ),
        ]

    def clean(self):
        super().clean()

        meter_diff = None
        if self.meter_start_kwh is not None and self.meter_end_kwh is not None:
            if self.meter_end_kwh < self.meter_start_kwh:
                raise ValidationError(
                    {
                        "meter_end_kwh": (
                            "Meter end reading must not be less than meter start reading."
                        )
                    }
                )
            meter_diff = self.meter_end_kwh - self.meter_start_kwh

        if self.kwh_total is None and meter_diff is None:
            raise ValidationError(
                "Provide either kwh_total or both meter_start_kwh and meter_end_kwh."
            )

        if self.kwh_total is not None and meter_diff is not None:
            if abs(meter_diff - self.kwh_total) > self.KWH_TOLERANCE:
                raise ValidationError(
                    "Meter difference and kwh_total disagree by more than "
                    f"{self.KWH_TOLERANCE} kWh."
                )

    @property
    def effective_kwh(self):
        if self.kwh_total is not None:
            return self.kwh_total
        if self.meter_start_kwh is not None and self.meter_end_kwh is not None:
            return self.meter_end_kwh - self.meter_start_kwh
        return None

    def __str__(self):
        kwh = self.effective_kwh
        kwh_str = f"{kwh:.3f}" if kwh is not None else "—"
        return f"{self.year}-{self.month:02d}: {kwh_str} kWh"
