"""CSV parsing for the KEBA charging-session export.

The wallbox emits the same semicolon-separated layout from both the
legacy web-GUI scrape (``/export.php``) and the documented REST API
(``/v2/sessions/export``) — verified byte-identical on 15 May 2026.
Lives in its own module so neither the new API client nor the
orchestration layer has to import from a ``_legacy_*`` file.
"""
from __future__ import annotations

import csv
import io


def parse_sessions_csv(text: str) -> list[dict[str, str]]:
    if not text.strip():
        return []
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    return [dict(row) for row in reader]
