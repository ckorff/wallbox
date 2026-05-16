"""Compact monthly statistics for the dashboard (Phase 2.9).

Pure DB queries — no API calls. Uses the shared
``session_energy_cost_eur`` helper from ``services.reports`` so the
per-session-tariff rule (which correctly handles mid-month tariff
changes) stays defined in one place.

Unlike ``generate_monthly_report``, a session that lacks a tariff does
not raise: the dashboard surfaces a ``missing_tariff`` warning instead
so users see partial numbers and a hint to add a tariff, rather than
an error stopping the whole page from rendering.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.utils import timezone

from charging.models import ChargingSession
from charging.services.reports import (
    BERLIN,
    _month_bounds,
    _quantize_kwh,
    _quantize_money,
    session_energy_cost_eur,
)


@dataclass
class MonthSummary:
    year: int
    month: int
    sessions: int
    kwh_total: Decimal
    cost_eur: Decimal
    missing_tariff: bool


def _today_berlin() -> date:
    return timezone.now().astimezone(BERLIN).date()


def _summary_for(year: int, month: int) -> MonthSummary:
    start_dt, end_dt, _ = _month_bounds(year, month)
    sessions = list(
        ChargingSession.objects.filter(
            started_at__gte=start_dt,
            started_at__lt=end_dt,
        )
    )
    kwh = Decimal("0")
    cost = Decimal("0")
    missing = False
    for session in sessions:
        kwh += session.energy_kwh
        per_session = session_energy_cost_eur(session)
        if per_session is None:
            missing = True
        else:
            cost += per_session
    return MonthSummary(
        year=year,
        month=month,
        sessions=len(sessions),
        kwh_total=_quantize_kwh(kwh),
        cost_eur=_quantize_money(cost),
        missing_tariff=missing,
    )


def current_month_summary(today: date | None = None) -> MonthSummary:
    today = today or _today_berlin()
    return _summary_for(today.year, today.month)


def previous_month_summary(today: date | None = None) -> MonthSummary:
    today = today or _today_berlin()
    if today.month == 1:
        return _summary_for(today.year - 1, 12)
    return _summary_for(today.year, today.month - 1)


@dataclass
class KwhTrend:
    direction: str  # "up", "down", or "flat"
    percent: Decimal | None  # None when previous month had 0 kWh


def kwh_trend(current: MonthSummary, previous: MonthSummary) -> KwhTrend:
    """Tri-state delta of current vs. previous month's energy total."""
    if previous.kwh_total == 0:
        if current.kwh_total == 0:
            return KwhTrend(direction="flat", percent=None)
        return KwhTrend(direction="up", percent=None)
    delta = (current.kwh_total - previous.kwh_total) / previous.kwh_total * 100
    delta = delta.quantize(Decimal("0.1"))
    if delta > 0:
        return KwhTrend(direction="up", percent=delta)
    if delta < 0:
        return KwhTrend(direction="down", percent=abs(delta))
    return KwhTrend(direction="flat", percent=delta)
