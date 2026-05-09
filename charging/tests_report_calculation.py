from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.test import TestCase

from charging.models import (
    ChargingSession,
    MonthlyHouseUsage,
    MonthlyReport,
    Tariff,
)
from charging.services.reports import (
    MissingTariffError,
    generate_monthly_report,
)


BERLIN = ZoneInfo("Europe/Berlin")


def _session(started_local, energy_kwh, ended_local=None, serial="KEBA-1"):
    return ChargingSession.objects.create(
        serial=serial,
        started_at=started_local,
        ended_at=ended_local,
        energy_kwh=Decimal(energy_kwh),
        raw_row={},
    )


def _dt(year, month, day, hour=12, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=BERLIN)


class GenerateMonthlyReportTests(TestCase):
    def test_empty_month_creates_zeroed_row_with_warning(self):
        report = generate_monthly_report(2026, 4)

        self.assertEqual(report.year, 2026)
        self.assertEqual(report.month, 4)
        self.assertEqual(report.wallbox_kwh_total, Decimal("0.000"))
        self.assertEqual(report.energy_cost_eur, Decimal("0.00"))
        self.assertEqual(report.prorated_base_fee_eur, Decimal("0.00"))
        self.assertEqual(report.total_amount_eur, Decimal("0.00"))
        self.assertTrue(report.warning_house_usage_missing)

    def test_single_tariff_single_session_with_house_usage(self):
        tariff = Tariff.objects.create(
            valid_from=date(2026, 1, 1),
            energy_price_ct_per_kwh=Decimal("38.500"),
            base_fee_eur_per_month=Decimal("16.40"),
        )
        _session(_dt(2026, 5, 15, 10, 0), Decimal("10.000"))
        MonthlyHouseUsage.objects.create(
            year=2026, month=5, kwh_total=Decimal("400.000")
        )

        report = generate_monthly_report(2026, 5)

        self.assertEqual(report.wallbox_kwh_total, Decimal("10.000"))
        self.assertEqual(report.energy_cost_eur, Decimal("3.85"))
        self.assertEqual(report.prorated_base_fee_eur, Decimal("0.41"))
        self.assertEqual(report.total_amount_eur, Decimal("4.26"))
        self.assertEqual(report.house_kwh_total, Decimal("400.000"))
        self.assertEqual(report.tariff_used, tariff)
        self.assertFalse(report.warning_house_usage_missing)

    def test_multiple_sessions_single_tariff_sums_correctly(self):
        tariff = Tariff.objects.create(
            valid_from=date(2026, 1, 1),
            energy_price_ct_per_kwh=Decimal("38.500"),
            base_fee_eur_per_month=Decimal("16.40"),
        )
        _session(_dt(2026, 5, 5), Decimal("4.000"))
        _session(_dt(2026, 5, 12), Decimal("6.000"))
        _session(_dt(2026, 5, 20), Decimal("2.500"))
        MonthlyHouseUsage.objects.create(
            year=2026, month=5, kwh_total=Decimal("400.000")
        )

        report = generate_monthly_report(2026, 5)

        self.assertEqual(report.wallbox_kwh_total, Decimal("12.500"))
        # 12.5 × 0.385 = 4.8125 -> 4.81
        self.assertEqual(report.energy_cost_eur, Decimal("4.81"))
        self.assertEqual(report.tariff_used, tariff)
        self.assertFalse(report.warning_house_usage_missing)

    def test_tariff_change_mid_month_handled_per_session(self):
        Tariff.objects.create(
            valid_from=date(2026, 1, 1),
            energy_price_ct_per_kwh=Decimal("38.500"),
            base_fee_eur_per_month=Decimal("16.40"),
        )
        Tariff.objects.create(
            valid_from=date(2026, 5, 15),
            energy_price_ct_per_kwh=Decimal("40.000"),
            base_fee_eur_per_month=Decimal("17.00"),
        )
        _session(_dt(2026, 5, 10), Decimal("5.000"))
        _session(_dt(2026, 5, 20), Decimal("5.000"))
        MonthlyHouseUsage.objects.create(
            year=2026, month=5, kwh_total=Decimal("400.000")
        )

        report = generate_monthly_report(2026, 5)

        # 5*0.385 + 5*0.400 = 1.925 + 2.000 = 3.925 -> 3.93 (HALF_UP)
        self.assertEqual(report.energy_cost_eur, Decimal("3.93"))
        # Tariff change occurred strictly inside the month -> tariff_used None
        self.assertIsNone(report.tariff_used)
        # Pro-rated base fee uses tariff valid on 2026-05-01 (Tariff A, 16.40)
        # (10 / 400) × 16.40 = 0.41
        self.assertEqual(report.prorated_base_fee_eur, Decimal("0.41"))

    def test_no_house_usage_sets_warning_and_zero_base_fee(self):
        Tariff.objects.create(
            valid_from=date(2026, 1, 1),
            energy_price_ct_per_kwh=Decimal("38.500"),
            base_fee_eur_per_month=Decimal("16.40"),
        )
        _session(_dt(2026, 5, 10), Decimal("10.000"))

        report = generate_monthly_report(2026, 5)

        self.assertTrue(report.warning_house_usage_missing)
        self.assertEqual(report.prorated_base_fee_eur, Decimal("0.00"))
        self.assertEqual(report.total_amount_eur, report.energy_cost_eur)
        self.assertIsNone(report.house_kwh_total)

    def test_session_to_month_assignment_uses_local_start_time(self):
        Tariff.objects.create(
            valid_from=date(2026, 1, 1),
            energy_price_ct_per_kwh=Decimal("38.500"),
            base_fee_eur_per_month=Decimal("16.40"),
        )
        # Session A: starts 2026-05-31 23:50 Berlin -> belongs to May
        _session(
            datetime(2026, 5, 31, 23, 50, tzinfo=BERLIN),
            Decimal("3.000"),
            ended_local=datetime(2026, 6, 1, 2, 0, tzinfo=BERLIN),
        )
        # Session B: starts 2026-06-01 00:01 Berlin -> NOT in May
        _session(
            datetime(2026, 6, 1, 0, 1, tzinfo=BERLIN),
            Decimal("7.000"),
        )

        may = generate_monthly_report(2026, 5)
        self.assertEqual(may.wallbox_kwh_total, Decimal("3.000"))

        june = generate_monthly_report(2026, 6)
        self.assertEqual(june.wallbox_kwh_total, Decimal("7.000"))

    def test_missing_tariff_raises_and_creates_no_report(self):
        Tariff.objects.create(
            valid_from=date(2026, 1, 1),
            energy_price_ct_per_kwh=Decimal("38.500"),
            base_fee_eur_per_month=Decimal("16.40"),
        )
        _session(_dt(2025, 12, 15), Decimal("4.000"))

        with self.assertRaises(MissingTariffError):
            generate_monthly_report(2025, 12)

        self.assertFalse(
            MonthlyReport.objects.filter(year=2025, month=12).exists()
        )

    def test_regeneration_replaces_existing_row(self):
        Tariff.objects.create(
            valid_from=date(2026, 1, 1),
            energy_price_ct_per_kwh=Decimal("38.500"),
            base_fee_eur_per_month=Decimal("16.40"),
        )
        _session(_dt(2026, 5, 10), Decimal("10.000"))
        MonthlyHouseUsage.objects.create(
            year=2026, month=5, kwh_total=Decimal("400.000")
        )

        first = generate_monthly_report(2026, 5)
        self.assertEqual(first.wallbox_kwh_total, Decimal("10.000"))

        _session(_dt(2026, 5, 20), Decimal("5.000"))
        second = generate_monthly_report(2026, 5)

        self.assertEqual(MonthlyReport.objects.filter(year=2026, month=5).count(), 1)
        self.assertEqual(second.pk, first.pk)
        self.assertEqual(second.wallbox_kwh_total, Decimal("15.000"))

    def test_decimal_precision_rounds_only_at_end(self):
        Tariff.objects.create(
            valid_from=date(2026, 1, 1),
            energy_price_ct_per_kwh=Decimal("38.500"),
            base_fee_eur_per_month=Decimal("16.40"),
        )
        _session(_dt(2026, 5, 10), Decimal("100.000"))
        MonthlyHouseUsage.objects.create(
            year=2026, month=5, kwh_total=Decimal("300.000")
        )

        report = generate_monthly_report(2026, 5)

        # Pro-rated base fee = 100/300 * 16.40 = 5.46666... -> 5.47 (HALF_UP)
        self.assertEqual(report.prorated_base_fee_eur, Decimal("5.47"))
        # 100 × 0.385 = 38.50
        self.assertEqual(report.energy_cost_eur, Decimal("38.50"))
        self.assertEqual(report.total_amount_eur, Decimal("43.97"))

        # All money fields have exactly 2 decimals
        self.assertEqual(report.energy_cost_eur.as_tuple().exponent, -2)
        self.assertEqual(report.prorated_base_fee_eur.as_tuple().exponent, -2)
        self.assertEqual(report.total_amount_eur.as_tuple().exponent, -2)
        # Energy fields: exactly 3 decimals
        self.assertEqual(report.wallbox_kwh_total.as_tuple().exponent, -3)
        self.assertEqual(report.house_kwh_total.as_tuple().exponent, -3)
