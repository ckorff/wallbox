"""KEBA P30 x-series wallbox client (Modbus TCP).

Modbus is used over TCP — and not the KeContact UDP protocol — because the
wallbox sits on Wi-Fi with ~600 ms latency, where UDP packet loss without
retransmission was unreliable in practice.

Register map and protocol details verified against the KEBA "KeContact P30
Charging Station Modbus TCP Programmers Guide" V1.04 (2022); applies to
both c-series and x-series. Each value is a UINT32 spanning two
consecutive 16-bit registers, big-endian (hi register first), read via
function code FC3 (read holding registers).

Operational notes from the same reference:
- Unit ID must be 255 (KEBA-specific; not the usual 1).
- Modbus TCP must be enabled on the wallbox (DSW1.3 = ON).
- Minimum firmware: x-series 1.11 / c-series 3.10.16.
- Modbus TCP and the UDP/KeContact interface are mutually exclusive.
- Recommended read interval >= 0.5 s; write interval >= 5 s.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import IntEnum
from typing import Protocol


# UINT32 register addresses (FC3, width=2, big-endian).
REG_CHARGING_STATE = 1000
REG_TOTAL_ENERGY = 1036
REG_SESSION_ENERGY = 1502

# KEBA reports energy in Wh per LSB; divide by 1000 to get kWh.
_ENERGY_DIVISOR = Decimal(1_000)
_ENERGY_QUANTUM = Decimal('0.001')

KEBA_UNIT_ID = 255


class ChargingState(IntEnum):
    """KEBA P30 charging state, verified against the V1.04 Modbus guide."""

    STARTUP = 0
    NOT_READY = 1
    READY = 2
    CHARGING = 3
    ERROR = 4
    # Charging temporarily interrupted: over-temperature or suspended mode.
    SUSPENDED = 5


class KebaError(RuntimeError):
    """Raised when a KEBA read fails or returns a value we cannot decode."""


@dataclass(frozen=True)
class KebaState:
    charging_state: ChargingState
    total_energy_kwh: Decimal
    session_energy_kwh: Decimal


class KebaTransport(Protocol):
    def read_uint32(self, register: int) -> int: ...
    def close(self) -> None: ...


class PymodbusTransport:
    """Concrete transport built on pymodbus' synchronous TCP client.

    Each KEBA value sits in two consecutive 16-bit holding registers
    encoded big-endian, which is what the P30 Modbus map specifies.
    """

    def __init__(self, host: str, port: int = 502, unit_id: int = KEBA_UNIT_ID, timeout: float = 3.0):
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
    def connect(cls, host: str, port: int = 502, unit_id: int = KEBA_UNIT_ID) -> 'KebaClient':
        return cls(PymodbusTransport(host, port=port, unit_id=unit_id))

    def read_state(self) -> KebaState:
        raw_state = self._transport.read_uint32(REG_CHARGING_STATE)
        try:
            charging_state = ChargingState(raw_state)
        except ValueError as exc:
            raise KebaError(f'Unknown KEBA charging state: {raw_state}') from exc

        total = self._decode_energy(self._transport.read_uint32(REG_TOTAL_ENERGY))
        session = self._decode_energy(self._transport.read_uint32(REG_SESSION_ENERGY))

        return KebaState(
            charging_state=charging_state,
            total_energy_kwh=total,
            session_energy_kwh=session,
        )

    def close(self) -> None:
        self._transport.close()

    @staticmethod
    def _decode_energy(raw_wh: int) -> Decimal:
        return (Decimal(raw_wh) / _ENERGY_DIVISOR).quantize(_ENERGY_QUANTUM)
