"""PDF helpers for QA-with-PDF seed uploads.

Two responsibilities:
  • probe (size + page count) at upload time, before persisting the bytes
  • base64-encode the bytes for OpenRouter's multimodal call (Phase 9 §10.4)

We do NOT extract text here — the multimodal Generator reads the PDF
directly. pypdf is used only for the page-count probe.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .constants import MAX_SEED_PDF_BYTES, MAX_SEED_PDF_PAGES

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PdfProbe:
    """Result of inspecting a PDF before persisting it."""

    size_bytes: int
    num_pages: int


class PdfTooLargeError(ValueError):
    """Raised when a PDF exceeds the byte cap."""


class PdfTooManyPagesError(ValueError):
    """Raised when a PDF has more pages than the configured cap."""


class PdfCorruptError(ValueError):
    """Raised when pypdf cannot parse the bytes as a PDF."""


def probe(
    pdf_bytes: bytes,
    *,
    max_bytes: int = MAX_SEED_PDF_BYTES,
    max_pages: int = MAX_SEED_PDF_PAGES,
) -> PdfProbe:
    """Inspect bytes and return (size, num_pages). Raises on any limit breach.

    Pure CPU; no network. Safe to call inline in an async handler — pypdf
    is fast enough for sub-100-page PDFs that we don't bother offloading.
    """
    size = len(pdf_bytes)
    if size == 0:
        raise PdfCorruptError("empty PDF bytes")
    if size > max_bytes:
        raise PdfTooLargeError(
            f"PDF size {size} bytes exceeds cap {max_bytes} "
            f"({max_bytes // (1024 * 1024)} MiB)"
        )

    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        # Force iteration so corruption surfaces as PdfReadError, not at
        # some later access.
        num_pages = len(reader.pages)
    except (PdfReadError, ValueError, KeyError, OSError) as exc:
        raise PdfCorruptError(f"failed to parse PDF: {exc}") from exc

    if num_pages == 0:
        raise PdfCorruptError("PDF has zero pages")
    if num_pages > max_pages:
        raise PdfTooManyPagesError(
            f"PDF has {num_pages} pages; cap is {max_pages}"
        )

    return PdfProbe(size_bytes=size, num_pages=num_pages)


def to_base64_data_url(pdf_bytes: bytes) -> str:
    """Encode `pdf_bytes` as a `data:application/pdf;base64,...` URL.

    OpenRouter's multimodal API for Gemini accepts this form inside the
    user content array (see §10.4 of the Phase 9 spec).
    """
    if not pdf_bytes:
        raise ValueError("pdf_bytes is empty")
    encoded = base64.b64encode(pdf_bytes).decode("ascii")
    return f"data:application/pdf;base64,{encoded}"


__all__ = [
    "PdfProbe",
    "PdfTooLargeError",
    "PdfTooManyPagesError",
    "PdfCorruptError",
    "probe",
    "to_base64_data_url",
]
