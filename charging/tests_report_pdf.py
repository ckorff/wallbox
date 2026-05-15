"""Tests for PDF rendering of MonthlyReport rows (Phase 2.4)."""
import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from django.template.loader import render_to_string

from charging.models import ChargingSession, MonthlyReport, Tariff
from charging.services.pdf import (
    attach_pdf_to_report,
    build_report_context,
    render_report_pdf,
)
from charging.services.reports import generate_monthly_report


BERLIN = ZoneInfo("Europe/Berlin")

# Deterministic reporter/vehicle profile for test assertions.
# Decoupled from .env so tests don't depend on the developer's real values.
REPORTER_OVERRIDES = {
    "REPORTER_NAME": "Test Reporter",
    "REPORTER_EMPLOYEE_ID": "EMP-42",
    "VEHICLE_MAKE_MODEL": "Test Make Model",
    "VEHICLE_LICENSE_PLATE": "TS T 1234",
    "CHARGING_LOCATION": "Teststraße 1, 12345 Testtown, Country",
}


def _session(started_local, energy_kwh, ended_local=None, serial="KEBA-1"):
    return ChargingSession.objects.create(
        serial=serial,
        started_at=started_local,
        ended_at=ended_local,
        energy_kwh=Decimal(energy_kwh),
        raw_row={},
    )


def _dt(year, month, day, hour=12, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=BERLIN)


def _seed_basic_report(year=2026, month=5):
    """Tariff + a couple of sessions + a generated report."""
    Tariff.objects.create(
        valid_from=date(2026, 1, 1),
        energy_price_ct_per_kwh=Decimal("38.500"),
    )
    _session(_dt(year, month, 5, 9, 30), Decimal("4.000"))
    _session(_dt(year, month, 17, 19, 15), Decimal("6.500"))
    return generate_monthly_report(year, month)


@override_settings(**REPORTER_OVERRIDES)
class RenderReportPdfTests(TestCase):
    def test_returns_bytes_starting_with_pdf_magic(self):
        report = _seed_basic_report()

        pdf = render_report_pdf(report)

        self.assertIsInstance(pdf, (bytes, bytearray))
        self.assertEqual(pdf[:5], b"%PDF-")
        self.assertGreater(len(pdf), 1000)


@override_settings(**REPORTER_OVERRIDES)
class ReportHtmlContentTests(TestCase):
    """Test the HTML template (faster, no WeasyPrint round-trip)."""

    def _render_html(self, report):
        context = build_report_context(report)
        return render_to_string("charging/report_pdf.html", context)

    def test_html_contains_month_and_year_header(self):
        report = _seed_basic_report(2026, 5)
        html = self._render_html(report)

        self.assertIn("May 2026", html)

    def test_html_contains_reporter_profile_from_settings(self):
        report = _seed_basic_report()
        html = self._render_html(report)

        self.assertIn("Test Reporter", html)
        self.assertIn("EMP-42", html)
        self.assertIn("Test Make Model", html)
        self.assertIn("TS T 1234", html)
        self.assertIn("Teststraße 1, 12345 Testtown, Country", html)

    def test_html_uses_only_english_long_form_dates(self):
        report = _seed_basic_report()
        html = self._render_html(report)

        # No German numeric date pattern (DD.MM.YYYY) anywhere.
        self.assertNotRegex(html, r"\b\d{2}\.\d{2}\.\d{4}\b")
        # And the English form is actually present.
        self.assertIn("May 2026", html)

    def test_html_contains_grand_total_with_euro_and_two_decimals(self):
        report = _seed_basic_report()
        html = self._render_html(report)

        # Total = 10.5 × 0.385 = 4.0425 -> 4.04
        self.assertEqual(report.total_amount_eur, Decimal("4.04"))
        self.assertIn("€", html)
        self.assertIn("4.04", html)

    def test_html_uses_css_custom_properties_for_design_tokens(self):
        report = _seed_basic_report()
        html = self._render_html(report)

        # The :root design-token block is the foundation of the styling;
        # asserting on the custom-property name guards against regressing
        # to ad-hoc inline styles.
        self.assertIn("--color-accent", html)

    def test_html_contains_a_row_per_session(self):
        report = _seed_basic_report()
        html = self._render_html(report)

        sessions = list(
            ChargingSession.objects.filter(
                started_at__gte=datetime(2026, 5, 1, tzinfo=BERLIN),
                started_at__lt=datetime(2026, 6, 1, tzinfo=BERLIN),
            )
        )
        self.assertEqual(len(sessions), 2)
        # Per-session kWh strings present
        self.assertIn("4.000", html)
        self.assertIn("6.500", html)
        # Per-session dates present in English long form, no leading zero on day.
        self.assertIn("5 May 2026", html)
        self.assertIn("17 May 2026", html)


@override_settings(MEDIA_ROOT=str(Path("/tmp/wallbox-test-media")), **REPORTER_OVERRIDES)
class AttachPdfToReportTests(TestCase):
    def setUp(self):
        media = Path("/tmp/wallbox-test-media")
        if media.exists():
            for f in media.rglob("*"):
                if f.is_file():
                    f.unlink()

    def test_saves_pdf_to_report(self):
        report = _seed_basic_report()

        attach_pdf_to_report(report)

        report.refresh_from_db()
        self.assertTrue(report.pdf)
        self.assertTrue(report.pdf.name.endswith(".pdf"))
        self.assertGreater(report.pdf.size, 0)
        self.assertTrue(Path(report.pdf.path).exists())

    def test_filename_pattern(self):
        report = _seed_basic_report(2026, 5)

        attach_pdf_to_report(report)

        report.refresh_from_db()
        self.assertTrue(report.pdf.name.endswith("report-2026-05.pdf"))

    def test_regeneration_replaces_file_on_disk(self):
        report = _seed_basic_report()

        attach_pdf_to_report(report)
        report.refresh_from_db()
        first_path = Path(report.pdf.path)
        first_bytes = first_path.read_bytes()
        self.assertGreater(len(first_bytes), 0)

        # Mutate the report so the rendered content changes.
        _session(_dt(2026, 5, 25, 8, 0), Decimal("2.500"))
        report = generate_monthly_report(2026, 5)

        attach_pdf_to_report(report)
        report.refresh_from_db()
        second_path = Path(report.pdf.path)
        second_bytes = second_path.read_bytes()

        self.assertTrue(second_path.exists())
        self.assertNotEqual(first_bytes, second_bytes)

        # No leftover orphan file: only one PDF should remain in the dir
        # for this (year, month).
        media_reports = Path(report.pdf.storage.location) / "reports"
        if media_reports.exists():
            files = sorted(media_reports.glob("report-2026-05*.pdf"))
            self.assertEqual(len(files), 1, files)

    def test_returns_saved_report(self):
        report = _seed_basic_report()

        result = attach_pdf_to_report(report)

        self.assertEqual(result.pk, report.pk)
        self.assertTrue(bool(result.pdf))


@override_settings(
    MEDIA_ROOT=str(Path("/tmp/wallbox-test-media-eichrecht")),
    **REPORTER_OVERRIDES,
)
class EichrechtPdfTests(TestCase):
    """Phase 2.7: Signed column + Eichrecht footer."""

    def setUp(self):
        self.media = Path("/tmp/wallbox-test-media-eichrecht")
        self.media.mkdir(parents=True, exist_ok=True)
        # Start each test from a known no-key state.
        key_file = self.media / "wallbox_mva_public_key.json"
        if key_file.exists():
            key_file.unlink()

    def _seed_signed_and_unsigned(self):
        Tariff.objects.create(
            valid_from=date(2026, 1, 1),
            energy_price_ct_per_kwh=Decimal("38.500"),
        )
        ChargingSession.objects.create(
            serial="34416115",
            started_at=_dt(2026, 5, 5, 9, 30),
            ended_at=_dt(2026, 5, 5, 10, 30),
            energy_kwh=Decimal("4.000"),
            raw_row={},
            mva_record_data='{"FV":"1.1"}',
            mva_record_signature='{"SD":"3046..."}',
        )
        ChargingSession.objects.create(
            serial="34416115",
            started_at=_dt(2026, 5, 17, 19, 15),
            ended_at=_dt(2026, 5, 17, 20, 30),
            energy_kwh=Decimal("6.500"),
            raw_row={},
            # No MVA records (e.g., CSV-imported before Phase 2.7)
        )
        return generate_monthly_report(2026, 5)

    def _archive_test_key(self, hex_key="3059ABCD"):
        (self.media / "wallbox_mva_public_key.json").write_text(
            json.dumps(
                {"wallbox_serial": "34416115", "public_key_hex": hex_key}
            )
        )

    def _render_html(self, report):
        return render_to_string(
            "charging/report_pdf.html", build_report_context(report)
        )

    def test_html_includes_signed_column_header(self):
        report = self._seed_signed_and_unsigned()
        html = self._render_html(report)

        self.assertIn("Signed", html)

    def test_html_marks_signed_session_and_leaves_unsigned_blank(self):
        report = self._seed_signed_and_unsigned()
        html = self._render_html(report)

        # Exactly one ✓ for the one session with mva_record_signature set.
        self.assertEqual(html.count("✓"), 1)

    def test_html_includes_eichrecht_footer_when_key_archived(self):
        self._archive_test_key("3059ABCD")
        report = self._seed_signed_and_unsigned()
        html = self._render_html(report)

        self.assertIn("Eichrecht", html)
        self.assertIn("34416115", html)
        expected_fp = hashlib.sha256(b"3059ABCD").hexdigest()
        self.assertIn(expected_fp, html)

    def test_html_omits_eichrecht_footer_when_key_missing(self):
        # No _archive_test_key call — file absent.
        report = self._seed_signed_and_unsigned()
        html = self._render_html(report)

        # Footer block (which is the only place "Eichrecht" appears)
        # must not render without an archived key.
        self.assertNotIn("Eichrecht", html)
