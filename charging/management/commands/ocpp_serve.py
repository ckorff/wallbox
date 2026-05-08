import asyncio
import logging

from django.core.management.base import BaseCommand

from charging.ocpp_server import run


class Command(BaseCommand):
    help = (
        'Run the OCPP 1.6-J WebSocket server. The wallbox connects here over '
        'ws://<host>:<port>/ocpp/<chargeBoxId> and pushes session events.'
    )

    def handle(self, *args, **options):
        # Surface OCPP/handler logs to stdout so this is useful as a foreground
        # process; systemd captures it into the journal in production.
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s %(name)s %(levelname)s: %(message)s',
        )
        try:
            asyncio.run(run())
        except KeyboardInterrupt:
            pass
