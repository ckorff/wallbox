from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from charging.keba import KebaClient, KebaError


class Command(BaseCommand):
    help = 'Read the current state of the KEBA wallbox via Modbus TCP and print it.'

    def handle(self, *args, **options):
        host = settings.KEBA_HOST
        if not host:
            raise CommandError(
                'KEBA_HOST is not configured. Set the wallbox IP in .env (KEBA_HOST=...).'
            )
        port = settings.KEBA_PORT

        client = KebaClient.connect(host, port=port)
        try:
            state = client.read_state()
        except KebaError as exc:
            raise CommandError(f'KEBA read failed: {exc}') from exc
        finally:
            client.close()

        self.stdout.write(self.style.SUCCESS(f'KEBA wallbox at {host}:{port}'))
        self.stdout.write(f'  charging state: {state.charging_state.name} ({state.charging_state.value})')
        self.stdout.write(f'  total energy:   {state.total_energy_kwh} kWh')
        self.stdout.write(f'  session energy: {state.session_energy_kwh} kWh')
