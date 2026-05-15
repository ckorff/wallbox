"""Archive the wallbox's MVA public key for Eichrecht-compliant PDFs.

The wallbox returns its public key under ``/v2/wallboxes/{serial}`` as a
JSON-encoded string (``mvaPublicKey``). We extract the ``UK`` field
(the actual hex-encoded key) and persist it once under
``media/wallbox_mva_public_key.json`` so the PDF footer can quote the
serial and fingerprint without re-hitting the wallbox on every render.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from django.conf import settings


def _default_path() -> Path:
    return Path(settings.MEDIA_ROOT) / "wallbox_mva_public_key.json"


def ensure_wallbox_key_archived(
    client, serial: str, *, path: Path | None = None
) -> dict | None:
    """Idempotently persist the wallbox MVA public key.

    Returns the archived record ``{"wallbox_serial", "public_key_hex"}``,
    or ``None`` if the wallbox does not expose an MVA public key (older
    firmware). Subsequent calls re-read the file without hitting the API.
    """
    path = path or _default_path()
    if path.exists():
        return json.loads(path.read_text())

    info = client.get_wallbox_info(serial)
    raw_key = info.get("mvaPublicKey")
    if not raw_key:
        return None

    parsed = json.loads(raw_key)
    record = {
        "wallbox_serial": info["serialNumber"],
        "public_key_hex": parsed["UK"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2))
    return record


def public_key_fingerprint(record: dict) -> str:
    """SHA-256 hex digest (64 chars, lowercase) of the public-key hex string."""
    return hashlib.sha256(record["public_key_hex"].encode("utf-8")).hexdigest()


def load_archived_key(*, path: Path | None = None) -> dict | None:
    """Return the archived wallbox key record, or None if not yet archived.

    Read by PDF rendering so the footer can quote the serial and
    fingerprint without re-hitting the wallbox.
    """
    path = path or _default_path()
    if not path.exists():
        return None
    return json.loads(path.read_text())
