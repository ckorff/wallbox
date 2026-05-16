"""Monthly report calculation for the KEBA wallbox."""
from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from charging.models import ChargingSession, MonthlyReport, Tariff


BERLIN = ZoneInfo("Europe/Berlin")
_MONEY = Decimal("0.01")
_KWH = Decimal("0.001")
_HUNDRED = Decimal(100)


class MissingTariffError(Exception):
    """Raised when a session falls on a date with no applicable tariff."""


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY, rounding=ROUND_HALF_UP)


def _quantize_kwh(value: Decimal) -> Decimal:
    return value.quantize(_KWH, rounding=ROUND_HALF_UP)


def session_energy_cost_eur(session) -> Decimal | None:
    """Cost for a single session in EUR, or None if no tariff applies.

    Shared by the monthly-report calculation (which raises on None) and
    the dashboard's monthly summary (which degrades gracefully). Keeps
    the per-session × `Tariff.for_date(session.started_at.date())` rule
    defined in one place — tariff changes inside a month are handled
    correctly because every session looks up its own date's tariff.
    """
    session_date = session.started_at.astimezone(BERLIN).date()
    tariff = Tariff.for_date(session_date)
    if tariff is None:
        return None
    return session.energy_kwh * tariff.energy_price_ct_per_kwh / _HUNDRED


def _month_bounds(year: int, month: int) -> tuple[datetime, datetime, date]:
    first = date(year, month, 1)
    if month == 12:
        next_first = date(year + 1, 1, 1)
    else:
        next_first = date(year, month + 1, 1)
    start_dt = datetime(year, first.month, 1, tzinfo=BERLIN)
    end_dt = datetime(next_first.year, next_first.month, 1, tzinfo=BERLIN)
    return start_dt, end_dt, next_first


def _resolve_tariff_used(year: int, month: int, next_first: date):
    first_of_month = date(year, month, 1)
    has_mid_month_change = Tariff.objects.filter(
        valid_from__gt=first_of_month,
        valid_from__lt=next_first,
    ).exists()
    if has_mid_month_change:
        return None
    return Tariff.for_date(first_of_month)


@transaction.atomic
def generate_monthly_report(year: int, month: int) -> MonthlyReport:
    start_dt, end_dt, next_first = _month_bounds(year, month)

    sessions = list(
        ChargingSession.objects.filter(
            started_at__gte=start_dt,
            started_at__lt=end_dt,
        )
    )

    wallbox_kwh_total = Decimal("0")
    energy_cost_eur_raw = Decimal("0")
    for session in sessions:
        cost = session_energy_cost_eur(session)
        if cost is None:
            session_date = session.started_at.astimezone(BERLIN).date()
            raise MissingTariffError(
                f"No tariff valid on {session_date.isoformat()} "
                f"for session {session.pk}"
            )
        wallbox_kwh_total += session.energy_kwh
        energy_cost_eur_raw += cost

    energy_cost_eur = _quantize_money(energy_cost_eur_raw)
    total_amount_eur = energy_cost_eur

    tariff_used = _resolve_tariff_used(year, month, next_first)

    defaults = {
        "wallbox_kwh_total": _quantize_kwh(wallbox_kwh_total),
        "energy_cost_eur": energy_cost_eur,
        "total_amount_eur": total_amount_eur,
        "tariff_used": tariff_used,
        "generated_at": timezone.now(),
    }

    report, _ = MonthlyReport.objects.update_or_create(
        year=year,
        month=month,
        defaults=defaults,
    )
    return report
