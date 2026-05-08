from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, override_settings

from charging.keba import ChargingState, KebaError, KebaState


class FakeClient:
    def __init__(self, state: KebaState | None = None, error: Exception | None = None):
        self.state = state
        self.error = error
        self.closed = False

    def read_state(self) -> KebaState:
        if self.error:
            raise self.error
        assert self.state is not None
        return self.state

    def close(self) -> None:
        self.closed = True


@override_settings(KEBA_HOST='192.168.1.50', KEBA_PORT=502)
class KebaStatusSuccessTests(SimpleTestCase):
    def _state(self) -> KebaState:
        return KebaState(
            charging_state=ChargingState.CHARGING,
            total_energy_kwh=Decimal('1234.5678'),
            session_energy_kwh=Decimal('42.5000'),
        )

    def test_prints_state_when_connection_succeeds(self):
        client = FakeClient(state=self._state())
        with patch('charging.management.commands.keba_status.KebaClient.connect', return_value=client) as connect:
            stdout = StringIO()
            call_command('keba_status', stdout=stdout)
        connect.assert_called_once_with('192.168.1.50', port=502)
        output = stdout.getvalue()
        self.assertIn('192.168.1.50', output)
        self.assertIn('CHARGING', output)
        self.assertIn('1234.5678', output)
        self.assertIn('42.5000', output)
        self.assertTrue(client.closed)

    def test_kebaerror_raises_commanderror_and_closes_client(self):
        client = FakeClient(error=KebaError('register 1000 unreachable'))
        with patch('charging.management.commands.keba_status.KebaClient.connect', return_value=client):
            with self.assertRaises(CommandError) as ctx:
                call_command('keba_status', stdout=StringIO(), stderr=StringIO())
        self.assertIn('register 1000 unreachable', str(ctx.exception))
        self.assertTrue(client.closed)


@override_settings(KEBA_HOST='', KEBA_PORT=502)
class KebaStatusMissingHostTests(SimpleTestCase):
    def test_missing_host_raises_commanderror(self):
        with self.assertRaises(CommandError) as ctx:
            call_command('keba_status', stdout=StringIO(), stderr=StringIO())
        self.assertIn('KEBA_HOST', str(ctx.exception))
