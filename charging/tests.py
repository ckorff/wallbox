from datetime import date
from decimal import Decimal

from django.db import IntegrityError
from django.test import TestCase

from charging.models import Tariff


class TariffStorageTests(TestCase):
    def test_stores_money_as_decimal(self):
        tariff = Tariff.objects.create(
            energy_price_ct_per_kwh=Decimal('38.5'),
            base_fee_eur=Decimal('16.40'),
            valid_from=date(2026, 5, 1),
        )
        tariff.refresh_from_db()
        self.assertIsInstance(tariff.energy_price_ct_per_kwh, Decimal)
        self.assertIsInstance(tariff.base_fee_eur, Decimal)
        self.assertEqual(tariff.energy_price_ct_per_kwh, Decimal('38.5'))
        self.assertEqual(tariff.base_fee_eur, Decimal('16.40'))

    def test_valid_from_is_unique(self):
        Tariff.objects.create(
            energy_price_ct_per_kwh=Decimal('30.0'),
            base_fee_eur=Decimal('10.00'),
            valid_from=date(2026, 1, 1),
        )
        with self.assertRaises(IntegrityError):
            Tariff.objects.create(
                energy_price_ct_per_kwh=Decimal('31.0'),
                base_fee_eur=Decimal('11.00'),
                valid_from=date(2026, 1, 1),
            )


class TariffForDateTests(TestCase):
    def test_returns_none_when_no_tariff_exists(self):
        self.assertIsNone(Tariff.for_date(date(2026, 5, 8)))

    def test_picks_most_recent_tariff_on_or_before_date(self):
        Tariff.objects.create(
            energy_price_ct_per_kwh=Decimal('30.0'),
            base_fee_eur=Decimal('10.00'),
            valid_from=date(2025, 1, 1),
        )
        newer = Tariff.objects.create(
            energy_price_ct_per_kwh=Decimal('38.5'),
            base_fee_eur=Decimal('16.40'),
            valid_from=date(2026, 5, 1),
        )
        self.assertEqual(Tariff.for_date(date(2026, 5, 8)), newer)
        self.assertEqual(Tariff.for_date(date(2026, 5, 1)), newer)

    def test_falls_back_to_older_tariff_before_newer_starts(self):
        older = Tariff.objects.create(
            energy_price_ct_per_kwh=Decimal('30.0'),
            base_fee_eur=Decimal('10.00'),
            valid_from=date(2025, 1, 1),
        )
        Tariff.objects.create(
            energy_price_ct_per_kwh=Decimal('38.5'),
            base_fee_eur=Decimal('16.40'),
            valid_from=date(2026, 5, 1),
        )
        self.assertEqual(Tariff.for_date(date(2026, 4, 30)), older)

    def test_ignores_future_tariffs(self):
        future = Tariff.objects.create(
            energy_price_ct_per_kwh=Decimal('40.0'),
            base_fee_eur=Decimal('17.00'),
            valid_from=date(2027, 1, 1),
        )
        self.assertIsNone(Tariff.for_date(date(2026, 12, 31)))
        self.assertEqual(Tariff.for_date(date(2027, 1, 1)), future)
