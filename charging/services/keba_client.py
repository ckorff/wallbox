"""Build a KebaApiClient with credentials sourced from DB first, .env second.

Single helper used by both the live importer and the settings-page Eichrecht
live-fetch, so DB-vs-env precedence and the missing-credential error
message stay in one place.
"""
from __future__ import annotations

from pathlib import Path

from django.conf import settings

from charging.keba_api import KebaApiClient
from charging.models import AppSettings


def build_keba_client() -> KebaApiClient:
    """Construct a KebaApiClient using AppSettings (DB) with .env fallback.

    Raises ``RuntimeError`` when the URL is missing or when neither source
    supplies a username/password. The error message points the user at the
    settings page first, since DB values take precedence.
    """
    app = AppSettings.current()
    username = app.keba_api_username or settings.KEBA_API_USERNAME
    password = app.keba_api_password or settings.KEBA_API_PASSWORD

    if not settings.KEBA_API_URL:
        raise RuntimeError("KEBA_API_URL is not set in .env.")
    if not (username and password):
        raise RuntimeError(
            "Wallbox API credentials missing — set them on the /settings/ "
            "page or in .env (KEBA_API_USERNAME / KEBA_API_PASSWORD)."
        )

    return KebaApiClient(
        base_url=settings.KEBA_API_URL,
        username=username,
        password=password,
        verify_tls=settings.KEBA_API_VERIFY_TLS,
        token_cache_path=Path(settings.MEDIA_ROOT) / ".keba_token.json",
    )
