"""Fetch charging sessions from the KEBA wallbox over its REST API.

Pre-Phase-3 this command was the primary import mechanism. The dashboard
auto-import (see ``charging.services.auto_import``) now handles the
common case automatically; this CLI stays useful for cron-free ad-hoc
imports, debugging via ``-v 2`` and the ``KEBA_DUMP_DIR=…`` tee for
inspecting the raw API response.
"""
from django.core.management.base import BaseCommand, CommandError

from charging.services.import_runner import run_keba_import


class Command(BaseCommand):
    help = "Fetch the latest KEBA wallbox sessions and upsert ChargingSession rows."

    def handle(self, *args, **options):
        verbosity = options.get("verbosity", 1)
        log = self.stdout.write if verbosity >= 2 else None
        try:
            result = run_keba_import(log=log)
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
