"""HTTP client for the documented KEBA KeMove REST API on port 8443.

Replaces the legacy PHP-scrape (``charging._legacy_keba_scrape``) as of
Phase 2.6. Endpoint reference: ``docs/keba_api.md``. JWT-based auth with
a 15-minute accessToken and a 30-day refreshToken; the 401 ladder is
refresh → fall back to full login → raise.

All HTTP funnels through ``_request`` so tests have one mock seam.
"""
from __future__ import annotations

import json
import os
import ssl
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class KebaAuthError(Exception):
    """Raised when the wallbox rejects credentials after a full retry ladder."""


class KebaTruncatedError(Exception):
    """Raised when the response body is shorter than its Content-Length header."""


@dataclass
class _Tokens:
    access_token: str
    refresh_token: str
    obtained_at: float


class KebaApiClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        verify_tls: bool = False,
        token_cache_path: Path,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.verify_tls = verify_tls
        self.token_cache_path = Path(token_cache_path)
        self.timeout = timeout
        self._tokens: _Tokens | None = self._load_cache()

    # ---- public API -----------------------------------------------------

    def login(self) -> None:
        status, _, body = self._request(
            "POST",
            "/v2/jwt/login",
            json_body={"username": self.username, "password": self.password},
        )
        if status != 200:
            raise KebaAuthError(f"Login failed (status {status})")
        data = json.loads(body)
        self._tokens = _Tokens(
            access_token=data["accessToken"],
            refresh_token=data["refreshToken"],
            obtained_at=time.time(),
        )
        self._save_cache(self._tokens)

    def refresh(self) -> None:
        if self._tokens is None:
            raise KebaAuthError("No refresh token available")
        status, _, body = self._request(
            "POST",
            "/v2/jwt/refresh",
            auth=f"Bearer {self._tokens.refresh_token}",
        )
        if status != 200:
            raise KebaAuthError(f"Refresh failed (status {status})")
        data = json.loads(body)
        self._tokens = _Tokens(
            access_token=data["accessToken"],
            refresh_token=self._tokens.refresh_token,
            obtained_at=time.time(),
        )
        self._save_cache(self._tokens)

    def export_sessions_csv(self) -> bytes:
        status, headers, body = self._authed_request(
            "GET", "/v2/sessions/export"
        )
        if status != 200:
            raise KebaAuthError(f"export_sessions_csv failed (status {status})")
        cl = headers.get("Content-Length")
        if cl is not None:
            expected = int(cl)
            if len(body) != expected:
                raise KebaTruncatedError(
                    f"Truncated CSV: Content-Length={expected}, got {len(body)} bytes"
                )
        return body

    def list_sessions(self) -> list[dict]:
        """Return the sessions array from /v2/sessions, MVA records included."""
        status, _, body = self._authed_request("GET", "/v2/sessions")
        if status != 200:
            raise KebaAuthError(f"list_sessions failed (status {status})")
        return json.loads(body)["sessions"]

    def get_state(self, serial: str) -> dict:
        status, _, body = self._authed_request(
            "GET", f"/v2/wallboxes/{serial}/state"
        )
        if status != 200:
            raise KebaAuthError(f"get_state failed (status {status})")
        return json.loads(body)

    def get_wallbox_info(self, serial: str) -> dict:
        status, _, body = self._authed_request(
            "GET", f"/v2/wallboxes/{serial}"
        )
        if status != 200:
            raise KebaAuthError(f"get_wallbox_info failed (status {status})")
        return json.loads(body)

    # ---- auth ladder ----------------------------------------------------

    def _authed_request(
        self, method: str, path: str, **kw
    ) -> tuple[int, dict, bytes]:
        if self._tokens is None:
            self.login()

        status, headers, body = self._send_with_access(method, path, **kw)
        if status != 401:
            return status, headers, body

        # 401: refresh; fall back to full login if refresh itself fails.
        try:
            self.refresh()
        except KebaAuthError:
            self.login()

        status, headers, body = self._send_with_access(method, path, **kw)
        if status != 401:
            return status, headers, body

        # Still 401 after refresh: try one final re-login.
        self.login()
        status, headers, body = self._send_with_access(method, path, **kw)
        if status == 401:
            raise KebaAuthError(
                "Authentication failed after refresh and re-login"
            )
        return status, headers, body

    def _send_with_access(
        self, method: str, path: str, **kw
    ) -> tuple[int, dict, bytes]:
        assert self._tokens is not None
        return self._request(
            method, path, auth=f"Bearer {self._tokens.access_token}", **kw
        )

    # ---- HTTP seam (single mock point) ---------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        auth: str | None = None,
    ) -> tuple[int, dict, bytes]:
        url = f"{self.base_url}{path}"
        data: bytes | None = None
        headers: dict[str, str] = {}
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if auth is not None:
            headers["Authorization"] = auth

        req = Request(url, data=data, method=method, headers=headers)
        ctx = self._ssl_context()
        try:
            with urlopen(req, timeout=self.timeout, context=ctx) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def _ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        if not self.verify_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    # ---- token cache ---------------------------------------------------

    def _load_cache(self) -> _Tokens | None:
        try:
            data = json.loads(self.token_cache_path.read_text())
            return _Tokens(
                access_token=data["access_token"],
                refresh_token=data["refresh_token"],
                obtained_at=data["obtained_at"],
            )
        except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
            return None

    def _save_cache(self, tokens: _Tokens) -> None:
        self.token_cache_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=self.token_cache_path.parent,
            prefix=".keba_token.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(asdict(tokens), f)
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, self.token_cache_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
            raise
