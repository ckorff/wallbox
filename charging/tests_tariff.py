import io
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.utils import timezone
from pypdf import PdfWriter

from charging.models import Tariff


User = get_user_model()


def _pdf_bytes(pages: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _pdf_upload(name="vattenfall.pdf"):
    return SimpleUploadedFile(name, _pdf_bytes(), content_type="application/pdf")


class TariffModelTests(TestCase):
    def test_create_tariff_with_required_fields(self):
        t = Tariff.objects.create(
            valid_from=date(2026, 5, 1),
            energy_price_ct_per_kwh=Decimal("38.500"),
        )
        self.assertEqual(t.valid_from, date(2026, 5, 1))
        self.assertEqual(t.energy_price_ct_per_kwh, Decimal("38.500"))
        self.assertIsNotNone(t.created_at)

    def test_valid_from_is_unique(self):
        Tariff.objects.create(
            valid_from=date(2026, 5, 1),
            energy_price_ct_per_kwh=Decimal("38.500"),
        )
        with self.assertRaises(IntegrityError):
            Tariff.objects.create(
                valid_from=date(2026, 5, 1),
                energy_price_ct_per_kwh=Decimal("40.000"),
            )

    def test_str_is_readable_summary(self):
        t = Tariff.objects.create(
            valid_from=date(2026, 5, 1),
            energy_price_ct_per_kwh=Decimal("38.500"),
        )
        self.assertEqual(str(t), "38.500 ct/kWh from 2026-05-01")


class TariffForDateTests(TestCase):
    def test_returns_none_when_no_tariff_exists(self):
        self.assertIsNone(Tariff.for_date(date(2026, 5, 1)))

    def test_returns_none_when_no_tariff_valid_yet(self):
        Tariff.objects.create(
            valid_from=date(2026, 5, 1),
            energy_price_ct_per_kwh=Decimal("38.500"),
        )
        self.assertIsNone(Tariff.for_date(date(2026, 4, 30)))

    def test_returns_most_recent_tariff_with_valid_from_le_d(self):
        t1 = Tariff.objects.create(
            valid_from=date(2025, 1, 1),
            energy_price_ct_per_kwh=Decimal("30.000"),
        )
        t2 = Tariff.objects.create(
            valid_from=date(2026, 1, 1),
            energy_price_ct_per_kwh=Decimal("35.000"),
        )
        t3 = Tariff.objects.create(
            valid_from=date(2026, 5, 1),
            energy_price_ct_per_kwh=Decimal("38.500"),
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
    """Tariff section of the consolidated /settings/ page (Phase 2.8)."""

    url = "/settings/"

    def setUp(self):
        self.staff_user = User.objects.create_user(
            username="staff", password="pw", is_staff=True
        )
        self.regular_user = User.objects.create_user(
            username="user", password="pw"
        )

    def _post_tariff(self, **fields):
        data = {"form_name": "tariff", **fields}
        return self.client.post(self.url, data=data)

    def _get_settings(self):
        # Avoid live wallbox calls in tariff tests.
        with patch(
            "charging.views.fetch_wallbox_status",
            return_value={"archived": False},
        ):
            return self.client.get(self.url)

    def test_anonymous_get_redirects_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"].lower())

    def test_staff_get_lists_tariffs_descending_with_active_marker(self):
        t_old = Tariff.objects.create(
            valid_from=date(2025, 1, 1),
            energy_price_ct_per_kwh=Decimal("30.000"),
        )
        t_active = Tariff.objects.create(
            valid_from=date(2026, 1, 1),
            energy_price_ct_per_kwh=Decimal("38.500"),
        )

        self.client.force_login(self.staff_user)
        response = self._get_settings()

        self.assertEqual(response.status_code, 200)
        tariffs = list(response.context["tariffs"])
        self.assertEqual(tariffs, [t_active, t_old])

        today = timezone.localdate()
        self.assertEqual(response.context["active_tariff"], Tariff.for_date(today))

    def test_staff_post_creates_new_tariff_and_redirects(self):
        self.client.force_login(self.staff_user)
        response = self._post_tariff(
            valid_from="01.05.2026",
            energy_price_ct_per_kwh="38.500",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.url + "#tariff")
        self.assertEqual(Tariff.objects.count(), 1)
        t = Tariff.objects.get()
        self.assertEqual(t.valid_from, date(2026, 5, 1))
        self.assertEqual(t.energy_price_ct_per_kwh, Decimal("38.500"))

        followed = self._get_settings()
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
        )
        self.client.force_login(self.staff_user)
        with patch(
            "charging.views.fetch_wallbox_status",
            return_value={"archived": False},
        ):
            response = self._post_tariff(
                valid_from="01.05.2026",
                energy_price_ct_per_kwh="40.000",
            )
        self.assertEqual(response.status_code, 200)
        form = response.context["tariff_form"]
        self.assertFalse(form.is_valid())
        self.assertIn("valid_from", form.errors)
        self.assertEqual(Tariff.objects.count(), 1)

    def test_staff_post_with_negative_energy_price_rerenders_with_field_error(self):
        self.client.force_login(self.staff_user)
        with patch(
            "charging.views.fetch_wallbox_status",
            return_value={"archived": False},
        ):
            response = self._post_tariff(
                valid_from="01.05.2026",
                energy_price_ct_per_kwh="-1.000",
            )
        self.assertEqual(response.status_code, 200)
        form = response.context["tariff_form"]
        self.assertFalse(form.is_valid())
        self.assertIn("energy_price_ct_per_kwh", form.errors)
        self.assertEqual(Tariff.objects.count(), 0)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="wallbox-test-tariff-doc-"))
class TariffDocumentFieldTests(TestCase):
    """Coverage for the supplier-PDF fields folded into Tariff."""

    def setUp(self):
        self.staff_user = User.objects.create_user(
            username="staff", password="pw", is_staff=True
        )

    def test_pdf_is_optional_on_create(self):
        t = Tariff.objects.create(
            valid_from=date(2026, 5, 1),
            energy_price_ct_per_kwh=Decimal("38.500"),
        )
        self.assertFalse(t.pdf)
        self.assertEqual(t.provider_name, "")
        self.assertEqual(t.notes, "")

    def test_filefield_round_trip(self):
        payload = _pdf_bytes(2)
        t = Tariff.objects.create(
            valid_from=date(2026, 5, 1),
            energy_price_ct_per_kwh=Decimal("38.500"),
            provider_name="Vattenfall",
            pdf=ContentFile(payload, name="vattenfall.pdf"),
        )
        t.refresh_from_db()
        with t.pdf.open("rb") as f:
            self.assertEqual(f.read(), payload)
        self.assertTrue(Path(t.pdf.path).exists())

    def test_staff_can_create_tariff_with_pdf_via_settings_form(self):
        self.client.force_login(self.staff_user)
        response = self.client.post(
            "/settings/",
            data={
                "form_name": "tariff",
                "valid_from": "01.05.2026",
                "energy_price_ct_per_kwh": "38.500",
                "provider_name": "Vattenfall",
                "notes": "May tariff",
                "pdf": _pdf_upload(),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/settings/#tariff")
        self.assertEqual(Tariff.objects.count(), 1)
        t = Tariff.objects.get()
        self.assertEqual(t.provider_name, "Vattenfall")
        self.assertEqual(t.notes, "May tariff")
        self.assertTrue(t.pdf.name)

    def test_staff_can_create_tariff_without_pdf_via_settings_form(self):
        self.client.force_login(self.staff_user)
        response = self.client.post(
            "/settings/",
            data={
                "form_name": "tariff",
                "valid_from": "01.05.2026",
                "energy_price_ct_per_kwh": "38.500",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Tariff.objects.count(), 1)
        t = Tariff.objects.get()
        self.assertFalse(t.pdf)
        self.assertEqual(t.provider_name, "")
