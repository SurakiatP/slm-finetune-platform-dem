"""Unit tests for pdf_loader.

Builds tiny in-memory PDFs via pypdf so we don't ship binary fixtures.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from pypdf import PdfWriter

from ai_engine.data_gen.pdf_loader import (
    PdfCorruptError,
    PdfTooLargeError,
    PdfTooManyPagesError,
    probe,
    to_base64_data_url,
)


def _make_pdf_bytes(num_pages: int) -> bytes:
    """Build a minimal valid PDF with `num_pages` blank pages."""
    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=72, height=72)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_probe_returns_size_and_pages():
    pdf = _make_pdf_bytes(3)
    p = probe(pdf)
    assert p.size_bytes == len(pdf)
    assert p.num_pages == 3


def test_probe_rejects_empty_bytes():
    with pytest.raises(PdfCorruptError):
        probe(b"")


def test_probe_rejects_garbage():
    with pytest.raises(PdfCorruptError):
        probe(b"this is definitely not a PDF")


def test_probe_rejects_oversized_pdf():
    pdf = _make_pdf_bytes(1)
    with pytest.raises(PdfTooLargeError):
        probe(pdf, max_bytes=10)  # cap below the file's actual size


def test_probe_rejects_too_many_pages():
    pdf = _make_pdf_bytes(5)
    with pytest.raises(PdfTooManyPagesError):
        probe(pdf, max_pages=2)


def test_to_base64_data_url_round_trips():
    import base64

    pdf = _make_pdf_bytes(1)
    url = to_base64_data_url(pdf)
    assert url.startswith("data:application/pdf;base64,")
    payload = url.removeprefix("data:application/pdf;base64,")
    decoded = base64.b64decode(payload)
    assert decoded == pdf


def test_to_base64_data_url_rejects_empty():
    with pytest.raises(ValueError):
        to_base64_data_url(b"")
