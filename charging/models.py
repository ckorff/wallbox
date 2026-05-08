from __future__ import annotations

from datetime import date

from django.db import models


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
