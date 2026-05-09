"""UDP client for the KEBA P30 KeContact report protocol.

The wallbox listens on UDP port 7090 and responds to ASCII commands
(`report 1`, `report 2`, `report 100`...`report 130`, ...) with a
JSON-encoded payload. Reports 100..130 expose the most recent 30
charging sessions.
"""
from __future__ import annotations

import json
import socket


DEFAULT_PORT = 7090
DEFAULT_TIMEOUT = 2.0
RECV_BUFSIZE = 4096


class KebaError(Exception):
    """Raised when the wallbox returns an unparseable response."""


class KebaClient:
    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.host = host
        self.port = port
        self.timeout = timeout

    def request(self, command: str) -> dict:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(self.timeout)
            sock.sendto(command.encode("ascii"), (self.host, self.port))
            data, _ = sock.recvfrom(RECV_BUFSIZE)

        text = data.decode("utf-8", errors="replace").strip().rstrip("\x00").strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise KebaError(
                f"Invalid JSON for {command!r}: {text!r}"
            ) from exc

    def fetch_session_reports(self) -> list[dict]:
        return [self.request(f"report {n}") for n in range(100, 131)]
