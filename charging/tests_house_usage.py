from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from charging.models import MonthlyHouseUsage


User = get_user_model()


def _full_clean_save(**kwargs):
    obj = MonthlyHouseUsage(**kwargs)
    obj.full_clean()
    obj.save()
    return obj


class MonthlyHouseUsageCreateTests(TestCase):
    def test_create_with_kwh_total_only(self):
        obj = _full_clean_save(
            year=2026,
            month=5,
            kwh_total=Decimal("412.345"),
        )
        self.assertEqual(obj.year, 2026)
        self.assertEqual(obj.month, 5)
        self.assertEqual(obj.kwh_total, Decimal("412.345"))
        self.assertIsNone(obj.meter_start_kwh)
        self.assertIsNone(obj.meter_end_kwh)

    def test_create_with_meter_pair_only(self):
        obj = _full_clean_save(
            year=2026,
            month=5,
            meter_start_kwh=Decimal("10000.000"),
            meter_end_kwh=Decimal("10412.345"),
        )
        self.assertEqual(obj.meter_start_kwh, Decimal("10000.000"))
        self.assertEqual(obj.meter_end_kwh, Decimal("10412.345"))
        self.assertIsNone(obj.kwh_total)

    def test_create_with_all_three_when_consistent(self):
        # Meter diff 412.345; kwh_total 412.346 → diff 0.001 (within tolerance).
        obj = _full_clean_save(
            year=2026,
            month=5,
            meter_start_kwh=Decimal("10000.000"),
            meter_end_kwh=Decimal("10412.345"),
            kwh_total=Decimal("412.346"),
        )
        self.assertEqual(obj.kwh_total, Decimal("412.346"))


class MonthlyHouseUsageValidationTests(TestCase):
    def test_neither_kwh_total_nor_complete_meter_pair_raises(self):
        with self.assertRaises(ValidationError):
            obj = MonthlyHouseUsage(
                year=2026,
                month=5,
                meter_start_kwh=Decimal("10000.000"),
            )
            obj.full_clean()

    def test_no_data_at_all_raises(self):
        with self.assertRaises(ValidationError):
            obj = MonthlyHouseUsage(year=2026, month=5)
            obj.full_clean()

    def test_meter_end_below_meter_start_raises(self):
        with self.assertRaises(ValidationError):
            obj = MonthlyHouseUsage(
                year=2026,
                month=5,
                meter_start_kwh=Decimal("10000.000"),
                meter_end_kwh=Decimal("9999.000"),
            )
            obj.full_clean()

    def test_meter_diff_disagrees_with_kwh_total_beyond_tolerance(self):
        # Meter diff 412.345; kwh_total 412.500 → off by 0.155.
        with self.assertRaises(ValidationError):
            obj = MonthlyHouseUsage(
                year=2026,
                month=5,
                meter_start_kwh=Decimal("10000.000"),
                meter_end_kwh=Decimal("10412.345"),
                kwh_total=Decimal("412.500"),
            )
            obj.full_clean()

    def test_month_below_one_raises(self):
        with self.assertRaises(ValidationError):
            obj = MonthlyHouseUsage(
                year=2026, month=0, kwh_total=Decimal("100.000")
            )
            obj.full_clean()

    def test_month_above_twelve_raises(self):
        with self.assertRaises(ValidationError):
            obj = MonthlyHouseUsage(
                year=2026, month=13, kwh_total=Decimal("100.000")
            )
            obj.full_clean()


class MonthlyHouseUsageUniqueTests(TestCase):
    def test_unique_year_month(self):
        MonthlyHouseUsage.objects.create(
            year=2026, month=5, kwh_total=Decimal("412.345")
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                MonthlyHouseUsage.objects.create(
                    year=2026, month=5, kwh_total=Decimal("500.000")
                )


class MonthlyHouseUsageEffectiveKwhTests(TestCase):
    def test_effective_kwh_returns_kwh_total_when_set(self):
        obj = MonthlyHouseUsage(
            year=2026, month=5, kwh_total=Decimal("412.345")
        )
        self.assertEqual(obj.effective_kwh, Decimal("412.345"))

    def test_effective_kwh_returns_meter_diff_when_only_meters_set(self):
        obj = MonthlyHouseUsage(
            year=2026,
            month=5,
            meter_start_kwh=Decimal("10000.000"),
            meter_end_kwh=Decimal("10412.345"),
        )
        self.assertEqual(obj.effective_kwh, Decimal("412.345"))

    def test_effective_kwh_prefers_kwh_total_when_both_set(self):
        obj = MonthlyHouseUsage(
            year=2026,
            month=5,
            meter_start_kwh=Decimal("10000.000"),
            meter_end_kwh=Decimal("10412.345"),
            kwh_total=Decimal("412.346"),
        )
        self.assertEqual(obj.effective_kwh, Decimal("412.346"))


class MonthlyHouseUsageStrTests(TestCase):
    def test_str_includes_year_month_and_effective_kwh(self):
        obj = MonthlyHouseUsage.objects.create(
            year=2026, month=5, kwh_total=Decimal("412.345")
        )
        self.assertEqual(str(obj), "2026-05: 412.345 kWh")


class HouseUsageViewTests(TestCase):
    url = "/house-usage/"

    def setUp(self):
        self.staff_user = User.objects.create_user(
            username="staff", password="pw", is_staff=True
        )

    def test_anonymous_get_redirects_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"].lower())

    def test_staff_get_lists_entries_descending(self):
        older = MonthlyHouseUsage.objects.create(
            year=2025, month=12, kwh_total=Decimal("400.000")
        )
        newer = MonthlyHouseUsage.objects.create(
            year=2026, month=5, kwh_total=Decimal("412.345")
        )
        even_older = MonthlyHouseUsage.objects.create(
            year=2025, month=11, kwh_total=Decimal("380.000")
        )

        self.client.force_login(self.staff_user)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        entries = list(response.context["entries"])
        self.assertEqual(entries, [newer, older, even_older])

    def test_staff_get_with_year_month_prefills_form(self):
        existing = MonthlyHouseUsage.objects.create(
            year=2026,
            month=5,
            meter_start_kwh=Decimal("10000.000"),
            meter_end_kwh=Decimal("10412.345"),
        )
        self.client.force_login(self.staff_user)
        response = self.client.get(self.url + "?year=2026&month=5")

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(form.instance.pk, existing.pk)
        self.assertEqual(response.context["edit_target"], (2026, 5))
        # Prefilled values are present in the rendered form.
        self.assertEqual(form.initial.get("year") or form.instance.year, 2026)
        self.assertEqual(form.initial.get("month") or form.instance.month, 5)

    def test_staff_post_creates_new_entry_and_redirects(self):
        self.client.force_login(self.staff_user)
        response = self.client.post(
            self.url,
            data={
                "year": "2026",
                "month": "5",
                "meter_start_kwh": "",
                "meter_end_kwh": "",
                "kwh_total": "412.345",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.url)
        self.assertEqual(MonthlyHouseUsage.objects.count(), 1)
        obj = MonthlyHouseUsage.objects.get()
        self.assertEqual(obj.year, 2026)
        self.assertEqual(obj.month, 5)
        self.assertEqual(obj.kwh_total, Decimal("412.345"))

        followed = self.client.get(self.url)
        messages = [m.message for m in followed.context["messages"]]
        self.assertTrue(messages, "expected a Django success message")

    def test_staff_post_with_existing_year_month_updates_in_place(self):
        existing = MonthlyHouseUsage.objects.create(
            year=2026, month=5, kwh_total=Decimal("400.000")
        )
        self.client.force_login(self.staff_user)
        response = self.client.post(
            self.url,
            data={
                "year": "2026",
                "month": "5",
                "meter_start_kwh": "",
                "meter_end_kwh": "",
                "kwh_total": "412.345",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(MonthlyHouseUsage.objects.count(), 1)
        existing.refresh_from_db()
        self.assertEqual(existing.kwh_total, Decimal("412.345"))

    def test_staff_post_missing_both_kwh_sources_rerenders_with_errors(self):
        self.client.force_login(self.staff_user)
        response = self.client.post(
            self.url,
            data={
                "year": "2026",
                "month": "5",
                "meter_start_kwh": "",
                "meter_end_kwh": "",
                "kwh_total": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertFalse(form.is_valid())
        # Either non-field or field error must be present.
        self.assertTrue(
            form.non_field_errors()
            or any(form.errors.values()),
            f"expected validation errors, got {form.errors!r}",
        )
        self.assertEqual(MonthlyHouseUsage.objects.count(), 0)

    def test_staff_post_with_meter_end_below_start_rerenders_with_errors(self):
        self.client.force_login(self.staff_user)
        response = self.client.post(
            self.url,
            data={
                "year": "2026",
                "month": "5",
                "meter_start_kwh": "10000.000",
                "meter_end_kwh": "9999.000",
                "kwh_total": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertFalse(form.is_valid())
        self.assertTrue(
            form.non_field_errors()
            or any(form.errors.values()),
            f"expected validation errors, got {form.errors!r}",
        )
        self.assertEqual(MonthlyHouseUsage.objects.count(), 0)
