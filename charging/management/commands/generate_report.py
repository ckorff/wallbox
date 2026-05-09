"""Generate (or regenerate) the monthly cost report PDF for a given month."""
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from charging.services.pdf import attach_pdf_to_report
from charging.services.reports import (
    MissingTariffError,
    generate_monthly_report,
)


class Command(BaseCommand):
    help = (
        "Generate the monthly charging cost report for the given (year, month)."
        " Replaces any existing report row and PDF file for that month."
    )

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, required=True, help="Calendar year, e.g. 2026")
        parser.add_argument("--month", type=int, required=True, help="Calendar month 1–12")

    def handle(self, *args, **options):
        year = options["year"]
        month = options["month"]

        if not (1 <= month <= 12):
            raise CommandError(
                f"Invalid month {month!r}: must be between 1 and 12."
            )

        label = date(year, month, 1).strftime("%B %Y")

        try:
            report = generate_monthly_report(year, month)
        except MissingTariffError as exc:
            raise CommandError(
                f"Cannot generate report for {label}: {exc}"
            ) from exc

        attach_pdf_to_report(report)
        report.refresh_from_db()

        pdf_path = report.pdf.path if report.pdf else "(no file)"
        self.stdout.write(
            self.style.SUCCESS(
                f"Report {year}-{month:02d}: total € {report.total_amount_eur} "
                f"→ {pdf_path}"
            )
        )
