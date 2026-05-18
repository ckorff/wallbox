"""Tests for the TariffDocument model — energy-supplier PDF history."""
import io
import tempfile
from datetime import date

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from pypdf import PdfReader, PdfWriter

from charging.models import TariffDocument


def _blank_pdf_bytes() -> bytes:
    """Return a 1-page blank PDF as raw bytes.

    Exercising pypdf in the model test set means an import-time problem
    surfaces here rather than only in the email-merge tests.
    """
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="wallbox-test-tariffdoc-"))
class TariffDocumentForDateTests(TestCase):
    def _create(self, valid_from, provider="Vattenfall"):
        return TariffDocument.objects.create(
            valid_from=valid_from,
            provider_name=provider,
            pdf=ContentFile(_blank_pdf_bytes(), name=f"{valid_from}.pdf"),
        )

    def test_returns_none_when_no_documents_exist(self):
        self.assertIsNone(TariffDocument.for_date(date(2026, 5, 1)))

    def test_returns_none_for_date_before_earliest(self):
        self._create(date(2026, 5, 1))
        self.assertIsNone(TariffDocument.for_date(date(2026, 4, 30)))

    def test_returns_most_recent_with_valid_from_le_d(self):
        d1 = self._create(date(2025, 1, 1), provider="OldCo")
        d2 = self._create(date(2026, 1, 1), provider="Vattenfall")
        d3 = self._create(date(2026, 5, 1), provider="Vattenfall")

        # exact boundary hits
        self.assertEqual(TariffDocument.for_date(date(2025, 1, 1)), d1)
        self.assertEqual(TariffDocument.for_date(date(2026, 1, 1)), d2)
        self.assertEqual(TariffDocument.for_date(date(2026, 5, 1)), d3)
        # between d2 and d3
        self.assertEqual(TariffDocument.for_date(date(2026, 4, 30)), d2)
        # well after d3
        self.assertEqual(TariffDocument.for_date(date(2030, 1, 1)), d3)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="wallbox-test-tariffdoc-"))
class TariffDocumentMiscTests(TestCase):
    def test_str_contains_provider_name(self):
        doc = TariffDocument.objects.create(
            valid_from=date(2026, 5, 1),
            provider_name="Vattenfall",
            pdf=ContentFile(_blank_pdf_bytes(), name="vattenfall.pdf"),
        )
        rendered = str(doc)
        self.assertTrue(rendered)
        self.assertIn("Vattenfall", rendered)

    def test_filefield_round_trip(self):
        payload = _blank_pdf_bytes()
        doc = TariffDocument.objects.create(
            valid_from=date(2026, 5, 1),
            provider_name="Vattenfall",
            pdf=ContentFile(payload, name="vattenfall.pdf"),
        )
        doc.refresh_from_db()
        with doc.pdf.open("rb") as f:
            stored = f.read()
        self.assertEqual(stored, payload)
        # And the bytes round-trip through pypdf as a real PDF.
        reader = PdfReader(io.BytesIO(stored))
        self.assertEqual(len(reader.pages), 1)
