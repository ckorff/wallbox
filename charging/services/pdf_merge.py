"""Combine a generated report PDF with one or more tariff documents.

Called from the email dispatcher at send time, not at report-generation
time — the stored report PDF in ``media/reports/`` stays untouched and
the merged attachment is built fresh from the current tariff documents.
"""
from __future__ import annotations

from collections.abc import Sequence
from io import BytesIO
from pathlib import Path

from pypdf import PdfWriter


def merge_report_with_tariffs(
    report_pdf: Path | str,
    tariff_pdfs: Sequence[Path | str],
) -> BytesIO:
    """Return an in-memory PDF: report first, then each tariff in order."""
    writer = PdfWriter()
    writer.append(str(report_pdf))
    for tariff_pdf in tariff_pdfs:
        writer.append(str(tariff_pdf))
    buffer = BytesIO()
    writer.write(buffer)
    writer.close()
    buffer.seek(0)
    return buffer
