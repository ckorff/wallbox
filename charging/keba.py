"""KEBA P30 x-series wallbox client (Modbus TCP).

Modbus is used over TCP — and not the KeContact UDP protocol — because the
wallbox sits on Wi-Fi with ~600 ms latency, where UDP packet loss without
retransmission was unreliable in practice.

Register addresses below are PLACEHOLDERS. Replace them with values from
the official KEBA P30 Modbus reference before talking to a real wallbox.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import IntEnum
from typing import Protocol


# TODO: confirm against the official KEBA P30 Modbus reference document.
# These addresses are placeholders chosen so the structure compiles; do not
# point this client at a real wallbox until they have been verified.
REG_CHARGING_STATE = 1000
REG_TOTAL_ENERGY = 1036
REG_SESSION_ENERGY = 1040
REG_SESSION_ID = 1500

# KEBA reports energy in 0.1 Wh per LSB; divide by 10000 to get kWh.
_ENERGY_DIVISOR = Decimal(10_000)
# Quantize to 4 decimal places so the Decimal output is consistent.
_ENERGY_QUANTUM = Decimal('0.0001')


class ChargingState(IntEnum):
    """KEBA P30 charging state values (function code 03, single uint32 register).

    Values are taken from the KEBA Modbus reference; verify before relying on
    them in production.
    """

    STARTUP = 0
    NOT_READY = 1
    READY = 2
    CHARGING = 3
    ERROR = 4
    AUTHORIZATION_REJECTED = 5


class KebaError(RuntimeError):
    """Raised when a KEBA read fails or returns a value we cannot decode."""


@dataclass(frozen=True)
class KebaState:
    charging_state: ChargingState
    total_energy_kwh: Decimal
    session_energy_kwh: Decimal
    session_id: int


class KebaTransport(Protocol):
    def read_uint32(self, register: int) -> int: ...
    def close(self) -> None: ...


class PymodbusTransport:
    """Concrete transport built on pymodbus' synchronous TCP client.

    Each KEBA value sits in two consecutive 16-bit holding registers
    encoded big-endian, which is what the P30 Modbus map specifies.
    """

    def __init__(self, host: str, port: int = 502, unit_id: int = 1, timeout: float = 3.0):
        # Imported lazily so tests that use a fake transport don't need
        # pymodbus installed.
        from pymodbus.client import ModbusTcpClient

        self._client = ModbusTcpClient(host, port=port, timeout=timeout)
        self._unit_id = unit_id

    def read_uint32(self, register: int) -> int:
        if not self._client.connected:
            self._client.connect()
        result = self._client.read_holding_registers(
            address=register, count=2, slave=self._unit_id,
        )
        if result.isError():
            raise KebaError(f'KEBA read failed at register {register}: {result}')
        hi, lo = result.registers
        return (hi << 16) | lo

    def close(self) -> None:
        self._client.close()


class KebaClient:
    """High-level client returning typed snapshots of the wallbox state."""

    def __init__(self, transport: KebaTransport):
        self._transport = transport

    @classmethod
    def connect(cls, host: str, port: int = 502, unit_id: int = 1) -> 'KebaClient':
        return cls(PymodbusTransport(host, port=port, unit_id=unit_id))

    def read_state(self) -> KebaState:
        raw_state = self._transport.read_uint32(REG_CHARGING_STATE)
        try:
            charging_state = ChargingState(raw_state)
        except ValueError as exc:
            raise KebaError(f'Unknown KEBA charging state: {raw_state}') from exc

        total = self._decode_energy(self._transport.read_uint32(REG_TOTAL_ENERGY))
        session = self._decode_energy(self._transport.read_uint32(REG_SESSION_ENERGY))
        session_id = self._transport.read_uint32(REG_SESSION_ID)

        return KebaState(
            charging_state=charging_state,
            total_energy_kwh=total,
            session_energy_kwh=session,
            session_id=session_id,
        )

    def close(self) -> None:
        self._transport.close()

    @staticmethod
    def _decode_energy(raw_units_of_0_1_wh: int) -> Decimal:
        return (Decimal(raw_units_of_0_1_wh) / _ENERGY_DIVISOR).quantize(_ENERGY_QUANTUM)
