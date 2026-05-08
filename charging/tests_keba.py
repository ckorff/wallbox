from decimal import Decimal

from django.test import SimpleTestCase

from charging import keba
from charging.keba import ChargingState, KebaClient, KebaError, KebaState


class FakeTransport:
    """In-memory stand-in for the pymodbus transport, keyed by register address."""

    def __init__(self, registers: dict[int, int], fail: bool = False):
        self.registers = registers
        self.fail = fail
        self.closed = False

    def read_uint32(self, register: int) -> int:
        if self.fail:
            raise KebaError(f'simulated read failure at {register}')
        return self.registers[register]

    def close(self) -> None:
        self.closed = True


class KebaClientReadStateTests(SimpleTestCase):
    def _transport(self, overrides: dict[int, int] | None = None) -> FakeTransport:
        registers = {
            keba.REG_CHARGING_STATE: ChargingState.CHARGING.value,
            keba.REG_TOTAL_ENERGY: 12_345_678,    # Wh -> 12345.678 kWh
            keba.REG_SESSION_ENERGY: 42_500,      # Wh -> 42.500 kWh
        }
        if overrides:
            registers.update(overrides)
        return FakeTransport(registers)

    def test_returns_keba_state(self):
        client = KebaClient(self._transport())
        state = client.read_state()
        self.assertIsInstance(state, KebaState)

    def test_charging_state_decoded_to_enum(self):
        client = KebaClient(self._transport())
        self.assertEqual(client.read_state().charging_state, ChargingState.CHARGING)

    def test_suspended_state_decoded(self):
        client = KebaClient(self._transport(
            {keba.REG_CHARGING_STATE: ChargingState.SUSPENDED.value},
        ))
        self.assertEqual(client.read_state().charging_state, ChargingState.SUSPENDED)

    def test_energy_registers_converted_to_decimal_kwh(self):
        client = KebaClient(self._transport())
        state = client.read_state()
        self.assertIsInstance(state.total_energy_kwh, Decimal)
        self.assertEqual(state.total_energy_kwh, Decimal('12345.678'))
        self.assertEqual(state.session_energy_kwh, Decimal('42.500'))

    def test_unknown_charging_state_raises(self):
        client = KebaClient(self._transport({keba.REG_CHARGING_STATE: 999}))
        with self.assertRaises(KebaError):
            client.read_state()

    def test_transport_failure_propagates_as_kebaerror(self):
        client = KebaClient(FakeTransport({}, fail=True))
        with self.assertRaises(KebaError):
            client.read_state()


class KebaClientCloseTests(SimpleTestCase):
    def test_close_delegates_to_transport(self):
        transport = FakeTransport({})
        KebaClient(transport).close()
        self.assertTrue(transport.closed)
