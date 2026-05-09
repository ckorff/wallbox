"""Poll the KEBA wallbox over UDP and persist any new ChargingSessions."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from charging.keba import KebaClient, KebaError
from charging.services import ingest_session_report


class Command(BaseCommand):
    help = "Fetch reports 100..130 from the KEBA wallbox and upsert them."

    def handle(self, *args, **options):
        host = settings.KEBA_HOST
        if not host:
            raise CommandError("KEBA_HOST is not set in .env")

        client = KebaClient(host=host, port=settings.KEBA_UDP_PORT)

        created = updated = skipped = errored = 0
        for n in range(100, 131):
            try:
                report = client.request(f"report {n}")
            except (KebaError, OSError) as exc:
                self.stderr.write(self.style.WARNING(f"report {n}: {exc}"))
                errored += 1
                continue

            obj, was_created = ingest_session_report(report)
            if obj is None:
                skipped += 1
            elif was_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"KEBA poll: {created} created, {updated} updated, "
                f"{skipped} skipped, {errored} errored"
            )
        )
