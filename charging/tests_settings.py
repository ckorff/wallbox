"""Tests for the Phase 2.8 settings page and AppSettings singleton."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.urls import reverse

from charging.models import AppSettings


User = get_user_model()


def _staff():
    return User.objects.create_user(
        username="staff", password="hunter2", is_staff=True
    )


_EICHRECHT_OFFLINE = {
    "archived": False,
    "serial": None,
    "fingerprint": None,
    "firmware_version": None,
    "dip_switch_settings": None,
    "live_fetch_error": None,
}


class AppSettingsModelTests(TestCase):
    def test_current_creates_then_returns_existing_row(self):
        # First call creates the singleton.
        s1 = AppSettings.current()
        self.assertEqual(AppSettings.objects.count(), 1)
        self.assertEqual(s1.pk, 1)

        # Second call returns the same row.
        s2 = AppSettings.current()
        self.assertEqual(s2.pk, s1.pk)
        self.assertEqual(AppSettings.objects.count(), 1)

    def test_save_clamps_pk_to_1_even_when_id_set(self):
        # Even if someone tries to insert at a different PK, save() forces 1.
        s = AppSettings(id=42, keba_api_username="admin")
        s.save()
        self.assertEqual(s.pk, 1)
        self.assertEqual(AppSettings.objects.count(), 1)

        # And a second blind create still overwrites pk=1.
        AppSettings(keba_api_username="other").save()
        self.assertEqual(AppSettings.objects.count(), 1)
        self.assertEqual(
            AppSettings.objects.get(pk=1).keba_api_username, "other"
        )

    def test_password_is_encrypted_in_db_and_decrypts_via_orm(self):
        s = AppSettings.current()
        s.keba_api_password = "secret123"
        s.save()

        # At rest in the DB: not plaintext, and shaped like a Fernet token.
        with connection.cursor() as cur:
            cur.execute(
                "SELECT keba_api_password FROM charging_appsettings WHERE id=1"
            )
            raw = cur.fetchone()[0]
        self.assertNotEqual(raw, "secret123")
        self.assertTrue(
            raw.startswith("gAAAAA"),
            f"Expected Fernet ciphertext prefix, got: {raw[:20]!r}",
        )

        # Via the ORM: plaintext.
        fresh = AppSettings.objects.get(pk=1)
        self.assertEqual(fresh.keba_api_password, "secret123")

    def test_empty_password_stays_empty(self):
        s = AppSettings.current()
        s.keba_api_password = ""
        s.save()

        with connection.cursor() as cur:
            cur.execute(
                "SELECT keba_api_password FROM charging_appsettings WHERE id=1"
            )
            raw = cur.fetchone()[0]
        # Empty stays empty — we don't encrypt the empty string.
        self.assertEqual(raw, "")

        fresh = AppSettings.objects.get(pk=1)
        self.assertEqual(fresh.keba_api_password, "")

    def test_password_round_trips_unicode_and_punctuation(self):
        s = AppSettings.current()
        s.keba_api_password = "Pä$$wörd! 🔑 with spaces"
        s.save()

        fresh = AppSettings.objects.get(pk=1)
        self.assertEqual(fresh.keba_api_password, "Pä$$wörd! 🔑 with spaces")


class SettingsPageTests(TestCase):
    url = "/settings/"

    def setUp(self):
        self.client.force_login(_staff())

    def _get(self):
        with patch(
            "charging.views.fetch_wallbox_status",
            return_value=_EICHRECHT_OFFLINE,
        ):
            return self.client.get(self.url)

    def test_page_renders_all_four_section_headings(self):
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, ">Tariff<")
        self.assertContains(response, "Wallbox API credentials")
        self.assertContains(response, "Report recipient")
        self.assertContains(response, "Eichrecht info")

    def test_old_tariff_url_is_404(self):
        response = self.client.get("/settings/tariff/")
        self.assertEqual(response.status_code, 404)

    def test_wallbox_api_form_saves_username_and_encrypts_password(self):
        response = self.client.post(
            self.url,
            data={
                "form_name": "wallbox_api",
                "keba_api_username": "admin",
                "keba_api_password": "topsecret",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.url + "#wallbox-api")

        s = AppSettings.current()
        self.assertEqual(s.keba_api_username, "admin")
        self.assertEqual(s.keba_api_password, "topsecret")

        # At-rest ciphertext check
        with connection.cursor() as cur:
            cur.execute(
                "SELECT keba_api_password FROM charging_appsettings WHERE id=1"
            )
            raw = cur.fetchone()[0]
        self.assertNotEqual(raw, "topsecret")
        self.assertTrue(raw.startswith("gAAAAA"))

    def test_wallbox_api_blank_password_preserves_existing(self):
        # Seed an initial password.
        s = AppSettings.current()
        s.keba_api_password = "original"
        s.save()

        # Submit the form with only the username changed; password blank.
        response = self.client.post(
            self.url,
            data={
                "form_name": "wallbox_api",
                "keba_api_username": "newuser",
                "keba_api_password": "",
            },
        )
        self.assertEqual(response.status_code, 302)

        fresh = AppSettings.objects.get(pk=1)
        self.assertEqual(fresh.keba_api_username, "newuser")
        self.assertEqual(fresh.keba_api_password, "original")

    def test_report_recipient_form_saves_email(self):
        response = self.client.post(
            self.url,
            data={
                "form_name": "report_recipient",
                "report_recipient_email": "hr@example.com",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.url + "#report-recipient")

        s = AppSettings.current()
        self.assertEqual(s.report_recipient_email, "hr@example.com")

    def test_eichrecht_archived_renders_serial_and_fingerprint(self):
        archived_view = {
            "archived": True,
            "serial": "34416115",
            "fingerprint": "deadbeef" * 8,
            "firmware_version": "P30 v 3.10.80",
            "dip_switch_settings": [False, False, True],
            "live_fetch_error": None,
        }
        with patch(
            "charging.views.fetch_wallbox_status",
            return_value=archived_view,
        ):
            response = self.client.get(self.url)
        self.assertContains(response, "34416115")
        self.assertContains(response, "deadbeef" * 8)
        self.assertContains(response, "P30 v 3.10.80")

    def test_eichrecht_live_fetch_error_renders_warning(self):
        partial = {
            "archived": True,
            "serial": "34416115",
            "fingerprint": "deadbeef" * 8,
            "firmware_version": None,
            "dip_switch_settings": None,
            "live_fetch_error": "TimeoutError: wallbox unreachable",
        }
        with patch(
            "charging.views.fetch_wallbox_status",
            return_value=partial,
        ):
            response = self.client.get(self.url)
        # Serial + fingerprint still appear (archived data).
        self.assertContains(response, "34416115")
        # Warning surfaces the underlying error class/message.
        self.assertContains(response, "Wallbox unreachable")
        self.assertContains(response, "TimeoutError")
