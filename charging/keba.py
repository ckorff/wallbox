"""KEBA P30 x-series wallbox client (Modbus TCP).

Modbus is used over TCP — and not the KeContact UDP protocol — because the
wallbox sits on Wi-Fi with ~600 ms latency, where UDP packet loss without
retransmission was unreliable in practice.

Register map and protocol details verified against the KEBA "KeContact P30
Modbus TCP Programmers Guide" V1.07 (December 2025), KEBA document 132578,
applicable to both c-series and x-series:
https://www.keba.com/download/x/44932c2bc8/kecontactp30modbustcp_pgen.pdf

Each value is a UINT32 spanning two consecutive 16-bit registers,
big-endian (hi register first), read via function code FC3.

Operational notes from the same reference:
- Unit ID must be 255 (KEBA-specific; not the usual 1).
- Modbus TCP must be enabled on the wallbox (DSW1.3 = ON).
- Minimum firmware: x-series 1.11 / c-series 3.10.16.
- Modbus TCP and the UDP/KeContact interface are mutually exclusive.
- Recommended read interval >= 0.5 s; write interval >= 5 s.
- The wallbox listens on TCP/502 (Modbus default). Some configurations
  also expose a listener on TCP/1502 that accepts connections but
  rejects every read with "Illegal Data Address"; if you see Illegal
  Data Address on registers known to be valid, double-check KEBA_PORT.

Note: V1.04 of the same guide (which an earlier draft of this module
followed) documented the energy registers as "Wh per LSB"; KEBA flagged
this as a documentation error in V1.06 (July 2024) and the actual unit
is 0.1 Wh per LSB.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from enum import IntEnum
from typing import Protocol


# UINT32 register addresses (FC3, width=2, big-endian).
REG_CHARGING_STATE = 1000
REG_FIRMWARE = 1018
REG_TOTAL_ENERGY = 1036
REG_SESSION_ENERGY = 1502

# KEBA reports energy in 0.1 Wh per LSB; divide by 10000 to get kWh.
_ENERGY_DIVISOR = Decimal(10_000)
_ENERGY_QUANTUM = Decimal('0.0001')

KEBA_UNIT_ID = 255

# KEBA Modbus TCP Programmers Guide V1.07 recommends >= 0.5 s between reads.
# Empirically, a fresh TCP connection also needs a discard read followed by
# this same delay before subsequent reads land reliably (observed on
# firmware 3.10.80: without the delay, register 1000 returned Illegal
# Data Address despite being valid).
MIN_READ_INTERVAL_S = 0.5


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
        # Connect eagerly: KEBA P30 firmware 3.10.80 misbehaves when the
        # first holding-register read also has to establish the TCP
        # connection (returns Illegal Data Address even for valid registers).
        if not self._client.connect():
            raise KebaError(f'Could not connect to KEBA wallbox at {host}:{port}')
        self._unit_id = unit_id

    def read_uint32(self, register: int) -> int:
        # Lazy import so SimpleTestCase tests don't have to hit pymodbus.
        from pymodbus.exceptions import ModbusException

        try:
            result = self._client.read_holding_registers(
                address=register, count=2, device_id=self._unit_id,
            )
        except (ModbusException, ConnectionError, OSError) as exc:
            raise KebaError(f'KEBA read failed at register {register}: {exc}') from exc
        if result.isError():
            raise KebaError(f'KEBA read failed at register {register}: {result}')
        hi, lo = result.registers
        return (hi << 16) | lo

    def close(self) -> None:
        self._client.close()


class KebaClient:
    """High-level client returning typed snapshots of the wallbox state."""

    def __init__(self, transport: KebaTransport, read_interval_s: float = MIN_READ_INTERVAL_S):
        self._transport = transport
        self._read_interval_s = read_interval_s

    @classmethod
    def connect(
        cls,
        host: str,
        port: int = 502,
        unit_id: int = KEBA_UNIT_ID,
        read_interval_s: float = MIN_READ_INTERVAL_S,
    ) -> 'KebaClient':
        client = cls(
            PymodbusTransport(host, port=port, unit_id=unit_id),
            read_interval_s=read_interval_s,
        )
        client.warmup()
        return client

    def warmup(self) -> None:
        """Discard one read so the first real read is not silently dropped.

        On firmware 3.10.80, the very first holding-register read after a
        fresh TCP connection is unreliable: the wallbox may close the
        connection, time out, or reply with Illegal Data Address even for
        valid registers. A throwaway read of REG_FIRMWARE (a static value
        present on every supported firmware) followed by the standard
        inter-read delay makes the next read reliable.
        """
        try:
            self._transport.read_uint32(REG_FIRMWARE)
        except KebaError:
            pass
        self._sleep_between_reads()

    def read_state(self) -> KebaState:
        raw_state = self._transport.read_uint32(REG_CHARGING_STATE)
        try:
            charging_state = ChargingState(raw_state)
        except ValueError as exc:
            raise KebaError(f'Unknown KEBA charging state: {raw_state}') from exc

        self._sleep_between_reads()
        total = self._decode_energy(self._transport.read_uint32(REG_TOTAL_ENERGY))
        self._sleep_between_reads()
        session = self._decode_energy(self._transport.read_uint32(REG_SESSION_ENERGY))

        return KebaState(
            charging_state=charging_state,
            total_energy_kwh=total,
            session_energy_kwh=session,
        )

    def close(self) -> None:
        self._transport.close()

    def _sleep_between_reads(self) -> None:
        if self._read_interval_s > 0:
            time.sleep(self._read_interval_s)

    @staticmethod
    def _decode_energy(raw_units_of_0_1_wh: int) -> Decimal:
        return (Decimal(raw_units_of_0_1_wh) / _ENERGY_DIVISOR).quantize(_ENERGY_QUANTUM)
