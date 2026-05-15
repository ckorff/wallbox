"""Tests for the Phase 2.8 settings page and AppSettings singleton."""
from django.db import connection
from django.test import TestCase

from charging.models import AppSettings


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
