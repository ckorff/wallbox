"""Combine a generated report PDF with the active tariff document.

Called from the email dispatcher at send time, not at report-generation
time — the stored report PDF in ``media/reports/`` stays untouched and
the merged attachment is built fresh from the current tariff document.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from pypdf import PdfWriter


def merge_report_with_tariff(report_pdf: Path | str, tariff_pdf: Path | str) -> BytesIO:
    """Return an in-memory PDF with the report first, then the tariff."""
    writer = PdfWriter()
    writer.append(str(report_pdf))
    writer.append(str(tariff_pdf))
    buffer = BytesIO()
    writer.write(buffer)
    writer.close()
    buffer.seek(0)
    return buffer
