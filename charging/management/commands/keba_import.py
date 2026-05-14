"""Import charging sessions from the KEBA wallbox CSV export.

By default fetches over HTTP from the configured wallbox; ``--file`` reads
a local CSV instead (handy for testing against a known good export).
"""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from charging.services.import_runner import run_keba_import


class Command(BaseCommand):
    help = "Fetch the KEBA charging-session CSV and upsert ChargingSession rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=Path,
            help="Read CSV from a local file instead of fetching over HTTP.",
        )
        parser.add_argument(
            "--host",
            help="Override settings.KEBA_HOST (HTTP fetch only).",
        )

    def handle(self, *args, **options):
        verbosity = options.get("verbosity", 1)
        log = self.stdout.write if verbosity >= 2 else None
        try:
            result = run_keba_import(
                file=options["file"], host=options["host"], log=log
            )
        except RuntimeError as exc:
            raise CommandError(str(exc))

        self.stdout.write(
            self.style.SUCCESS(
                f"KEBA import: {result.sessions_imported} created, "
                f"{result.sessions_updated} updated, "
                f"{result.sessions_skipped} skipped (0 kWh) "
                f"of {result.rows_seen} rows"
            )
        )
