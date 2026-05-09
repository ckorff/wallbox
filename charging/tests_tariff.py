from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from charging.models import Tariff


User = get_user_model()


class TariffModelTests(TestCase):
    def test_create_tariff_with_required_fields(self):
        t = Tariff.objects.create(
            valid_from=date(2026, 5, 1),
            energy_price_ct_per_kwh=Decimal("38.500"),
            base_fee_eur_per_month=Decimal("16.40"),
        )
        self.assertEqual(t.valid_from, date(2026, 5, 1))
        self.assertEqual(t.energy_price_ct_per_kwh, Decimal("38.500"))
        self.assertEqual(t.base_fee_eur_per_month, Decimal("16.40"))
        self.assertIsNotNone(t.created_at)

    def test_valid_from_is_unique(self):
        Tariff.objects.create(
            valid_from=date(2026, 5, 1),
            energy_price_ct_per_kwh=Decimal("38.500"),
            base_fee_eur_per_month=Decimal("16.40"),
        )
        with self.assertRaises(IntegrityError):
            Tariff.objects.create(
                valid_from=date(2026, 5, 1),
                energy_price_ct_per_kwh=Decimal("40.000"),
                base_fee_eur_per_month=Decimal("17.00"),
            )

    def test_str_is_readable_summary(self):
        t = Tariff.objects.create(
            valid_from=date(2026, 5, 1),
            energy_price_ct_per_kwh=Decimal("38.500"),
            base_fee_eur_per_month=Decimal("16.40"),
        )
        self.assertEqual(str(t), "38.500 ct/kWh + 16.40 €/month from 2026-05-01")


class TariffForDateTests(TestCase):
    def test_returns_none_when_no_tariff_exists(self):
        self.assertIsNone(Tariff.for_date(date(2026, 5, 1)))

    def test_returns_none_when_no_tariff_valid_yet(self):
        Tariff.objects.create(
            valid_from=date(2026, 5, 1),
            energy_price_ct_per_kwh=Decimal("38.500"),
            base_fee_eur_per_month=Decimal("16.40"),
        )
        self.assertIsNone(Tariff.for_date(date(2026, 4, 30)))

    def test_returns_most_recent_tariff_with_valid_from_le_d(self):
        t1 = Tariff.objects.create(
            valid_from=date(2025, 1, 1),
            energy_price_ct_per_kwh=Decimal("30.000"),
            base_fee_eur_per_month=Decimal("12.00"),
        )
        t2 = Tariff.objects.create(
            valid_from=date(2026, 1, 1),
            energy_price_ct_per_kwh=Decimal("35.000"),
            base_fee_eur_per_month=Decimal("14.00"),
        )
        t3 = Tariff.objects.create(
            valid_from=date(2026, 5, 1),
            energy_price_ct_per_kwh=Decimal("38.500"),
            base_fee_eur_per_month=Decimal("16.40"),
        )

        # before any tariff
        self.assertIsNone(Tariff.for_date(date(2024, 12, 31)))

        # exactly on t1.valid_from
        self.assertEqual(Tariff.for_date(date(2025, 1, 1)), t1)
        # day before t2 boundary -> t1
        self.assertEqual(Tariff.for_date(date(2025, 12, 31)), t1)
        # exactly on t2.valid_from -> t2
        self.assertEqual(Tariff.for_date(date(2026, 1, 1)), t2)
        # day before t3 boundary -> t2
        self.assertEqual(Tariff.for_date(date(2026, 4, 30)), t2)
        # exactly on t3.valid_from -> t3
        self.assertEqual(Tariff.for_date(date(2026, 5, 1)), t3)
        # well after t3 -> t3
        self.assertEqual(Tariff.for_date(date(2030, 1, 1)), t3)


class TariffSettingsViewTests(TestCase):
    url = "/settings/tariff/"

    def setUp(self):
        self.staff_user = User.objects.create_user(
            username="staff", password="pw", is_staff=True
        )
        self.regular_user = User.objects.create_user(
            username="user", password="pw"
        )

    def test_anonymous_get_redirects_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"].lower())

    def test_staff_get_lists_tariffs_descending_with_active_marker(self):
        t_old = Tariff.objects.create(
            valid_from=date(2025, 1, 1),
            energy_price_ct_per_kwh=Decimal("30.000"),
            base_fee_eur_per_month=Decimal("12.00"),
        )
        t_active = Tariff.objects.create(
            valid_from=date(2026, 1, 1),
            energy_price_ct_per_kwh=Decimal("38.500"),
            base_fee_eur_per_month=Decimal("16.40"),
        )

        self.client.force_login(self.staff_user)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        tariffs = list(response.context["tariffs"])
        self.assertEqual(tariffs, [t_active, t_old])

        today = timezone.localdate()
        self.assertEqual(response.context["active_tariff"], Tariff.for_date(today))

    def test_staff_post_creates_new_tariff_and_redirects(self):
        self.client.force_login(self.staff_user)
        response = self.client.post(
            self.url,
            data={
                "valid_from": "2026-05-01",
                "energy_price_ct_per_kwh": "38.500",
                "base_fee_eur_per_month": "16.40",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.url)
        self.assertEqual(Tariff.objects.count(), 1)
        t = Tariff.objects.get()
        self.assertEqual(t.valid_from, date(2026, 5, 1))
        self.assertEqual(t.energy_price_ct_per_kwh, Decimal("38.500"))
        self.assertEqual(t.base_fee_eur_per_month, Decimal("16.40"))

        # Follow the redirect to confirm the success message is rendered.
        followed = self.client.get(self.url)
        self.assertEqual(followed.status_code, 200)
        messages = [m.message for m in followed.context["messages"]]
        self.assertTrue(messages, "expected at least one Django message")
        self.assertTrue(
            any("tariff" in m.lower() for m in messages),
            f"expected a success message about the tariff, got {messages!r}",
        )

    def test_staff_post_with_duplicate_valid_from_rerenders_with_field_error(self):
        Tariff.objects.create(
            valid_from=date(2026, 5, 1),
            energy_price_ct_per_kwh=Decimal("38.500"),
            base_fee_eur_per_month=Decimal("16.40"),
        )
        self.client.force_login(self.staff_user)
        response = self.client.post(
            self.url,
            data={
                "valid_from": "2026-05-01",
                "energy_price_ct_per_kwh": "40.000",
                "base_fee_eur_per_month": "17.00",
            },
        )
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertFalse(form.is_valid())
        self.assertIn("valid_from", form.errors)
        self.assertEqual(Tariff.objects.count(), 1)

    def test_staff_post_with_negative_energy_price_rerenders_with_field_error(self):
        self.client.force_login(self.staff_user)
        response = self.client.post(
            self.url,
            data={
                "valid_from": "2026-05-01",
                "energy_price_ct_per_kwh": "-1.000",
                "base_fee_eur_per_month": "16.40",
            },
        )
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertFalse(form.is_valid())
        self.assertIn("energy_price_ct_per_kwh", form.errors)
        self.assertEqual(Tariff.objects.count(), 0)

    def test_staff_post_with_negative_base_fee_rerenders_with_field_error(self):
        self.client.force_login(self.staff_user)
        response = self.client.post(
            self.url,
            data={
                "valid_from": "2026-05-01",
                "energy_price_ct_per_kwh": "38.500",
                "base_fee_eur_per_month": "-0.01",
            },
        )
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertFalse(form.is_valid())
        self.assertIn("base_fee_eur_per_month", form.errors)
        self.assertEqual(Tariff.objects.count(), 0)
