"""Tests for charging.services.wallbox_state (Phase 2.9)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from charging.services.wallbox_state import fetch_live_state


def _write_archived_key(media_root: Path, serial: str = "34416115") -> None:
    media_root.mkdir(parents=True, exist_ok=True)
    (media_root / "wallbox_mva_public_key.json").write_text(
        json.dumps({"wallbox_serial": serial, "public_key_hex": "deadbeef"})
    )


class FetchLiveStateTests(TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.media_root = Path(self._tmpdir.name)
        self._settings_cm = override_settings(MEDIA_ROOT=str(self.media_root))
        self._settings_cm.enable()

    def tearDown(self):
        self._settings_cm.disable()
        self._tmpdir.cleanup()

    def test_not_linked_when_no_archived_key(self):
        # No archived public key file → never calls build_keba_client.
        with patch(
            "charging.services.keba_client.build_keba_client"
        ) as build_mock:
            view = fetch_live_state()

        self.assertTrue(view.not_linked)
        self.assertIsNone(view.state)
        build_mock.assert_not_called()

    def test_credentials_missing_surfaces_distinctly(self):
        _write_archived_key(self.media_root)

        with patch(
            "charging.services.keba_client.build_keba_client",
            side_effect=RuntimeError("Wallbox API credentials missing"),
        ):
            view = fetch_live_state()

        self.assertTrue(view.credentials_missing)
        self.assertFalse(view.not_linked)
        self.assertIn("credentials missing", view.unreachable_reason)

    def test_idle_state_no_extra_info_call(self):
        _write_archived_key(self.media_root)
        client = MagicMock()
        client.get_state.return_value = {"state": "IDLE"}

        with patch(
            "charging.services.keba_client.build_keba_client",
            return_value=client,
        ):
            view = fetch_live_state()

        self.assertEqual(view.state, "IDLE")
        self.assertIsNone(view.power_w)
        self.assertFalse(view.stale)
        client.get_state.assert_called_once_with("34416115")
        client.get_wallbox_info.assert_not_called()

    def test_charging_pulls_power_from_wallbox_info(self):
        _write_archived_key(self.media_root)
        client = MagicMock()
        client.get_state.return_value = {"state": "CHARGING"}
        client.get_wallbox_info.return_value = {
            "meter": {"totalActivePower": 11023}
        }

        with patch(
            "charging.services.keba_client.build_keba_client",
            return_value=client,
        ):
            view = fetch_live_state()

        self.assertEqual(view.state, "CHARGING")
        self.assertEqual(view.power_w, 11023)
        client.get_wallbox_info.assert_called_once_with("34416115")

    def test_unreachable_with_no_cache_returns_unreachable_reason(self):
        _write_archived_key(self.media_root)
        client = MagicMock()
        client.get_state.side_effect = TimeoutError("read timeout")

        with patch(
            "charging.services.keba_client.build_keba_client",
            return_value=client,
        ):
            view = fetch_live_state()

        self.assertIsNone(view.state)
        self.assertFalse(view.stale)
        self.assertIn("TimeoutError", view.unreachable_reason)

    def test_unreachable_with_cache_returns_stale_view(self):
        _write_archived_key(self.media_root)
        # Seed the cache with a previous successful read.
        (self.media_root / ".wallbox_state.json").write_text(
            json.dumps(
                {
                    "state": "IDLE",
                    "power_w": None,
                    "error_code": None,
                    "fetched_at": "2026-05-16T08:00:00+00:00",
                }
            )
        )
        client = MagicMock()
        client.get_state.side_effect = OSError("connection refused")

        with patch(
            "charging.services.keba_client.build_keba_client",
            return_value=client,
        ):
            view = fetch_live_state()

        self.assertEqual(view.state, "IDLE")
        self.assertTrue(view.stale)
        self.assertEqual(view.last_seen_at, "2026-05-16T08:00:00+00:00")
        self.assertIn("OSError", view.unreachable_reason)

    def test_successful_call_writes_cache_file(self):
        _write_archived_key(self.media_root)
        client = MagicMock()
        client.get_state.return_value = {"state": "IDLE"}

        with patch(
            "charging.services.keba_client.build_keba_client",
            return_value=client,
        ):
            fetch_live_state()

        cache = json.loads(
            (self.media_root / ".wallbox_state.json").read_text()
        )
        self.assertEqual(cache["state"], "IDLE")
        self.assertIn("fetched_at", cache)
