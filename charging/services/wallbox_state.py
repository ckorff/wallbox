"""Live wallbox state for the dashboard (Phase 2.9).

Fetches ``/v2/wallboxes/{serial}/state`` on every dashboard render, with
an optional follow-up to ``/v2/wallboxes/{serial}`` for the current
power when the wallbox is CHARGING. Falls back to a cached last-known
state when the wallbox is unreachable, mirroring the token-cache
pattern under ``media/``.

The serial is read from the archived MVA public-key file (Phase 2.7),
so if no import has ever succeeded the dashboard shows a "not linked"
stub and never tries to build the client.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from django.conf import settings

from charging.services.wallbox_key import load_archived_key


def _cache_path() -> Path:
    return Path(settings.MEDIA_ROOT) / ".wallbox_state.json"


def format_power_kw(milliwatts: Optional[int]) -> Optional[str]:
    """Format ``meter.totalActivePower`` (mW on the wire) as ``"X.X kW"``."""
    if milliwatts is None:
        return None
    return f"{milliwatts / 1_000_000:.1f} kW"


@dataclass
class LiveStateView:
    state: Optional[str] = None
    power_kw_display: Optional[str] = None
    error_code: Optional[str] = None
    fetched_at: Optional[str] = None
    stale: bool = False
    last_seen_at: Optional[str] = None
    unreachable_reason: Optional[str] = None
    not_linked: bool = False
    credentials_missing: bool = False


def fetch_live_state() -> LiveStateView:
    """Return the current wallbox state for the dashboard.

    Order of operations:

    1. If no archived MVA key exists, we don't know the serial — bail
       out with ``not_linked=True``; the user must run an import first.
    2. Try to build the API client. ``RuntimeError`` from
       ``build_keba_client`` means credentials are missing — surface
       that distinctly so the dashboard can link to settings.
    3. Call ``/state``; on ``CHARGING`` follow up with the full info
       endpoint for ``meter.totalActivePower``.
    4. On any other exception, return the last successful state from
       the on-disk cache with ``stale=True``. No cache → just the
       unreachable reason.
    """
    from charging.services.keba_client import build_keba_client

    archived = load_archived_key()
    if not archived:
        return LiveStateView(not_linked=True)

    serial = archived["wallbox_serial"]

    try:
        client = build_keba_client()
    except RuntimeError as exc:
        return LiveStateView(
            credentials_missing=True,
            unreachable_reason=str(exc),
        )

    try:
        state_payload = client.get_state(serial)
        state = state_payload.get("state")
        power_kw_display: Optional[str] = None
        error_code: Optional[str] = None
        if state == "CHARGING":
            info = client.get_wallbox_info(serial)
            meter = info.get("meter") or {}
            power_kw_display = format_power_kw(meter.get("totalActivePower"))
        elif state == "ERROR":
            info = client.get_wallbox_info(serial)
            error_code = info.get("errorCode") or state_payload.get("errorCode")
    except Exception as exc:
        cached = _load_cache()
        if cached is None:
            return LiveStateView(
                unreachable_reason=f"{type(exc).__name__}: {exc}",
            )
        return LiveStateView(
            state=cached.get("state"),
            power_kw_display=cached.get("power_kw_display"),
            error_code=cached.get("error_code"),
            fetched_at=cached.get("fetched_at"),
            stale=True,
            last_seen_at=cached.get("fetched_at"),
            unreachable_reason=f"{type(exc).__name__}: {exc}",
        )

    fetched_at = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
    view = LiveStateView(
        state=state,
        power_kw_display=power_kw_display,
        error_code=error_code,
        fetched_at=fetched_at,
    )
    _save_cache(view)
    return view


def _load_cache() -> dict | None:
    try:
        return json.loads(_cache_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_cache(view: LiveStateView) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        k: v
        for k, v in asdict(view).items()
        if k in {"state", "power_kw_display", "error_code", "fetched_at"}
    }
    fd, tmp_path = tempfile.mkstemp(
        dir=path.parent, prefix=".wallbox_state.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise
