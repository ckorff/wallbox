"""OCPP 1.6-J WebSocket server.

The wallbox is the OCPP "Charge Point" (CP) and the connecting party; this
process is the "Central System" (CS) that listens. One physical wallbox is
expected, but the URL path (/ocpp/<cp_id>) is preserved so the server can
serve multiple chargeBoxIds without code changes if a second wallbox ever
appears.

Authentication is HTTP Basic Auth, checked at the WebSocket handshake by
websockets' built-in basic_auth process_request hook. Credentials live in
.env (OCPP_BASIC_AUTH_USERNAME / _PASSWORD) and are mirrored into the
wallbox's web UI under "OCPP backend".
"""

from __future__ import annotations

import logging

from django.conf import settings
from websockets.asyncio.server import ServerConnection, basic_auth, serve
from websockets.exceptions import ConnectionClosed

from charging.ocpp_handler import KebaChargePoint


logger = logging.getLogger(__name__)

OCPP_URL_PREFIX = '/ocpp/'


def extract_charge_point_id(path: str, prefix: str = OCPP_URL_PREFIX) -> str | None:
    """Return the chargeBoxId from a /ocpp/<cp_id> path, or None if invalid."""
    if not path.startswith(prefix):
        return None
    cp_id = path[len(prefix):].split('?')[0].rstrip('/')
    return cp_id or None


async def _handler(connection: ServerConnection) -> None:
    path = connection.request.path
    cp_id = extract_charge_point_id(path)
    if not cp_id:
        logger.warning('Rejecting OCPP connection: bad path %r', path)
        await connection.close(code=1008, reason='Invalid path')
        return
    logger.info('OCPP charge point connected: %s from %s', cp_id, connection.remote_address)
    cp = KebaChargePoint(id=cp_id, connection=connection)
    try:
        await cp.start()
    except ConnectionClosed:
        logger.info('OCPP charge point %s disconnected', cp_id)


async def run() -> None:
    host = settings.OCPP_LISTEN_HOST
    port = settings.OCPP_LISTEN_PORT
    username = settings.OCPP_BASIC_AUTH_USERNAME
    password = settings.OCPP_BASIC_AUTH_PASSWORD
    if not username or not password:
        raise RuntimeError(
            'OCPP_BASIC_AUTH_USERNAME and OCPP_BASIC_AUTH_PASSWORD must be set in .env',
        )
    process_request = basic_auth(
        realm='Wallbox OCPP',
        credentials=(username, password),
    )
    logger.info(
        'OCPP server listening on ws://%s:%s%s<chargeBoxId>',
        host, port, OCPP_URL_PREFIX,
    )
    async with serve(
        _handler, host, port,
        subprotocols=['ocpp1.6'],
        process_request=process_request,
    ) as server:
        await server.serve_forever()
