"""Tests for charging.services.monthly_summary (Phase 2.9)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.test import TestCase

from charging.models import ChargingSession, Tariff
from charging.services.monthly_summary import (
    current_month_summary,
    kwh_trend,
    previous_month_summary,
)


BERLIN = ZoneInfo("Europe/Berlin")


def _session(started_local, energy_kwh, serial="KEBA-1"):
    return ChargingSession.objects.create(
        serial=serial,
        started_at=started_local,
        ended_at=None,
        energy_kwh=Decimal(energy_kwh),
        raw_row={},
    )


class CurrentMonthSummaryTests(TestCase):
    def setUp(self):
        Tariff.objects.create(
            valid_from=date(2026, 1, 1),
            energy_price_ct_per_kwh=Decimal("38.500"),
        )
        # In May 2026
        _session(datetime(2026, 5, 1, 10, 0, tzinfo=BERLIN), "4.000")
        _session(datetime(2026, 5, 7, 15, 33, tzinfo=BERLIN), "27.640")
        # Outside May → must not bleed in
        _session(datetime(2026, 4, 20, 12, 0, tzinfo=BERLIN), "5.500")
        _session(datetime(2026, 6, 1, 0, 1, tzinfo=BERLIN), "1.000")

    def test_only_current_month_sessions_counted(self):
        s = current_month_summary(today=date(2026, 5, 16))

        self.assertEqual(s.year, 2026)
        self.assertEqual(s.month, 5)
        self.assertEqual(s.sessions, 2)
        self.assertEqual(s.kwh_total, Decimal("31.640"))
        # 31.640 * 38.5 / 100 = 12.1814 → rounds to 12.18
        self.assertEqual(s.cost_eur, Decimal("12.18"))
        self.assertFalse(s.missing_tariff)

    def test_session_at_local_midnight_assigned_by_started_at(self):
        # The June 1 00:01 session must belong to June, not May.
        # Re-check by asking for June.
        s = current_month_summary(today=date(2026, 6, 1))
        self.assertEqual(s.sessions, 1)
        self.assertEqual(s.kwh_total, Decimal("1.000"))


class MidMonthTariffChangeTests(TestCase):
    def test_each_session_uses_its_own_date_tariff(self):
        Tariff.objects.create(
            valid_from=date(2026, 1, 1),
            energy_price_ct_per_kwh=Decimal("30.000"),
        )
        Tariff.objects.create(
            valid_from=date(2026, 5, 15),
            energy_price_ct_per_kwh=Decimal("40.000"),
        )
        _session(datetime(2026, 5, 1, 10, 0, tzinfo=BERLIN), "10.000")   # @ 30
        _session(datetime(2026, 5, 20, 10, 0, tzinfo=BERLIN), "10.000")  # @ 40

        s = current_month_summary(today=date(2026, 5, 25))

        # 10 * 0.30 + 10 * 0.40 = 7.00
        self.assertEqual(s.cost_eur, Decimal("7.00"))
        self.assertEqual(s.kwh_total, Decimal("20.000"))


class MissingTariffGracefulTests(TestCase):
    def test_missing_tariff_flagged_but_does_not_raise(self):
        # Tariff only valid from June, but May session exists.
        Tariff.objects.create(
            valid_from=date(2026, 6, 1),
            energy_price_ct_per_kwh=Decimal("38.500"),
        )
        _session(datetime(2026, 5, 10, 10, 0, tzinfo=BERLIN), "5.000")

        s = current_month_summary(today=date(2026, 5, 16))

        self.assertEqual(s.sessions, 1)
        self.assertEqual(s.kwh_total, Decimal("5.000"))
        self.assertEqual(s.cost_eur, Decimal("0.00"))
        self.assertTrue(s.missing_tariff)


class PreviousMonthEdgeTests(TestCase):
    def test_january_rolls_back_to_december_prior_year(self):
        Tariff.objects.create(
            valid_from=date(2025, 1, 1),
            energy_price_ct_per_kwh=Decimal("30.000"),
        )
        _session(datetime(2025, 12, 15, 10, 0, tzinfo=BERLIN), "8.000")

        s = previous_month_summary(today=date(2026, 1, 10))

        self.assertEqual(s.year, 2025)
        self.assertEqual(s.month, 12)
        self.assertEqual(s.sessions, 1)


class KwhTrendTests(TestCase):
    def _summary(self, kwh):
        # Bypass DB — KwhTrend only uses kwh_total
        from charging.services.monthly_summary import MonthSummary
        return MonthSummary(
            year=2026,
            month=5,
            sessions=0,
            kwh_total=Decimal(kwh),
            cost_eur=Decimal("0.00"),
            missing_tariff=False,
        )

    def test_up(self):
        t = kwh_trend(self._summary("150.000"), self._summary("100.000"))
        self.assertEqual(t.direction, "up")
        self.assertEqual(t.percent, Decimal("50.0"))

    def test_down(self):
        t = kwh_trend(self._summary("80.000"), self._summary("100.000"))
        self.assertEqual(t.direction, "down")
        self.assertEqual(t.percent, Decimal("20.0"))

    def test_flat(self):
        t = kwh_trend(self._summary("100.000"), self._summary("100.000"))
        self.assertEqual(t.direction, "flat")

    def test_no_previous_baseline(self):
        t = kwh_trend(self._summary("10.000"), self._summary("0.000"))
        self.assertEqual(t.direction, "up")
        self.assertIsNone(t.percent)
