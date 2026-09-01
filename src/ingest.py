"""PDF ingestion: text extraction, whitespace cleaning, and chunking.

This module is pure data logic — no Streamlit calls — so it can be reused and
unit-tested independently. It is deliberately robust to messy PDFs: per-page
extraction failures are logged and skipped rather than aborting the whole file.
"""

from __future__ import annotations

import hashlib
import logging
import re
from io import BytesIO
from pathlib import Path
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

from src.config import CHUNK_OVERLAP, CHUNK_SIZE

logger = logging.getLogger(__name__)

_WHITESPACE_RE = re.compile(r"\s+")


def _clean(text: str) -> str:
    """Collapse runs of whitespace into single spaces and strip the ends."""
    return _WHITESPACE_RE.sub(" ", text or "").strip()


def file_hash(data: bytes) -> str:
    """Return the sha256 hex digest of raw file bytes (used for de-dup)."""
    return hashlib.sha256(data).hexdigest()


def _load_pdf_bytes(file: Any) -> bytes:
    if isinstance(file, (str, Path)):
        return Path(file).read_bytes()

    if hasattr(file, "getvalue"):
        return file.getvalue()

    if hasattr(file, "read"):
        data = file.read()
        try:
            file.seek(0)
        except Exception:
            pass
        return data

    raise TypeError("Unsupported PDF source type")


def _try_ocr_pdf(data: bytes) -> list[dict]:
    try:
        import pytesseract
        from pdf2image import convert_from_bytes
    except ImportError:
        logger.info(
            "OCR fallback unavailable: install pytesseract and pdf2image to extract text from scanned PDFs."
        )
        return []

    try:
        images = convert_from_bytes(data, dpi=300)
    except Exception as exc:
        logger.warning("OCR conversion failed: %s", exc)
        return []

    pages: list[dict] = []
    for index, image in enumerate(images, start=1):
        try:
            raw = pytesseract.image_to_string(image.convert("RGB"), lang="eng", config="--psm 3")
        except Exception as exc:
            logger.warning("OCR failed on page %d: %s", index, exc)
            continue

        text = _clean(raw)
        if not text:
            logger.info("Skipping page %d (OCR found no text)", index)
            continue

        pages.append({"page": index, "text": text})

    return pages


def extract_pages(file: Any, page_range: list[int] | None = None) -> list[dict]:
    """Extract per-page text from a PDF, optionally filtering by page range (1-indexed, inclusive).

    Args:
        file: A path string or a file-like object (e.g. a Streamlit upload).
        page_range: Optional [start_page, end_page] list.

    Returns:
        A list of ``{"page": int, "text": str}`` dicts, 1-indexed by page,
        with empty/blank pages skipped.
    """
    data = _load_pdf_bytes(file)
    pages: list[dict] = []

    try:
        reader = PdfReader(BytesIO(data))
    except Exception as exc:  # noqa: BLE001 - surface a friendly message upstream
        logger.error("Failed to open PDF: %s", exc)
        raise

    for index, page in enumerate(reader.pages, start=1):
        if page_range and not (page_range[0] <= index <= page_range[1]):
            continue
        try:
            raw = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001 - one bad page shouldn't kill the file
            logger.warning("Skipping page %d (extraction error): %s", index, exc)
            continue

        text = _clean(raw)
        if not text:
            logger.info("Skipping page %d (no extractable text)", index)
            continue

        pages.append({"page": index, "text": text})

    if not pages:
        logger.info("No extractable text found in PDF, attempting OCR fallback.")
        pages = _try_ocr_pdf(data)
        if page_range:
            pages = [p for p in pages if page_range[0] <= p["page"] <= page_range[1]]

    return pages


def chunk_pages(pages: list[dict], source_name: str) -> list[dict]:
    """Split page texts into overlapping chunks with source metadata.

    Args:
        pages: Output of :func:`extract_pages`.
        source_name: Original filename, stored as chunk metadata.

    Returns:
        A list of chunk dicts, each shaped as::

            {
                "id": "<hash>",
                "text": "<chunk text>",
                "metadata": {
                    "source": source_name,
                    "page": page_number,
                    "chunk_index": i,
                },
            }
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )

    chunks: list[dict] = []
    running_index = 0
    for page in pages:
        page_number = page["page"]
        for piece in splitter.split_text(page["text"]):
            piece = piece.strip()
            if not piece:
                continue
            chunk_id = hashlib.sha256(f"{source_name}_{running_index}_{piece}".encode('utf-8')).hexdigest()
            chunks.append(
                {
                    "id": chunk_id,
                    "text": piece,
                    "metadata": {
                        "source": source_name,
                        "page": page_number,
                        "chunk_index": running_index,
                    },
                }
            )
            running_index += 1

    return chunks
