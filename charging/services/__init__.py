"""Service layer for the charging app.

Modules here own the business logic so the views and management commands
stay thin. ``ingest_json_row`` is re-exported because it's the single
entry point both the live importer and the dashboard auto-import use to
persist one wallbox session.
"""
from .ingest import ingest_json_row

__all__ = ["ingest_json_row"]
