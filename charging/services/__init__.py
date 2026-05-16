"""Service layer for the charging app.

Modules here own the business logic so the views and management commands
stay thin. The ``ingest_*`` helpers are re-exported for convenience because
they're the single entry point for both the live importer and the
dashboard auto-import.
"""
from .ingest import ingest_csv_row, ingest_json_row

__all__ = ["ingest_csv_row", "ingest_json_row"]
