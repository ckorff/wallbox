"""Import charging sessions from the KEBA wallbox CSV export.

By default fetches over HTTP from the configured wallbox; ``--file`` reads
a local CSV instead (handy for testing against a known good export).
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from charging.keba_http import fetch_sessions_csv, parse_sessions_csv
from charging.services import ingest_csv_row


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
        if options["file"]:
            text = options["file"].read_text(encoding="utf-8")
        else:
            host = options["host"] or settings.KEBA_HOST
            if not host:
                raise CommandError("KEBA_HOST is not set in .env (or pass --host).")
            if not (settings.KEBA_USERNAME and settings.KEBA_PASSWORD):
                raise CommandError(
                    "KEBA_USERNAME and KEBA_PASSWORD must be set in .env."
                )
            text = fetch_sessions_csv(
                host, settings.KEBA_USERNAME, settings.KEBA_PASSWORD
            )

        rows = parse_sessions_csv(text)
        created = updated = skipped = 0
        for row in rows:
            obj, was_created = ingest_csv_row(row)
            if obj is None:
                skipped += 1
            elif was_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"KEBA import: {created} created, {updated} updated, "
                f"{skipped} skipped (0 kWh) of {len(rows)} rows"
            )
        )
