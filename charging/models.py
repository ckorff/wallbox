from __future__ import annotations

from datetime import date

from django.db import models
from django.db.models import CheckConstraint, F, Q


class Tariff(models.Model):
    """Energy tariff valid from a given date until a newer one supersedes it."""

    energy_price_ct_per_kwh = models.DecimalField(max_digits=6, decimal_places=3)
    base_fee_eur = models.DecimalField(max_digits=8, decimal_places=2)
    valid_from = models.DateField(unique=True)

    class Meta:
        ordering = ['-valid_from']

    def __str__(self) -> str:
        return f'{self.energy_price_ct_per_kwh} ct/kWh, {self.base_fee_eur} EUR/month from {self.valid_from.isoformat()}'

    @classmethod
    def for_date(cls, day: date) -> 'Tariff | None':
        return cls.objects.filter(valid_from__lte=day).order_by('-valid_from').first()


class ChargingSession(models.Model):
    """A single charging session captured from the wallbox."""

    start = models.DateTimeField()
    end = models.DateTimeField(null=True, blank=True)
    kwh = models.DecimalField(max_digits=8, decimal_places=3)
    meter_start = models.DecimalField(max_digits=12, decimal_places=3)
    meter_end = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ['-start']
        constraints = [
            CheckConstraint(
                check=Q(end__isnull=True) | Q(end__gte=F('start')),
                name='chargingsession_end_after_start',
            ),
            CheckConstraint(
                check=Q(meter_end__isnull=True) | Q(meter_end__gte=F('meter_start')),
                name='chargingsession_meter_end_after_start',
            ),
        ]

    def __str__(self) -> str:
        end = self.end.isoformat() if self.end else 'in progress'
        return f'{self.start.isoformat()} -> {end} ({self.kwh} kWh)'


class MonthlyHouseUsage(models.Model):
    """Total household electricity consumption for a calendar month, entered manually."""

    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    household_kwh = models.DecimalField(max_digits=10, decimal_places=3)

    class Meta:
        ordering = ['-year', '-month']
        constraints = [
            models.UniqueConstraint(fields=['year', 'month'], name='monthlyhouseusage_year_month_unique'),
            CheckConstraint(
                check=Q(month__gte=1) & Q(month__lte=12),
                name='monthlyhouseusage_month_range',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.year}-{self.month:02d}: {self.household_kwh} kWh'
