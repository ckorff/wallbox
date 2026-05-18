"""Tests for the /settings/ tariff-document sub-section views."""
import io
import tempfile
from datetime import date
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse
from pypdf import PdfWriter

from charging.models import TariffDocument


User = get_user_model()


def _blank_pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _pdf_upload(name="vattenfall.pdf"):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(name, _blank_pdf_bytes(), content_type="application/pdf")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="wallbox-test-tdoc-views-"))
class TariffDocumentAuthTests(TestCase):
    def setUp(self):
        self.create_url = reverse("tariff_document_create")

    def test_anonymous_create_redirects_to_login(self):
        response = self.client.post(self.create_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"].lower())

    def test_anonymous_delete_redirects_to_login(self):
        doc = TariffDocument.objects.create(
            valid_from=date(2026, 5, 1),
            provider_name="Vattenfall",
            pdf=ContentFile(_blank_pdf_bytes(), name="v.pdf"),
        )
        response = self.client.post(
            reverse("tariff_document_delete", args=[doc.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"].lower())

    def test_non_staff_create_redirects_to_login(self):
        User.objects.create_user(username="u", password="pw")
        self.client.login(username="u", password="pw")
        response = self.client.post(self.create_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"].lower())


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="wallbox-test-tdoc-views-"))
class TariffDocumentCreateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="staff", password="pw", is_staff=True
        )
        self.client.force_login(self.user)
        self.url = reverse("tariff_document_create")

    def test_staff_can_create_with_pdf(self):
        response = self.client.post(
            self.url,
            {
                "provider_name": "Vattenfall",
                "valid_from": "2026-05-01",
                "notes": "May tariff",
                "pdf": _pdf_upload(),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("settings_page") + "#tariff")
        self.assertEqual(TariffDocument.objects.count(), 1)
        doc = TariffDocument.objects.get()
        self.assertEqual(doc.provider_name, "Vattenfall")
        self.assertEqual(doc.valid_from, date(2026, 5, 1))
        self.assertEqual(doc.notes, "May tariff")
        self.assertTrue(doc.pdf.name)

        followed = self.client.get(reverse("settings_page"))
        msgs = [m.message for m in followed.context["messages"]]
        self.assertTrue(
            any("tariff document" in m.lower() for m in msgs),
            f"Expected success flash about the tariff document, got {msgs!r}",
        )

    def test_post_without_pdf_renders_settings_with_form_error(self):
        response = self.client.post(
            self.url,
            {
                "provider_name": "Vattenfall",
                "valid_from": "2026-05-01",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(TariffDocument.objects.count(), 0)
        form = response.context["tariff_document_form"]
        self.assertFalse(form.is_valid())
        self.assertIn("pdf", form.errors)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="wallbox-test-tdoc-views-"))
class TariffDocumentDeleteTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="staff", password="pw", is_staff=True
        )
        self.client.force_login(self.user)
        self.doc = TariffDocument.objects.create(
            valid_from=date(2026, 5, 1),
            provider_name="Vattenfall",
            pdf=ContentFile(_blank_pdf_bytes(), name="vattenfall-may.pdf"),
        )

    def test_staff_delete_removes_row_and_file(self):
        stored_path = Path(self.doc.pdf.path)
        self.assertTrue(stored_path.exists())

        response = self.client.post(
            reverse("tariff_document_delete", args=[self.doc.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("settings_page") + "#tariff")
        self.assertFalse(TariffDocument.objects.filter(pk=self.doc.pk).exists())
        self.assertFalse(stored_path.exists())

        followed = self.client.get(reverse("settings_page"))
        msgs = [m.message for m in followed.context["messages"]]
        self.assertTrue(
            any("tariff document" in m.lower() for m in msgs),
            f"Expected success flash about deletion, got {msgs!r}",
        )

    def test_delete_get_is_rejected(self):
        response = self.client.get(
            reverse("tariff_document_delete", args=[self.doc.pk])
        )
        # GET on a POST-only endpoint either 405s or redirects;
        # in either case the row must still exist.
        self.assertIn(response.status_code, (302, 405))
        self.assertTrue(TariffDocument.objects.filter(pk=self.doc.pk).exists())
