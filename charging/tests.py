from datetime import date, datetime, timezone
from decimal import Decimal

from django.db import IntegrityError
from django.test import TestCase

from charging.models import ChargingSession, Tariff


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


class ChargingSessionTests(TestCase):
    def _dt(self, hour: int, minute: int = 0) -> datetime:
        return datetime(2026, 5, 8, hour, minute, tzinfo=timezone.utc)

    def test_create_completed_session(self):
        session = ChargingSession.objects.create(
            start=self._dt(18, 0),
            end=self._dt(21, 30),
            kwh=Decimal('42.500'),
            meter_start=Decimal('1234.000'),
            meter_end=Decimal('1276.500'),
            note='evening top-up',
        )
        session.refresh_from_db()
        self.assertEqual(session.kwh, Decimal('42.500'))
        self.assertEqual(session.meter_end - session.meter_start, Decimal('42.500'))
        self.assertEqual(session.note, 'evening top-up')

    def test_in_progress_session_allows_null_end_and_meter_end(self):
        session = ChargingSession.objects.create(
            start=self._dt(18, 0),
            kwh=Decimal('0.000'),
            meter_start=Decimal('1234.000'),
        )
        session.refresh_from_db()
        self.assertIsNone(session.end)
        self.assertIsNone(session.meter_end)
        self.assertEqual(session.note, '')

    def test_default_ordering_is_newest_first(self):
        older = ChargingSession.objects.create(
            start=self._dt(8, 0),
            end=self._dt(9, 0),
            kwh=Decimal('5.000'),
            meter_start=Decimal('1000.000'),
            meter_end=Decimal('1005.000'),
        )
        newer = ChargingSession.objects.create(
            start=self._dt(20, 0),
            end=self._dt(21, 0),
            kwh=Decimal('5.000'),
            meter_start=Decimal('1005.000'),
            meter_end=Decimal('1010.000'),
        )
        self.assertEqual(list(ChargingSession.objects.all()), [newer, older])

    def test_end_before_start_is_rejected(self):
        with self.assertRaises(IntegrityError):
            ChargingSession.objects.create(
                start=self._dt(20, 0),
                end=self._dt(19, 0),
                kwh=Decimal('1.000'),
                meter_start=Decimal('1000.000'),
                meter_end=Decimal('1001.000'),
            )

    def test_meter_end_below_meter_start_is_rejected(self):
        with self.assertRaises(IntegrityError):
            ChargingSession.objects.create(
                start=self._dt(20, 0),
                end=self._dt(21, 0),
                kwh=Decimal('1.000'),
                meter_start=Decimal('1000.000'),
                meter_end=Decimal('999.000'),
            )
