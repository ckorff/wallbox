"""HTTP client for the KEBA P30 web UI.

The wallbox web UI does not expose a documented API, but the JS-driven
session export (``/export.php?chargingsessions=``) returns a clean
semicolon-separated CSV once you hold a valid PHPSESSID cookie. The login
flow we replay mirrors what the browser does:

1. ``GET /`` — receive a fresh ``PHPSESSID`` cookie and a CSRF token
   embedded in ``<meta name="csrf-token">``.
2. ``POST /ajax.php`` — JSON body with ``username``, ``password`` and
   ``csrftoken``.
3. ``GET /export.php?chargingsessions=&t=<ms>`` — returns the CSV.
"""
from __future__ import annotations

import csv
import io
import json
import re
import time
from http.cookiejar import CookieJar
from urllib.request import HTTPCookieProcessor, Request, build_opener


DEFAULT_TIMEOUT = 5.0
_CSRF_RE = re.compile(
    r'<meta\s+name="csrf-token"\s+content="([0-9a-fA-F]+)"'
)


class KebaAuthError(Exception):
    """Raised when the wallbox login flow fails."""


def _open(opener, url, *, data=None, timeout):
    req = Request(url, data=data, method="POST" if data is not None else "GET")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with opener.open(req, timeout=timeout) as response:
        return response.read().decode("utf-8")


def _extract_csrf_token(html: str) -> str:
    match = _CSRF_RE.search(html)
    if not match:
        raise KebaAuthError("CSRF token not found in login page")
    return match.group(1)


def fetch_sessions_csv(
    host: str,
    username: str,
    password: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    opener = build_opener(HTTPCookieProcessor(CookieJar()))

    login_html = _open(opener, f"http://{host}/", timeout=timeout)
    csrf_token = _extract_csrf_token(login_html)

    payload = json.dumps(
        {"username": username, "password": password, "csrftoken": csrf_token}
    ).encode("utf-8")
    _open(opener, f"http://{host}/ajax.php", data=payload, timeout=timeout)

    cache_buster = int(time.time() * 1000)
    body = _open(
        opener,
        f"http://{host}/export.php?chargingsessions=&t={cache_buster}",
        timeout=timeout,
    )
    # On bad credentials the export endpoint 302s to the login page and
    # urllib silently follows the redirect, so we get HTML instead of CSV.
    if body.lstrip().startswith("<"):
        raise KebaAuthError(
            "Export returned HTML — login probably failed (check KEBA_USERNAME/KEBA_PASSWORD)"
        )
    return body


def parse_sessions_csv(text: str) -> list[dict[str, str]]:
    if not text.strip():
        return []
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    return [dict(row) for row in reader]
